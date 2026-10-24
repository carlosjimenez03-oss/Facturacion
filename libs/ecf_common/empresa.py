"""Varias empresas (emisores) en una misma instalación de Kontara.

Ver docs/MULTIEMPRESA.md. En resumen:

- **Registro**: `configuracion.empresas` (una fila por RNC) y
  `configuracion.empresa_usuarios` (quién entra a cuál, con rol admin o
  usuario). La empresa 1 es la de siempre: la de `configuracion.empresa` /
  EMISOR_RNC, a la que quedan asignadas todas las filas que ya existían.
- **Aislamiento en Postgres (RLS)**: cada tabla de negocio tiene
  `empresa_id` (por defecto `configuracion.empresa_actual()`) y una política
  `empresa_aislada` que solo deja ver, insertar y cambiar las filas de la
  empresa del request. La empresa del request viaja en la variable de sesión
  `kontara.empresa_id`, que pone `preparar_conexion` (el `setup` del pool de
  asyncpg, en cada `acquire`) a partir del ContextVar de este módulo. Así
  ninguna consulta existente tuvo que cambiar para no devolver filas de otra
  empresa, y una consulta nueva que olvide filtrar tampoco puede hacerlo.
- **Rol sin privilegios**: un superusuario (o un rol con BYPASSRLS) salta
  RLS; en ese caso cada conexión hace `SET ROLE kontara_app` (lo crea la
  migración, NOLOGIN y sin BYPASSRLS). Si no se puede (sin el rol, o el
  pooler de Supabase en modo transacción, que no conserva variables de
  sesión), las conexiones solo se entregan para la empresa 1: una
  instalación de una sola empresa sigue igual y la segunda falla cerrada.
- **Empresa del request**: cabecera `X-Kontara-Empresa` (la manda el
  frontend; nginx la reemplaza por la que verificó erp-api en
  `/auth/verificar`). `instalar_empresa` la valida contra la membresía del
  usuario de la sesión (403 si no es miembro); las llamadas internas
  (`X-Kontara-Interno`) la pasan explícita. Sin cabecera: la empresa 1.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable, Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from http.cookies import CookieError, SimpleCookie
from typing import Any
from urllib.parse import urlsplit

import asyncpg
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

EMPRESA_PREDETERMINADA = 1
# Valor especial de `kontara.empresa_id`: ve las filas de todas las empresas
# (búsquedas que no saben de qué empresa es algo: el token de un enlace del
# portal público, la respuesta de la pasarela de pagos). Los INSERT fallan
# (empresa_id NOT NULL): hay que entrar a la empresa de la fila primero.
TODAS = "*"
CABECERA_EMPRESA = "X-Kontara-Empresa"
COOKIE_EMPRESA = "kontara_empresa"
ROL_APLICACION = "kontara_app"

_EMPRESA: ContextVar[int | str | None] = ContextVar("kontara_empresa", default=None)
# Migraciones y DDL en caliente: sin SET ROLE (el dueño de las tablas).
_SISTEMA: ContextVar[bool] = ContextVar("kontara_empresa_sistema", default=False)


class AislamientoNoDisponibleError(RuntimeError):
    """No se puede garantizar que la conexión solo vea la empresa pedida."""


class EmpresaNoPermitidaError(Exception):
    pass


# --- empresa del contexto -------------------------------------------------------------------

def empresa_actual() -> int:
    """Id de la empresa del request / tarea en curso (1 si no se indicó)."""
    valor = _EMPRESA.get()
    if valor is None:
        return EMPRESA_PREDETERMINADA
    if valor == TODAS:
        raise RuntimeError("empresa_actual() dentro de todas_las_empresas(): entre a una empresa primero")
    return int(valor)


def empresa_indicada() -> int | str | None:
    """Lo que se fijó con `en_empresa` (None = nada: la predeterminada)."""
    return _EMPRESA.get()


@contextmanager
def en_empresa(empresa_id: int | None) -> Iterator[None]:
    marca = _EMPRESA.set(int(empresa_id) if empresa_id is not None else None)
    try:
        yield
    finally:
        _EMPRESA.reset(marca)


@contextmanager
def todas_las_empresas() -> Iterator[None]:
    marca = _EMPRESA.set(TODAS)
    try:
        yield
    finally:
        _EMPRESA.reset(marca)


@contextmanager
def modo_sistema() -> Iterator[None]:
    """Conexiones como el dueño de las tablas (sin SET ROLE): migraciones e
    índices que se crean al arrancar. Sigue en la empresa del contexto."""
    marca = _SISTEMA.set(True)
    try:
        yield
    finally:
        _SISTEMA.reset(marca)


def cabeceras_empresa() -> dict[str, str]:
    """`X-Kontara-Empresa` para llamar a otro servicio de Kontara en nombre
    de la empresa en curso ({} si es la predeterminada sin indicar)."""
    valor = _EMPRESA.get()
    if valor is None or valor == TODAS:
        return {}
    return {CABECERA_EMPRESA: str(valor)}


def empresa_de_texto(valor: str | None) -> int | None:
    texto = (valor or "").strip()
    if not texto.isdigit() or len(texto) > 9:
        return None
    numero = int(texto)
    return numero if numero >= 1 else None


# --- conexiones (setup del pool de asyncpg) -------------------------------------------------------

# Un solo viaje por `acquire`: fija la empresa y el rol de la conexión.
# - 'empresa': si la sesión salta RLS (superusuario o BYPASSRLS) y puede, SET
#   ROLE kontara_app (sujeto a la política de una empresa);
# - 'todas': SET ROLE kontara_todas (lee todas las empresas, no escribe);
# - 'sistema' (migraciones): el usuario de la conexión.
# `aislado` dice si en modo 'empresa' la conexión queda sujeta a las
# políticas. Postgres 16 separa ser miembro de un rol de poder hacer SET
# ROLE: se comprueba el privilegio SET (antes, la membresía). role 'none' =
# el usuario de la conexión (RESET ALL de asyncpg no toca el rol: se fija
# siempre).
_SQL_PREPARAR = """
WITH s AS (
    SELECT COALESCE((SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = session_user), false) AS salta,
           current_setting('server_version_num')::integer >= 160000 AS pg16
), r AS (
    SELECT s.salta,
           COALESCE((SELECT CASE WHEN s.pg16 THEN pg_has_role(session_user, oid, 'SET')
                                 ELSE pg_has_role(session_user, oid, 'MEMBER') END
                     FROM pg_roles WHERE rolname = 'kontara_app'), false) AS puede_app,
           COALESCE((SELECT CASE WHEN s.pg16 THEN pg_has_role(session_user, oid, 'SET')
                                 ELSE pg_has_role(session_user, oid, 'MEMBER') END
                     FROM pg_roles WHERE rolname = 'kontara_todas'), false) AS puede_todas
    FROM s
)
SELECT set_config('kontara.empresa_id', $1, false) AS empresa,
       set_config('role', CASE WHEN $2 = 'empresa' AND r.salta AND r.puede_app THEN 'kontara_app'
                               WHEN $2 = 'todas' AND r.puede_todas THEN 'kontara_todas'
                               ELSE 'none' END, false) AS rol,
       (NOT r.salta OR r.puede_app) AS aislado,
       r.salta, r.puede_app, r.puede_todas
