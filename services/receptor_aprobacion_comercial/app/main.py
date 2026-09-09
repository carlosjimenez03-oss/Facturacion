"""Receptor Electrónico -- Aprobación Comercial (obligatorio, ver
docs/dgii/EMISORES_ELECTRONICOS.md).

Recibe las aprobaciones comerciales (ACECF) que otros contribuyentes nos
emiten sobre nuestras propias facturas (cuando actuamos como emisor y el
comprador nos responde). El estándar solo exige devolver HTTP 200
(satisfactorio) o HTTP 400 (insatisfactorio) -- a diferencia de
`receptor_recepcion`, no hay ningún XML de respuesta que firmar. Cada
recepción se audita (best-effort) en facturacion.envios_dgii.
"""
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from lxml import etree

from libs.ecf_common.auth.jwt_tokens import TokenInvalidoError, validar_token
from libs.ecf_common.auth.signing import CredencialFirma, cargar_certificado_p12
from libs.ecf_common.db import cerrar_pool, crear_pool, registrar_envio

from .config import ReceptorAprobacionComercialSettings, get_settings

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
def health(settings: ReceptorAprobacionComercialSettings = Depends(get_settings)) -> dict:
    return {"status": "ok", "app_env": settings.app_env}


def _verificar_bearer(authorization: str | None, credencial: CredencialFirma) -> None:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Falta el header Authorization: Bearer <token>")
    token = authorization.split(" ", 1)[1]
    try:
        validar_token(credencial, token)
    except TokenInvalidoError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@app.post("/fe/aprobacioncomercial/api/ecf")
async def recibir_aprobacion_comercial(
    xml: UploadFile = File(..., description="Aprobación comercial (ACECF) que nos emite el comprador"),
    authorization: str | None = Header(default=None),
    settings: ReceptorAprobacionComercialSettings = Depends(get_settings),
) -> dict:
    if settings.requiere_autenticacion:
        try:
            credencial = cargar_certificado_p12(settings.certificado_path, settings.certificado_password)
        except (FileNotFoundError, ValueError) as exc:
            logger.error("Error de configuración/certificado propio: %s", exc)
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        _verificar_bearer(authorization, credencial)

    xml_bytes = await xml.read()
    try:
        root = etree.fromstring(xml_bytes)
    except etree.XMLSyntaxError as exc:
        raise HTTPException(status_code=400, detail=f"XML mal formado: {exc}") from exc

    rnc_emisor = root.findtext(".//RNCEmisor")
    encf = root.findtext(".//eNCF")

    await registrar_envio(
        app.state.db_pool,
        modulo="receptor_aprobacion_comercial",
        ambiente=settings.app_env,
        rnc_emisor=rnc_emisor or settings.emisor_rnc,
        encf=encf,
        estado="recibido",
    )

    return {"status": "ok"}
