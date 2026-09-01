"""Módulo 3: Recepción de resumen factura de consumo electrónica (RFCE).

Recibe (de un sistema interno) el resumen de una Factura de Consumo
Electrónica (tipo 32) con monto inferior a RD$250,000.00, lo firma con el
certificado digital del emisor y lo envía a la DGII. El contribuyente debe
conservar igualmente el e-CF completo para futuros procesos.

Cada envío se audita (best-effort) en facturacion.envios_dgii vía
libs.ecf_common.db — ver el docstring de ese módulo.
"""
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from libs.ecf_common.auth.signing import cargar_certificado_p12, firmar_xml
from libs.ecf_common.auth.token_provider import TokenProvider
from libs.ecf_common.db import cerrar_pool, crear_pool, registrar_envio
from libs.ecf_common.http_client import DGIIServiceError
from libs.ecf_common.xml_tools import nombre_archivo_ecf

from .config import RecepcionRFCESettings, get_settings
from .dgii_client import RecepcionRFCEDGIIClient
from .schemas import RecepcionRFCEResponse

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
def health(settings: RecepcionRFCESettings = Depends(get_settings)) -> dict:
    return {
        "status": "ok",
        "app_env": settings.app_env,
        "dgii_environment": settings.dgii_environment.value,
    }


@app.post("/rfce", response_model=RecepcionRFCEResponse)
async def enviar_rfce(
    encf: str = Form(..., description="e-NCF del comprobante tipo 32"),
    xml: UploadFile = File(..., description="XML del resumen RFCE, sin firmar"),
    settings: RecepcionRFCESettings = Depends(get_settings),
) -> RecepcionRFCEResponse:
    """Firma el RFCE recibido y lo envía a la DGII."""
    try:
        credencial = cargar_certificado_p12(settings.certificado_path, settings.certificado_password)
        xml_bytes = await xml.read()
        xml_firmado = firmar_xml(xml_bytes, credencial)

        token = await _token_provider.obtener_token()
        nombre_archivo = nombre_archivo_ecf(settings.emisor_rnc, encf)

        cliente = RecepcionRFCEDGIIClient(settings.dgii_environment)
        respuesta = await cliente.enviar_rfce(xml_firmado, nombre_archivo, token.token)

        await registrar_envio(
            app.state.db_pool,
            modulo="recepcion_rfce",
            ambiente=settings.app_env,
            rnc_emisor=settings.emisor_rnc,
            encf=respuesta.encf or encf,
            estado=respuesta.estado,
            codigo=str(respuesta.codigo),
            respuesta=respuesta.model_dump(),
        )
        return respuesta
    except DGIIServiceError as exc:
        logger.error("La DGII rechazó el envío del RFCE %s: %s", encf, exc)
        await registrar_envio(
            app.state.db_pool,
            modulo="recepcion_rfce",
            ambiente=settings.app_env,
            rnc_emisor=settings.emisor_rnc,
            encf=encf,
            error=str(exc),
        )
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except (FileNotFoundError, ValueError) as exc:
        logger.error("Error de configuración/certificado: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
