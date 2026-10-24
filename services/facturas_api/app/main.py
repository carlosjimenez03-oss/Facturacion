"""Servicio de Facturas: genera, valida, firma (vista previa) y envía e-CF.

Backend del frontend React de facturación. Persiste cada factura en
`facturacion.facturas` (Postgres) a lo largo de su ciclo de vida:

    borrador -> generado -> firmado -> enviado -> (aceptado | rechazado | error)
                                  `-> contingencia -> enviado (en lote, al reconectar)

- POST /facturas/{id}/generar-xml arma el XML a partir de `datos` (ver
  `xml_builder`) y lo valida contra el XSD oficial del `tipo_ecf`.
- POST /facturas/{id}/firmar produce una VISTA PREVIA firmada (XML-DSig, con
  el certificado configurado en este mismo servicio) que se guarda para
  referencia del usuario -- no es lo que efectivamente se transmite.
- POST /facturas/{id}/enviar firma el XML (o reutiliza la firma ya
  guardada: la de la representación impresa) y delega el envío a la DGII al
  módulo `recepcion` (POST /ecf con firmado=true, ver services/recepcion:
  token + envío + auditoría), que lo envía tal cual; registra el resultado
  (trackId / error). Si la DGII no responde (sin conexión, timeout, 5xx) la
  factura queda en 'contingencia' -- firmada, entregable con la leyenda de
  contingencia -- y se envía sola por lotes al reconectar (ver
  `app.contingencia`; estado y lotes en GET /contingencia).
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

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

from libs.ecf_common.auth.signing import cargar_certificado_p12, firmar_xml
from libs.ecf_common.auth.sesion_supabase import instalar_verificacion_sesion
from libs.ecf_common.db import cerrar_pool, crear_pool, registrar_envio

from . import (
    contingencia,
    contingencia_repository,
    envio,
    estado_dgii,
    inventario_client,
    repository,
    secuencias_encf,
)
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
    EstadoContingencia,
    FacturaActualizar,
    FacturaCrear,
    FacturaDetalle,
    FacturaResumen,
    GenerarXmlResponse,
    LoteContingencia,
    ReintentoContingencia,
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
    # Índice único (rnc_emisor, encf): solo si no hay duplicados (nunca falla).
    await secuencias_encf.asegurar_indice_encf_unico(pool)

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
async def crear_factura(
    payload: FacturaCrear, response: Response, settings: FacturasApiSettings = Depends(get_settings)
):
    """201 con la factura nueva; 200 con la ya creada si `clave_idempotencia`
    se repite (reintento del POS al sincronizar ventas hechas sin conexión)."""
    if payload.tipo_ecf not in TIPOS_SOPORTADOS:
        raise HTTPException(
            status_code=422,
            detail=f"tipo_ecf debe ser uno de {sorted(TIPOS_SOPORTADOS)}",
        )
    # El e-NCF lo asigna el servidor (secuencia bloqueada, sin duplicados):
    # ver app/secuencias_encf.py.
    try:
        factura = await secuencias_encf.crear_factura(
            _pool(),
            tipo_ecf=payload.tipo_ecf,
            ambiente=settings.app_env,
            rnc_emisor=settings.emisor_rnc,
            encf=payload.encf,
            asignar_encf=payload.asignar_encf,
            datos=payload.datos,
            items_inventario=[item.model_dump() for item in payload.items_inventario or []] or None,
            vendedor_id=payload.vendedor_id,
            clave_idempotencia=payload.clave_idempotencia,
        )
    except secuencias_encf.FacturaYaCreadaError as exc:
        response.status_code = 200
        return exc.factura
    except (secuencias_encf.EncfNoDisponibleError, secuencias_encf.ClaveIdempotenciaReusadaError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
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
    try:
        await secuencias_encf.validar_encf_para_actualizar(_pool(), fid, actual["rnc_emisor"], payload.encf)
    except secuencias_encf.EncfNoDisponibleError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
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
    if factura["estado"] == "contingencia":
        raise HTTPException(
            status_code=409,
            detail="La factura se emitió en contingencia (firmada y entregable al comprador); su XML no se puede "
            "regenerar: se enviará tal cual a la DGII.",
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
    if factura.get("xml_firmado") and factura["estado"] not in ("borrador", "generado", "firmado", "error"):
        # Enviada o en contingencia: su XML firmado es el que tiene (o
        # tendrá) la DGII y el que se le entregó al comprador -- volver a
        # firmar podría cambiar el código de seguridad de la RI.
        return factura
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


# Compatibilidad: los helpers del 606 viven en app.envio (también los usa
# el envío por lotes de la contingencia).
_TIPOS_COMPRAS_606 = envio.TIPOS_COMPRAS_606


def _respuesta_contingencia(factura: dict) -> EnviarResponse:
    return EnviarResponse(
        id=uuid.UUID(str(factura["id"])),
        estado=factura["estado"],
        track_id=None,
        error=factura.get("error"),
        contingencia=True,
        mensaje=contingencia.MENSAJE_EMITIDA,
        firmado_en=factura.get("firmado_en"),
    )


@app.post("/facturas/{factura_id}/enviar", response_model=EnviarResponse)
async def enviar_factura(factura_id: str, settings: FacturasApiSettings = Depends(get_settings)):
    """Firma el e-CF (o reutiliza su firma) y lo envía a la DGII vía el
    módulo `recepcion`. Si no llega por falta de conectividad, la factura
    queda emitida en contingencia (ver app.contingencia): 200 con
    `estado = 'contingencia'`, no un error."""
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

    # El XML firmado que se envía es el de la representación impresa (y, en
    # contingencia, el que se le entrega al comprador): se firma y guarda
    # antes de enviar. Sin certificado aquí, `recepcion` firma (flujo
    # anterior) y no hay contingencia posible.
    firmado = True
    try:
        factura = await envio.asegurar_firma(_pool(), factura, settings)
    except envio.FirmaNoDisponibleError as exc:
        logger.warning("facturas_api no puede firmar (%s): recepcion firmará el e-CF", exc)
        firmado = False

    estado_contingencia = await contingencia.leer_estado_seguro(_pool())
    if firmado and estado_contingencia and estado_contingencia.get("activa"):
        disponible, detalle = await contingencia.sondear(settings)
        if not disponible:
            error = detalle or "La DGII no responde"
            await contingencia.registrar_falla_segura(_pool(), error)
            actualizada = await repository.marcar_contingencia(_pool(), fid, error=error)
            return _respuesta_contingencia(actualizada)

    resultado = await envio.enviar_a_recepcion(settings, factura, firmado=firmado)

    if resultado.sin_conexion:
        logger.error("El e-CF %s no llegó a la DGII: %s", encf, resultado.error)
        await contingencia.registrar_falla_segura(_pool(), resultado.error or "La DGII no responde")
        if firmado:
            try:
                actualizada = await repository.marcar_contingencia(_pool(), fid, error=resultado.error)
            except FacturaNoEncontradaError as exc:
                # Ya tenía trackId (reenvío de una factura que llegó a la DGII).
                raise HTTPException(status_code=502, detail=resultado.error) from exc
            return _respuesta_contingencia(actualizada)
        actualizada = await repository.registrar_resultado_envio(
            _pool(), fid, estado="error", error=resultado.error
        )
        raise HTTPException(status_code=502, detail=actualizada["error"])

    if resultado.status_code is not None and resultado.status_code >= 400:
        await repository.registrar_resultado_envio(_pool(), fid, estado="error", error=resultado.error)
        if resultado.status_code != 500 and estado_contingencia is not None:
            # La DGII respondió (aunque fuera para rechazarlo): hay conexión.
            await contingencia.registrar_exito_seguro(_pool())
        raise HTTPException(status_code=502, detail=resultado.error)

    track_id = resultado.track_id
    actualizada = await repository.registrar_resultado_envio(
        _pool(),
        fid,
        estado="enviado" if track_id else "error",
        track_id=track_id,
        dgii_respuesta=resultado.cuerpo,
        error=resultado.error,
    )

    if track_id:
        await envio.aplicar_flujo_tras_envio(_pool(), {**factura, **actualizada}, settings)
        # La DGII respondió: fin de la contingencia, y lo que quedó
        # pendiente se envía ya en segundo plano (sin esperar la tarea de fondo).
        if estado_contingencia is not None:
            await contingencia.registrar_exito_seguro(_pool())
            if estado_contingencia.get("pendientes"):
                contingencia.lanzar_envio_pendientes(_pool(), settings, sonda_hecha=True)
        actualizada = await _consultar_resultado_tras_envio(actualizada, settings)

    return EnviarResponse(
        id=fid,
        estado=actualizada["estado"],
        track_id=track_id,
        error=actualizada.get("error"),
        firmado_en=actualizada.get("firmado_en"),
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
    if factura["estado"] == "contingencia":
        raise HTTPException(
            status_code=409,
            detail="La factura se emitió en contingencia y todavía no se ha enviado a la DGII: se enviará "
            "automáticamente al restablecerse la conexión (Centro de sincronización → Reintentar conexión ahora).",
        )
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


# --- Modo contingencia ---------------------------------------------------------

def _estado_contingencia(estado: dict, lotes: list[dict], settings: FacturasApiSettings) -> dict:
    return {
        **estado,
        "vence_en": contingencia.vencimiento(estado, settings),
        "horas_limite": settings.contingencia_horas_limite,
        "lote_tamano": settings.contingencia_lote_tamano,
        "envio_en_curso": contingencia.envio_en_curso(),
        "lotes": lotes,
    }


@app.get("/contingencia", response_model=EstadoContingencia)
async def obtener_contingencia(
    lotes: int = Query(10, ge=0, le=100), settings: FacturasApiSettings = Depends(get_settings)
):
    """Estado del modo contingencia (facturacion.contingencia), lo pendiente
    de envío y los últimos lotes."""
    estado = await contingencia_repository.leer_estado(_pool())
    lista = await contingencia_repository.listar_lotes(_pool(), limite=lotes) if lotes else []
    return _estado_contingencia(estado, lista, settings)


@app.get("/contingencia/lotes", response_model=list[LoteContingencia])
async def listar_lotes_contingencia(limit: int = Query(20, ge=1, le=200)):
    return await contingencia_repository.listar_lotes(_pool(), limite=limit)


@app.post("/contingencia/reintentar", response_model=ReintentoContingencia)
async def reintentar_contingencia(settings: FacturasApiSettings = Depends(get_settings)):
    """"Reintentar conexión ahora": sonda corta y, si la DGII responde y hay
    pendientes, lanza el envío por lotes en segundo plano (no espera a que
    termine: el progreso se ve en GET /contingencia)."""
    disponible, detalle = await contingencia.sondear(settings)
    lanzado = False
    if disponible:
        estado = await contingencia_repository.leer_estado(_pool())
        if estado["pendientes"]:
            lanzado = contingencia.lanzar_envio_pendientes(_pool(), settings, sonda_hecha=True) or contingencia.envio_en_curso()
        else:
            await contingencia_repository.registrar_exito(_pool())
    else:
        await contingencia_repository.registrar_falla(_pool(), detalle or "La DGII no responde")
    estado = await contingencia_repository.leer_estado(_pool())
    lista = await contingencia_repository.listar_lotes(_pool(), limite=10)
    return {
        "disponible": disponible,
        "detalle": detalle,
        "envio_iniciado": lanzado,
        "estado": _estado_contingencia(estado, lista, settings),
    }
