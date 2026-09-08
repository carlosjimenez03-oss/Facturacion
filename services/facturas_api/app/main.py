"""Servicio de Facturas: genera, valida, firma (vista previa) y envía e-CF.

Backend del frontend React de facturación. Persiste cada factura en
`facturacion.facturas` (Postgres) a lo largo de su ciclo de vida:

    borrador -> generado -> firmado -> enviado -> (aceptado | rechazado | error)

- POST /facturas/{id}/generar-xml arma el XML a partir de `datos` (ver
  `xml_builder`) y lo valida contra el XSD oficial del `tipo_ecf`.
- POST /facturas/{id}/firmar produce una VISTA PREVIA firmada (XML-DSig, con
  el certificado configurado en este mismo servicio) que se guarda para
  referencia del usuario -- no es lo que efectivamente se transmite.
- POST /facturas/{id}/enviar delega la firma real y el envío a la DGII al
  módulo `recepcion` (POST /ecf, ver services/recepcion), que ya implementa
  ese flujo (firmar + token + envío + auditoría). Este servicio solo arma el
  XML sin firmar y registra el resultado (trackId / error) que `recepcion`
  devuelve.

Nota: los e-CF tipo 32 (Factura de Consumo Electrónica) con monto por debajo
del umbral que exige la DGII para RFCE normalmente se envían como un RESUMEN
distinto (formato RFCE, ver services/recepcion_rfce) en vez del e-CF
completo. Este servicio siempre genera y envía el e-CF COMPLETO tipo 32 vía
`recepcion` -- el flujo de RFCE es una funcionalidad aparte, ya existente,
fuera del alcance de este módulo.
"""
import asyncio
import logging
import uuid
from contextlib import asynccontextmanager

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

from libs.ecf_common.auth.signing import cargar_certificado_p12, firmar_xml
from libs.ecf_common.db import cerrar_pool, crear_pool
from libs.ecf_common.xml_tools import nombre_archivo_ecf

from . import inventario_client, repository
from .config import FacturasApiSettings, get_settings
from .inventario_client import InventarioApiError, StockInsuficienteError
from .repository import FacturaNoEncontradaError
from .representacion_impresa import RepresentacionImpresaError, generar_pdf
from .schemas import (
    EnviarResponse,
    FacturaActualizar,
    FacturaCrear,
    FacturaDetalle,
    FacturaResumen,
    GenerarXmlResponse,
)
from .xml_builder import TIPOS_SOPORTADOS, generar_y_validar

settings = get_settings()
logging.basicConfig(level=settings.log_level)
logger = logging.getLogger(__name__)


_POOL_REINTENTOS = 10
_POOL_ESPERA_SEGUNDOS = 3


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Reintenta con espera: en Docker Compose, `depends_on: condition:
    # service_healthy` solo se respeta en el `up` inicial -- si Postgres se
    # reinicia luego (p. ej. presión de memoria en el host) y este contenedor
    # se reinicia casi al mismo tiempo por su `restart: unless-stopped`, un
    # solo intento puede perder la carrera contra Postgres todavía
    # arrancando.
    pool = None
    for intento in range(1, _POOL_REINTENTOS + 1):
        pool = await crear_pool(settings.database_url)
        if pool is not None:
            break
        logger.warning(
            "No se pudo conectar a la base de datos (intento %s/%s), "
            "reintentando en %ss...",
            intento,
            _POOL_REINTENTOS,
            _POOL_ESPERA_SEGUNDOS,
        )
        await asyncio.sleep(_POOL_ESPERA_SEGUNDOS)
    if pool is None:
        raise RuntimeError(
            "facturas_api requiere DATABASE_URL configurado y una base de datos "
            "alcanzable -- a diferencia de la auditoría de los demás módulos, "
            "aquí Postgres no es opcional (es donde viven las facturas)."
        )
    app.state.db_pool = pool
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


def _pool():
    return app.state.db_pool


def _parse_id(factura_id: str) -> uuid.UUID:
    try:
        return uuid.UUID(factura_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="id de factura inválido") from exc


@app.get("/health")
def health(settings: FacturasApiSettings = Depends(get_settings)) -> dict:
    return {
        "status": "ok",
        "app_env": settings.app_env,
        "dgii_environment": settings.dgii_environment.value,
    }


