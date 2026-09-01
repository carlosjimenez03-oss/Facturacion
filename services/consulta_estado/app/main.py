"""Módulo 6: Consulta de estado e-CF.

Consulta la validez/estado de un e-CF por parte del emisor o el receptor
(deben estar delegados). También permite consultar los enviados vía RFCE.
"""
import logging

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from libs.ecf_common.auth.token_provider import TokenProvider
from libs.ecf_common.http_client import DGIIServiceError

from .config import ConsultaEstadoSettings, get_settings
from .dgii_client import ConsultaEstadoDGIIClient
from .schemas import ConsultaEstadoResponse

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
def health(settings: ConsultaEstadoSettings = Depends(get_settings)) -> dict:
    return {
        "status": "ok",
        "app_env": settings.app_env,
        "dgii_environment": settings.dgii_environment.value,
    }


@app.get("/estado", response_model=ConsultaEstadoResponse)
async def consultar_estado(
    rnc_emisor: str,
    ncf_electronico: str,
    rnc_comprador: str | None = None,
    codigo_seguridad: str | None = None,
    settings: ConsultaEstadoSettings = Depends(get_settings),
) -> ConsultaEstadoResponse:
    try:
        token = await _token_provider.obtener_token()
        cliente = ConsultaEstadoDGIIClient(settings.dgii_environment)
        return await cliente.consultar(
            rnc_emisor, ncf_electronico, token.token, rnc_comprador, codigo_seguridad
        )
    except DGIIServiceError as exc:
        logger.error(
            "La DGII rechazó la consulta de estado %s/%s: %s", rnc_emisor, ncf_electronico, exc
        )
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except (FileNotFoundError, ValueError) as exc:
        logger.error("Error de configuración/certificado: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
