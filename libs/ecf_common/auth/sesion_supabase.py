"""Verificación de la sesión de los usuarios de la aplicación (Supabase Auth
/ GoTrue).

El frontend inicia sesión contra GoTrue (`/auth/v1/token`) y manda el
`access_token` en `Authorization: Bearer <token>` a cada API (o en la cookie
`kontara_sesion` cuando el navegador abre un enlace directo de descarga: el
PDF de una factura o el XML de la bandeja). Ese token es un JWT HS256
firmado con el `JWT_SECRET` del recurso de Supabase -- aquí
`SUPABASE_JWT_SECRET` --, con `aud = "authenticated"` y `role =
"authenticated"` para un usuario con sesión.

Además de una firma válida, el usuario tiene que estar AUTORIZADO en Kontara:
`app_metadata.kontara = true` (lo pone erp-api con la API admin de GoTrue al
crear o invitar desde Configuración → Usuarios; `app_metadata` solo lo cambia
la SERVICE_ROLE_KEY, nunca el propio usuario). Así un usuario que exista en
el mismo GoTrue por otro motivo (registro abierto, OTP, magic link, SSO,
anónimo...) no entra. Para el primer usuario, creado a mano en Studio, está
`KONTARA_ADMIN_EMAILS` (ver docs/AUTH.md): sus correos se aceptan sin la
marca y erp-api se la pone en el primer request verificado.

La cookie solo vale para GET/HEAD de las rutas de descarga
(`RUTAS_DESCARGA_COOKIE`): cualquier otro request necesita la cabecera
`Authorization`, que un sitio ajeno no puede poner (CSRF).

Dos usos (ver docs/AUTH.md):

- `erp_api` expone `GET /auth/verificar`, al que nginx (frontend) le
  pregunta con `auth_request` antes de reenviar cualquier `/api*`.
- `instalar_verificacion_sesion` agrega la misma verificación dentro de cada
  API cuando `REQUIRE_AUTH=true` (o `ERP_REQUIRE_AUTH` en erp-api). Las
  llamadas internas entre servicios no llevan sesión: se identifican con la
  cabecera `X-Kontara-Interno` (= `KONTARA_TOKEN_INTERNO`), que nginx borra de
  todo request que viene del navegador.

Este módulo no tiene nada que ver con `jwt_tokens.py` (tokens RS256 que
emite `receptor_autenticacion` para otros contribuyentes / la DGII).
"""
from __future__ import annotations

import hmac
import logging
import re
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from http.cookies import CookieError, SimpleCookie
from typing import Any

import jwt
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

ALGORITMOS = ["HS256"]
AUDIENCIA = "authenticated"
ROL_USUARIO = "authenticated"
COOKIE_SESION = "kontara_sesion"

# app_metadata del usuario en GoTrue: `kontara` = puede entrar a la
# aplicación; `kontara_admin` = además puede, p. ej., mandar el correo de
# prueba a otra dirección. Solo la API admin (SERVICE_ROLE_KEY) los cambia.
MARCA_ACCESO = "kontara"
MARCA_ADMIN = "kontara_admin"

# Llamadas internas entre servicios (erp-api -> facturas-api, facturas-api
# -> inventario-api...): no pasan por nginx ni llevan sesión. nginx borra
# esta cabecera de lo que manda el navegador (frontend/nginx.conf).
CABECERA_INTERNA = "X-Kontara-Interno"
LARGO_MINIMO_TOKEN_INTERNO = 32

# Únicas rutas en que vale la cookie (enlaces que el navegador abre sin
# fetch), con o sin el prefijo del proxy (/api, /api-erp). El id solo admite
# hexadecimales y guiones: así la ruta "cruda" ($request_uri) que reenvía
# nginx no puede llevar `..`, `%2F` ni `//`.
RUTAS_DESCARGA_COOKIE = (
    re.compile(r"(?:^|/)facturas/[0-9A-Fa-f-]{1,64}/representacion-impresa$"),
    re.compile(r"(?:^|/)compras/bandeja/[0-9A-Fa-f-]{1,64}/xml$"),
)
METODOS_COOKIE = frozenset({"GET", "HEAD"})

# Rutas que nunca piden sesión: el healthcheck de Docker y el de
# "Estado de las soluciones" (erp-api lo consulta por la red interna).
RUTAS_PUBLICAS = frozenset({"/health"})

SIN_ACCESO = (
    "Su usuario no tiene acceso a Kontara: pida a un administrador que se lo dé en "
    "Configuración → Usuarios"
)


class SesionInvalidaError(Exception):
    pass


@dataclass(frozen=True)
class UsuarioSesion:
    id: str
    correo: str | None
    rol: str
    expira: int
    sesion_id: str | None = None
    # Entró por KONTARA_ADMIN_EMAILS sin tener todavía la marca en
    # app_metadata: erp-api se la pone (plataforma.py).
    falta_marca: bool = False
    admin: bool = False