@app.post("/facturas", response_model=FacturaDetalle, status_code=201)
async def crear_factura(payload: FacturaCrear, settings: FacturasApiSettings = Depends(get_settings)):
    if payload.tipo_ecf not in TIPOS_SOPORTADOS:
        raise HTTPException(
            status_code=422,
            detail=f"tipo_ecf debe ser uno de {sorted(TIPOS_SOPORTADOS)}",
        )
    factura = await repository.crear_factura(
        _pool(),
        tipo_ecf=payload.tipo_ecf,
        ambiente=settings.app_env,
        rnc_emisor=settings.emisor_rnc,
        encf=payload.encf,
        datos=payload.datos,
        items_inventario=[item.model_dump() for item in payload.items_inventario or []] or None,
    )
    return factura


@app.get("/facturas", response_model=list[FacturaResumen])
async def listar_facturas(
    tipo_ecf: int | None = None,
    estado: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    return await repository.listar_facturas(
        _pool(), tipo_ecf=tipo_ecf, estado=estado, limite=limit, offset=offset
    )


@app.get("/facturas/{factura_id}", response_model=FacturaDetalle)
async def obtener_factura(factura_id: str):
    try:
        return await repository.obtener_factura(_pool(), _parse_id(factura_id))
    except FacturaNoEncontradaError as exc:
        raise HTTPException(status_code=404, detail="Factura no encontrada") from exc


@app.put("/facturas/{factura_id}", response_model=FacturaDetalle)
async def actualizar_factura(factura_id: str, payload: FacturaActualizar):
    fid = _parse_id(factura_id)
    actual = await _obtener_o_404(fid)
    if actual["estado"] != "borrador":
        raise HTTPException(
            status_code=409,
            detail=f"Solo se pueden editar facturas en estado 'borrador' (estado actual: {actual['estado']})",
        )
    return await repository.actualizar_datos(
        _pool(),
        fid,
        encf=payload.encf,
        datos=payload.datos,
        items_inventario=[item.model_dump() for item in payload.items_inventario or []] or None,
    )


@app.delete("/facturas/{factura_id}", status_code=204)
async def eliminar_factura(factura_id: str):
    fid = _parse_id(factura_id)
    actual = await _obtener_o_404(fid)
    if actual["estado"] != "borrador":
        raise HTTPException(
            status_code=409,
            detail=f"Solo se pueden eliminar facturas en estado 'borrador' (estado actual: {actual['estado']})",
        )
    await repository.eliminar_factura(_pool(), fid)


async def _obtener_o_404(fid: uuid.UUID) -> dict:
    try:
        return await repository.obtener_factura(_pool(), fid)
    except FacturaNoEncontradaError as exc:
        raise HTTPException(status_code=404, detail="Factura no encontrada") from exc


@app.post("/facturas/{factura_id}/generar-xml", response_model=GenerarXmlResponse)
async def generar_xml(factura_id: str):
    fid = _parse_id(factura_id)
    factura = await _obtener_o_404(fid)

    xml_bytes, errores = generar_y_validar(factura["tipo_ecf"], factura["datos"])
    if errores:
        return GenerarXmlResponse(id=fid, estado=factura["estado"], errores=errores)

    actualizada = await repository.guardar_xml_generado(_pool(), fid, xml_bytes.decode("utf-8"))
    return GenerarXmlResponse(
        id=fid, estado=actualizada["estado"], xml_generado=actualizada["xml_generado"], errores=[]
    )


@app.post("/facturas/{factura_id}/firmar", response_model=FacturaDetalle)
async def firmar_factura(factura_id: str, settings: FacturasApiSettings = Depends(get_settings)):
    """Genera una VISTA PREVIA firmada del XML (no envía nada a la DGII)."""
    fid = _parse_id(factura_id)
    factura = await _obtener_o_404(fid)
    if not factura.get("xml_generado"):
        raise HTTPException(
            status_code=409, detail="Generar el XML primero (POST /facturas/{id}/generar-xml)"
        )
    try:
        credencial = cargar_certificado_p12(settings.certificado_path, settings.certificado_password)
        xml_firmado = firmar_xml(factura["xml_generado"].encode("utf-8"), credencial)
    except (FileNotFoundError, ValueError) as exc:
        logger.error("Error de configuración/certificado: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return await repository.guardar_xml_firmado(_pool(), fid, xml_firmado.decode("utf-8"))


@app.get("/facturas/{factura_id}/representacion-impresa")
async def obtener_representacion_impresa(
    factura_id: str, settings: FacturasApiSettings = Depends(get_settings)
):
    """PDF de la Representación Impresa (RI) del e-CF, a partir de la vista
    previa firmada (POST /facturas/{id}/firmar)."""
    fid = _parse_id(factura_id)
    factura = await _obtener_o_404(fid)
    try:
        pdf_bytes = generar_pdf(factura, settings.dgii_environment)
    except RepresentacionImpresaError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    nombre_archivo = f"RI-{factura['encf'] or factura['id']}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{nombre_archivo}"'},
    )


@app.post("/facturas/{factura_id}/enviar", response_model=EnviarResponse)
async def enviar_factura(factura_id: str, settings: FacturasApiSettings = Depends(get_settings)):
    """Envía el e-CF a la DGII delegando la firma real y el envío al módulo `recepcion`."""
    fid = _parse_id(factura_id)
    factura = await _obtener_o_404(fid)
    if not factura.get("xml_generado"):
        raise HTTPException(
            status_code=409, detail="Generar el XML primero (POST /facturas/{id}/generar-xml)"
        )
    encf = factura.get("encf")
    if not encf:
        raise HTTPException(status_code=422, detail="La factura no tiene un eNCF asignado")

    items_inventario = factura.get("items_inventario") or []
    if items_inventario:
        try:
            await inventario_client.validar_disponibilidad(settings.inventario_api_url, items_inventario)
        except StockInsuficienteError as exc:
            detalle = "Stock insuficiente: " + "; ".join(
                f"{f['nombre']} (solicitado {f['solicitado']}, disponible {f['disponible']})"
                for f in exc.faltantes
            )
            raise HTTPException(status_code=409, detail=detalle) from exc
        except InventarioApiError as exc:
            logger.error("No se pudo validar disponibilidad de inventario: %s", exc)
            raise HTTPException(
                status_code=502, detail=f"No se pudo validar el inventario antes de facturar: {exc}"
            ) from exc

    nombre_archivo = nombre_archivo_ecf(factura["rnc_emisor"], encf)
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            respuesta = await client.post(
                f"{settings.recepcion_service_url}/ecf",
                data={"encf": encf},
                files={"xml": (nombre_archivo, factura["xml_generado"].encode("utf-8"), "text/xml")},
            )
    except httpx.HTTPError as exc:
        logger.error("No se pudo contactar al servicio de recepción: %s", exc)
        actualizada = await repository.registrar_resultado_envio(
            _pool(), fid, estado="error", error=f"No se pudo contactar recepcion: {exc}"
        )
        raise HTTPException(status_code=502, detail=actualizada["error"]) from exc

    if respuesta.status_code >= 400:
        detalle = respuesta.text
        actualizada = await repository.registrar_resultado_envio(
            _pool(), fid, estado="error", error=detalle
        )
        raise HTTPException(status_code=502, detail=detalle)

    cuerpo = respuesta.json()
    track_id = cuerpo.get("trackId")
    actualizada = await repository.registrar_resultado_envio(
        _pool(),
        fid,
        estado="enviado" if track_id else "error",
        track_id=track_id,
        dgii_respuesta=cuerpo,
        error=cuerpo.get("error") or cuerpo.get("mensaje"),
    )

    if track_id and items_inventario:
        # El e-CF ya se envió exitosamente (documento fiscal irreversible) --
        # un fallo acá NUNCA debe hacer fallar esta respuesta, solo se deja
        # constancia para poder detectarlo y resolverlo manualmente (ver
        # repository.registrar_sincronizacion_inventario).
        try:
            await inventario_client.registrar_salida_por_factura(
                settings.inventario_api_url, str(fid), items_inventario
            )
            await repository.registrar_sincronizacion_inventario(
                _pool(), fid, sincronizado=True, error=None
            )
        except InventarioApiError as exc:
            logger.error(
                "No se pudo registrar la salida de inventario de la factura %s: %s", fid, exc
            )
            await repository.registrar_sincronizacion_inventario(
                _pool(), fid, sincronizado=False, error=str(exc)
            )

    return EnviarResponse(
        id=fid, estado=actualizada["estado"], track_id=track_id, error=actualizada.get("error")
    )
