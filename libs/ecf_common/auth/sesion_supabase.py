"""Verificación de la sesión de los usuarios de la aplicación (Supabase Auth
/ GoTrue).

El frontend inicia sesión contra GoTrue (`/auth/v1/token`) y manda el
`access_token` en `Authorization: Bearer <token>` a cada API (o en la cookie
`kontara_sesion` cuando el navegador abre un enlace directo, p. ej. el PDF
de una factura). Ese token es un JWT HS256 firmado con el `JWT_SECRET` del
recurso de Supabase -- aquí `SUPABASE_JWT_SECRET` --, con `aud =
"authenticated"` y `role = "authenticated"` para un usuario con sesión.

Dos usos (ver docs/AUTH.md):

- `erp_api` expone `GET /auth/verificar`, al que nginx (frontend) le
  pregunta con `auth_request` antes de reenviar cualquier `/api*`.
- `instalar_verificacion_sesion` agrega la misma verificación dentro de cada
  API cuando `REQUIRE_AUTH=true` (defensa en profundidad para quien llegue
  al servicio sin pasar por nginx). Por defecto está apagada, para no romper
  las llamadas internas entre servicios (facturas-api -> inventario-api...),
  que no llevan token.

Este módulo no tiene nada que ver con `jwt_tokens.py` (tokens RS256 que
emite `receptor_autenticacion` para otros contribuyentes / la DGII).
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from http.cookies import CookieError, SimpleCookie

import jwt
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

ALGORITMOS = ["HS256"]
AUDIENCIA = "authenticated"
ROL_USUARIO = "authenticated"
COOKIE_SESION = "kontara_sesion"

# Rutas que nunca piden sesión: el healthcheck de Docker y el de
# "Estado de las soluciones" (erp-api lo consulta por la red interna).
RUTAS_PUBLICAS = frozenset({"/health"})


class SesionInvalidaError(Exception):
    pass


@dataclass(frozen=True)
class UsuarioSesion:
    id: str
    correo: str | None
    rol: str
    expira: int


def verificar_token_supabase(token: str, secreto: str) -> UsuarioSesion:
    """Valida firma (HS256 con `secreto`), vencimiento (`exp`), audiencia y
    rol del `access_token` de GoTrue. Lanza `SesionInvalidaError` con el
    motivo si algo no cuadra."""
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
    return UsuarioSesion(
        id=str(claims["sub"]),
        correo=claims.get("email"),
        rol=rol,
        expira=int(claims["exp"]),
    )


def token_de_cabeceras(authorization: str | None, cookie: str | None) -> str | None:
    """`Authorization: Bearer <token>` o, si no viene, la cookie de sesión."""
    if authorization:
        esquema, _, valor = authorization.partition(" ")
        if esquema.lower() == "bearer" and valor.strip():
            return valor.strip()
    if cookie:
        try:
            galleta = SimpleCookie(cookie)
        except CookieError:
            return None
        if COOKIE_SESION in galleta and galleta[COOKIE_SESION].value:
            return galleta[COOKIE_SESION].value
    return None


def usuario_de_request(request: Request, secreto: str) -> UsuarioSesion:
    token = token_de_cabeceras(request.headers.get("authorization"), request.headers.get("cookie"))
    return verificar_token_supabase(token or "", secreto)


def respuesta_sin_sesion(motivo: str) -> JSONResponse:
    return JSONResponse(
        status_code=401,
        content={"detail": motivo},
        headers={"WWW-Authenticate": "Bearer"},
    )


def instalar_verificacion_sesion(
    app: FastAPI,
    settings,
    *,
    rutas_publicas: Iterable[str] = (),
    es_publica: Callable[[str], bool] | None = None,
) -> None:
    """Exige sesión en todas las rutas de `app` si `settings.require_auth`.

    Llamarlo ANTES de `app.add_middleware(CORSMiddleware, ...)`: así CORS
    queda por fuera y también los 401 llevan sus cabeceras."""
    if not getattr(settings, "require_auth", False):
        return
    publicas = RUTAS_PUBLICAS | frozenset(rutas_publicas)

    @app.middleware("http")
    async def exigir_sesion(request: Request, call_next):
        ruta = request.url.path
        if request.method == "OPTIONS" or ruta in publicas or (es_publica and es_publica(ruta)):
            return await call_next(request)
        try:
            request.state.usuario = usuario_de_request(request, settings.supabase_jwt_secret)
        except SesionInvalidaError as exc:
            return respuesta_sin_sesion(str(exc))
        return await call_next(request)
