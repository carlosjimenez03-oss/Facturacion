"""Módulo 1: Autenticación.

Expone un API REST propio (para que sistemas externos no-Python, p. ej. un
ERP, lo consuman) que envuelve el servicio de Autenticación de la DGII. La
lógica en sí (semilla, firma, validarsemilla, cache) vive en
`libs.ecf_common.auth.token_provider.TokenProvider`, reutilizada también por
el resto de módulos Python de este monorepo sin pasar por HTTP.
"""
import logging

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from libs.ecf_common.auth.dgii_client import DGIIAutenticacionError
from libs.ecf_common.auth.schemas import TokenResponse
from libs.ecf_common.auth.token_provider import TokenProvider

from .config import AutenticacionSettings, get_settings

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

_token_provider = TokenProvider(
    ambiente=settings.dgii_environment,
    certificado_path=settings.certificado_path,
    certificado_password=settings.certificado_password,
    margen_segundos=settings.token_cache_margen_segundos,
)


@app.get("/health")
def health(settings: AutenticacionSettings = Depends(get_settings)) -> dict:
    return {
        "status": "ok",
        "app_env": settings.app_env,
        "dgii_environment": settings.dgii_environment.value,
    }


@app.get("/token", response_model=TokenResponse)
async def obtener_token(forzar_renovacion: bool = False) -> TokenResponse:
    """Retorna un token de sesión vigente, renovándolo con la DGII si hace falta."""
    try:
        return await _token_provider.obtener_token(forzar_renovacion)
    except DGIIAutenticacionError as exc:
        logger.error("Fallo al autenticar con la DGII: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except (FileNotFoundError, ValueError) as exc:
        logger.error("Error de configuración/certificado: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
