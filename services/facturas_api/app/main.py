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
- POST /facturas/{id}/actualizar-estado consulta por trackId el resultado de
  la validación de la DGII (vía `consulta_resultado`) y pasa la factura de
  'enviado' a 'aceptado' o 'rechazado'. Lo mismo ocurre una vez, best-effort,
  justo después de enviar, y periódicamente en una tarea de fondo (ver
  `app.estado_dgii`).

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
from contextlib import asynccontextmanager, suppress
from decimal import Decimal, InvalidOperation

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

from libs.ecf_common.auth.signing import cargar_certificado_p12, firmar_xml
from libs.ecf_common.auth.sesion_supabase import instalar_verificacion_sesion
from libs.ecf_common.compras606 import fecha_dgii_a_aaaammdd, registrar_compra_606
from libs.ecf_common.db import cerrar_pool, crear_pool, registrar_envio
from libs.ecf_common.xml_tools import nombre_archivo_ecf

from . import estado_dgii, inventario_client, repository
from .certificado import estado_certificado
from .config import FacturasApiSettings, get_settings
from .consulta_resultado_client import ConsultaResultadoError
from .inventario_client import InventarioApiError, StockInsuficienteError
from .migraciones import aplicar_migraciones
from .repository import FacturaNoEncontradaError
from .representacion_impresa import RepresentacionImpresaError, generar_pdf
from .schemas import (
    CertificadoEstado,
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
    # Columnas nuevas de facturacion.facturas (vendedor_id...): en Supabase
    # nadie corre db/init. Si falla (permisos, tabla aún sin crear) se avisa
    # en el log pero el servicio arranca igual.
    try:
        await aplicar_migraciones(pool)
    except Exception:
        logger.exception("No se pudieron aplicar las migraciones de facturacion.facturas")

    # Tarea de fondo que lleva a 'aceptado'/'rechazado' las facturas que
    # siguen en 'enviado' (ver app.estado_dgii). Con 0 queda desactivada --
    # así la corren los tests, para no lanzarla con el pool de mentira.
    sincronizacion = None
    if settings.sincronizar_estado_cada_segundos > 0:
        sincronizacion = asyncio.create_task(estado_dgii.bucle_sincronizacion(pool, settings))
    yield
    if sincronizacion is not None:
        sincronizacion.cancel()
        with suppress(asyncio.CancelledError):
            await sincronizacion
    await cerrar_pool(app.state.db_pool)


app = FastAPI(title=settings.app_name, debug=settings.debug, lifespan=lifespan)
app.state.db_pool = None

# REQUIRE_AUTH=true: exige la sesión de Supabase Auth (ver docs/AUTH.md).
instalar_verificacion_sesion(app, settings)

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


@app.get("/certificado", response_model=CertificadoEstado)
def certificado(settings: FacturasApiSettings = Depends(get_settings)):
    """Vigencia, titular y emisor del certificado .p12 con el que se firma
    (Configuración → Certificado digital). Nunca devuelve la llave privada
    ni la contraseña."""
    return estado_certificado(settings)


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
        vendedor_id=payload.vendedor_id,
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
        vendedor_id=payload.vendedor_id,
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
    # Una vez enviada a la DGII, el XML es el que la DGII tiene: regenerarlo
    # cambiaría FechaHoraFirma (y con ello el código de seguridad del PDF) y
    # devolvería la factura a 'generado', permitiendo reenviar el mismo e-NCF.
    # Solo se permite si el envío falló sin llegar a la DGII (sin trackId).
    enviada = factura["estado"] not in ("borrador", "generado", "firmado", "error") or (
        factura["estado"] == "error" and factura.get("track_id")
    )
    if enviada:
        raise HTTPException(
            status_code=409,
            detail="La factura ya fue enviada a la DGII; su XML no se puede regenerar.",
        )

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


# Tipos de e-CF autoemitidos que también forman parte del Formato 606
# (Compras de Bienes y Servicios) -- ver docs/dgii/FORMATO_606.md.
_TIPOS_COMPRAS_606 = frozenset({41, 43, 47})


def _rnc_cedula_para_606(tipo_ecf: int, datos: dict, settings: FacturasApiSettings) -> str | None:
    """A quién le compramos, para el campo "RNC o Cédula" del 606 -- distinto
    según el tipo (ver docs/dgii/FORMATO_606.md, "RNC/Cédula a reportar por
    tipo"): tipo 41 (Compras) reporta al proveedor informal (campo
    `Comprador` de la factura, ya que `Emisor` siempre somos nosotros); 43
    (Gastos Menores) y 47 (Pagos al Exterior) no tienen una contraparte
    identificada en su XSD y, por regla explícita del PDF oficial, reportan
    el RNC del propio encabezado."""
    if tipo_ecf == 41:
        return (datos.get("Encabezado", {}).get("Comprador") or {}).get("RNCComprador")
    return settings.emisor_rnc


def _separar_bienes_servicios(datos: dict) -> tuple[Decimal, Decimal]:
    """Suma DetallesItems.Item por IndicadorBienoServicio (1=Bien, 2=Servicio)
    -- el Formato 606 exige la proporción Bienes/Servicios por separado y
    ningún e-CF la trae ya calculada."""
    servicios = Decimal("0")
    bienes = Decimal("0")
    items = (datos.get("DetallesItems") or {}).get("Item") or []
    if isinstance(items, dict):
        items = [items]
    for item in items:
        try:
            monto = Decimal(str(item.get("MontoItem") or "0"))
        except InvalidOperation:
            continue
        if str(item.get("IndicadorBienoServicio")) == "2":
            servicios += monto
        else:
            bienes += monto
    return servicios, bienes


async def _registrar_compra_606_autoemitida(
    pool, factura: dict, encf: str, settings: FacturasApiSettings
) -> None:
    """Best-effort: la factura ya se envió exitosamente a la DGII (no se
    puede deshacer); un fallo aquí solo implica que ese renglón habrá que
    agregarlo a mano en el Formato 606, nunca debe afectar la respuesta."""
    try:
        datos = factura["datos"]
        fecha_emision = (datos.get("Encabezado", {}).get("Emisor") or {}).get("FechaEmision")
        fecha_comprobante = fecha_dgii_a_aaaammdd(fecha_emision) if fecha_emision else None
        if not fecha_comprobante:
            logger.warning(
                "Factura %s sin FechaEmision válida, no se registra en compras606", factura["id"]
            )
            return
        rnc_cedula = _rnc_cedula_para_606(factura["tipo_ecf"], datos, settings)
        if not rnc_cedula:
            logger.warning(
                "Factura %s sin RNC/Cédula de contraparte, no se registra en compras606",
                factura["id"],
            )
            return
        ncf_modificado = (datos.get("InformacionReferencia") or {}).get("NCFModificado")
        try:
            itbis_facturado = Decimal(str((datos.get("Encabezado", {}).get("Totales") or {}).get("TotalITBIS") or "0"))
        except InvalidOperation:
            itbis_facturado = Decimal("0")
        servicios, bienes = _separar_bienes_servicios(datos)
        await registrar_compra_606(
            pool,
            origen="auto_emitido",
            referencia_tipo="factura",
            referencia_id=str(factura["id"]),
            rnc_cedula=rnc_cedula,
            ncf=encf,
            ncf_modificado=ncf_modificado,
            fecha_comprobante=fecha_comprobante,
            monto_servicios=servicios,
            monto_bienes=bienes,
            itbis_facturado=itbis_facturado,
        )
    except Exception:
        logger.exception("No se pudo registrar la factura %s en compras606", factura.get("id"))


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
                settings.inventario_api_url, str(fid), items_inventario, documento=encf
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

    if track_id and factura["tipo_ecf"] in _TIPOS_COMPRAS_606:
        await _registrar_compra_606_autoemitida(_pool(), factura, encf, settings)

    if track_id:
        actualizada = await _consultar_resultado_tras_envio(actualizada, settings)

    return EnviarResponse(
        id=fid, estado=actualizada["estado"], track_id=track_id, error=actualizada.get("error")
    )


# Pausa antes de la consulta inmediata del resultado: la DGII valida en
# ~200 ms en promedio, consultar al instante casi siempre daría "En Proceso".
_ESPERA_CONSULTA_TRAS_ENVIO_SEGUNDOS = 1.0


async def _consultar_resultado_tras_envio(factura: dict, settings: FacturasApiSettings) -> dict:
    """Best-effort: el e-CF ya se envió (trackId en mano). Si la consulta
    falla o la DGII sigue "En Proceso", la factura queda en 'enviado' y la
    resuelven la tarea de fondo o el botón "Actualizar estado DGII" -- nunca
    debe hacer fallar la respuesta de /enviar."""
    try:
        await asyncio.sleep(_ESPERA_CONSULTA_TRAS_ENVIO_SEGUNDOS)
        return await estado_dgii.actualizar_estado_factura(_pool(), factura, settings)
    except Exception as exc:
        logger.warning(
            "No se pudo consultar el resultado de la factura %s tras enviarla: %s",
            factura.get("id"),
            exc,
        )
        return factura


@app.post("/facturas/{factura_id}/actualizar-estado", response_model=FacturaDetalle)
async def actualizar_estado_dgii(factura_id: str, settings: FacturasApiSettings = Depends(get_settings)):
    """Consulta a la DGII (por trackId) el resultado de la validación del e-CF
    y lo aplica: 'aceptado' (también condicional), 'rechazado', o sin cambios
    mientras siga "En Proceso". Para una factura en estado definitivo
    devuelve la factura tal cual, sin consultar."""
    fid = _parse_id(factura_id)
    factura = await _obtener_o_404(fid)
    if not factura.get("track_id"):
        raise HTTPException(
            status_code=409,
            detail="La factura no tiene trackId: todavía no se ha enviado a la DGII (o el envío no llegó a la DGII).",
        )
    if not estado_dgii.admite_actualizacion(factura):
        return factura

    try:
        return await estado_dgii.actualizar_estado_factura(_pool(), factura, settings)
    except ConsultaResultadoError as exc:
        logger.error("No se pudo consultar el resultado de la factura %s: %s", fid, exc)
        await registrar_envio(
            _pool(),
            modulo=estado_dgii.MODULO_AUDITORIA,
            ambiente=settings.app_env,
            rnc_emisor=factura["rnc_emisor"],
            encf=factura.get("encf"),
            track_id=factura["track_id"],
            error=str(exc),
        )
        raise HTTPException(
            status_code=502,
            detail=f"No se pudo consultar el estado del comprobante en la DGII; intente de nuevo en unos minutos. ({exc})",
        ) from exc
