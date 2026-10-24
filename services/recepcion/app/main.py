"""Módulo 2: Recepción de e-CF.

Recibe (de un sistema interno, p. ej. el módulo de facturación/ERP) el XML
tentativo de un e-CF sin firmar, lo firma con el certificado digital del
emisor y lo envía a la DGII, retornando el `trackId` (acuse de recibo) para
su posterior consulta de estado.

Cada envío se audita (best-effort) en facturacion.envios_dgii vía
libs.ecf_common.db — ver el docstring de ese módulo.

Modo contingencia (ver docs/dgii/CONTINGENCIA.md): `facturas_api` firma el
e-CF él mismo -- el XML firmado es el que se entrega al comprador y el que
lleva el código de seguridad de la representación impresa -- y lo manda con
`firmado=true`: aquí se envía tal cual, byte a byte, sin volver a firmarlo.

Ambiente DGII (ver libs/ecf_common/ambiente.py): el de la factura si
facturas_api lo manda (`ambiente`) o, si no, el activo de Configuración →
Ambiente DGII (con caché corta), y sin fila guardada, APP_ENV.
Si la DGII no responde (error de conexión, timeout o HTTP 5xx) se responde
503, para que quien llama lo distinga de un rechazo (502) y lo deje en
contingencia. `GET /disponibilidad` es la sonda corta de conectividad.

Nota: este servicio NO recibe Facturas de Consumo Electrónicas (tipo 32) con
monto inferior a RD$250,000.00 — esas se envían por el módulo
`services/recepcion_rfce` (Recepción de resumen factura de consumo).
"""
import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from lxml import etree

from libs.ecf_common.ambiente import AmbienteDGII, AmbienteInvalidoError, TokenProviderPorAmbiente
from libs.ecf_common.auth.signing import cargar_certificado_p12, firmar_xml
from libs.ecf_common.db import cerrar_pool, crear_pool, registrar_envio
from libs.ecf_common.environments import DGIIService, dgii_environment_for, dgii_service_base_url
from libs.ecf_common.http_client import DGIIAsyncClient, DGIIServiceError
from libs.ecf_common.xml_tools import nombre_archivo_ecf

from .config import RecepcionSettings, get_settings
from .dgii_client import RecepcionDGIIClient
from .schemas import RecepcionResponse

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


# Timeout de la sonda de GET /disponibilidad: corto a propósito (la usa
# facturas_api antes de cada envío mientras dura una contingencia).
_TIMEOUT_SONDA_SEGUNDOS = 4.0


def _tiene_firma(xml_bytes: bytes) -> bool:
    try:
        raiz = etree.fromstring(xml_bytes)
    except etree.XMLSyntaxError:
        return False
    return raiz.find("{http://www.w3.org/2000/09/xmldsig#}Signature") is not None


@app.get("/disponibilidad")
async def disponibilidad(ambiente: str | None = None) -> dict:
    """Sonda corta: ¿responde el servicio de Recepción de la DGII? Cualquier
    respuesta HTTP por debajo de 500 (incluido un 404/405 al GET) cuenta como
    disponible; un error de conexión, un timeout o un 5xx, como no."""
    dgii = dgii_environment_for(await _ambiente_de(ambiente))
    url = f"{dgii_service_base_url(DGIIService.RECEPCION_ECF, dgii)}/api/facturaselectronicas"
    try:
        async with DGIIAsyncClient(timeout=_TIMEOUT_SONDA_SEGUNDOS) as client:
            respuesta = await client.get(url)
    except httpx.HTTPError as exc:
        return {"disponible": False, "detalle": f"La DGII no responde: {exc.__class__.__name__} {exc}".strip()}
    if respuesta.status_code >= 500:
        return {"disponible": False, "detalle": f"La DGII respondió HTTP {respuesta.status_code}"}
    return {"disponible": True, "detalle": None}


@app.post("/ecf", response_model=RecepcionResponse)
async def enviar_ecf(
    encf: str = Form(..., description="e-NCF del comprobante, p. ej. E310000000001"),
    xml: UploadFile = File(..., description="XML del e-CF tentativo, sin firmar (o ya firmado, con firmado=true)"),
    firmado: bool = Form(False, description="El XML ya viene firmado por el emisor: se envía tal cual"),
    ambiente: str | None = Form(None, description="Ambiente de la factura (test|qa|production); por defecto el activo"),
    settings: RecepcionSettings = Depends(get_settings),
) -> RecepcionResponse:
    """Firma el e-CF recibido (salvo `firmado=true`) y lo envía a la DGII;
    retorna el trackId."""
    ambiente = await _ambiente_de(ambiente)
    dgii = dgii_environment_for(ambiente)
    try:
        xml_bytes = await xml.read()
        if firmado:
            if not _tiene_firma(xml_bytes):
                raise HTTPException(status_code=422, detail="El XML indicado como firmado no trae ds:Signature")
            xml_firmado = xml_bytes
        else:
            credencial = cargar_certificado_p12(settings.certificado_path, settings.certificado_password)
            xml_firmado = firmar_xml(xml_bytes, credencial)

        token = await _token_provider.obtener_token(ambiente=dgii)
        nombre_archivo = nombre_archivo_ecf(settings.emisor_rnc, encf)

        cliente = RecepcionDGIIClient(dgii)
        respuesta = await cliente.enviar_ecf(xml_firmado, nombre_archivo, token.token)

        await registrar_envio(
            app.state.db_pool,
            modulo="recepcion",
            ambiente=ambiente,
            rnc_emisor=settings.emisor_rnc,
            encf=encf,
            track_id=respuesta.trackId,
            respuesta=respuesta.model_dump(),
        )
        return respuesta
    except DGIIServiceError as exc:
        indisponible = exc.es_indisponibilidad
        logger.error(
            "%s el envío del e-CF %s: %s",
            "La DGII no está disponible para" if indisponible else "La DGII rechazó",
            encf,
            exc,
        )
        await registrar_envio(
            app.state.db_pool,
            modulo="recepcion",
            ambiente=ambiente,
            rnc_emisor=settings.emisor_rnc,
            encf=encf,
            error=str(exc),
        )
        raise HTTPException(status_code=503 if indisponible else 502, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        # Sin respuesta de la DGII (conexión rechazada, DNS, timeout) al
        # pedir el token o al enviar: contingencia, no un rechazo.
        detalle = f"La DGII no responde: {exc.__class__.__name__} {exc}".strip()
        logger.error("No se pudo enviar el e-CF %s: %s", encf, detalle)
        await registrar_envio(
            app.state.db_pool,
            modulo="recepcion",
            ambiente=ambiente,
            rnc_emisor=settings.emisor_rnc,
            encf=encf,
            error=detalle,
        )
        raise HTTPException(status_code=503, detail=detalle) from exc
    except (FileNotFoundError, ValueError) as exc:
        logger.error("Error de configuración/certificado: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
