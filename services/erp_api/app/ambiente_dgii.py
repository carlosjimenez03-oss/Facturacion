"""Configuración → Ambiente DGII (fase 7): cambiar entre TesteCF, CerteCF y
eCF sin tocar las variables de Dokploy. Contrato con el frontend:
`frontend/src/lib/configuracionApi.ts` (`EstadoAmbiente`, `AmbientePayload`).

El ambiente elegido queda en `configuracion.ambiente_dgii` (una fila) y lo
leen en tiempo de ejecución facturas-api, recepcion y los servicios de
consulta (libs/ecf_common/ambiente.py: la fila manda sobre APP_ENV, caché
de 15 s; sin fila, APP_ENV como siempre). Cada cambio queda en
`configuracion.ambiente_dgii_cambios` (quién y cuándo).

Reglas del cambio (`PUT /configuracion/ambiente`):

- Solo un administrador (`app_metadata.kontara_admin` o KONTARA_ADMIN_EMAILS;
  403 si no). Sin inicio de sesión configurado (SUPABASE_JWT_SECRET vacío,
  desarrollo local) se permite y la auditoría queda sin usuario.
- No con facturas en `contingencia` ni lotes de contingencia abiertos
  (`en_cola`, `enviando`, `interrumpido`): 409. Esos e-CF ya están firmados
  para el ambiente actual y deben llegar a él (la DGII los recibe en un
  plazo de 72 horas, ver docs/dgii/CONTINGENCIA.md).
- A producción (eCF): con `confirmacion = true` (el diálogo del frontend,
  que advierte que desde ese momento los comprobantes tienen validez
  fiscal; 422 sin ella) y con un certificado vigente que pertenezca al RNC
  de la empresa (409 si no: la DGII no autentica sin él y todo envío
  fallaría).
- Las secuencias e-NCF son por ambiente (la DGII autoriza rangos distintos
  en cada uno): si el ambiente nuevo no tiene ninguna activa, el cambio se
  hace igual pero la respuesta lo advierte (`avisos`).

Las facturas ya creadas conservan su ambiente (ver
services/facturas_api/app/ambiente.py): las enviadas se siguen consultando
en el suyo y las no enviadas de otro ambiente no se pueden enviar.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Literal

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from libs.ecf_common.ambiente import (
    AMBIENTES,
    AmbienteDGII,
    AmbienteInvalidoError,
    leer_ambiente_guardado,
    nombre,
    validar_ambiente,
)
from libs.ecf_common.auth import certificado_activo
from libs.ecf_common.auth.sesion_supabase import SesionInvalidaError, UsuarioSesion, usuario_de_request
from libs.ecf_common.empresa import EMPRESA_PREDETERMINADA, empresa_actual
from libs.ecf_common.environments import dgii_environment_for

from . import configuracion_repository as repo
from .config import ErpApiSettings, get_settings

router = APIRouter(prefix="/configuracion", tags=["configuracion"])

# Ambiente activo para erp-api (secuencias, datos de la empresa, estado de
# las soluciones, timbre del POS). Al guardar se fija sin esperar la caché.
ACTIVO = AmbienteDGII(get_settings().app_env)

# Distinta de las demás claves de advisory lock del repo (7_0SS_NNN).
_LOCK_CAMBIO = 7_013_002

# Lotes de contingencia que todavía tienen e-CF por enviar (ver
# services/facturas_api/app/contingencia_repository.py).
_LOTES_ABIERTOS = ("en_cola", "enviando", "interrumpido")

ESTADOS_CERTIFICADO_VALIDOS = ("vigente", "por_vencer")

AVISO_PRODUCCION = (
    "Desde este cambio cada comprobante que se emita y envíe a la DGII tiene validez fiscal: es un "
    "comprobante real que cuenta para el ITBIS, el ISR y los formatos 606/607 de la empresa y del cliente."
)


class AmbientePayload(BaseModel):
    ambiente: str = Field(..., description="test | qa | production (también testecf, certecf, ecf)")
    # El usuario confirmó el diálogo de validez fiscal (obligatorio para eCF).
    confirmacion: bool = False


class CambioAmbiente(BaseModel):
    id: int
    anterior: str
    nuevo: str
    usuario_id: str | None = None
    usuario_correo: str | None = None
    creado_en: datetime


class CertificadoParaProduccion(BaseModel):
    estado: str
    valido: bool
    motivo: str | None = None


class EstadoAmbiente(BaseModel):
    ambiente: Literal["test", "qa", "production"]
    ambiente_dgii: str
    nombre: str
    # 'configuracion' = elegido aquí; 'variables' = APP_ENV (nunca se cambió aquí).
    origen: Literal["configuracion", "variables"]
    app_env: str
    cambiado_por: str | None = None
    cambiado_en: datetime | None = None
    puede_cambiar: bool
    motivo_sin_permiso: str | None = None
    # Por qué no se puede cambiar ahora (contingencia, lotes abiertos).
    bloqueos: list[str] = Field(default_factory=list)
    pendientes_contingencia: int = 0
    lotes_abiertos: int = 0
    certificado: CertificadoParaProduccion
    # Secuencias activas registradas y facturas, por ambiente.
    secuencias: dict[str, int]
    facturas: dict[str, int]
    cambios: list[CambioAmbiente] = Field(default_factory=list)
    avisos: list[str] = Field(default_factory=list)
    aviso_produccion: str = AVISO_PRODUCCION


def _pool(request: Request):
    return request.app.state.db_pool


async def ambiente_activo(pool) -> str:
    return await ACTIVO.actual(pool)


# --- Lecturas ---------------------------------------------------------

async def _existe(conn: asyncpg.Connection, tabla: str) -> bool:
    return await conn.fetchval("SELECT to_regclass($1)", tabla) is not None


async def _bloqueos(conn: asyncpg.Connection) -> tuple[int, int, list[str]]:
    pendientes = lotes = 0
    if await _existe(conn, "facturacion.facturas"):
        pendientes = await conn.fetchval("SELECT count(*) FROM facturacion.facturas WHERE estado = 'contingencia'")
    if await _existe(conn, "facturacion.lotes_contingencia"):
        lotes = await conn.fetchval(
            "SELECT count(*) FROM facturacion.lotes_contingencia WHERE estado = ANY($1::text[])", list(_LOTES_ABIERTOS)
        )
    motivos = []
    if pendientes:
        motivos.append(
            f"Hay {pendientes} factura(s) emitidas en contingencia sin enviar a la DGII: deben llegar al ambiente en que "
            "se firmaron. Espere a que se envíen (Centro de sincronización → Reintentar conexión ahora)."
        )
    if lotes:
        motivos.append(f"Hay {lotes} lote(s) de contingencia enviándose o por reanudar: espere a que terminen.")
    return pendientes, lotes, motivos


async def _conteos(conn: asyncpg.Connection) -> tuple[dict[str, int], dict[str, int]]:
    secuencias = dict.fromkeys(AMBIENTES, 0)
    facturas = dict.fromkeys(AMBIENTES, 0)
    columnas = await conn.fetchval(
        """
        SELECT count(*) FROM information_schema.columns
        WHERE table_schema = 'configuracion' AND table_name = 'secuencias' AND column_name = 'ambiente'
        """
    )
    if columnas:
        for fila in await conn.fetch(
            "SELECT ambiente, count(*) AS n FROM configuracion.secuencias WHERE activa GROUP BY ambiente"
        ):
            secuencias[fila["ambiente"]] = fila["n"]
    if await _existe(conn, "facturacion.facturas"):
        for fila in await conn.fetch("SELECT ambiente, count(*) AS n FROM facturacion.facturas GROUP BY ambiente"):
            if fila["ambiente"] in facturas:
                facturas[fila["ambiente"]] = fila["n"]
    return secuencias, facturas


async def _fila(conn: asyncpg.Connection) -> dict[str, Any] | None:
    if not await _existe(conn, "configuracion.ambiente_dgii"):
        return None
    fila = await conn.fetchrow("SELECT * FROM configuracion.ambiente_dgii WHERE id = 1")
    return dict(fila) if fila else None


async def listar_cambios(pool: asyncpg.Pool, *, limite: int = 10) -> list[dict[str, Any]]:
    async with pool.acquire() as conn:
        if not await _existe(conn, "configuracion.ambiente_dgii_cambios"):
            return []
        filas = await conn.fetch(
            "SELECT * FROM configuracion.ambiente_dgii_cambios ORDER BY creado_en DESC, id DESC LIMIT $1", limite
        )
    return [dict(f) for f in filas]


def _rnc_empresa(settings: ErpApiSettings, fila: dict | None) -> str:
    # EMISOR_RNC solo vale para la empresa 1 (docs/MULTIEMPRESA.md).
    variables = settings.emisor_rnc if empresa_actual() == EMPRESA_PREDETERMINADA else ""
    return "".join(c for c in (variables or (fila or {}).get("rnc") or "") if c.isdigit())


async def certificado_para_produccion(pool, settings: ErpApiSettings) -> dict[str, Any]:
    """Si el certificado con que se firma sirve para producción: vigente (o
    por vencer) y del RNC de la empresa."""
    rnc = _rnc_empresa(settings, await repo.obtener_empresa(pool))
    estado = await asyncio.to_thread(
        certificado_activo.estado_certificado, settings.certificado_path, settings.certificado_password, rnc
    )
    if estado["estado"] not in ESTADOS_CERTIFICADO_VALIDOS:
        motivo = {
            "vencido": "El certificado digital está vencido",
            "no_configurado": "No hay certificado digital configurado",
        }.get(estado["estado"], f"El certificado digital no se puede usar ({estado.get('error') or estado['estado']})")
        return {"estado": estado["estado"], "valido": False, "motivo": f"{motivo}: cargue uno vigente en Configuración → Certificado digital."}
    if estado.get("coincide_rnc_emisor") is False:
        return {
            "estado": estado["estado"],
            "valido": False,
            "motivo": f"El certificado digital no pertenece al RNC {rnc} de la empresa: cargue el del emisor.",
        }
    return {"estado": estado["estado"], "valido": True, "motivo": None}


def _usuario(request: Request, settings: ErpApiSettings) -> tuple[UsuarioSesion | None, str | None]:
    """(usuario, motivo si no puede cambiar el ambiente). Sin inicio de
    sesión configurado no hay a quién pedirle permisos: se permite."""
    if not settings.supabase_jwt_secret:
        return None, None
    # El que verificó el middleware trae `admin` = su rol en esta empresa.
    usuario = getattr(request.state, "usuario", None)
    try:
        if usuario is None:
            usuario = usuario_de_request(request, settings.supabase_jwt_secret, admins=settings.kontara_admin_emails)
    except SesionInvalidaError as exc:
        return None, f"Inicie sesión como administrador para cambiar el ambiente ({exc})."
    if not usuario.admin:
        return usuario, "Solo un administrador puede cambiar el ambiente DGII."
    return usuario, None


async def estado(request: Request, settings: ErpApiSettings, *, avisos: list[str] | None = None) -> dict[str, Any]:
    pool = _pool(request)
    async with pool.acquire() as conn:
        fila = await _fila(conn)
        pendientes, lotes, motivos = await _bloqueos(conn)
        secuencias, facturas = await _conteos(conn)
    guardado = validar_ambiente(fila["ambiente"]) if fila else None
    activo = guardado or ACTIVO.defecto
    _, sin_permiso = _usuario(request, settings)
    return {
        "ambiente": activo,
        "ambiente_dgii": dgii_environment_for(activo).value,
        "nombre": nombre(activo),
        "origen": "configuracion" if guardado else "variables",
        "app_env": settings.app_env,
        "cambiado_por": (fila or {}).get("cambiado_por"),
        "cambiado_en": (fila or {}).get("cambiado_en"),
        "puede_cambiar": sin_permiso is None and not motivos,
        "motivo_sin_permiso": sin_permiso,
        "bloqueos": motivos,
        "pendientes_contingencia": pendientes,
        "lotes_abiertos": lotes,
        "certificado": await certificado_para_produccion(pool, settings),
        "secuencias": secuencias,
        "facturas": facturas,
        "cambios": await listar_cambios(pool),
        "avisos": avisos or [],
    }


# --- Cambio ---------------------------------------------------------

class CambioRechazadoError(Exception):
    def __init__(self, status: int, detalle: str):
        self.status = status
        super().__init__(detalle)


async def cambiar_ambiente(
    pool: asyncpg.Pool,
    nuevo: str,
    *,
    usuario: UsuarioSesion | None,
    defecto: str,
) -> tuple[str, bool]:
    """Guarda el ambiente y su auditoría en una transacción, con los
    bloqueos verificados dentro de ella (advisory lock: dos cambios a la vez
    se serializan). Devuelve (anterior, cambió). Lanza CambioRechazadoError."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock($1)", _LOCK_CAMBIO)
            anterior = await leer_ambiente_guardado(conn) or defecto
            if anterior == nuevo:
                return anterior, False
            _, _, motivos = await _bloqueos(conn)
            if motivos:
                raise CambioRechazadoError(409, "No se puede cambiar el ambiente ahora. " + " ".join(motivos))
            usuario_id = usuario.id if usuario else None
            correo = usuario.correo if usuario else None
            await conn.execute(
                """
                INSERT INTO configuracion.ambiente_dgii (id, ambiente, cambiado_por_id, cambiado_por, cambiado_en)
                VALUES (1, $1, $2, $3, now())
                ON CONFLICT (empresa_id, id) DO UPDATE SET
                    ambiente = EXCLUDED.ambiente,
                    cambiado_por_id = EXCLUDED.cambiado_por_id,
                    cambiado_por = EXCLUDED.cambiado_por,
                    cambiado_en = EXCLUDED.cambiado_en
                """,
                nuevo,
                usuario_id,
                correo,
            )
            await conn.execute(
                """
                INSERT INTO configuracion.ambiente_dgii_cambios (anterior, nuevo, usuario_id, usuario_correo)
                VALUES ($1, $2, $3, $4)
                """,
                anterior,
                nuevo,
                usuario_id,
                correo,
            )
    return anterior, True


