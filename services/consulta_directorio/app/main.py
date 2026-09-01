"""Módulo 10: Consulta Directorio de Servicios.

Retorna los contribuyentes electrónicos autorizados y las URLs de sus
servicios de recepción de e-CF, aprobación comercial y autenticación
(opcional) — necesarios para que un emisor pueda comunicarse con un receptor
electrónico (o viceversa).
"""
import logging

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from libs.ecf_common.auth.token_provider import TokenProvider
from libs.ecf_common.http_client import DGIIServiceError

from .config import ConsultaDirectorioSettings, get_settings
from .dgii_client import ConsultaDirectorioDGIIClient
from .schemas import ContribuyenteDirectorio

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
def health(settings: ConsultaDirectorioSettings = Depends(get_settings)) -> dict:
    return {
        "status": "ok",
        "app_env": settings.app_env,
        "dgii_environment": settings.dgii_environment.value,
    }


@app.get("/directorio", response_model=list[ContribuyenteDirectorio])
async def listado_directorio(
    settings: ConsultaDirectorioSettings = Depends(get_settings),
) -> list[ContribuyenteDirectorio]:
    try:
        token = await _token_provider.obtener_token()
        cliente = ConsultaDirectorioDGIIClient(settings.dgii_environment)
        return await cliente.listado(token.token)
    except DGIIServiceError as exc:
        logger.error("La DGII rechazó la consulta del directorio: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except (FileNotFoundError, ValueError) as exc:
        logger.error("Error de configuración/certificado: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/directorio/{rnc}", response_model=list[ContribuyenteDirectorio])
async def directorio_por_rnc(
    rnc: str,
    settings: ConsultaDirectorioSettings = Depends(get_settings),
) -> list[ContribuyenteDirectorio]:
    try:
        token = await _token_provider.obtener_token()
        cliente = ConsultaDirectorioDGIIClient(settings.dgii_environment)
        return await cliente.obtener_por_rnc(rnc, token.token)
    except DGIIServiceError as exc:
        logger.error("La DGII rechazó la consulta del directorio para RNC %s: %s", rnc, exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except (FileNotFoundError, ValueError) as exc:
        logger.error("Error de configuración/certificado: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
