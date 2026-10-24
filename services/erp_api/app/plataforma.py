"""Rutas de plataforma de erp-api (fase 6): verificación de la sesión para
el `auth_request` de nginx y "Estado de las soluciones". Ver docs/AUTH.md.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request, Response

from libs.ecf_common.auth.sesion_supabase import SesionInvalidaError, respuesta_sin_sesion, usuario_de_request

from .config import ErpApiSettings, get_settings
from .configuracion_schemas import EstadoServicios
from .estado_servicios import estado_de_servicios

router = APIRouter(tags=["plataforma"])


@router.get("/auth/verificar", status_code=204, responses={401: {"description": "Sin sesión válida"}})
async def verificar_sesion(request: Request, settings: ErpApiSettings = Depends(get_settings)):
    """204 si el request trae un `access_token` válido de Supabase Auth
    (cabecera `Authorization: Bearer` o cookie `kontara_sesion`), 401 si no.

    nginx (frontend/nginx.conf, `auth_request`) lo consulta antes de reenviar
    cada `/api*`. Sin `SUPABASE_JWT_SECRET` responde siempre 401 (no hay con
    qué validar): nginx solo activa `auth_request` cuando esa variable
    existe, así que en ese caso nadie llega aquí."""
    try:
        usuario = usuario_de_request(request, settings.supabase_jwt_secret)
    except SesionInvalidaError as exc:
        return respuesta_sin_sesion(str(exc))
    return Response(
        status_code=204,
        headers={"X-Usuario-Id": usuario.id, "Cache-Control": "no-store"},
    )


@router.get("/estado-servicios", response_model=EstadoServicios)
async def estado_servicios(request: Request, settings: ErpApiSettings = Depends(get_settings)):
    servicios = await estado_de_servicios(settings, request.app.state.db_pool)
    return {
        "ambiente": settings.app_env,
        "ambiente_dgii": settings.dgii_environment.value,
        "verificado_en": datetime.now(timezone.utc),
        "servicios": servicios,
    }
