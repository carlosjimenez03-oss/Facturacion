"""Módulo 8: Recepción de aprobación comercial.

Recibe (de un sistema interno) el XML (ACECF) con la conformidad
(aprobación/rechazo) del receptor sobre un e-CF recibido de un emisor, lo
firma con el certificado digital y lo envía a la DGII. Devuelve la respuesta
de la DGII más el ACECF firmado (`xml_firmado`), que el receptor también
debe entregar al emisor (erp-api lo hace, best-effort).

Cada envío se audita (best-effort) en facturacion.envios_dgii vía
libs.ecf_common.db — ver el docstring de ese módulo.
"""
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from libs.ecf_common.auth.signing import cargar_certificado_p12, firmar_xml
from libs.ecf_common.ambiente import AmbienteDGII, AmbienteInvalidoError, TokenProviderPorAmbiente
from libs.ecf_common.db import cerrar_pool, crear_pool, registrar_envio
from libs.ecf_common.environments import dgii_environment_for
from libs.ecf_common.http_client import DGIIServiceError
from libs.ecf_common.xml_tools import nombre_archivo_ecf

from .config import AprobacionComercialSettings, get_settings
from .dgii_client import AprobacionComercialDGIIClient
from .schemas import AprobacionComercialResponse

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


@app.post("/aprobacion", response_model=AprobacionComercialResponse)
async def enviar_aprobacion(
    encf: str = Form(..., description="e-NCF del e-CF que se está aprobando/rechazando"),
    xml: UploadFile = File(..., description="XML ACECF, sin firmar"),
    ambiente: str | None = Form(None, description="Ambiente (test|qa|production); por defecto el activo"),
    settings: AprobacionComercialSettings = Depends(get_settings),
) -> AprobacionComercialResponse:
    ambiente = await _ambiente_de(ambiente)
    dgii = dgii_environment_for(ambiente)
    try:
        credencial = cargar_certificado_p12(settings.certificado_path, settings.certificado_password)
        xml_bytes = await xml.read()
        xml_firmado = firmar_xml(xml_bytes, credencial)

        token = await _token_provider.obtener_token(ambiente=dgii)
        nombre_archivo = nombre_archivo_ecf(settings.emisor_rnc, encf)

        cliente = AprobacionComercialDGIIClient(dgii)
        respuesta = await cliente.enviar_aprobacion(xml_firmado, nombre_archivo, token.token)

        await registrar_envio(
            app.state.db_pool,
            modulo="aprobacion_comercial",
            ambiente=ambiente,
            rnc_emisor=settings.emisor_rnc,
            encf=encf,
            estado=respuesta.estado,
            codigo=respuesta.codigo,
            respuesta=respuesta.model_dump(exclude={"xml_firmado"}),
        )
        return respuesta.model_copy(update={"xml_firmado": xml_firmado.decode("utf-8")})
    except DGIIServiceError as exc:
        logger.error("La DGII rechazó la aprobación comercial de %s: %s", encf, exc)
        await registrar_envio(
            app.state.db_pool,
            modulo="aprobacion_comercial",
            ambiente=ambiente,
            rnc_emisor=settings.emisor_rnc,
            encf=encf,
            error=str(exc),
        )
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except (FileNotFoundError, ValueError) as exc:
        logger.error("Error de configuración/certificado: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
