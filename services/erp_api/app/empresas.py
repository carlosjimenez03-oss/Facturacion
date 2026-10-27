"""Mis empresas (multiempresa, ver docs/MULTIEMPRESA.md y
libs/ecf_common/empresa.py): las empresas del usuario, agregar una nueva
(RNC + datos fiscales), elegir con cuál trabajar (se recuerda por usuario) y
los miembros de cada una (Configuración → Usuarios, ver configuracion.py).

Contrato con el frontend: `frontend/src/lib/empresasApi.ts`.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from libs.ecf_common.empresa import (
    EMPRESA_PREDETERMINADA,
    aislamiento_disponible,
    cabeceras_empresa,
    empresa_actual,
    SQL_LEGADO,
    en_empresa,
    es_admin_plataforma,
    invalidar_cache,
    rol_en_empresa,
)

from libs.ecf_common.auth import certificado_activo

from . import configuracion_repository as conf_repo
from . import membresia, padron
from .config import ErpApiSettings, get_settings
from .contabilidad_calculos import ddl_semilla_catalogo
from .padron import Contribuyente
from .repository import DatosInvalidosError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/empresas", tags=["empresas"])

Rol = Literal["admin", "usuario"]

SIN_AISLAMIENTO = (
    "La base de datos no puede aislar los datos de cada empresa (falta el rol kontara_app o la conexión es por el "
    "pooler de Supabase en modo transacción): ver docs/MULTIEMPRESA.md. Mientras tanto se trabaja con una sola empresa."
)
SOLO_ADMIN = "Solo un administrador de la empresa puede hacer esto"
SOLO_PLATAFORMA = (
    "Solo un administrador de la instalación puede agregar empresas (KONTARA_ADMIN_EMAILS o la marca kontara_admin; "
    "ver docs/MULTIEMPRESA.md)"
)


class EmpresaResumen(BaseModel):
    id: int
    rnc: str | None = None
    razon_social: str
    nombre_comercial: str | None = None
    estado: str
    rol: Rol
    predeterminada: bool


class Invitacion(BaseModel):
    id: int
    rnc: str | None = None
    razon_social: str
    nombre_comercial: str | None = None
    rol: Rol
    agregado_por: str | None = None


class MisEmpresas(BaseModel):
    actual: int
    empresas: list[EmpresaResumen]
    # Invitaciones pendientes a otras empresas (las acepta el usuario).
    invitaciones: list[Invitacion] = []
    # El usuario puede agregar empresas (administrador de la instalación) y
    # la base las puede aislar.
    puede_agregar: bool
    aislamiento: bool
    motivo: str | None = None


class NuevaEmpresa(BaseModel):
    rnc: str = Field(..., min_length=9, max_length=20)
    razon_social: str = Field(..., min_length=1, max_length=250)
    nombre_comercial: str | None = Field(None, max_length=250)
    actividad_economica: str | None = Field(None, max_length=250)
    direccion: str | None = Field(None, max_length=500)
    provincia: str | None = Field(None, max_length=120)
    municipio: str | None = Field(None, max_length=120)
    telefono: str | None = Field(None, max_length=40)
    correo: str | None = Field(None, max_length=250)


class Seleccion(BaseModel):
    empresa_id: int = Field(..., ge=1)


class RespuestaInvitacion(BaseModel):
    aceptar: bool


class ConsultaRnc(BaseModel):
    rnc: str
    # Ya es una empresa de esta instalación (no se puede agregar de nuevo).
    registrada: bool
    # Padrón de RNC de la DGII (padron.py): razón social, nombre comercial,
    # actividad, estado y régimen. None = no aparece o el padrón no se ha
    # importado (padron_detalle lo explica).
    padron: Contribuyente | None = None
    padron_detalle: str | None = None
    # Lo que dice el directorio de contribuyentes electrónicos de la DGII
    # (consulta_directorio, solo con certificado digital); `consultado`
    # false si no se pudo consultar (`detalle` lo explica).
    consultado: bool
    nombre: str | None = None
    emisor_electronico: bool | None = None
    detalle: str | None = None


def _pool(request: Request):
    return request.app.state.db_pool


def _usuario(request: Request):
    return getattr(request.state, "usuario", None)


def _digitos(texto: str | None) -> str:
    return re.sub(r"\D", "", texto or "")


def es_admin(request: Request) -> bool:
    """Admin de la empresa del request. Sin inicio de sesión configurado no
    hay a quién pedirle permisos: se permite (como el resto de la aplicación)."""
    usuario = _usuario(request)
    return usuario is None or bool(usuario.admin)


def exigir_admin(request: Request) -> None:
    if not es_admin(request):
        raise HTTPException(status_code=403, detail=SOLO_ADMIN)


# --- consultas ------------------------------------------------------------------

# Sus membresías activas en empresas activas (y, sin ninguna fila, la 1:
# ver libs/ecf_common/empresa.py, _SQL_ROL).
_SQL_MIS_EMPRESAS = """
SELECT e.id, e.rnc, e.razon_social, e.nombre_comercial, e.estado, eu.rol
FROM configuracion.empresas e
JOIN configuracion.empresa_usuarios eu ON eu.empresa_id = e.id AND eu.usuario_id = $1 AND eu.estado = 'activo'
WHERE e.estado = 'activa'
UNION ALL
SELECT e.id, e.rnc, e.razon_social, e.nombre_comercial, e.estado,
       CASE WHEN $2 OR COALESCE((SELECT previos_admin FROM configuracion.multiempresa_legado WHERE id = 1), false)
            THEN 'admin' ELSE 'usuario' END
