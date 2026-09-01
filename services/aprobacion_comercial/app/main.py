"""Módulo 8: Recepción de aprobación comercial.

Recibe (de un sistema interno) el XML (ACECF) con la conformidad
(aprobación/rechazo) del receptor sobre un e-CF recibido de un emisor, lo
firma con el certificado digital y lo envía a la DGII.
"""
import logging

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from libs.ecf_common.auth.signing import cargar_certificado_p12, firmar_xml
from libs.ecf_common.auth.token_provider import TokenProvider
from libs.ecf_common.http_client import DGIIServiceError
from libs.ecf_common.xml_tools import nombre_archivo_ecf

from .config import AprobacionComercialSettings, get_settings
from .dgii_client import AprobacionComercialDGIIClient
from .schemas import AprobacionComercialResponse

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
def health(settings: AprobacionComercialSettings = Depends(get_settings)) -> dict:
    return {
        "status": "ok",
        "app_env": settings.app_env,
        "dgii_environment": settings.dgii_environment.value,
    }


@app.post("/aprobacion", response_model=AprobacionComercialResponse)
async def enviar_aprobacion(
    encf: str = Form(..., description="e-NCF del e-CF que se está aprobando/rechazando"),
    xml: UploadFile = File(..., description="XML ACECF, sin firmar"),
    settings: AprobacionComercialSettings = Depends(get_settings),
) -> AprobacionComercialResponse:
    try:
        credencial = cargar_certificado_p12(settings.certificado_path, settings.certificado_password)
        xml_bytes = await xml.read()
        xml_firmado = firmar_xml(xml_bytes, credencial)

        token = await _token_provider.obtener_token()
        nombre_archivo = nombre_archivo_ecf(settings.emisor_rnc, encf)

        cliente = AprobacionComercialDGIIClient(settings.dgii_environment)
        return await cliente.enviar_aprobacion(xml_firmado, nombre_archivo, token.token)
    except DGIIServiceError as exc:
        logger.error("La DGII rechazó la aprobación comercial de %s: %s", encf, exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except (FileNotFoundError, ValueError) as exc:
        logger.error("Error de configuración/certificado: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
