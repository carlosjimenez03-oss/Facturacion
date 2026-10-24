"""Módulo 10: Consulta Directorio de Servicios.

Retorna los contribuyentes electrónicos autorizados y las URLs de sus
servicios de recepción de e-CF, aprobación comercial y autenticación
(opcional) — necesarios para que un emisor pueda comunicarse con un receptor
electrónico (o viceversa).
"""
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from libs.ecf_common.ambiente import AmbienteDGII, AmbienteInvalidoError, TokenProviderPorAmbiente
from libs.ecf_common.db import cerrar_pool, crear_pool
from libs.ecf_common.environments import dgii_environment_for
from libs.ecf_common.http_client import DGIIServiceError

from .config import ConsultaDirectorioSettings, get_settings
from .dgii_client import ConsultaDirectorioDGIIClient
from .schemas import ContribuyenteDirectorio

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

_token_provider = TokenProviderPorAmbiente(
    certificado_path=settings.certificado_path,
    certificado_password=settings.certificado_password,
)
# Ambiente DGII activo (Configuración → Ambiente DGII, con caché corta; sin
# fila guardada, APP_ENV) o el que pida quien llama (`ambiente`, p. ej. el de
# la factura). Ver libs/ecf_common/ambiente.py.
_ambiente = AmbienteDGII(settings.app_env)


async def _ambiente_de(pedido: str | None) -> str:
    try:
        return await _ambiente.resolver(app.state.db_pool, pedido)
    except AmbienteInvalidoError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/health")
async def health() -> dict:
    ambiente = await _ambiente_de(None)
    return {
        "status": "ok",
        "app_env": ambiente,
        "dgii_environment": dgii_environment_for(ambiente).value,
    }


@app.get("/directorio", response_model=list[ContribuyenteDirectorio])
async def listado_directorio(
    ambiente: str | None = None,
    settings: ConsultaDirectorioSettings = Depends(get_settings),
) -> list[ContribuyenteDirectorio]:
    ambiente = await _ambiente_de(ambiente)
    dgii = dgii_environment_for(ambiente)
    try:
        token = await _token_provider.obtener_token(ambiente=dgii)
        cliente = ConsultaDirectorioDGIIClient(dgii)
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
    ambiente: str | None = None,
    settings: ConsultaDirectorioSettings = Depends(get_settings),
) -> list[ContribuyenteDirectorio]:
    ambiente = await _ambiente_de(ambiente)
    dgii = dgii_environment_for(ambiente)
    try:
        token = await _token_provider.obtener_token(ambiente=dgii)
        cliente = ConsultaDirectorioDGIIClient(dgii)
        return await cliente.obtener_por_rnc(rnc, token.token)
    except DGIIServiceError as exc:
        logger.error("La DGII rechazó la consulta del directorio para RNC %s: %s", rnc, exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except (FileNotFoundError, ValueError) as exc:
        logger.error("Error de configuración/certificado: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