FROM r
"""

# Sin cambiar de rol (si SET ROLE falló): solo la empresa y si la sesión salta RLS.
_SQL_PREPARAR_SIN_ROL = """
SELECT set_config('kontara.empresa_id', $1, false) AS empresa,
       set_config('role', 'none', false) AS rol,
       NOT COALESCE((SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = session_user), false) AS aislado
"""


def _modo() -> str:
    if _SISTEMA.get():
        return "sistema"
    return "todas" if _EMPRESA.get() == TODAS else "empresa"


def _valor_guc(valor: int | str | None) -> str:
    return "" if valor is None or valor == TODAS else str(valor)


def crear_preparador(database_url: str) -> Callable[[Any], Any]:
    """El `setup` del pool.

    - Con el pooler de Supabase en modo transacción (puerto 6543) las
      variables de sesión y el SET ROLE no son de esta conexión: quedarían
      en la conexión del servidor para el próximo cliente. Ahí no se fija
      nada (la política ve la empresa 1) y se niega cualquier otra empresa.
    - Si SET ROLE falla (p. ej. un rol sin el privilegio SET), se registra
      una vez y el pool sigue sin cambiar de rol: modo "solo la empresa 1"
      (docs/MULTIEMPRESA.md), nunca un error en cada conexión."""
    transaccion = urlsplit(database_url).port == 6543
    estado: dict[str, Any] = {"transaccion": transaccion, "sin_rol": False, "aislado": None}

    async def preparar_conexion(conn) -> None:
        valor = _EMPRESA.get()
        modo = _modo()
        otra = valor is not None and valor != TODAS and valor != EMPRESA_PREDETERMINADA
        if transaccion:
            if otra:
                raise AislamientoNoDisponibleError(
                    "Varias empresas requieren la conexión a Postgres en modo sesión (pooler Supabase en el puerto 5432)"
                )
            estado["aislado"] = False
            return
        fila = None
        if not estado["sin_rol"]:
            try:
                fila = await conn.fetchrow(_SQL_PREPARAR, _valor_guc(valor), modo)
            except asyncpg.InsufficientPrivilegeError as exc:
                estado["sin_rol"] = True
                logger.error(
                    "No se puede cambiar al rol kontara_app/kontara_todas (%s): se trabaja SOLO con la empresa 1 "
                    "(ver docs/MULTIEMPRESA.md, 'Rol sin privilegios')",
                    exc,
                )
        if fila is None:
            fila = await conn.fetchrow(_SQL_PREPARAR_SIN_ROL, _valor_guc(valor))
        aislado = bool(fila["aislado"])
        estado["aislado"] = aislado
        if otra and modo != "sistema" and not aislado:
            raise AislamientoNoDisponibleError(
                "La base de datos no puede aislar las empresas (falta el rol kontara_app o la conexión salta RLS): "
                "ver docs/MULTIEMPRESA.md"
            )

    preparar_conexion.estado = estado  # type: ignore[attr-defined]
    return preparar_conexion


async def aislamiento_disponible(pool: Any) -> bool:
    """¿Las conexiones de este pool quedan sujetas a RLS? (Agregar una
    segunda empresa solo tiene sentido si sí.)"""
    if pool is None:
        return False
    estado = getattr(getattr(pool, "_setup", None), "estado", None)
    if estado is not None and estado.get("transaccion"):
        return False
    try:
        with en_empresa(None):
            async with pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
    except Exception as exc:  # sin permisos sobre pg_roles, base caída...
        logger.warning("No se pudo comprobar el aislamiento entre empresas: %s", exc)
        return False
    if estado is None:  # un pool sin nuestro setup (pruebas)
        return False
    return bool(estado.get("aislado"))


async def diagnosticar(pool: Any) -> str:
    """Al arrancar: en qué modo queda el aislamiento entre empresas, en el
    log (una línea). Devuelve el código del modo."""
    estado = getattr(getattr(pool, "_setup", None), "estado", None) or {}
    if estado.get("transaccion"):
        logger.warning(
            "Multiempresa: pooler en modo transacción (puerto 6543): solo la empresa 1 (use el modo sesión, 5432)"
        )
        return "transaccion"
    try:
        with en_empresa(None):
            async with pool.acquire() as conn:
                fila = await conn.fetchrow(
                    "SELECT COALESCE((SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = session_user), false)"
                    " AS salta, (SELECT count(*) FROM configuracion.empresas) AS empresas"
                    " WHERE to_regclass('configuracion.empresas') IS NOT NULL"
                )
    except Exception as exc:
        logger.warning("Multiempresa: no se pudo diagnosticar el aislamiento: %s", exc)
        return "desconocido"
    empresas = fila["empresas"] if fila else 0
    if not estado.get("aislado"):
        nivel = logging.ERROR if empresas > 1 else logging.WARNING
        logger.log(
            nivel,
            "Multiempresa: las conexiones NO quedan aisladas por empresa (sin el rol kontara_app o sin SET sobre él): "
            "solo se atiende la empresa 1%s. Ver docs/MULTIEMPRESA.md",
            f" — y la base ya tiene {empresas} empresas: sus datos podrían verse juntos" if empresas > 1 else "",
        )
        return "sin_aislamiento"
    if fila and not fila["salta"]:
        logger.warning(
            "Multiempresa: el usuario de la base es el dueño de las tablas sin BYPASSRLS: las migraciones y tareas "
            "de sistema solo ven las filas de la empresa 1 (correcto para las migraciones de hoy; ver docs/MULTIEMPRESA.md)"
        )
        return "dueno_sujeto_a_rls"
    logger.info("Multiempresa: aislamiento por empresa activo (SET ROLE kontara_app)")
    return "aislado"


# --- registro de empresas y membresías (con caché corta) ----------------------------------------------

TTL_SEGUNDOS = 30.0


@dataclass(frozen=True)
class Empresa:
    id: int
    rnc: str | None
    razon_social: str
    nombre_comercial: str | None
    estado: str

    @property
    def activa(self) -> bool:
        return self.estado == "activa"


class _Cache:
    def __init__(self, ttl: float = TTL_SEGUNDOS, maximo: int = 5000):
        self.ttl = ttl
        self.maximo = maximo
        self._datos: dict[Any, tuple[Any, float]] = {}

    def leer(self, clave) -> tuple[bool, Any]:
        valor = self._datos.get(clave)
        if valor is None or valor[1] < time.monotonic():
            return False, None
        return True, valor[0]

    def guardar(self, clave, valor) -> None:
        if len(self._datos) >= self.maximo:
            self._datos.clear()
        self._datos[clave] = (valor, time.monotonic() + self.ttl)

    def limpiar(self) -> None:
        self._datos.clear()


_EMPRESAS = _Cache()
_MEMBRESIAS = _Cache()


def invalidar_cache() -> None:
    """Tras crear/cambiar una empresa o una membresía (erp-api)."""
    _EMPRESAS.limpiar()
    _MEMBRESIAS.limpiar()


async def _hay_registro(conn) -> bool:
    return await conn.fetchval("SELECT to_regclass('configuracion.empresa_usuarios')") is not None


async def obtener_empresa(pool: Any, empresa_id: int) -> Empresa | None:
    hay, valor = _EMPRESAS.leer(empresa_id)
    if hay:
        return valor
    async with pool.acquire() as conn:
        if not await _hay_registro(conn):
            return None
        fila = await conn.fetchrow(
            "SELECT id, rnc, razon_social, nombre_comercial, estado FROM configuracion.empresas WHERE id = $1",
            empresa_id,
        )
    empresa = Empresa(**dict(fila)) if fila else None
    _EMPRESAS.guardar(empresa_id, empresa)
    return empresa


def _digitos(texto: str | None) -> str:
    return "".join(c for c in (texto or "") if c.isdigit())


async def rnc_emisor(pool: Any, defecto: str = "") -> str:
    """RNC (solo dígitos) de la empresa en curso. La predeterminada usa
    EMISOR_RNC (`defecto`) si está, como siempre; las demás, el del registro."""
    empresa_id = empresa_actual()
    if empresa_id == EMPRESA_PREDETERMINADA and _digitos(defecto):
        return _digitos(defecto)
    if pool is None:
        return _digitos(defecto) if empresa_id == EMPRESA_PREDETERMINADA else ""
    try:
        empresa = await obtener_empresa(pool, empresa_id)
    except Exception as exc:
        logger.warning("No se pudo leer el RNC de la empresa %s: %s", empresa_id, exc)
        empresa = None
    return _digitos(empresa.rnc if empresa else "")


async def razon_social(pool: Any, defecto: str = "") -> str:
    empresa_id = empresa_actual()
    try:
        empresa = await obtener_empresa(pool, empresa_id) if pool is not None else None
    except Exception:
        empresa = None
    if empresa_id == EMPRESA_PREDETERMINADA and defecto:
        return defecto
    return (empresa.razon_social if empresa else "") or defecto


async def empresa_por_rnc(pool: Any, rnc: str | None, emisor_rnc: str = "") -> int | None:
    """La empresa activa con ese RNC (receptor electrónico: a cuál de las
    empresas le mandaron el e-CF). EMISOR_RNC (`emisor_rnc`) es siempre la
    1. None si no es de ninguna."""
    rnc = _digitos(rnc)
    if not rnc:
        return None
    if rnc == _digitos(emisor_rnc):
        return EMPRESA_PREDETERMINADA
    if pool is None:
        return None
    try:
        async with pool.acquire() as conn:
            if not await _hay_registro(conn):
                return None
            return await conn.fetchval(
                "SELECT id FROM configuracion.empresas WHERE rnc = $1 AND estado = 'activa'", rnc
            )
    except Exception as exc:
        logger.warning("No se pudo buscar la empresa del RNC %s: %s", rnc, exc)
        return None


async def empresas_activas(pool: Any) -> list[int]:
    """Ids de las empresas activas (tareas de fondo: una vuelta por cada
    una). Sin registro todavía: solo la predeterminada."""
    try:
        async with pool.acquire() as conn:
            if not await _hay_registro(conn):
                return [EMPRESA_PREDETERMINADA]
            ids = await conn.fetch("SELECT id FROM configuracion.empresas WHERE estado = 'activa' ORDER BY id")
    except Exception as exc:
        logger.warning("No se pudo listar las empresas (se usa solo la predeterminada): %s", exc)
        return [EMPRESA_PREDETERMINADA]
    return [f["id"] for f in ids] or [EMPRESA_PREDETERMINADA]


# Rol del usuario en la empresa: su membresía ACTIVA. Un usuario sin
# NINGUNA fila (las filas no se borran: es uno de antes de la multiempresa
# que todavía no inició sesión) es miembro de la empresa 1, admin si tiene la
# marca global (`kontara_admin` / KONTARA_ADMIN_EMAILS) o si la instalación
# no tenía administradores (`multiempresa_legado.previos_admin`).
_SQL_ROL = """
SELECT eu.rol FROM configuracion.empresa_usuarios eu
JOIN configuracion.empresas e ON e.id = eu.empresa_id
WHERE eu.usuario_id = $1 AND eu.empresa_id = $2 AND eu.estado = 'activo' AND e.estado = 'activa'
UNION ALL
SELECT CASE WHEN $3 OR COALESCE((SELECT previos_admin FROM configuracion.multiempresa_legado WHERE id = 1), false)
            THEN 'admin' ELSE 'usuario' END
