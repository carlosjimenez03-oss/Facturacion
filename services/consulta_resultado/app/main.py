"""Módulo 5: Consulta de resultado e-CF.

Consulta el estado de procesamiento de un e-CF enviado exclusivamente por el
servicio de Recepción de e-CF (services/recepcion), a través de su trackId.
"""
import logging

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from libs.ecf_common.auth.token_provider import TokenProvider
from libs.ecf_common.http_client import DGIIServiceError

from .config import ConsultaResultadoSettings, get_settings
from .dgii_client import ConsultaResultadoDGIIClient
from .schemas import ConsultaResultadoResponse

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
)


@app.get("/health")
def health(settings: ConsultaResultadoSettings = Depends(get_settings)) -> dict:
    return {
        "status": "ok",
        "app_env": settings.app_env,
        "dgii_environment": settings.dgii_environment.value,
    }


@app.get("/resultado/{track_id}", response_model=ConsultaResultadoResponse)
async def consultar_resultado(
    track_id: str,
    settings: ConsultaResultadoSettings = Depends(get_settings),
) -> ConsultaResultadoResponse:
    try:
        token = await _token_provider.obtener_token()
        cliente = ConsultaResultadoDGIIClient(settings.dgii_environment)
        return await cliente.consultar(track_id, token.token)
    except DGIIServiceError as exc:
        logger.error("La DGII rechazó la consulta de resultado %s: %s", track_id, exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except (FileNotFoundError, ValueError) as exc:
        logger.error("Error de configuración/certificado: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
