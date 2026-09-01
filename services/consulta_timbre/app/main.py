"""Módulo 11: Consulta timbre (QR) — e-CF y FC.

A diferencia del resto de módulos, este NO llama a la DGII con un token de
sesión: construye la URL pública de verificación (la que se codifica en el
QR de la representación impresa) y, opcionalmente, la consulta directamente
(sin autenticación — es un servicio público de cara al receptor final).
"""
import logging

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from libs.ecf_common.http_client import DGIIServiceError

from .config import ConsultaTimbreSettings, get_settings
from .dgii_client import ConsultaTimbreDGIIClient
from .schemas import TimbreContenidoResponse, TimbreURLResponse

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


@app.get("/health")
def health(settings: ConsultaTimbreSettings = Depends(get_settings)) -> dict:
    return {
        "status": "ok",
        "app_env": settings.app_env,
        "dgii_environment": settings.dgii_environment.value,
    }


@app.get("/timbre/url", response_model=TimbreURLResponse)
def construir_url_timbre_ecf(
    rnc_emisor: str,
    rnc_comprador: str,
    encf: str,
    fecha_emision: str,
    monto_total: str,
    fecha_firma: str,
    codigo_seguridad: str,
    settings: ConsultaTimbreSettings = Depends(get_settings),
) -> TimbreURLResponse:
    """Construye la URL de verificación de e-CF a codificar en el QR de la RI."""
    cliente = ConsultaTimbreDGIIClient(settings.dgii_environment)
    url = cliente.construir_url_ecf(
        rnc_emisor, rnc_comprador, encf, fecha_emision, monto_total, fecha_firma, codigo_seguridad
    )
    return TimbreURLResponse(url=url)


@app.get("/timbre/consulta", response_model=TimbreContenidoResponse)
async def consultar_timbre_ecf(
    rnc_emisor: str,
    rnc_comprador: str,
    encf: str,
    fecha_emision: str,
    monto_total: str,
    fecha_firma: str,
    codigo_seguridad: str,
    settings: ConsultaTimbreSettings = Depends(get_settings),
) -> TimbreContenidoResponse:
    """Construye y consulta de inmediato la URL de verificación de e-CF."""
    try:
        cliente = ConsultaTimbreDGIIClient(settings.dgii_environment)
        url = cliente.construir_url_ecf(
            rnc_emisor, rnc_comprador, encf, fecha_emision, monto_total, fecha_firma, codigo_seguridad
        )
        return await cliente.consultar(url)
    except DGIIServiceError as exc:
        logger.error("La DGII rechazó la consulta de timbre para %s: %s", encf, exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/timbre-fc/url", response_model=TimbreURLResponse)
def construir_url_timbre_fc(
    rnc_emisor: str,
    encf: str,
    monto_total: str,
    codigo_seguridad: str,
    settings: ConsultaTimbreSettings = Depends(get_settings),
) -> TimbreURLResponse:
    """Construye la URL de verificación de RFCE a codificar en el QR de la RI."""
    cliente = ConsultaTimbreDGIIClient(settings.dgii_environment)
    url = cliente.construir_url_fc(rnc_emisor, encf, monto_total, codigo_seguridad)
    return TimbreURLResponse(url=url)


@app.get("/timbre-fc/consulta", response_model=TimbreContenidoResponse)
async def consultar_timbre_fc(
    rnc_emisor: str,
    encf: str,
    monto_total: str,
    codigo_seguridad: str,
    settings: ConsultaTimbreSettings = Depends(get_settings),
) -> TimbreContenidoResponse:
    """Construye y consulta de inmediato la URL de verificación de RFCE."""
    try:
        cliente = ConsultaTimbreDGIIClient(settings.dgii_environment)
        url = cliente.construir_url_fc(rnc_emisor, encf, monto_total, codigo_seguridad)
        return await cliente.consultar(url)
    except DGIIServiceError as exc:
        logger.error("La DGII rechazó la consulta de timbre FC para %s: %s", encf, exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