WHERE $2 = 1 AND NOT EXISTS (SELECT 1 FROM configuracion.empresa_usuarios WHERE usuario_id = $1)
LIMIT 1
"""

# La que el usuario eligió por última vez (si sigue siendo miembro activo de
# una empresa activa) u otra de sus empresas activas; sin filas, la 1.
_SQL_PREFERIDA = """
SELECT empresa_id FROM (
    SELECT u.empresa_id, 0 AS orden FROM configuracion.usuario_empresa_actual u
    JOIN configuracion.empresa_usuarios eu ON eu.empresa_id = u.empresa_id AND eu.usuario_id = u.usuario_id
    JOIN configuracion.empresas e ON e.id = eu.empresa_id
    WHERE u.usuario_id = $1 AND eu.estado = 'activo' AND e.estado = 'activa'
    UNION ALL
    SELECT eu.empresa_id, 1 FROM configuracion.empresa_usuarios eu
    JOIN configuracion.empresas e ON e.id = eu.empresa_id AND e.estado = 'activa'
    WHERE eu.usuario_id = $1 AND eu.estado = 'activo'
) x ORDER BY orden, empresa_id LIMIT 1
"""

# La fila del usuario de antes (sin filas) en la empresa 1, en su primer
# inicio de sesión: desde ahí es un miembro más (desactivable, con su rol).
SQL_LEGADO = """
INSERT INTO configuracion.empresa_usuarios (empresa_id, usuario_id, correo, rol, agregado_por)
SELECT 1, $1, $2,
       CASE WHEN $3 OR COALESCE((SELECT previos_admin FROM configuracion.multiempresa_legado WHERE id = 1), false)
            THEN 'admin' ELSE 'usuario' END,
       'primer inicio de sesión'