FROM configuracion.empresas e
WHERE e.id = 1 AND NOT EXISTS (SELECT 1 FROM configuracion.empresa_usuarios WHERE usuario_id = $1)
ORDER BY 1
"""

_SQL_TODAS = """
SELECT id, rnc, razon_social, nombre_comercial, estado, 'admin' AS rol
FROM configuracion.empresas WHERE estado = 'activa' ORDER BY id
"""


async def mis_empresas(pool, usuario) -> list[dict[str, Any]]:
    async with pool.acquire() as conn:
        if usuario is None:
            filas = await conn.fetch(_SQL_TODAS)
        else:
            filas = await conn.fetch(_SQL_MIS_EMPRESAS, usuario.id, bool(usuario.admin_global))
    return [{**dict(f), "predeterminada": f["id"] == EMPRESA_PREDETERMINADA} for f in filas]


async def inicializar_empresa(pool, empresa_id: int) -> None:
    """Datos que cada empresa nueva necesita desde el primer día y que en la
    empresa 1 sembraron las migraciones: el catálogo de cuentas sugerido y
    sus cuentas por defecto. (El almacén principal, la fila de contingencia
    y las configuraciones de Soluciones se crean solas al primer uso.)"""
    with en_empresa(empresa_id):
        async with pool.acquire() as conn:
            await conn.execute(ddl_semilla_catalogo(conflicto="(empresa_id, concepto)"))


async def crear_empresa(pool, datos: NuevaEmpresa, usuario) -> dict[str, Any]:
    razon, rnc, correo, _ = conf_repo.validar_datos_empresa(
        razon_social=datos.razon_social, rnc=datos.rnc, correo=datos.correo, logo=None
    )
    if not rnc:
        raise DatosInvalidosError("El RNC es obligatorio")
    texto = conf_repo._texto  # noqa: SLF001 (mismo criterio de limpieza que Configuración → Empresa)
    async with pool.acquire() as conn:
        async with conn.transaction():
            if await conn.fetchval("SELECT 1 FROM configuracion.empresas WHERE rnc = $1", rnc):
                raise conf_repo.RncEnUsoError(f"El RNC {rnc} ya es una empresa de esta instalación")
            fila = await conn.fetchrow(
                """
                INSERT INTO configuracion.empresas
                    (rnc, razon_social, nombre_comercial, actividad_economica, direccion, provincia, municipio,
                     telefono, correo, creado_por)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                RETURNING id, rnc, razon_social, nombre_comercial, estado
                """,
                rnc,
                razon,
                texto(datos.nombre_comercial),
                texto(datos.actividad_economica),
                texto(datos.direccion),
                texto(datos.provincia),
                texto(datos.municipio),
                texto(datos.telefono),
                correo,
                usuario.correo if usuario is not None else None,
            )
            if usuario is not None:
                # Quien la crea, si es un usuario de antes sin filas: su fila
                # de la empresa 1 primero (normalmente ya la escribió su
                # inicio de sesión), para que no la pierda al tener esta.
                await conn.execute(SQL_LEGADO, usuario.id, usuario.correo, bool(usuario.admin_global or usuario.admin))
                await conn.execute(
                    """
                    INSERT INTO configuracion.empresa_usuarios (empresa_id, usuario_id, correo, rol, agregado_por)
                    VALUES ($1, $2, $3, 'admin', $3)
                    """,
                    fila["id"],
                    usuario.id,
                    usuario.correo,
                )
    invalidar_cache()
    await inicializar_empresa(pool, fila["id"])
    logger.info("Empresa %s (RNC %s) agregada por %s", fila["id"], rnc, usuario.correo if usuario else "sin sesión")
    return {**dict(fila), "rol": "admin", "predeterminada": False}


async def recordar_seleccion(pool, usuario_id: str, empresa_id: int) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO configuracion.usuario_empresa_actual (usuario_id, empresa_id) VALUES ($1, $2)
            ON CONFLICT (usuario_id) DO UPDATE SET empresa_id = EXCLUDED.empresa_id, actualizado_en = now()
            """,
            usuario_id,
            empresa_id,
        )
    invalidar_cache()


