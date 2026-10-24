"""Rutas de plataforma de erp-api (fase 6): verificación de la sesión para
el `auth_request` de nginx y "Estado de las soluciones". Ver docs/AUTH.md.
"""
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse

from libs.ecf_common.auth.sesion_supabase import (
    SIN_ACCESO,
    RevocacionSesiones,
    SesionInvalidaError,
    UsuarioSesion,
    cookie_permitida,
    respuesta_sin_sesion,
    usuario_de_request,
)
from libs.ecf_common.empresa import CABECERA_EMPRESA, EMPRESA_PREDETERMINADA, empresa_pedida, resolver_empresa
from libs.ecf_common.environments import dgii_environment_for

from . import ambiente_dgii, gotrue_admin
from .config import ErpApiSettings, get_settings
from .configuracion_schemas import EstadoServicios
from .estado_servicios import estado_de_servicios

logger = logging.getLogger(__name__)

router = APIRouter(tags=["plataforma"])

# Sesiones cerradas o usuarios desactivados antes de que venza su token
# (auth.sessions de Supabase, caché de 60 s). La comparten /auth/verificar y
# el middleware ERP_REQUIRE_AUTH (main.py).
REVOCACION = RevocacionSesiones()

# Usuarios de KONTARA_ADMIN_EMAILS a los que ya se les puso la marca en este
# proceso (para no llamar a GoTrue en cada request).
_MARCADOS: set[str] = set()


async def marcar_acceso_si_falta(settings: ErpApiSettings, usuario: UsuarioSesion) -> None:
    """Entró por KONTARA_ADMIN_EMAILS sin `app_metadata.kontara`: se la pone
    (con `kontara_admin`) por la API admin de GoTrue, para que siga entrando
    aunque luego se quite de la variable. Best-effort: si GoTrue no responde
    se reintenta en el próximo request."""
    if not usuario.falta_marca or usuario.id in _MARCADOS or not gotrue_admin.configurado(settings):
        return
    _MARCADOS.add(usuario.id)
    try:
        await gotrue_admin.dar_acceso(settings, usuario.id, admin=True)
        logger.info("Acceso a Kontara marcado en app_metadata de %s (KONTARA_ADMIN_EMAILS)", usuario.correo)
    except gotrue_admin.GoTrueError as exc:
        _MARCADOS.discard(usuario.id)
        logger.warning("No se pudo marcar el acceso de %s en GoTrue: %s", usuario.correo, exc)


def _sin_sesion(motivo: str, codigo: str | None) -> Response:
    """401 con el motivo. `X-Kontara-Motivo` (un código fijo, ASCII) lo lee
    nginx (`auth_request_set`) para que su propio 401 diga lo mismo, p. ej.
    "Su usuario no tiene acceso a Kontara" en vez de "Inicie sesión"."""
    respuesta = respuesta_sin_sesion(motivo)
    if codigo:
        respuesta.headers["X-Kontara-Motivo"] = codigo
    return respuesta


@router.get(
    "/auth/verificar",
    status_code=204,
    responses={
        401: {"description": "Sin sesión válida"},
        403: {"description": "El usuario no es miembro de la empresa pedida (X-Kontara-Empresa)"},
        503: {"description": "Sin SUPABASE_JWT_SECRET"},
    },
)
async def verificar_sesion(request: Request, settings: ErpApiSettings = Depends(get_settings)):
    """204 si el request trae un `access_token` válido de Supabase Auth de un
    usuario con acceso a Kontara, 401 si no.

    nginx (frontend/nginx.conf, `auth_request`) lo consulta antes de reenviar
    cada `/api*`, con el método y la ruta originales en `X-Original-Method` /
    `X-Original-URI`: la cookie `kontara_sesion` solo vale para GET/HEAD de
    las descargas (PDF, XML); todo lo demás necesita `Authorization: Bearer`.

    Sin `SUPABASE_JWT_SECRET` responde 503 (no hay con qué validar): nginx lo
    trata como error y no deja pasar nada, y el frontend lo muestra en rojo
    (no es el 401 que espera)."""
    if not settings.supabase_jwt_secret:
        return JSONResponse(
            status_code=503,
            content={"detail": "SUPABASE_JWT_SECRET no está configurado en erp-api: no se puede validar la sesión"},
            headers={"Cache-Control": "no-store"},
        )
    try:
        usuario = usuario_de_request(
            request,
            settings.supabase_jwt_secret,
            admins=settings.kontara_admin_emails,
            metodo=request.headers.get("x-original-method"),
            ruta=request.headers.get("x-original-uri"),
        )
    except SesionInvalidaError as exc:
        return _sin_sesion(str(exc), "sin_acceso" if str(exc) == SIN_ACCESO else None)
    pool = request.app.state.db_pool
    if not await REVOCACION.vigente(pool, usuario):
        return _sin_sesion("La sesión se cerró: inicie sesión de nuevo", "sesion_cerrada")
    await marcar_acceso_si_falta(settings, usuario)
    # Multiempresa: la empresa que pide el navegador (X-Kontara-Empresa, o la
    # cookie kontara_empresa en las descargas) tiene que ser una de las del
    # usuario. nginx reenvía a las APIs la que se devuelve aquí, en lugar de
    # la que mandó el navegador (docs/MULTIEMPRESA.md).
    metodo = request.headers.get("x-original-method") or request.method
    ruta = request.headers.get("x-original-uri") or request.url.path
    pedida = empresa_pedida(request, cookie_permitida=lambda _m, _r: cookie_permitida(metodo, ruta))
    try:
        empresa_id, _, error = await resolver_empresa(pool, usuario, pedida, interna=False)
    except Exception:
        logger.exception("No se pudo verificar la empresa del usuario %s", usuario.correo)
        return JSONResponse(
            status_code=503, content={"detail": "No se pudo verificar la empresa"}, headers={"Cache-Control": "no-store"}
        )
    if error is not None:
        error.headers["Cache-Control"] = "no-store"
        error.headers["X-Kontara-Motivo"] = "sin_empresa"
        return error
    return Response(
        status_code=204,
        headers={
            "X-Usuario-Id": usuario.id,
            CABECERA_EMPRESA: str(empresa_id or EMPRESA_PREDETERMINADA),
            "Cache-Control": "no-store",
        },
    )


@router.get("/estado-servicios", response_model=EstadoServicios)
async def estado_servicios(request: Request, settings: ErpApiSettings = Depends(get_settings)):
    servicios = await estado_de_servicios(settings, request.app.state.db_pool)
    ambiente = await ambiente_dgii.ambiente_activo(request.app.state.db_pool)
    return {
        "ambiente": ambiente,
        "ambiente_dgii": dgii_environment_for(ambiente).value,
        "verificado_en": datetime.now(timezone.utc),
        "servicios": servicios,
    }