WHERE NOT EXISTS (SELECT 1 FROM configuracion.empresa_usuarios WHERE usuario_id = $1)
ON CONFLICT DO NOTHING
"""

_LEGADOS: set[str] = set()


async def rol_en_empresa(pool: Any, usuario_id: str, empresa_id: int, *, admin_global: bool = False) -> str | None:
    """'admin' | 'usuario' | None (no es miembro activo)."""
    clave = (usuario_id, empresa_id, admin_global)
    hay, valor = _MEMBRESIAS.leer(clave)
    if hay:
        return valor
    async with pool.acquire() as conn:
        if not await _hay_registro(conn):
            rol = ("admin" if admin_global else "usuario") if empresa_id == EMPRESA_PREDETERMINADA else None
        else:
            rol = await conn.fetchval(_SQL_ROL, usuario_id, empresa_id, admin_global)
    _MEMBRESIAS.guardar(clave, rol)
    return rol


async def registrar_usuario_previo(pool: Any, usuario: Any) -> None:
    """Escribe la membresía en la empresa 1 del usuario de antes que inicia
    sesión por primera vez (una vez por proceso y usuario; best-effort)."""
    if pool is None or usuario is None or usuario.id in _LEGADOS:
        return
    try:
        async with pool.acquire() as conn:
            if await _hay_registro(conn):
                await conn.execute(SQL_LEGADO, usuario.id, usuario.correo, bool(usuario.admin_global or usuario.admin))
        _LEGADOS.add(usuario.id)
    except Exception as exc:
        logger.warning("No se pudo registrar la membresía de %s: %s", usuario.correo, exc)


async def es_admin_plataforma(pool: Any, usuario: Any) -> bool:
    """Administrador de la INSTALACIÓN (no de una empresa): agregar empresas,
    cambiar datos globales (tasas de nómina). La marca global
    (`kontara_admin` / KONTARA_ADMIN_EMAILS) o, si la instalación no tenía
    administradores configurados (`previos_admin`), un admin de la empresa 1.
    Sin inicio de sesión configurado (usuario None): sí."""
    if usuario is None or getattr(usuario, "admin_global", False):
        return True
    if pool is None:
        return False
    async with pool.acquire() as conn:
        if await conn.fetchval("SELECT to_regclass('configuracion.multiempresa_legado')") is None:
            return False
        previos = await conn.fetchval("SELECT previos_admin FROM configuracion.multiempresa_legado WHERE id = 1")
    if not previos:
        return False
    return await rol_en_empresa(pool, usuario.id, EMPRESA_PREDETERMINADA) == "admin"


async def empresa_preferida(pool: Any, usuario_id: str) -> int:
    clave = ("preferida", usuario_id)
    hay, valor = _MEMBRESIAS.leer(clave)
    if hay:
        return valor
    async with pool.acquire() as conn:
        if not await _hay_registro(conn):
            return EMPRESA_PREDETERMINADA
        try:
            preferida = await conn.fetchval(_SQL_PREFERIDA, usuario_id)
        except Exception:  # sin la tabla de preferencia (erp-api aún sin migrar)
            preferida = None
    preferida = preferida or EMPRESA_PREDETERMINADA
    _MEMBRESIAS.guardar(clave, preferida)
    return preferida


# --- empresa del request ------------------------------------------------------------------------

SIN_EMPRESA = "Su usuario no tiene acceso a esta empresa: pida a su administrador que lo agregue en Configuración → Usuarios"
EMPRESA_INEXISTENTE = "La empresa indicada no existe o está desactivada"


def _respuesta(status: int, detalle: str, codigo: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"detail": detalle, "codigo": codigo})


def empresa_pedida(request: Request, *, cookie_permitida: Callable[[str, str], bool] | None = None) -> int | None:
    """La empresa que pide el request: cabecera `X-Kontara-Empresa` o, en
    las descargas que abre el navegador sin fetch (PDF, XML), la cookie
    `kontara_empresa`."""
    cabecera = request.headers.get(CABECERA_EMPRESA)
    if cabecera is not None and cabecera.strip():
        return empresa_de_texto(cabecera) or -1
    galleta = request.headers.get("cookie")
    if galleta and cookie_permitida and cookie_permitida(request.method, request.url.path):
        try:
            valor = SimpleCookie(galleta)
        except CookieError:
            return None
        if COOKIE_EMPRESA in valor:
            return empresa_de_texto(valor[COOKIE_EMPRESA].value)
    return None


async def resolver_empresa(
    pool: Any, usuario: Any, pedida: int | None, *, interna: bool
) -> tuple[int | None, Any, JSONResponse | None]:
    """(empresa, usuario con el rol de esa empresa, respuesta de error).

    - Llamada interna: la que indica (o la predeterminada).
    - Usuario con sesión: la pedida (o su preferida) si es miembro; 403 si no.
    - Sin sesión verificada en este servicio: la pedida, que en producción
      es la que nginx verificó con erp-api y puso él mismo (borra la del
      navegador); sin inicio de sesión (KONTARA_AUTH=off) nginx no pone
      ninguna y queda la predeterminada."""
    if pedida == -1:
        return None, usuario, _respuesta(422, f"{CABECERA_EMPRESA} inválida", "empresa_invalida")
    if interna or usuario is None:
        empresa_id = pedida
    else:
        if pool is None:
            return pedida, usuario, None
        empresa_id = pedida or await empresa_preferida(pool, usuario.id)
        rol = await rol_en_empresa(pool, usuario.id, empresa_id, admin_global=usuario.admin_global or usuario.admin)
        if rol is None:
            return None, usuario, _respuesta(403, SIN_EMPRESA, "sin_empresa")
        usuario = replace(usuario, admin=rol == "admin", admin_global=usuario.admin_global or usuario.admin)
    if empresa_id is not None and empresa_id != EMPRESA_PREDETERMINADA and pool is not None:
        empresa = await obtener_empresa(pool, empresa_id)
        if empresa is None or not empresa.activa:
            return None, usuario, _respuesta(403, EMPRESA_INEXISTENTE, "empresa_inexistente")
    return empresa_id, usuario, None


def instalar_empresa(
    app: FastAPI,
    settings,
    *,
    rutas_libres: Iterable[str] = (),
    es_libre: Callable[[str], bool] | None = None,
    empresa_libre: Callable[[Request, Any], Awaitable[int | None]] | None = None,
) -> None:
    """Fija la empresa de cada request (ver `resolver_empresa`) para que las
    conexiones del pool la usen. Llamarlo ANTES de
    `instalar_verificacion_sesion`: así queda por dentro de él y ve el
    usuario ya verificado (`request.state.usuario`).

    `rutas_libres` / `es_libre`: rutas que no exigen ser miembro (/health,
    el portal público de clientes). `empresa_libre(request, pool)` da la
    empresa de una ruta libre por lo que trae (p. ej. el token del enlace
    del portal); sin ella, o si devuelve None, la predeterminada."""
    from libs.ecf_common.auth.sesion_supabase import cookie_permitida, es_llamada_interna

    libres = frozenset({"/health"}) | frozenset(rutas_libres)
    token_interno = getattr(settings, "kontara_token_interno", "")

    class _EmpresaMiddleware:
        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            if scope["type"] != "http":
                return await self.app(scope, receive, send)
            request = Request(scope)
            ruta = request.url.path
            pool = getattr(request.app.state, "db_pool", None)
            if request.method == "OPTIONS" or ruta in libres or (es_libre and es_libre(ruta)):
                empresa_id = None
                if empresa_libre is not None and pool is not None and request.method != "OPTIONS":
                    try:
                        empresa_id = await empresa_libre(request, pool)
                    except Exception:
                        logger.exception("No se pudo resolver la empresa de %s", ruta)
                with en_empresa(empresa_id):
                    return await self.app(scope, receive, send)
            usuario = scope.get("state", {}).get("usuario")
            try:
                empresa_id, usuario, error = await resolver_empresa(
                    pool,
                    usuario,
                    # La cookie de las descargas solo con un usuario verificado
                    # aquí (que se vuelve a validar como miembro): sin sesión en
                    # el servicio, la empresa es la que puso nginx o ninguna.
                    empresa_pedida(request, cookie_permitida=cookie_permitida if usuario is not None else None),
                    interna=es_llamada_interna(request, token_interno),
                )
            except Exception:
                logger.exception("No se pudo resolver la empresa del request")
                error = _respuesta(503, "No se pudo verificar la empresa: intente de nuevo", "empresa_no_verificada")
            if error is not None:
                return await error(scope, receive, send)
            if usuario is not None:
                scope.setdefault("state", {})["usuario"] = usuario
            scope.setdefault("state", {})["empresa_id"] = empresa_id or EMPRESA_PREDETERMINADA
            with en_empresa(empresa_id):
                await self.app(scope, receive, send)

    app.add_middleware(_EmpresaMiddleware)

    # Una empresa que no sea la 1 con la base sin aislamiento (p. ej. el rol
    # perdió el privilegio SET): 503 claro, nunca sus datos ni un 500.
    async def _sin_aislamiento(_request: Request, exc: Exception) -> JSONResponse:
        logger.error("Request negado: %s", exc)
        return _respuesta(503, str(exc), "sin_aislamiento")

    app.add_exception_handler(AislamientoNoDisponibleError, _sin_aislamiento)