def correos_admin(valor: str | Iterable[str] | None) -> frozenset[str]:
    """KONTARA_ADMIN_EMAILS (lista separada por comas) en minúsculas."""
    if not valor:
        return frozenset()
    partes = valor.split(",") if isinstance(valor, str) else valor
    return frozenset(p.strip().lower() for p in partes if p and p.strip())


def verificar_token_supabase(
    token: str, secreto: str, *, admins: Iterable[str] | str = frozenset()
) -> UsuarioSesion:
    """Valida firma (HS256 con `secreto`), vencimiento (`exp`), audiencia,
    rol y la autorización de Kontara (`app_metadata.kontara`, o el correo en
    `admins` = KONTARA_ADMIN_EMAILS) del `access_token` de GoTrue. Lanza
    `SesionInvalidaError` con el motivo si algo no cuadra."""
    if not secreto:
        raise SesionInvalidaError("SUPABASE_JWT_SECRET no está configurado")
    if not token:
        raise SesionInvalidaError("Falta el token de sesión")
    try:
        claims = jwt.decode(
            token,
            secreto,
            algorithms=ALGORITMOS,
            audience=AUDIENCIA,
            options={"require": ["exp", "sub"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise SesionInvalidaError("La sesión expiró") from exc
    except jwt.InvalidTokenError as exc:
        raise SesionInvalidaError(f"Token de sesión inválido: {exc}") from exc
    rol = claims.get("role")
    if rol != ROL_USUARIO:
        # La ANON_KEY y la SERVICE_ROLE_KEY también son JWT firmados con el
        # mismo secreto (role anon / service_role): no son sesiones de usuario.
        raise SesionInvalidaError(f"El token no es de un usuario con sesión (role={rol!r})")
    if claims.get("is_anonymous"):
        raise SesionInvalidaError("Los usuarios anónimos no tienen acceso a Kontara")
    metadatos = claims.get("app_metadata")
    metadatos = metadatos if isinstance(metadatos, dict) else {}
    correo = claims.get("email") if isinstance(claims.get("email"), str) else None
    por_correo = bool(correo) and correo.lower() in correos_admin(admins)
    marcado = metadatos.get(MARCA_ACCESO) is True
    if not (marcado or por_correo):
        raise SesionInvalidaError(SIN_ACCESO)
    return UsuarioSesion(
        id=str(claims["sub"]),
        correo=correo,
        rol=rol,
        expira=int(claims["exp"]),
        sesion_id=str(claims["session_id"]) if claims.get("session_id") else None,
        falta_marca=not marcado,
        admin=metadatos.get(MARCA_ADMIN) is True or por_correo,
    )


def cookie_permitida(metodo: str | None, ruta: str | None) -> bool:
    """La cookie de sesión solo autentica descargas (GET/HEAD)."""
    if (metodo or "").upper() not in METODOS_COOKIE or not ruta:
        return False
    camino = ruta.split("?", 1)[0]
    return any(patron.search(camino) for patron in RUTAS_DESCARGA_COOKIE)


def token_de_cabeceras(
    authorization: str | None,
    cookie: str | None,
    *,
    metodo: str | None = None,
    ruta: str | None = None,
) -> str | None:
    """`Authorization: Bearer <token>` o, si no viene y es una descarga
    (`cookie_permitida(metodo, ruta)`), la cookie de sesión."""
    if authorization:
        esquema, _, valor = authorization.partition(" ")
        if esquema.lower() == "bearer" and valor.strip():
            return valor.strip()
    if cookie and cookie_permitida(metodo, ruta):
        try:
            galleta = SimpleCookie(cookie)
        except CookieError:
            return None
        if COOKIE_SESION in galleta and galleta[COOKIE_SESION].value:
            return galleta[COOKIE_SESION].value
    return None


def usuario_de_request(
    request: Request,
    secreto: str,
    *,
    admins: Iterable[str] | str = frozenset(),
    metodo: str | None = None,
    ruta: str | None = None,
) -> UsuarioSesion:
    """`metodo`/`ruta`: los del request original cuando quien pregunta es
    nginx (`/auth/verificar`, cabeceras X-Original-*); por defecto, los de
    este request."""
    token = token_de_cabeceras(
        request.headers.get("authorization"),
        request.headers.get("cookie"),
        metodo=metodo or request.method,
        ruta=ruta or request.url.path,
    )
    return verificar_token_supabase(token or "", secreto, admins=admins)


def respuesta_sin_sesion(motivo: str) -> JSONResponse:
    return JSONResponse(
        status_code=401,
        content={"detail": motivo},
        headers={"WWW-Authenticate": "Bearer"},
    )


# --- llamadas internas entre servicios ---------------------------------------------------------

def token_interno_valido(token: str | None) -> bool:
    return bool(token) and len(token) >= LARGO_MINIMO_TOKEN_INTERNO


def cabeceras_internas(token: str | None) -> dict[str, str]:
    """Cabecera para llamar a otro servicio de Kontara por la red interna
    ({} si KONTARA_TOKEN_INTERNO no está configurado o es muy corto)."""
    return {CABECERA_INTERNA: token} if token_interno_valido(token) else {}


def es_llamada_interna(request: Request, token: str | None) -> bool:
    recibido = request.headers.get(CABECERA_INTERNA)
    if not (recibido and token_interno_valido(token)):
        return False
    return hmac.compare_digest(recibido.encode("utf-8"), token.encode("utf-8"))


# --- sesiones cerradas antes de que venza el token ---------------------------------------------------------

_CONSULTA_SESION = """
    SELECT s.not_after, u.banned_until, u.deleted_at
    FROM auth.sessions s JOIN auth.users u ON u.id = s.user_id
    WHERE s.id = $1::uuid
"""
_CONSULTA_SESION_SIMPLE = "SELECT 1 FROM auth.sessions WHERE id = $1::uuid"


class RevocacionSesiones:
    """El access_token de GoTrue vale hasta su `exp` (1 h) aunque el usuario
    cierre sesión o lo desactiven. Si la base de la aplicación es la de
    Supabase (tiene el esquema `auth`), se comprueba además que la sesión
    (`session_id` del token) siga en `auth.sessions` y que el usuario no esté
    baneado ni borrado, con una caché de `ttl` segundos: un cierre de sesión
    o una desactivación se aplican en ~1 minuto. Si el esquema no existe o no
    hay permiso, se apaga sola (se registra una vez) y todo sigue como antes."""

    def __init__(self, ttl: float = 60.0, maximo: int = 5000):
        self.ttl = ttl
        self.maximo = maximo
        self.desactivada = False
        self._simple = False
        self._cache: dict[str, tuple[bool, float]] = {}

    async def _consultar(self, pool: Any, sesion_id: str) -> bool:
        import asyncpg  # solo aquí: no todos los servicios lo usan

        async with pool.acquire() as conn:
            if not self._simple:
                try:
                    fila = await conn.fetchrow(_CONSULTA_SESION, sesion_id)
                except asyncpg.UndefinedColumnError:
                    # GoTrue viejo, sin not_after / deleted_at.
                    self._simple = True
                else:
                    if fila is None:
                        return False
                    ahora = datetime.now(timezone.utc)
                    if fila["not_after"] is not None and fila["not_after"] <= ahora:
                        return False
                    if fila["banned_until"] is not None and fila["banned_until"] > ahora:
                        return False
                    return fila["deleted_at"] is None
            return await conn.fetchval(_CONSULTA_SESION_SIMPLE, sesion_id) is not None

    async def vigente(self, pool: Any, usuario: UsuarioSesion) -> bool:
        if self.desactivada or pool is None or not usuario.sesion_id:
            return True
        ahora = time.monotonic()
        en_cache = self._cache.get(usuario.sesion_id)
        if en_cache and en_cache[1] > ahora:
            return en_cache[0]
        try:
            vigente = await self._consultar(pool, usuario.sesion_id)
        except Exception as exc:  # sin esquema auth, sin permiso, otra base...
            self.desactivada = True
            logger.warning(
                "No se puede consultar auth.sessions (%s: %s): las sesiones se validan solo por su vencimiento",
                type(exc).__name__,
                exc,
            )
            return True
        if len(self._cache) >= self.maximo:
            self._cache = {k: v for k, v in self._cache.items() if v[1] > ahora}
        self._cache[usuario.sesion_id] = (vigente, ahora + self.ttl)
        return vigente


def instalar_verificacion_sesion(
    app: FastAPI,
    settings,
    *,
    rutas_publicas: Iterable[str] = (),
    es_publica: Callable[[str], bool] | None = None,
    revocacion: RevocacionSesiones | None = None,
    al_verificar: Callable[[Request, UsuarioSesion], Awaitable[None]] | None = None,
) -> None:
    """Exige sesión en todas las rutas de `app` si `settings.require_auth`.

    Las llamadas internas con `X-Kontara-Interno` = KONTARA_TOKEN_INTERNO
    pasan sin sesión. Llamarlo ANTES de `app.add_middleware(CORSMiddleware,
    ...)`: así CORS queda por fuera y también los 401 llevan sus cabeceras."""
    if not getattr(settings, "require_auth", False):
        return
    publicas = RUTAS_PUBLICAS | frozenset(rutas_publicas)
    admins = correos_admin(getattr(settings, "kontara_admin_emails", ""))
    token_interno = getattr(settings, "kontara_token_interno", "")

    @app.middleware("http")
    async def exigir_sesion(request: Request, call_next):
        ruta = request.url.path
        if request.method == "OPTIONS" or ruta in publicas or (es_publica and es_publica(ruta)):
            return await call_next(request)
        if es_llamada_interna(request, token_interno):
            request.state.usuario = None
            return await call_next(request)
        try:
            usuario = usuario_de_request(request, settings.supabase_jwt_secret, admins=admins)
        except SesionInvalidaError as exc:
            return respuesta_sin_sesion(str(exc))
        pool = getattr(request.app.state, "db_pool", None)
        if revocacion is not None and not await revocacion.vigente(pool, usuario):
            return respuesta_sin_sesion("La sesión se cerró: inicie sesión de nuevo")
        request.state.usuario = usuario
        if al_verificar is not None:
            await al_verificar(request, usuario)
        return await call_next(request)
