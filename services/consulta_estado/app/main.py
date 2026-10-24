"""Módulo 6: Consulta de estado e-CF.

Consulta la validez/estado de un e-CF por parte del emisor o el receptor
(deben estar delegados). También permite consultar los enviados vía RFCE.
"""
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from libs.ecf_common.ambiente import AmbienteDGII, AmbienteInvalidoError, TokenProviderPorAmbiente
from libs.ecf_common.db import cerrar_pool, crear_pool
from libs.ecf_common.empresa import instalar_empresa
from libs.ecf_common.environments import dgii_environment_for
from libs.ecf_common.http_client import DGIIServiceError

from .config import ConsultaEstadoSettings, get_settings
from .dgii_client import ConsultaEstadoDGIIClient
from .schemas import ConsultaEstadoResponse

settings = get_settings()
logging.basicConfig(level=settings.log_level)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Solo para leer el ambiente DGII activo (configuracion.ambiente_dgii):
    # sin DATABASE_URL queda en None y se usa APP_ENV.
    app.state.db_pool = await crear_pool(settings.database_url, max_size=2)
    yield
    await cerrar_pool(app.state.db_pool)


app = FastAPI(title=settings.app_name, debug=settings.debug, lifespan=lifespan)
app.state.db_pool = None

# Empresa del request (multiempresa, docs/MULTIEMPRESA.md): la que manda
# quien llama por la red interna (X-Kontara-Empresa); con ella se eligen el
# certificado, el ambiente y el token de la DGII. Sin cabecera: la empresa 1.
instalar_empresa(app, settings)

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


@app.get("/estado", response_model=ConsultaEstadoResponse)
async def consultar_estado(
    rnc_emisor: str,
    ncf_electronico: str,
    rnc_comprador: str | None = None,
    codigo_seguridad: str | None = None,
    ambiente: str | None = None,
    settings: ConsultaEstadoSettings = Depends(get_settings),
) -> ConsultaEstadoResponse:
    ambiente = await _ambiente_de(ambiente)
    dgii = dgii_environment_for(ambiente)
    try:
        token = await _token_provider.obtener_token(ambiente=dgii)
        cliente = ConsultaEstadoDGIIClient(dgii)
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
