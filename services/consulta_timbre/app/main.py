"""Módulo 11: Consulta timbre (QR) — e-CF y FC.

A diferencia del resto de módulos, este NO llama a la DGII con un token de
sesión: construye la URL pública de verificación (la que se codifica en el
QR de la representación impresa) y, opcionalmente, la consulta directamente
(sin autenticación — es un servicio público de cara al receptor final).
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from libs.ecf_common.ambiente import AmbienteDGII, AmbienteInvalidoError
from libs.ecf_common.db import cerrar_pool, crear_pool
from libs.ecf_common.environments import DGIIEnvironment, dgii_environment_for
from libs.ecf_common.http_client import DGIIServiceError

from .config import get_settings
from .dgii_client import ConsultaTimbreDGIIClient
from .schemas import TimbreContenidoResponse, TimbreURLResponse

settings = get_settings()
logging.basicConfig(level=settings.log_level)
logger = logging.getLogger(__name__)



@asynccontextmanager
async def lifespan(app: FastAPI):
    # Solo para leer el ambiente DGII activo (configuracion.ambiente_dgii):
    # sin DATABASE_URL queda en None y se usa APP_ENV.
    app.state.db_pool = await crear_pool(settings.database_url)
    yield
    await cerrar_pool(app.state.db_pool)


app = FastAPI(title=settings.app_name, debug=settings.debug, lifespan=lifespan)
app.state.db_pool = None

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Ambiente DGII activo (Configuración → Ambiente DGII, con caché corta; sin
# fila guardada, APP_ENV) o el que pida quien llama (`ambiente`: el de la
# factura, para que el QR apunte al ambiente donde se envió). Ver
# libs/ecf_common/ambiente.py.
_ambiente = AmbienteDGII(settings.app_env)


async def _dgii(pedido: str | None) -> DGIIEnvironment:
    try:
        return await _ambiente.dgii(app.state.db_pool, pedido)
    except AmbienteInvalidoError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/health")
async def health() -> dict:
    ambiente = await _ambiente.actual(app.state.db_pool)
    return {
        "status": "ok",
        "app_env": ambiente,
        "dgii_environment": dgii_environment_for(ambiente).value,
    }


@app.get("/timbre/url", response_model=TimbreURLResponse)
async def construir_url_timbre_ecf(
    rnc_emisor: str,
    rnc_comprador: str,
    encf: str,
    fecha_emision: str,
    monto_total: str,
    fecha_firma: str,
    codigo_seguridad: str,
    ambiente: str | None = None,
) -> TimbreURLResponse:
    """Construye la URL de verificación de e-CF a codificar en el QR de la RI."""
    cliente = ConsultaTimbreDGIIClient(await _dgii(ambiente))
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
    ambiente: str | None = None,
) -> TimbreContenidoResponse:
    """Construye y consulta de inmediato la URL de verificación de e-CF."""
    try:
        cliente = ConsultaTimbreDGIIClient(await _dgii(ambiente))
        url = cliente.construir_url_ecf(
            rnc_emisor, rnc_comprador, encf, fecha_emision, monto_total, fecha_firma, codigo_seguridad
        )
        return await cliente.consultar(url)
    except DGIIServiceError as exc:
        logger.error("La DGII rechazó la consulta de timbre para %s: %s", encf, exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/timbre-fc/url", response_model=TimbreURLResponse)
async def construir_url_timbre_fc(
    rnc_emisor: str,
    encf: str,
    monto_total: str,
    codigo_seguridad: str,
    ambiente: str | None = None,
) -> TimbreURLResponse:
    """Construye la URL de verificación de RFCE a codificar en el QR de la RI."""
    cliente = ConsultaTimbreDGIIClient(await _dgii(ambiente))
    url = cliente.construir_url_fc(rnc_emisor, encf, monto_total, codigo_seguridad)
    return TimbreURLResponse(url=url)


@app.get("/timbre-fc/consulta", response_model=TimbreContenidoResponse)
async def consultar_timbre_fc(
    rnc_emisor: str,
    encf: str,
    monto_total: str,
    codigo_seguridad: str,
    ambiente: str | None = None,
) -> TimbreContenidoResponse:
    """Construye y consulta de inmediato la URL de verificación de RFCE."""
    try:
        cliente = ConsultaTimbreDGIIClient(await _dgii(ambiente))
        url = cliente.construir_url_fc(rnc_emisor, encf, monto_total, codigo_seguridad)
        return await cliente.consultar(url)
    except DGIIServiceError as exc:
        logger.error("La DGII rechazó la consulta de timbre FC para %s: %s", encf, exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
