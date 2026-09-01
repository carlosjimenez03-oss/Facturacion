"""Módulo 2: Recepción de e-CF.

Recibe (de un sistema interno, p. ej. el módulo de facturación/ERP) el XML
tentativo de un e-CF sin firmar, lo firma con el certificado digital del
emisor y lo envía a la DGII, retornando el `trackId` (acuse de recibo) para
su posterior consulta de estado.

Nota: este servicio NO recibe Facturas de Consumo Electrónicas (tipo 32) con
monto inferior a RD$250,000.00 — esas se envían por el módulo
`services/recepcion_rfce` (Recepción de resumen factura de consumo).
"""
import logging

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from libs.ecf_common.auth.signing import cargar_certificado_p12, firmar_xml
from libs.ecf_common.auth.token_provider import TokenProvider
from libs.ecf_common.http_client import DGIIServiceError
from libs.ecf_common.xml_tools import nombre_archivo_ecf

from .config import RecepcionSettings, get_settings
from .dgii_client import RecepcionDGIIClient
from .schemas import RecepcionResponse

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
def health(settings: RecepcionSettings = Depends(get_settings)) -> dict:
    return {
        "status": "ok",
        "app_env": settings.app_env,
        "dgii_environment": settings.dgii_environment.value,
    }


@app.post("/ecf", response_model=RecepcionResponse)
async def enviar_ecf(
    encf: str = Form(..., description="e-NCF del comprobante, p. ej. E310000000001"),
    xml: UploadFile = File(..., description="XML del e-CF tentativo, sin firmar"),
    settings: RecepcionSettings = Depends(get_settings),
) -> RecepcionResponse:
    """Firma el e-CF recibido y lo envía a la DGII; retorna el trackId."""
    try:
        credencial = cargar_certificado_p12(settings.certificado_path, settings.certificado_password)
        xml_bytes = await xml.read()
        xml_firmado = firmar_xml(xml_bytes, credencial)

        token = await _token_provider.obtener_token()
        nombre_archivo = nombre_archivo_ecf(settings.emisor_rnc, encf)

        cliente = RecepcionDGIIClient(settings.dgii_environment)
        return await cliente.enviar_ecf(xml_firmado, nombre_archivo, token.token)
    except DGIIServiceError as exc:
        logger.error("La DGII rechazó el envío del e-CF %s: %s", encf, exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except (FileNotFoundError, ValueError) as exc:
        logger.error("Error de configuración/certificado: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
