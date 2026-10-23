"""Receptor Electrónico -- Recepción (obligatorio, ver
docs/dgii/EMISORES_ELECTRONICOS.md).

Recibe el e-CF que otro contribuyente nos emite (cuando actuamos como
receptor/comprador) y devuelve un ARECF (acuse de recibo) firmado
digitalmente con nuestro propio certificado. Cada recepción se audita
(best-effort) en facturacion.envios_dgii, igual que los módulos que envían
hacia la DGII -- ver el docstring de `libs.ecf_common.db`.

Además, registra (best-effort) un renglón pendiente de clasificar en
compras606.registros -- el e-CF recibido es una de las dos fuentes del
Formato 606 (Compras de Bienes y Servicios), ver docs/dgii/FORMATO_606.md y
`services/formato_606` -- y guarda el XML en recepcion.ecf_recibidos para la
bandeja de entrada de Compras de `erp_api` (aprobación comercial), ver
`libs.ecf_common.ecf_recibidos`.
"""
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from lxml import etree

from libs.ecf_common.auth.jwt_tokens import TokenInvalidoError, validar_token
from libs.ecf_common.auth.signing import CredencialFirma, cargar_certificado_p12, firmar_xml
from libs.ecf_common.compras606 import registrar_compra_606
from libs.ecf_common.db import cerrar_pool, crear_pool, registrar_envio
from libs.ecf_common.ecf_recibidos import datos_606_desde_ecf, ecf_rechazado, parsear_xml, registrar_ecf_recibido

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


def _parse_ecf(xml_bytes: bytes) -> etree._Element:
    try:
        return parsear_xml(xml_bytes)  # sin entidades ni red: viene de un tercero
    except etree.XMLSyntaxError as exc:
        raise HTTPException(status_code=422, detail=f"XML mal formado: {exc}") from exc


def _extraer_campos_ecf(root: etree._Element) -> tuple[str, str | None, str]:
    rnc_emisor = root.findtext(".//RNCEmisor")
    rnc_comprador = root.findtext(".//RNCComprador")
    encf = root.findtext(".//eNCF")
    if not rnc_emisor or not encf:
        raise HTTPException(status_code=422, detail="El e-CF no incluye RNCEmisor o eNCF")
    return rnc_emisor, rnc_comprador, encf


async def _registrar_compra_606_desde_ecf(
    pool, root: etree._Element, rnc_emisor: str, encf: str
) -> None:
    """Best-effort: un e-CF con FechaEmision faltante/inválida simplemente no
    se registra en compras606 (se puede agregar a mano luego) -- nunca debe
    impedir que se devuelva el ARECF, que es la parte obligatoria. Los
    montos salen de `datos_606_desde_ecf` (los mismos con los que erp-api
    crea el renglón desde la bandeja): sin ITBIS aunque las líneas lo
    incluyan (IndicadorMontoGravado=1) y con selectivo, otros impuestos y
    propina legal en sus casillas."""
    try:
        datos = datos_606_desde_ecf(root)
        if datos is None:
            logger.warning("e-CF %s sin FechaEmision válida, no se registra en compras606", encf)
            return
        if await ecf_rechazado(pool, rnc_emisor, encf):
            logger.info("e-CF %s reenviado, pero ya fue rechazado comercialmente: no vuelve al 606", encf)
            return
        await registrar_compra_606(
            pool,
            origen="receptor_recepcion",
            referencia_tipo="ecf_recibido",
            referencia_id=None,
            **{**datos, "rnc_cedula": rnc_emisor, "ncf": encf},
        )
    except Exception:
        logger.exception("No se pudo registrar el e-CF %s en compras606", encf)


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
    root = _parse_ecf(xml_bytes)
    rnc_emisor, rnc_comprador, encf = _extraer_campos_ecf(root)

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
    await _registrar_compra_606_desde_ecf(app.state.db_pool, root, rnc_emisor, encf)
    # Después del 606, para enlazar su renglón. Best-effort como los dos
    # anteriores y sin esperar candados: verifica la firma del e-CF pero
    # nunca cambia la respuesta (ver libs/ecf_common/ecf_recibidos.py).
    await registrar_ecf_recibido(app.state.db_pool, xml_bytes, root)

    return Response(content=arecf_firmado, media_type="text/xml")
