"""Módulo 1: Autenticación.

Expone un API REST propio (para que otros módulos/sistemas internos lo
consuman) que envuelve el servicio de Autenticación de la DGII: obtiene la
semilla, la firma con el certificado digital del emisor y la valida para
obtener un token, cacheándolo en memoria mientras siga vigente.
"""
import logging

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from .config import AutenticacionSettings, get_settings
from .dgii_client import AutenticacionDGIIClient, DGIIAutenticacionError
from .schemas import TokenResponse
from .signing import cargar_certificado_p12, firmar_xml
from .token_cache import TokenCache

settings = get_settings()
logging.basicConfig(level=settings.log_level)
logger = logging.getLogger(__name__)

app = FastAPI(title=settings.app_name, debug=settings.debug)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

_cache = TokenCache()


@app.get("/health")
def health(settings: AutenticacionSettings = Depends(get_settings)) -> dict:
    return {
        "status": "ok",
        "app_env": settings.app_env,
        "dgii_environment": settings.dgii_environment.value,
    }


async def _obtener_token(settings: AutenticacionSettings, forzar_renovacion: bool) -> TokenResponse:
    if not forzar_renovacion:
        token = _cache.vigente()
        if token is not None:
            return token

    async with _cache.lock:
        if not forzar_renovacion:
            token = _cache.vigente()
            if token is not None:
                return token

        credencial = cargar_certificado_p12(settings.certificado_path, settings.certificado_password)
        cliente = AutenticacionDGIIClient(settings.dgii_environment)

        semilla = await cliente.obtener_semilla()
        semilla_firmada = firmar_xml(semilla, credencial)
        token = await cliente.validar_semilla(semilla_firmada)

        _cache.guardar(token)
        return token


@app.get("/token", response_model=TokenResponse)
async def obtener_token(
    forzar_renovacion: bool = False,
    settings: AutenticacionSettings = Depends(get_settings),
) -> TokenResponse:
    """Retorna un token de sesión vigente, renovándolo con la DGII si hace falta."""
    try:
        return await _obtener_token(settings, forzar_renovacion)
    except DGIIAutenticacionError as exc:
        logger.error("Fallo al autenticar con la DGII: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except (FileNotFoundError, ValueError) as exc:
        logger.error("Error de configuración/certificado: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