@router.get("/ambiente", response_model=EstadoAmbiente)
async def obtener_ambiente(request: Request, settings: ErpApiSettings = Depends(get_settings)):
    return await estado(request, settings)


@router.put("/ambiente", response_model=EstadoAmbiente)
async def guardar_ambiente(
    request: Request, payload: AmbientePayload, settings: ErpApiSettings = Depends(get_settings)
):
    usuario, sin_permiso = _usuario(request, settings)
    if sin_permiso:
        raise HTTPException(status_code=403, detail=sin_permiso)
    try:
        nuevo = validar_ambiente(payload.ambiente)
    except AmbienteInvalidoError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    pool = _pool(request)
    if nuevo == "production":
        if not payload.confirmacion:
            raise HTTPException(
                status_code=422,
                detail="Confirme el cambio a producción: " + AVISO_PRODUCCION,
            )
        certificado = await certificado_para_produccion(pool, settings)
        if not certificado["valido"]:
            raise HTTPException(
                status_code=409, detail="No se puede pasar a producción (eCF). " + certificado["motivo"]
            )
    try:
        _, cambio = await cambiar_ambiente(pool, nuevo, usuario=usuario, defecto=ACTIVO.defecto)
    except CambioRechazadoError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    ACTIVO.fijar(nuevo)
    avisos: list[str] = []
    if cambio:
        async with pool.acquire() as conn:
            secuencias, _ = await _conteos(conn)
        if not secuencias.get(nuevo):
            avisos.append(
                f"El ambiente {nombre(nuevo)} no tiene secuencias e-NCF registradas: regístrelas en Configuración → "
                "Secuencias autorizadas antes de facturar (las secuencias son distintas en cada ambiente)."
            )
        avisos.append(
            "Los demás servicios toman el cambio en unos segundos (hasta 15 s). Las facturas ya creadas conservan "
            "su ambiente."
        )
    return await estado(request, settings, avisos=avisos)