SIN_CERTIFICADO_DIRECTORIO = (
    "Para saber si es emisor electrónico hace falta el certificado digital (.p12) de la empresa en uso "
    "(Configuración → Certificado digital): la DGII solo responde esa consulta con él."
)
DIRECTORIO_NO_RESPONDE = (
    "No se pudo saber si es emisor electrónico: la consulta del directorio de la DGII no respondió "
    "(necesita el certificado digital vigente de la empresa en uso). Puede seguir: no hace falta para agregarla."
)


def _hay_certificado(settings: ErpApiSettings) -> bool:
    try:
        estado = certificado_activo.estado_certificado(settings.certificado_path, settings.certificado_password, "")
    except Exception:  # noqa: BLE001 (best-effort: sin certificado legible no se consulta)
        return False
    return estado.get("estado") in ("vigente", "por_vencer")


async def consultar_directorio(settings: ErpApiSettings, rnc: str) -> dict[str, Any]:
    """Si el RNC está en el directorio de emisores electrónicos de la DGII
    (consulta_directorio). Best-effort y solo con certificado digital (la
    DGII pide el token de la empresa): sin él, se explica y se sigue."""
    if not await asyncio.to_thread(_hay_certificado, settings):
        return {"consultado": False, "detalle": SIN_CERTIFICADO_DIRECTORIO}
    try:
        async with httpx.AsyncClient(timeout=settings.consulta_rnc_timeout_segundos) as cliente:
            respuesta = await cliente.get(
                f"{settings.consulta_directorio_url.rstrip('/')}/directorio/{rnc}", headers=cabeceras_empresa()
            )
    except httpx.HTTPError:
        return {"consultado": False, "detalle": DIRECTORIO_NO_RESPONDE}
    if respuesta.status_code >= 400:
        logger.info("consulta_directorio respondió %s para %s", respuesta.status_code, rnc)
        return {"consultado": False, "detalle": DIRECTORIO_NO_RESPONDE}
    try:
        entradas = respuesta.json()
    except ValueError:
        return {"consultado": False, "detalle": DIRECTORIO_NO_RESPONDE}
    entrada = next((e for e in entradas if isinstance(e, dict) and _digitos(e.get("rnc")) == rnc), None) if isinstance(
        entradas, list
    ) else None
    if entrada is None:
        return {
            "consultado": True,
            "emisor_electronico": False,
            "detalle": "El RNC no aparece en el directorio de emisores electrónicos de la DGII",
        }
    return {"consultado": True, "emisor_electronico": True, "nombre": entrada.get("nombre")}


# --- rutas ------------------------------------------------------------------

@router.get("", response_model=MisEmpresas)
async def listar(request: Request):
    pool = _pool(request)
    usuario = _usuario(request)
    aislamiento = await aislamiento_disponible(pool)
    return {
        "actual": empresa_actual(),
        "empresas": await mis_empresas(pool, usuario),
        "invitaciones": await membresia.invitaciones(pool, usuario.id) if usuario is not None else [],
        "puede_agregar": aislamiento and await es_admin_plataforma(pool, usuario),
        "aislamiento": aislamiento,
        "motivo": None if aislamiento else SIN_AISLAMIENTO,
    }


@router.post("", response_model=EmpresaResumen, status_code=201)
async def agregar(request: Request, payload: NuevaEmpresa):
    """Agrega una empresa (su RNC y datos fiscales). Quien la agrega queda
    como su administrador; luego carga su certificado, sus secuencias y su
    ambiente DGII en Configuración, ya dentro de ella."""
    pool = _pool(request)
    if not await es_admin_plataforma(pool, _usuario(request)):
        raise HTTPException(status_code=403, detail=SOLO_PLATAFORMA)
    if not await aislamiento_disponible(pool):
        raise HTTPException(status_code=409, detail=SIN_AISLAMIENTO)
    try:
        return await crear_empresa(pool, payload, _usuario(request))
    except DatosInvalidosError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except conf_repo.RncEnUsoError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.put("/actual", response_model=Seleccion)
