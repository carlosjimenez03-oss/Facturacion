"""Módulo 1: Autenticación.

Expone un API REST propio (para que sistemas externos no-Python, p. ej. un
ERP, lo consuman) que envuelve el servicio de Autenticación de la DGII. La
lógica en sí (semilla, firma, validarsemilla, cache) vive en
`libs.ecf_common.auth.token_provider.TokenProvider`, reutilizada también por
el resto de módulos Python de este monorepo sin pasar por HTTP.
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from libs.ecf_common.auth.dgii_client import DGIIAutenticacionError
from libs.ecf_common.auth.schemas import TokenResponse
from libs.ecf_common.ambiente import AmbienteDGII, AmbienteInvalidoError, TokenProviderPorAmbiente
from libs.ecf_common.db import cerrar_pool, crear_pool
from libs.ecf_common.environments import dgii_environment_for

from .config import get_settings

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

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

_token_provider = TokenProviderPorAmbiente(
    certificado_path=settings.certificado_path,
    certificado_password=settings.certificado_password,
    margen_segundos=settings.token_cache_margen_segundos,
)


# Ambiente DGII activo (Configuración → Ambiente DGII, con caché corta; sin
# fila guardada, APP_ENV) o el que pida quien llama. Ver
# libs/ecf_common/ambiente.py.
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


@app.get("/token", response_model=TokenResponse)
async def obtener_token(forzar_renovacion: bool = False, ambiente: str | None = None) -> TokenResponse:
    """Retorna un token de sesión vigente, renovándolo con la DGII si hace falta."""
    dgii = dgii_environment_for(await _ambiente_de(ambiente))
    try:
        return await _token_provider.obtener_token(forzar_renovacion, ambiente=dgii)
    except DGIIAutenticacionError as exc:
        logger.error("Fallo al autenticar con la DGII: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except (FileNotFoundError, ValueError) as exc:
        logger.error("Error de configuración/certificado: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
