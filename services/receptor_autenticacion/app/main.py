"""Receptor Electrónico -- Autenticación (opcional según el estándar, ver
docs/dgii/EMISORES_ELECTRONICOS.md).

Es el mismo protocolo semilla -> firma -> token que la propia DGII expone
para que ESTE contribuyente se autentique con ella (ver `services/autenticacion`
y `libs.ecf_common.auth.token_provider`), pero aquí en el rol inverso: OTRO
contribuyente (o la DGII, actuando como intermediario) nos pide una semilla,
la firma con SU certificado, y nosotros validamos esa firma y le emitimos un
token propio (JWT) para poder llamar a `receptor_recepcion` /
`receptor_aprobacion_comercial`.
"""
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from lxml import etree

from libs.ecf_common.auth.jwt_tokens import emitir_token
from libs.ecf_common.auth.semilla_store import SemillaStore
from libs.ecf_common.auth.signing import cargar_certificado_p12
from libs.ecf_common.auth.xml_signature import FirmaInvalidaError, rnc_del_certificado, verificar_firma
from libs.ecf_common.db import cerrar_pool, crear_pool, registrar_envio

from .config import ReceptorAutenticacionSettings, get_settings

settings = get_settings()
logging.basicConfig(level=settings.log_level)
logger = logging.getLogger(__name__)

_FORMATO_FECHA = "%Y-%m-%dT%H:%M:%SZ"


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

_semilla_store = SemillaStore(ttl_segundos=settings.semilla_ttl_segundos)


@app.get("/health")
def health(settings: ReceptorAutenticacionSettings = Depends(get_settings)) -> dict:
    return {"status": "ok", "app_env": settings.app_env}


@app.get("/fe/autenticacion/api/semilla")
def obtener_semilla() -> Response:
    """Emite una semilla (valor de un solo uso) sin firmar, para que el
    llamante la firme con su certificado y nos la devuelva en
    POST /fe/autenticacion/api/validacioncertificado."""
    valor, fecha = _semilla_store.emitir()
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"<SemillaModel><valor>{valor}</valor><fecha>{fecha.strftime(_FORMATO_FECHA)}</fecha></SemillaModel>"
    ).encode("utf-8")
    return Response(content=xml, media_type="text/xml")


def _extraer_valor_semilla(xml_bytes: bytes) -> str:
    try:
        root = etree.fromstring(xml_bytes)
    except etree.XMLSyntaxError as exc:
        raise FirmaInvalidaError(f"XML mal formado: {exc}") from exc
    valor = root.findtext(".//valor")
    if not valor:
        raise FirmaInvalidaError("El XML de la semilla firmada no tiene un elemento <valor>")
    return valor


@app.post("/fe/autenticacion/api/validacioncertificado")
async def validar_certificado(
    xml: UploadFile = File(..., description="Semilla firmada con el certificado del llamante"),
    accept: str = Header(default="application/json"),
    settings: ReceptorAutenticacionSettings = Depends(get_settings),
):
    """Valida la firma de la semilla devuelta y, si es válida y no ha sido
    usada antes, emite un token (JWT) identificando al RNC/Cédula/Pasaporte
    del certificado del llamante."""
    xml_bytes = await xml.read()
    try:
        certificado = verificar_firma(xml_bytes)
        valor = _extraer_valor_semilla(xml_bytes)
        rnc_llamante = rnc_del_certificado(certificado)
    except FirmaInvalidaError as exc:
        logger.warning("Semilla firmada rechazada: %s", exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not _semilla_store.consumir(valor):
        raise HTTPException(
            status_code=400, detail="La semilla no es válida, ya fue usada, o expiró"
        )

    try:
        credencial = cargar_certificado_p12(settings.certificado_path, settings.certificado_password)
    except (FileNotFoundError, ValueError) as exc:
        logger.error("Error de configuración/certificado propio: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    emitido = emitir_token(credencial, rnc_llamante)

    await registrar_envio(
        app.state.db_pool,
        modulo="receptor_autenticacion",
        ambiente=settings.app_env,
        rnc_emisor=rnc_llamante,
        estado="token_emitido",
    )

    if "xml" in accept.lower():
        xml_respuesta = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            f"<RespuestaAutenticacion><token>{emitido.token}</token>"
            f"<expira>{emitido.expira_str}</expira>"
            f"<expedido>{emitido.expedido_str}</expedido></RespuestaAutenticacion>"
        ).encode("utf-8")
        return Response(content=xml_respuesta, media_type="text/xml")

    return JSONResponse(
        {"token": emitido.token, "expira": emitido.expira_str, "expedido": emitido.expedido_str}
    )