async def elegir(request: Request, payload: Seleccion):
    """Recuerda la empresa con que trabaja el usuario (la que se abre la
    próxima vez si el navegador no tiene otra guardada)."""
    usuario = _usuario(request)
    pool = _pool(request)
    if usuario is not None:
        rol = await rol_en_empresa(pool, usuario.id, payload.empresa_id, admin_global=usuario.admin_global)
        if rol is None:
            raise HTTPException(status_code=403, detail="Su usuario no tiene acceso a esa empresa")
        await recordar_seleccion(pool, usuario.id, payload.empresa_id)
    return payload


@router.post("/{empresa_id}/invitacion", response_model=MisEmpresas)
async def responder_invitacion(request: Request, empresa_id: int, payload: RespuestaInvitacion):
    """El usuario invitado acepta (entra a la empresa) o rechaza."""
    usuario = _usuario(request)
    if usuario is None:
        raise HTTPException(status_code=409, detail="Las invitaciones necesitan el inicio de sesión")
    if not await membresia.responder_invitacion(_pool(request), usuario.id, empresa_id, payload.aceptar):
        raise HTTPException(status_code=404, detail="No hay una invitación pendiente a esa empresa")
    return await listar(request)


@router.get("/consulta-rnc/{rnc}", response_model=ConsultaRnc)
async def consulta_rnc(request: Request, rnc: str, settings: ErpApiSettings = Depends(get_settings)):
    """Antes de agregar una empresa: si ya está registrada aquí, sus datos en
    el padrón de RNC de la DGII (razón social, nombre comercial, actividad,
    estado, régimen) y, con certificado, si es emisor electrónico."""
    limpio = _digitos(rnc)
    if len(limpio) not in (9, 11):
        raise HTTPException(status_code=422, detail="El RNC debe tener 9 dígitos (o 11 si es una cédula)")
    pool = _pool(request)
    async with pool.acquire() as conn:
        registrada = bool(await conn.fetchval("SELECT 1 FROM configuracion.empresas WHERE rnc = $1", limpio))
    en_padron, detalle_padron = None, None
    try:
        en_padron = await padron.buscar(pool, settings, limpio)
        if en_padron is None:
            async with pool.acquire() as conn:
                detalle_padron = padron.texto_no_encontrado(limpio, await padron.leer_estado(conn))
    except padron.PadronNoDisponibleError as exc:
        detalle_padron = str(exc)
    directorio = await consultar_directorio(settings, limpio)
    if en_padron is not None:
        directorio["nombre"] = en_padron["razon_social"]
    return {"rnc": limpio, "registrada": registrada, "padron": en_padron, "padron_detalle": detalle_padron, **directorio}


# --- rutas públicas (portal de clientes, retorno de la pasarela de pagos) ---------------------------

_RUTA_PAGO = re.compile(r"^/portal/publico/pagos/([A-Za-z0-9_-]{1,64})(?:/|$)")
_RUTA_PORTAL = re.compile(r"^/portal/publico/([A-Za-z0-9_-]{32,64})(?:/|$)")


async def empresa_de_ruta_publica(request: Request, pool) -> int | None:
    """Las rutas públicas no traen empresa: es la del enlace del portal (por
    el hash de su token) o la del pago en línea (por su referencia), que se
    buscan en todas las empresas (siguen siendo únicos globales). None =
    no existe: la ruta responde su 404 de siempre."""
    from libs.ecf_common.empresa import todas_las_empresas

    from .soluciones_calculos import hash_token

    ruta = request.url.path
    pago = _RUTA_PAGO.match(ruta)
    portal = None if pago else _RUTA_PORTAL.match(ruta)
    if not (pago or portal):
        return None
    with todas_las_empresas():
        async with pool.acquire() as conn:
            if pago:
                if await conn.fetchval("SELECT to_regclass('soluciones.pagos_en_linea')") is None:
                    return None
                return await conn.fetchval(
                    "SELECT empresa_id FROM soluciones.pagos_en_linea WHERE referencia = $1", pago.group(1)
                )
            return await conn.fetchval(
                "SELECT empresa_id FROM soluciones.portal_enlaces WHERE token_hash = $1", hash_token(portal.group(1))
            )
