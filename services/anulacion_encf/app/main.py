"""Módulo 9: Anulación de e-NCF (ANECF).

Recibe (de un sistema interno) el XML (ANECF) con los rangos de secuencias
no utilizadas a anular, lo firma con el certificado digital y lo envía a la
DGII.

Cada envío se audita (best-effort) en facturacion.envios_dgii vía
libs.ecf_common.db — ver el docstring de ese módulo. Junto con la respuesta
de la DGII se guardan los rangos anulados y la fecha de la anulación
(`detalle.py`), que la DGII no devuelve y erp_api usa para el Formato 608.
"""
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from libs.ecf_common.auth.signing import cargar_certificado_p12, firmar_xml
from libs.ecf_common.auth.token_provider import TokenProvider
from libs.ecf_common.db import cerrar_pool, crear_pool, registrar_envio
from libs.ecf_common.http_client import DGIIServiceError

from .config import AnulacionSettings, get_settings
from .detalle import extraer_detalle_anulacion
from .dgii_client import AnulacionDGIIClient
from .schemas import AnulacionRangoResponse

settings = get_settings()
logging.basicConfig(level=settings.log_level)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
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

_token_provider = TokenProvider(
    ambiente=settings.dgii_environment,
    certificado_path=settings.certificado_path,
    certificado_password=settings.certificado_password,
)


@app.get("/health")
def health(settings: AnulacionSettings = Depends(get_settings)) -> dict:
    return {
        "status": "ok",
        "app_env": settings.app_env,
        "dgii_environment": settings.dgii_environment.value,
    }


@app.post("/anulacion", response_model=AnulacionRangoResponse)
async def anular_rango(
    xml: UploadFile = File(..., description="XML ANECF (rangos a anular), sin firmar"),
    settings: AnulacionSettings = Depends(get_settings),
) -> AnulacionRangoResponse:
    try:
        credencial = cargar_certificado_p12(settings.certificado_path, settings.certificado_password)
        xml_bytes = await xml.read()
        xml_firmado = firmar_xml(xml_bytes, credencial)

        token = await _token_provider.obtener_token()
        nombre_archivo = f"{settings.emisor_rnc}-anulacion.xml"

        cliente = AnulacionDGIIClient(settings.dgii_environment)
        respuesta = await cliente.anular_rango(xml_firmado, nombre_archivo, token.token)

        # La DGII ya aplicó la anulación: una sorpresa al leer el detalle del
        # XML nunca debe convertir esto en un 500 sin fila de auditoría.
        try:
            detalle = extraer_detalle_anulacion(xml_bytes)
        except Exception:
            logger.exception("No se pudo extraer el detalle de la anulación para la auditoría")
            detalle = {}

        await registrar_envio(
            app.state.db_pool,
            modulo="anulacion_encf",
            ambiente=settings.app_env,
            rnc_emisor=settings.emisor_rnc,
            codigo=respuesta.codigo,
            respuesta=respuesta.model_dump() | detalle,
        )
        return respuesta
    except DGIIServiceError as exc:
        logger.error("La DGII rechazó la anulación de rango: %s", exc)
        await registrar_envio(
            app.state.db_pool,
            modulo="anulacion_encf",
            ambiente=settings.app_env,
            rnc_emisor=settings.emisor_rnc,
            error=str(exc),
        )
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except (FileNotFoundError, ValueError) as exc:
        logger.error("Error de configuración/certificado: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
