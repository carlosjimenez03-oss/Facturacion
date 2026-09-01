"""Módulo 7: Consulta de trackId e-CF.

Retorna el listado de TrackIds recibidos por la DGII para un e-NCF dado
(puede haber varios si el mismo comprobante se reenvió más de una vez).
"""
import logging

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from libs.ecf_common.auth.token_provider import TokenProvider
from libs.ecf_common.http_client import DGIIServiceError

from .config import ConsultaTrackIdsSettings, get_settings
from .dgii_client import ConsultaTrackIdsDGIIClient
from .schemas import TrackingDetalle

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
def health(settings: ConsultaTrackIdsSettings = Depends(get_settings)) -> dict:
    return {
        "status": "ok",
        "app_env": settings.app_env,
        "dgii_environment": settings.dgii_environment.value,
    }


@app.get("/trackids", response_model=list[TrackingDetalle])
async def consultar_trackids(
    rnc_emisor: str,
    encf: str,
    settings: ConsultaTrackIdsSettings = Depends(get_settings),
) -> list[TrackingDetalle]:
    try:
        token = await _token_provider.obtener_token()
        cliente = ConsultaTrackIdsDGIIClient(settings.dgii_environment)
        return await cliente.consultar(rnc_emisor, encf, token.token)
    except DGIIServiceError as exc:
        logger.error("La DGII rechazó la consulta de trackIds %s/%s: %s", rnc_emisor, encf, exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except (FileNotFoundError, ValueError) as exc:
        logger.error("Error de configuración/certificado: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
