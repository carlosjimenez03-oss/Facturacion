"""Módulo 4: Consulta de Resumen de Factura de Consumo Electrónica (RFCE).

Consulta el estado de validación de un resumen de Factura de Consumo (tipo
32, monto < RD$250,000.00) previamente enviado por el módulo
`services/recepcion_rfce`.
"""
import logging

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from libs.ecf_common.auth.token_provider import TokenProvider
from libs.ecf_common.http_client import DGIIServiceError

from .config import ConsultaRFCESettings, get_settings
from .dgii_client import ConsultaRFCEDGIIClient
from .schemas import ConsultaRFCEResponse

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
def health(settings: ConsultaRFCESettings = Depends(get_settings)) -> dict:
    return {
        "status": "ok",
        "app_env": settings.app_env,
        "dgii_environment": settings.dgii_environment.value,
    }


@app.get("/rfce", response_model=ConsultaRFCEResponse)
async def consultar_rfce(
    rnc_emisor: str,
    encf: str,
    codigo_seguridad: str,
    settings: ConsultaRFCESettings = Depends(get_settings),
) -> ConsultaRFCEResponse:
    try:
        token = await _token_provider.obtener_token()
        cliente = ConsultaRFCEDGIIClient(settings.dgii_environment)
        return await cliente.consultar(rnc_emisor, encf, codigo_seguridad, token.token)
    except DGIIServiceError as exc:
        logger.error("La DGII rechazó la consulta de RFCE %s/%s: %s", rnc_emisor, encf, exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except (FileNotFoundError, ValueError) as exc:
        logger.error("Error de configuración/certificado: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
