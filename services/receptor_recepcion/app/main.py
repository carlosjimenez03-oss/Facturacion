"""Receptor Electrónico -- Recepción (obligatorio, ver
docs/dgii/EMISORES_ELECTRONICOS.md).

Recibe el e-CF que otro contribuyente nos emite (cuando actuamos como
receptor/comprador) y devuelve un ARECF (acuse de recibo) firmado
digitalmente con nuestro propio certificado. Cada recepción se audita
(best-effort) en facturacion.envios_dgii, igual que los módulos que envían
hacia la DGII -- ver el docstring de `libs.ecf_common.db`.
"""
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from lxml import etree

from libs.ecf_common.auth.jwt_tokens import TokenInvalidoError, validar_token
from libs.ecf_common.auth.signing import CredencialFirma, cargar_certificado_p12, firmar_xml
from libs.ecf_common.db import cerrar_pool, crear_pool, registrar_envio

from .arecf_builder import construir_arecf
from .config import ReceptorRecepcionSettings, get_settings

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


@app.get("/health")
def health(settings: ReceptorRecepcionSettings = Depends(get_settings)) -> dict:
    return {"status": "ok", "app_env": settings.app_env}


def _extraer_campos_ecf(xml_bytes: bytes) -> tuple[str, str | None, str]:
    try:
        root = etree.fromstring(xml_bytes)
    except etree.XMLSyntaxError as exc:
        raise HTTPException(status_code=422, detail=f"XML mal formado: {exc}") from exc
    rnc_emisor = root.findtext(".//RNCEmisor")
    rnc_comprador = root.findtext(".//RNCComprador")
    encf = root.findtext(".//eNCF")
    if not rnc_emisor or not encf:
        raise HTTPException(status_code=422, detail="El e-CF no incluye RNCEmisor o eNCF")
    return rnc_emisor, rnc_comprador, encf


def _verificar_bearer(authorization: str | None, credencial: CredencialFirma) -> None:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Falta el header Authorization: Bearer <token>")
    token = authorization.split(" ", 1)[1]
    try:
        validar_token(credencial, token)
    except TokenInvalidoError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@app.post("/fe/recepcion/api/ecf")
async def recibir_ecf(
    xml: UploadFile = File(..., description="e-CF que otro contribuyente nos emite como receptor"),
    authorization: str | None = Header(default=None),
    settings: ReceptorRecepcionSettings = Depends(get_settings),
) -> Response:
    try:
        credencial = cargar_certificado_p12(settings.certificado_path, settings.certificado_password)
    except (FileNotFoundError, ValueError) as exc:
        logger.error("Error de configuración/certificado propio: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    if settings.requiere_autenticacion:
        _verificar_bearer(authorization, credencial)

    xml_bytes = await xml.read()
    rnc_emisor, rnc_comprador, encf = _extraer_campos_ecf(xml_bytes)

    if rnc_comprador and settings.emisor_rnc and rnc_comprador != settings.emisor_rnc:
        raise HTTPException(
            status_code=422,
            detail=f"Este e-CF está dirigido al RNC {rnc_comprador}, no a {settings.emisor_rnc}",
        )

    arecf = construir_arecf(
        rnc_emisor=rnc_emisor, rnc_comprador=rnc_comprador or settings.emisor_rnc, encf=encf
    )
    arecf_firmado = firmar_xml(arecf, credencial)

    await registrar_envio(
        app.state.db_pool,
        modulo="receptor_recepcion",
        ambiente=settings.app_env,
        rnc_emisor=rnc_emisor,
        encf=encf,
        estado="recibido",
    )

    return Response(content=arecf_firmado, media_type="text/xml")
