"""Acceso a Postgres de Compras (fase 3): bandeja de entrada de e-CF
recibidos, facturas de compra y cuentas por pagar, pagos a proveedores,
comprobantes E41/E43 y retenciones del mes.

Modelo (ver docs/ERP_API.md, "Compras"):

- Una **factura de compra** es un renglón de `compras606.registros`: ahí
  están los datos fiscales (proveedor, NCF, montos, retenciones, fecha y
  forma de pago). Llega de un e-CF recibido (receptor_recepcion), de un
  E41/E47 autoemitido (facturas_api) o se registra en Compras
  (`crear_factura_compra`, origen 'compras'). `compras.facturas` guarda lo
  que el 606 no tiene (nombre del proveedor, contacto, compra de inventario
  vinculada, concepto, vencimiento).
- El **proveedor** se identifica por el RNC/cédula del 606; el nombre sale,
  en ese orden, de la factura de compra, del e-CF recibido, del contacto
  (`ventas.clientes`), de `inventario.proveedores` o del E41.
- Un **pago** (`compras.pagos`) copia sus retenciones al renglón del 606
  (fecha_pago, itbis_retenido, tipo_retencion_isr, monto_retencion_renta,
  forma_pago), de donde el IT-1 y el IR-17 las leen por mes de pago. Esto
  se escribe con SQL directo y en la MISMA transacción que el pago, no por
  `PUT /registros/{id}` de formato_606: así el pago y el 606 no pueden
  quedar desalineados si falla la red entre los dos servicios, y el
  `FOR UPDATE` del renglón serializa los pagos concurrentes. Las reglas de
  validación son las de `libs/ecf_common/compras606.py` (mismas funciones).

Igual que `repository.py`, este repositorio SÍ requiere una base de datos.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from collections.abc import Awaitable, Callable
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import asyncpg

from libs.ecf_common.compras606 import (
    FORMAS_PAGO,
    TIPOS_BIENES_SERVICIOS,
    TIPOS_RETENCION_ISR,
    periodo_de_fecha,
    tipo_identificacion,
    validar_fecha,
    validar_ncf,
    validar_periodo,
    validar_rnc_cedula,
)
from libs.ecf_common.ecf_recibidos import datos_606_desde_ecf, items_ecf, parsear_xml

from . import compras_calculos
from .aprobacion_client import AprobacionComercialError
from .compras_calculos import CamposPago606, Pago
from . import impuestos_repository
from .impuestos_repository import ConflictoImpuestosError, validar_periodo_mensual
from .precalculos import nombre_periodo
from .repository import (
    _ZONA_RD,
    DatosInvalidosError,
    _monto_dgii,
    _redondear,
    bloquear_cuenta_bancaria,
    hoy,
    liberar_conciliacion,
    validar_cuenta_bancaria,
)

logger = logging.getLogger(__name__)

_CERO = Decimal("0")


class CompraNoEncontradaError(Exception):
    """e-CF recibido, factura de compra o pago inexistente (404)."""


class ConflictoComprasError(Exception):
    """La operación no aplica en el estado actual (409)."""


_NOMBRE_FORMULARIO = {
    "606": "Formato 606", "607": "Formato 607", "608": "Formato 608", "it1": "IT-1", "ir17": "IR-17", "ir2": "IR-2",
}


async def _exigir_no_presentada(conn: asyncpg.Connection, formulario: str, periodo: str) -> None:
    """`impuestos_repository._exigir_no_presentada` (candado del período y
    409 si ya está presentado) con el mensaje para la pantalla: "El IT-1 de
    octubre 2026 ya está marcado como presentado…"."""
    try:
        await impuestos_repository._exigir_no_presentada(conn, formulario, periodo)
    except ConflictoImpuestosError:
        presentada = await conn.fetchval(
            "SELECT presentada_en FROM impuestos.declaraciones_presentadas WHERE formulario = $1 AND periodo = $2",
            formulario,
            periodo,
        )
        cuando = f" (el {presentada:%d/%m/%Y})" if presentada else ""
        raise ConflictoImpuestosError(
            f"El {_NOMBRE_FORMULARIO.get(formulario, formulario)} de {nombre_periodo(periodo)} ya está marcado "
            f"como presentado{cuando}: desmárquelo en Impuestos para poder modificarlo"
        ) from None


def _rnc(valor: str | None) -> str:
    return re.sub(r"[^0-9A-Za-z]", "", valor or "").upper()


# --- Expresiones SQL ---------------------------------------------------------

def _fecha_606(columna: str) -> str:
    """AAAAMMDD del 606 -> date (NULL si falta o no existe)."""
    return (
        f"ventas.fecha_dgii_segura(substr({columna}, 7, 2) || '-' || substr({columna}, 5, 2)"
        f" || '-' || substr({columna}, 1, 4))"
    )


_TOTAL_606 = (
    "(r.monto_servicios + r.monto_bienes + r.itbis_facturado + r.impuesto_selectivo_consumo"
    " + r.otros_impuestos + r.monto_propina_legal)"
)
# Total por pagar de un renglón `r` del 606 con su e-CF recibido `er`: el
# MontoTotal del e-CF (lo que el proveedor cobra, con ITBIS, selectivo,
# otros impuestos y propina) o, sin e-CF, la suma de las casillas del 606.
_TOTAL_CXP = f"(CASE WHEN er.id IS NOT NULL AND er.monto_total > 0 THEN er.monto_total ELSE {_TOTAL_606} END)"
# Mismo criterio que `_SIGNO_606` de impuestos_repository.
_ES_NOTA_CREDITO = "(r.ncf ~* '^(E34|B04)' OR (length(r.ncf) = 19 AND substr(r.ncf, 10, 2) = '04'))"
# Ventana tras la que un envío de aprobación comercial que quedó 'enviando'
# (el proceso murió a mitad) se puede volver a intentar. Mayor que el
# timeout de aprobacion_comercial (60 s) más la entrega al proveedor.
_ENVIO_VENCIDO = "interval '5 minutes'"

# Datos del proveedor de un renglón `r` del 606 (ver docstring del módulo).
_JOINS_PROVEEDOR = """
    LEFT JOIN compras.facturas cf ON cf.registro606_id = r.id
    LEFT JOIN recepcion.ecf_recibidos er ON er.compra606_id = r.id
    LEFT JOIN ventas.clientes clx ON clx.id = cf.contacto_id
    LEFT JOIN ventas.clientes cl ON cl.rnc = r.rnc_cedula
    LEFT JOIN LATERAL (
        SELECT p.nombre FROM inventario.proveedores p
        WHERE regexp_replace(COALESCE(p.rnc, ''), '[^0-9]', '', 'g') = r.rnc_cedula
        ORDER BY p.activo DESC, p.creado_en
        LIMIT 1
    ) ip ON true
    LEFT JOIN facturacion.facturas f ON r.referencia_tipo = 'factura' AND f.id::text = r.referencia_id
    CROSS JOIN LATERAL (SELECT f.datos::jsonb AS d) x
"""
_NOMBRE_PROVEEDOR = (
    "COALESCE(NULLIF(cf.proveedor_nombre, ''), er.razon_social_emisor, clx.nombre, cl.nombre, ip.nombre,"
    " NULLIF(d #>> '{Encabezado,Comprador,RazonSocialComprador}', ''), '')"
)

# Cuentas por pagar: renglones del 606 que son facturas por pagar (no notas
# de crédito, no E43 -- el gasto menor se paga al momento --, no e-CF
# rechazados comercialmente) con lo pagado y lo pendiente:
#   pendiente = total − notas de crédito del proveedor que la modifican
#               − retenciones del 606 − pagos no anulados.
# `cxp_docs` incluye además las notas de crédito (para su detalle y para
# editarlas): sin pendiente propio, con la factura que modifican (`afecta`).
_CTE_CXP = f"""
    cxp_pagos AS (
        SELECT registro606_id, sum(monto) AS pagado
        FROM compras.pagos
        WHERE NOT anulado
        GROUP BY registro606_id
    ),
    cxp_notas AS (
        SELECT r.rnc_cedula, upper(r.ncf_modificado) AS ncf_afectado, sum({_TOTAL_CXP}) AS acreditado
        FROM compras606.registros r
        LEFT JOIN recepcion.ecf_recibidos er ON er.compra606_id = r.id
        WHERE r.ncf_modificado IS NOT NULL AND {_ES_NOTA_CREDITO}
          AND COALESCE(er.estado_aprobacion, '') <> 'rechazado'
        GROUP BY 1, 2
    ),
    cxp_docs AS (
        SELECT
            r.id AS registro606_id,
            r.ncf,
            r.periodo,
            r.origen,
            r.rnc_cedula AS proveedor_rnc,
            {_NOMBRE_PROVEEDOR} AS proveedor_nombre,
            COALESCE(cf.contacto_id, cl.id) AS contacto_id,
            COALESCE(cf.compra_inventario_id, er.compra_inventario_id) AS compra_inventario_id,
            er.id AS ecf_recibido_id,
            CASE WHEN er.estado_aprobacion = 'enviando' THEN 'pendiente' ELSE er.estado_aprobacion END
                AS estado_aprobacion,
            f.id AS factura_id,
            cf.concepto,
            r.tipo_bienes_servicios,
            fc.fecha AS fecha_comprobante,
            COALESCE(
                cf.vence,
                er.fecha_limite_pago,
                fc.fecha + COALESCE(clx.condicion_pago_dias, cl.condicion_pago_dias, 30)
            ) AS vence,
            (r.monto_servicios + r.monto_bienes)::numeric(14, 2) AS monto_facturado,
            r.itbis_facturado,
            {_TOTAL_CXP}::numeric(14, 2) AS total,
            COALESCE(n.acreditado, 0)::numeric(14, 2) AS notas_credito,
            r.tipo_retencion_isr,
            r.monto_retencion_renta AS retencion_isr,
            r.itbis_retenido AS retencion_itbis,
            (r.itbis_retenido + r.monto_retencion_renta)::numeric(14, 2) AS retenciones,
            COALESCE(p.pagado, 0)::numeric(14, 2) AS pagado,
            CASE WHEN {_ES_NOTA_CREDITO} THEN 0
                 ELSE {_TOTAL_CXP} - COALESCE(n.acreditado, 0) - r.itbis_retenido - r.monto_retencion_renta
                      - COALESCE(p.pagado, 0)
            END::numeric(14, 2) AS pendiente,
            {_fecha_606("r.fecha_pago")} AS fecha_pago,
            r.forma_pago,
            {_monto_dgii("{Encabezado,Totales,TotalISRRetencion}")} AS retencion_isr_ecf,
            {_monto_dgii("{Encabezado,Totales,TotalITBISRetenido}")} AS retencion_itbis_ecf,
            {_ES_NOTA_CREDITO} AS es_nota_credito,
            r.ncf_modificado,
            CASE WHEN {_ES_NOTA_CREDITO} AND r.ncf_modificado IS NOT NULL THEN (
                SELECT a.id FROM compras606.registros a
                WHERE a.rnc_cedula = r.rnc_cedula AND upper(a.ncf) = upper(r.ncf_modificado)
            ) END AS afecta_registro606_id
        FROM compras606.registros r
        {_JOINS_PROVEEDOR}
        CROSS JOIN LATERAL (
            SELECT COALESCE({_fecha_606("r.fecha_comprobante")}, r.creado_en::date) AS fecha
        ) fc
        LEFT JOIN cxp_pagos p ON p.registro606_id = r.id
        LEFT JOIN cxp_notas n ON n.rnc_cedula = r.rnc_cedula AND n.ncf_afectado = upper(r.ncf)
        WHERE r.ncf !~* '^E43'
          AND COALESCE(er.estado_aprobacion, '') <> 'rechazado'
    ),
    cxp AS (
        SELECT * FROM cxp_docs WHERE NOT es_nota_credito
    )
"""

# Estado calculado de cada cuenta por pagar (`cxp`) o documento (`cxp_docs`,
# también las notas de crédito, que quedan 'pagada': no se pagan); $1 = hoy.
def _select_con_estado(origen: str) -> str:
    return f"""
    WITH {_CTE_CXP}
    SELECT * FROM (
        SELECT
            {origen}.*,
            CASE
                WHEN pendiente <= 0 THEN 'pagada'
                WHEN vence < $1::date THEN 'vencida'
                ELSE 'pendiente'
            END AS estado,
            CASE WHEN pendiente > 0 AND vence < $1::date THEN $1::date - vence ELSE 0 END AS dias_vencida
        FROM {origen}
    ) cuentas
"""


_SELECT_CXP_CON_ESTADO = _select_con_estado("cxp")
_SELECT_DOCS_CON_ESTADO = _select_con_estado("cxp_docs")


def _fila_cxp(fila: asyncpg.Record) -> dict[str, Any]:
    datos = dict(fila)
    for campo in (
        "registro606_id", "contacto_id", "compra_inventario_id", "ecf_recibido_id", "factura_id",
        "afecta_registro606_id",
    ):
        if datos.get(campo) is not None:
            datos[campo] = str(datos[campo])
    datos["tipo_ecf"] = compras_calculos.tipo_ecf_de_ncf(datos["ncf"])
    datos["tipo_retencion_isr_label"] = compras_calculos.etiqueta_retencion_isr(datos["tipo_retencion_isr"])
    return datos


# --- Bandeja de entrada (recepcion.ecf_recibidos) ---------------------------------------------------------

# 'enviando' (la aprobación/rechazo en camino a la DGII) se muestra como
# 'pendiente' con `envio_en_curso`: la decisión todavía no está tomada.
_COLUMNAS_ECF_RECIBIDO = f"""
    id, rnc_emisor, razon_social_emisor, rnc_comprador, encf, tipo_ecf, fecha_emision,
    fecha_limite_pago, monto_total, itbis_total, recibido_en, estado_aprobacion, motivo_rechazo,
    aprobado_en, error_aprobacion, compra606_id, compra_inventario_id, firma_valida, firma_detalle,
    enviado_al_emisor, error_envio_emisor, decision_en_envio,
    (estado_aprobacion = 'enviando' AND envio_iniciado_en > now() - {_ENVIO_VENCIDO}) AS envio_en_curso
"""


def _fila_ecf(fila: asyncpg.Record) -> dict[str, Any]:
    datos = dict(fila)
    for campo in ("id", "compra606_id", "compra_inventario_id"):
        if datos.get(campo) is not None:
            datos[campo] = str(datos[campo])
    if datos["estado_aprobacion"] == "enviando":
        datos["estado_aprobacion"] = "pendiente"
    datos.pop("decision_en_envio", None)
    datos["envio_en_curso"] = bool(datos["envio_en_curso"])
    return datos


async def listar_ecf_recibidos(
    pool: asyncpg.Pool,
    *,
    estado: str | None = None,
    q: str | None = None,
    periodo: str | None = None,
    limite: int = 200,
    offset: int = 0,
) -> list[dict[str, Any]]:
    condiciones = []
    valores: list[Any] = []
    if estado is not None:
        valores.append(["pendiente", "enviando"] if estado == "pendiente" else [estado])
        condiciones.append(f"estado_aprobacion = ANY(${len(valores)}::text[])")
    if q:
        valores.append(f"%{q}%")
        n = len(valores)
        condiciones.append(f"(encf ILIKE ${n} OR rnc_emisor ILIKE ${n} OR razon_social_emisor ILIKE ${n})")
    if periodo:
        valores.append(validar_periodo_mensual(periodo))
        condiciones.append(
            f"to_char(COALESCE(fecha_emision, (recibido_en AT TIME ZONE 'America/Santo_Domingo')::date),"
            f" 'YYYYMM') = ${len(valores)}"
        )
    where = f"WHERE {' AND '.join(condiciones)}" if condiciones else ""
    valores.extend([limite, offset])
    query = f"""
        SELECT {_COLUMNAS_ECF_RECIBIDO} FROM recepcion.ecf_recibidos
        {where}
        ORDER BY recibido_en DESC
        LIMIT ${len(valores) - 1} OFFSET ${len(valores)}
    """
    async with pool.acquire() as conn:
        filas = await conn.fetch(query, *valores)
    return [_fila_ecf(fila) for fila in filas]


def _detalle_ecf(fila: asyncpg.Record) -> dict[str, Any]:
    datos = _fila_ecf(fila)
    xml = datos.pop("xml")
    respuesta = datos.pop("respuesta_aprobacion")
    if isinstance(respuesta, str):
        respuesta = json.loads(respuesta)
    try:
        items = items_ecf(parsear_xml(xml))
    except Exception:  # XML guardado tal cual llegó; si no se puede leer, sin líneas
        items = []
    datos.update(
        xml=xml,
        items=items,
        subtotal=datos["monto_total"] - datos["itbis_total"],
        respuesta_dgii=respuesta,
    )
    return datos


async def _obtener_ecf(conn: asyncpg.Connection, ecf_id: uuid.UUID, *, bloquear: bool = False) -> asyncpg.Record:
    fila = await conn.fetchrow(
        f"SELECT {_COLUMNAS_ECF_RECIBIDO}, xml, respuesta_aprobacion FROM recepcion.ecf_recibidos WHERE id = $1"
        + (" FOR UPDATE" if bloquear else ""),
        ecf_id,
    )
    if fila is None:
        raise CompraNoEncontradaError(str(ecf_id))
    return fila


async def obtener_ecf_recibido(pool: asyncpg.Pool, ecf_id: uuid.UUID) -> dict[str, Any]:
    async with pool.acquire() as conn:
        return _detalle_ecf(await _obtener_ecf(conn, ecf_id))


async def xml_ecf_recibido(pool: asyncpg.Pool, ecf_id: uuid.UUID) -> tuple[str, str]:
    """(nombre de archivo RNC+e-NCF.xml, contenido)."""
    async with pool.acquire() as conn:
        fila = await conn.fetchrow(
            "SELECT rnc_emisor, encf, xml FROM recepcion.ecf_recibidos WHERE id = $1", ecf_id
        )
    if fila is None:
        raise CompraNoEncontradaError(str(ecf_id))
    return f"{fila['rnc_emisor']}{fila['encf']}.xml", fila["xml"]


EnviarAprobacion = Callable[[str, bytes], Awaitable[dict[str, Any]]]
# (rnc_emisor, encf, ACECF firmado): entrega al proveedor; lanza si falla.
EntregarAlEmisor = Callable[[str, str, bytes], Awaitable[Any]]


async def _quitar_606_rechazado(conn: asyncpg.Connection, fila: asyncpg.Record, *, borrar: bool) -> None:
    """Un e-CF rechazado comercialmente no es una compra válida para el
    comprador: su renglón del 606 (el vinculado o el de su RNC + e-NCF) sale
    del 606 y con él su ITBIS adelantado del IT-1. Valida (409) que no tenga
    pagos vigentes y que el 606 / IT-1 (y, si tenía retenciones clasificadas
    a mano, el IT-1 / IR-17 del mes de pago) no estén presentados; con
    `borrar`, además lo borra. Dentro de una transacción. El rechazo no se
    puede deshacer (el ACECF ya está en la DGII), así que no hay que
    restaurarlo; si el proveedor reenvía el e-CF, receptor_recepcion no lo
    vuelve a registrar (`ecf_recibidos.ecf_rechazado`)."""
    registros = await conn.fetch(
        """
        SELECT * FROM compras606.registros
        WHERE id = $1 OR (rnc_cedula = $2 AND upper(ncf) = upper($3))
        FOR UPDATE
        """,
        fila["compra606_id"],
        fila["rnc_emisor"],
        fila["encf"],
    )
    for registro in registros:
        pagos = await conn.fetchval(
            "SELECT count(*) FROM compras.pagos WHERE registro606_id = $1 AND NOT anulado", registro["id"]
        )
        if pagos:
            raise ConflictoComprasError(
                f"El e-CF {fila['encf']} tiene pagos registrados: anúlelos antes de rechazarlo "
                "(sus retenciones están en el IT-1/IR-17)"
            )
        afectadas = {("606", registro["periodo"])}
        if registro["itbis_facturado"] or registro["itbis_costo"] or registro["itbis_proporcionalidad"]:
            afectadas.add(("it1", registro["periodo"]))
        afectadas |= compras_calculos.declaraciones_afectadas(
            CamposPago606.desde(registro), CamposPago606(fecha_pago=None, forma_pago=None)
        )
        for formulario, periodo in sorted(afectadas):
            await _exigir_no_presentada(conn, formulario, periodo)
        if borrar:
            await conn.execute("UPDATE recepcion.ecf_recibidos SET compra606_id = NULL WHERE compra606_id = $1", registro["id"])
            await conn.execute("DELETE FROM compras.pagos WHERE registro606_id = $1", registro["id"])
            await conn.execute("DELETE FROM compras.facturas WHERE registro606_id = $1", registro["id"])
            await conn.execute("DELETE FROM compras606.registros WHERE id = $1", registro["id"])


async def _tomar_envio(
    conn: asyncpg.Connection, ecf_id: uuid.UUID, *, aprobado: bool, motivo: str | None, rnc_comprador: str
) -> tuple[asyncpg.Record, bytes]:
    """Paso 1 de `decidir_aprobacion`, en una transacción corta: valida, arma
    el ACECF y marca el e-CF 'enviando'. Devuelve (fila, ACECF sin firmar)."""
    async with conn.transaction():
        fila = await conn.fetchrow(
            f"SELECT {_COLUMNAS_ECF_RECIBIDO} FROM recepcion.ecf_recibidos WHERE id = $1 FOR UPDATE", ecf_id
        )
        if fila is None:
            raise CompraNoEncontradaError(str(ecf_id))
        if fila["estado_aprobacion"] == "enviando" and fila["envio_en_curso"]:
            raise ConflictoComprasError(
                f"La {'aprobación' if fila['decision_en_envio'] == 'aprobado' else 'decisión'} comercial del "
                f"e-CF {fila['encf']} se está enviando a la DGII: espere el resultado"
            )
        if fila["estado_aprobacion"] not in ("pendiente", "enviando"):
            raise ConflictoComprasError(
                f"El e-CF {fila['encf']} ya fue {fila['estado_aprobacion']} comercialmente"
            )
        if fila["fecha_emision"] is None:
            raise DatosInvalidosError(
                f"El e-CF {fila['encf']} no tiene una FechaEmision válida y el ACECF la exige: "
                "no se puede aprobar ni rechazar por este medio"
            )
        if not aprobado:
            await _quitar_606_rechazado(conn, fila, borrar=False)  # falla antes de enviar nada
        xml = compras_calculos.construir_acecf(
            rnc_emisor=fila["rnc_emisor"],
            encf=fila["encf"],
            fecha_emision=fila["fecha_emision"],
            monto_total=fila["monto_total"],
            rnc_comprador=_rnc(fila["rnc_comprador"]) or _rnc(rnc_comprador),
            aprobado=aprobado,
            motivo_rechazo=motivo,
            fecha_hora=datetime.now(_ZONA_RD),
        )
        await conn.execute(
            """
            UPDATE recepcion.ecf_recibidos
            SET estado_aprobacion = 'enviando', decision_en_envio = $2, envio_iniciado_en = now(),
                motivo_rechazo = $3, actualizado_en = now()
            WHERE id = $1
            """,
            ecf_id,
            "aprobado" if aprobado else "rechazado",
            motivo,
        )
    return fila, xml


async def _devolver_a_pendiente(pool: asyncpg.Pool, ecf_id: uuid.UUID, error: str) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE recepcion.ecf_recibidos
            SET estado_aprobacion = 'pendiente', decision_en_envio = NULL, envio_iniciado_en = NULL,
                motivo_rechazo = NULL, error_aprobacion = $2, actualizado_en = now()
            WHERE id = $1 AND estado_aprobacion = 'enviando'
            """,
            ecf_id,
            error,
        )


async def decidir_aprobacion(
    pool: asyncpg.Pool,
    ecf_id: uuid.UUID,
    *,
    aprobado: bool,
    motivo: str | None,
    rnc_comprador: str,
    enviar: EnviarAprobacion,
    entregar: EntregarAlEmisor | None = None,
) -> dict[str, Any]:
    """Arma el ACECF (aprobación o rechazo), lo envía con `enviar(encf, xml)`
    -- `aprobacion_comercial`, que lo firma y lo manda a la DGII -- y guarda
    el resultado. Solo desde 'pendiente' (409 si ya se decidió o si se está
    enviando); 409 también si se rechaza un e-CF con pagos vigentes, y 422
    si no tiene FechaEmision (el ACECF la exige).

    Tres pasos para no tener la fila bloqueada ni una conexión ocupada
    durante la llamada HTTP (hasta 60 s; receptor_recepcion debe poder
    actualizar la fila de un reenvío sin esperar, y 5 aprobaciones lentas
    no deben agotar el pool):

    1. Transacción corta: valida y marca 'enviando' (dos clics simultáneos
       no mandan dos ACECF: el segundo ve 'enviando' y recibe 409). Si un
       envío se queda 'enviando' más de `_ENVIO_VENCIDO` (el proceso murió),
       se puede volver a intentar.
    2. Envía, sin conexión tomada.
    3. Guarda el resultado. Solo `codigo` "1" de la DGII es éxito
       (`compras_calculos.resultado_aprobacion_dgii`); si no, vuelve a
       'pendiente' con `error_aprobacion` y lanza AprobacionComercialError
       (502), o ConflictoComprasError (409) si la DGII dice que el e-CF no
       requiere aprobación comercial.

    Aceptado por la DGII, entrega el ACECF firmado al proveedor con
    `entregar` (best-effort: el resultado queda en `enviado_al_emisor` /
    `error_envio_emisor` y nunca hace fallar la aprobación).
    """
    motivo = (motivo or "").strip() or None
    if not aprobado and motivo is None:
        raise DatosInvalidosError("Indique el motivo del rechazo")
    async with pool.acquire() as conn:
        fila, xml = await _tomar_envio(conn, ecf_id, aprobado=aprobado, motivo=motivo, rnc_comprador=rnc_comprador)
    try:
        respuesta = dict(await enviar(fila["encf"], xml))
    except Exception as exc:
        await _devolver_a_pendiente(pool, ecf_id, str(exc))
        raise
    xml_firmado = respuesta.pop("xml_firmado", None)
    resultado, mensajes = compras_calculos.resultado_aprobacion_dgii(respuesta)
    if resultado != "aceptada":
        codigo = respuesta.get("codigo")
        if resultado == "no_requerida":
            error = f"La DGII indica que el e-CF {fila['encf']} no requiere aprobación comercial: {mensajes}"
        else:
            error = f"La DGII no aceptó la aprobación comercial del e-CF {fila['encf']} (código {codigo}): {mensajes}"
        await _devolver_a_pendiente(pool, ecf_id, error)
        if resultado == "no_requerida":
            raise ConflictoComprasError(error)
        raise AprobacionComercialError(error)
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE recepcion.ecf_recibidos
            SET estado_aprobacion = decision_en_envio, decision_en_envio = NULL, envio_iniciado_en = NULL,
                aprobado_en = now(), respuesta_aprobacion = $2::jsonb, acecf_xml = $3, error_aprobacion = NULL,
                actualizado_en = now()
            WHERE id = $1 AND estado_aprobacion = 'enviando'
            """,
            ecf_id,
            json.dumps(respuesta),
            xml_firmado,
        )
        if not aprobado:
            try:
                async with conn.transaction():
                    await _quitar_606_rechazado(conn, await _obtener_ecf(conn, ecf_id), borrar=True)
            except (ConflictoComprasError, ConflictoImpuestosError) as exc:
                # Cambió algo durante el envío (p. ej. se marcó el IT-1): el rechazo
                # ya está en la DGII; el renglón queda en el 606 para quitarlo a mano.
                logger.warning("El e-CF %s se rechazó, pero su renglón del 606 sigue: %s", fila["encf"], exc)
    if entregar is not None:
        if xml_firmado:
            try:
                await entregar(fila["rnc_emisor"], fila["encf"], xml_firmado.encode("utf-8"))
            except Exception as exc:
                logger.warning("No se pudo entregar el ACECF de %s al proveedor: %s", fila["encf"], exc)
                enviado, error_envio = False, str(exc) or exc.__class__.__name__
            else:
                enviado, error_envio = True, None
        else:
            enviado, error_envio = False, "aprobacion-comercial no devolvió el ACECF firmado"
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE recepcion.ecf_recibidos
                SET enviado_al_emisor = $2, error_envio_emisor = $3, actualizado_en = now()
                WHERE id = $1
                """,
                ecf_id,
                enviado,
                error_envio,
            )
    async with pool.acquire() as conn:
        return _detalle_ecf(await _obtener_ecf(conn, ecf_id))


async def _insertar_606_desde_ecf(conn: asyncpg.Connection, xml: str) -> uuid.UUID:
    datos = datos_606_desde_ecf(parsear_xml(xml))
    if datos is None:
        raise DatosInvalidosError("El e-CF no tiene una FechaEmision válida: regístrelo a mano en el 606")
    periodo = periodo_de_fecha(datos["fecha_comprobante"])
    await _exigir_no_presentada(conn, "606", periodo)
    if datos["itbis_facturado"]:
        await _exigir_no_presentada(conn, "it1", periodo)
    return await conn.fetchval(
        """
        INSERT INTO compras606.registros
            (periodo, origen, referencia_tipo, referencia_id, rnc_cedula, tipo_id,
             ncf, ncf_modificado, fecha_comprobante, monto_servicios, monto_bienes, itbis_facturado,
             impuesto_selectivo_consumo, otros_impuestos, monto_propina_legal)
        VALUES ($1, 'receptor_recepcion', 'ecf_recibido', NULL, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
        ON CONFLICT (rnc_cedula, ncf) DO UPDATE SET actualizado_en = compras606.registros.actualizado_en
        RETURNING id
        """,
        periodo,
        datos["rnc_cedula"],
        tipo_identificacion(datos["rnc_cedula"]),
        datos["ncf"],
        datos["ncf_modificado"],
        datos["fecha_comprobante"],
        datos["monto_servicios"],
        datos["monto_bienes"],
        datos["itbis_facturado"],
        datos["impuesto_selectivo_consumo"],
        datos["otros_impuestos"],
        datos["monto_propina_legal"],
    )


async def vincular_ecf_recibido(
    pool: asyncpg.Pool, ecf_id: uuid.UUID, campos: dict[str, Any]
) -> dict[str, Any]:
    """Vincula el e-CF recibido con su renglón del 606 y, opcionalmente, con
    una compra de inventario (recepción de mercancía).

    - `registro606_id` (opcional): un renglón existente del mismo proveedor
      y con el mismo NCF que el e-NCF del e-CF (422 si no: un renglón de otro
      NCF dejaría sin bandeja al e-CF que sí le corresponde). Si no se
      indica y el e-CF no tiene uno, se busca por (RNC, e-NCF) o se crea a
      partir del XML (mismos campos que guarda receptor_recepcion).
    - `compra_inventario_id`: inventario.compras.id; null lo desvincula.
      También queda en la factura de compra (`compras.facturas`).
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            fila = await _obtener_ecf(conn, ecf_id, bloquear=True)
            if fila["estado_aprobacion"] == "rechazado":
                raise ConflictoComprasError("El e-CF fue rechazado comercialmente: no se registra como compra")
            registro_id = campos.get("registro606_id") or fila["compra606_id"]
            if campos.get("registro606_id"):
                registro = await conn.fetchrow(
                    "SELECT rnc_cedula, ncf FROM compras606.registros WHERE id = $1", campos["registro606_id"]
                )
                if registro is None:
                    raise DatosInvalidosError("El renglón del 606 indicado no existe")
                if registro["rnc_cedula"] != fila["rnc_emisor"]:
                    raise DatosInvalidosError("El renglón del 606 indicado es de otro proveedor")
                if registro["ncf"].upper() != fila["encf"].upper():
                    raise DatosInvalidosError(
                        f"El renglón del 606 indicado tiene el NCF {registro['ncf']}, no el e-NCF {fila['encf']} "
                        "de este e-CF"
                    )
            elif registro_id is None:
                registro_id = await conn.fetchval(
                    "SELECT id FROM compras606.registros WHERE rnc_cedula = $1 AND ncf = $2",
                    fila["rnc_emisor"],
                    fila["encf"],
                ) or await _insertar_606_desde_ecf(conn, fila["xml"])
            compra_inventario_id = fila["compra_inventario_id"]
            if "compra_inventario_id" in campos:
                compra_inventario_id = campos["compra_inventario_id"]
                if compra_inventario_id is not None:
                    existe = await conn.fetchval(
                        "SELECT 1 FROM inventario.compras WHERE id = $1", compra_inventario_id
                    )
                    if existe is None:
                        raise DatosInvalidosError("La compra de inventario indicada no existe")
            try:
                await conn.execute(
                    """
                    UPDATE recepcion.ecf_recibidos
                    SET compra606_id = $2, compra_inventario_id = $3, actualizado_en = now()
                    WHERE id = $1
                    """,
                    ecf_id,
                    registro_id,
                    compra_inventario_id,
                )
            except asyncpg.UniqueViolationError as exc:
                raise ConflictoComprasError("Ese renglón del 606 ya está vinculado a otro e-CF recibido") from exc
            await conn.execute(
                """
                INSERT INTO compras.facturas (registro606_id, compra_inventario_id) VALUES ($1, $2)
                ON CONFLICT (registro606_id) DO UPDATE
                SET compra_inventario_id = EXCLUDED.compra_inventario_id, actualizado_en = now()
                """,
                registro_id,
                compra_inventario_id,
            )
            fila = await _obtener_ecf(conn, ecf_id)
    return _detalle_ecf(fila)


# --- Cuentas por pagar y facturas de compra ---------------------------------------------------------

async def listar_cuentas_por_pagar(
    pool: asyncpg.Pool,
    *,
    estado: str | None = None,
    proveedor_rnc: str | None = None,
    q: str | None = None,
    periodo: str | None = None,
) -> list[dict[str, Any]]:
    """`estado` None = lo que falta por pagar (pendiente + vencida);
    'todas' = también las pagadas. `periodo` = mes del comprobante (AAAAMM)."""
    valores: list[Any] = [hoy()]
    condiciones = []
    if estado is None:
        condiciones.append("estado <> 'pagada'")
    elif estado != "todas":
        valores.append(estado)
        condiciones.append(f"estado = ${len(valores)}")
    if proveedor_rnc:
        valores.append(_rnc(proveedor_rnc))
        condiciones.append(f"proveedor_rnc = ${len(valores)}")
    if q:
        valores.append(f"%{q}%")
        n = len(valores)
        condiciones.append(f"(ncf ILIKE ${n} OR proveedor_nombre ILIKE ${n} OR proveedor_rnc ILIKE ${n})")
    if periodo:
        valores.append(validar_periodo_mensual(periodo))
        condiciones.append(f"periodo = ${len(valores)}")
    where = f"WHERE {' AND '.join(condiciones)}" if condiciones else ""
    query = f"""
        {_SELECT_CXP_CON_ESTADO}
        {where}
        ORDER BY vence, fecha_comprobante, ncf
    """
    async with pool.acquire() as conn:
        filas = await conn.fetch(query, *valores)
    return [_fila_cxp(fila) for fila in filas]


async def _cuenta(conn: asyncpg.Connection, registro_id: uuid.UUID) -> dict[str, Any] | None:
    fila = await conn.fetchrow(f"{_SELECT_CXP_CON_ESTADO} WHERE registro606_id = $2", hoy(), registro_id)
    return _fila_cxp(fila) if fila is not None else None


async def _documento(conn: asyncpg.Connection, registro_id: uuid.UUID) -> dict[str, Any] | None:
    """Como `_cuenta`, pero también si es una nota de crédito del proveedor."""
    fila = await conn.fetchrow(f"{_SELECT_DOCS_CON_ESTADO} WHERE registro606_id = $2", hoy(), registro_id)
    return _fila_cxp(fila) if fila is not None else None


async def _factura_compra(conn: asyncpg.Connection, registro_id: uuid.UUID) -> dict[str, Any]:
    cuenta = await _documento(conn, registro_id)
    if cuenta is None:
        raise CompraNoEncontradaError(str(registro_id))
    cuenta["pagos"] = await _pagos(conn, registro606_id=registro_id)
    return cuenta


async def obtener_factura_compra(pool: asyncpg.Pool, registro_id: uuid.UUID) -> dict[str, Any]:
    async with pool.acquire() as conn:
        return await _factura_compra(conn, registro_id)


def _validar_montos(campos: dict[str, Any], nombres: tuple[str, ...]) -> None:
    for nombre in nombres:
        if campos.get(nombre) is not None and campos[nombre] < 0:
            raise DatosInvalidosError(f"{nombre} no puede ser negativo")


async def _validar_contacto_y_compra(conn: asyncpg.Connection, campos: dict[str, Any]) -> None:
    if campos.get("contacto_id") is not None:
        if await conn.fetchval("SELECT 1 FROM ventas.clientes WHERE id = $1", campos["contacto_id"]) is None:
            raise DatosInvalidosError("El contacto indicado no existe")
    if campos.get("compra_inventario_id") is not None:
        existe = await conn.fetchval("SELECT 1 FROM inventario.compras WHERE id = $1", campos["compra_inventario_id"])
        if existe is None:
            raise DatosInvalidosError("La compra de inventario indicada no existe")


_MONTOS_FACTURA = (
    "monto_servicios",
    "monto_bienes",
    "itbis_facturado",
    "impuesto_selectivo_consumo",
    "otros_impuestos",
    "monto_propina_legal",
)


async def crear_factura_compra(pool: asyncpg.Pool, **campos: Any) -> dict[str, Any]:
    """Registra una factura de compra de un proveedor con NCF/e-NCF propio
    (la de un proveedor informal se emite como E41 desde facturas_api): un
    renglón nuevo del 606 (origen 'compras') + sus datos de gestión. Las
    retenciones y la fecha de pago NO se ponen aquí: las pone el pago.

    También una **nota de crédito** del proveedor (B04/E34): exige
    `ncf_modificado` y descuenta su total del pendiente de la factura que
    modifica (si está registrada; si no, solo queda en el 606). Su forma de
    pago por defecto es 06 (notas de crédito). Se devuelve con
    `es_nota_credito` y `afecta_registro606_id`, sin pendiente propio.

    409 si el 606 o el IT-1 (ITBIS adelantado) del período del comprobante
    ya están marcados como presentados."""
    rnc = _rnc(campos["proveedor_rnc"])
    if not validar_rnc_cedula(rnc):
        raise DatosInvalidosError("RNC o cédula del proveedor inválido (9 u 11 dígitos)")
    ncf = campos["ncf"].strip().upper()
    if not validar_ncf(ncf):
        raise DatosInvalidosError("NCF inválido")
    if compras_calculos.tipo_ecf_de_ncf(ncf) in (41, 43, 47):
        raise DatosInvalidosError("Los comprobantes E41, E43 y E47 se emiten desde Facturas, no se registran aquí")
    es_nota = compras_calculos.es_nota_credito(ncf)
    ncf_modificado = (campos.get("ncf_modificado") or "").strip().upper() or None
    if ncf_modificado and not validar_ncf(ncf_modificado):
        raise DatosInvalidosError("NCF modificado inválido")
    if es_nota and not ncf_modificado:
        raise DatosInvalidosError("Indique el NCF de la factura que modifica la nota de crédito")
    if ncf_modificado == ncf:
        raise DatosInvalidosError("El NCF modificado no puede ser el mismo comprobante")
    fecha_comprobante = campos["fecha_comprobante"].strftime("%Y%m%d")
    if not validar_fecha(fecha_comprobante) or not validar_periodo(fecha_comprobante[:6]):
        raise DatosInvalidosError("Fecha del comprobante inválida (desde mayo 2018)")
    if campos["tipo_bienes_servicios"] not in TIPOS_BIENES_SERVICIOS:
        raise DatosInvalidosError("Tipo de bienes y servicios inválido (1-11)")
    forma_pago = campos.get("forma_pago") or (
        compras_calculos.FORMA_PAGO_NOTA_CREDITO if es_nota else compras_calculos.FORMA_PAGO_CREDITO
    )
    if forma_pago not in FORMAS_PAGO:
        raise DatosInvalidosError("Forma de pago inválida (1-7)")
    _validar_montos(campos, _MONTOS_FACTURA)
    if campos["monto_servicios"] + campos["monto_bienes"] <= 0:
        raise DatosInvalidosError("Indique el monto de bienes y/o servicios")
    if campos.get("vence") and campos["vence"] < campos["fecha_comprobante"]:
        raise DatosInvalidosError("El vencimiento no puede ser anterior a la fecha del comprobante")
    periodo = periodo_de_fecha(fecha_comprobante)
    async with pool.acquire() as conn:
        async with conn.transaction():
            await _validar_contacto_y_compra(conn, campos)
            await _exigir_no_presentada(conn, "606", periodo)
            if campos["itbis_facturado"]:
                await _exigir_no_presentada(conn, "it1", periodo)
            if es_nota:
                # Bloquea la factura que modifica: serializa con sus pagos, que
                # validan lo pendiente (del que la nota se descuenta).
                await conn.fetchval(
                    "SELECT id FROM compras606.registros WHERE rnc_cedula = $1 AND upper(ncf) = $2 FOR UPDATE",
                    rnc,
                    ncf_modificado,
                )
            try:
                registro_id = await conn.fetchval(
                    """
                    INSERT INTO compras606.registros
                        (periodo, origen, referencia_tipo, referencia_id, rnc_cedula, tipo_id,
                         tipo_bienes_servicios, ncf, ncf_modificado, fecha_comprobante,
                         monto_servicios, monto_bienes, itbis_facturado, impuesto_selectivo_consumo,
                         otros_impuestos, monto_propina_legal, forma_pago)
                    VALUES ($1, 'compras', 'factura_compra', NULL, $2, $3, $4, $5, $6, $7,
                            $8, $9, $10, $11, $12, $13, $14)
                    RETURNING id
                    """,
                    periodo,
                    rnc,
                    tipo_identificacion(rnc),
                    campos["tipo_bienes_servicios"],
                    ncf,
                    ncf_modificado,
                    fecha_comprobante,
                    *(_redondear(campos[c]) for c in _MONTOS_FACTURA),
                    forma_pago,
                )
            except asyncpg.UniqueViolationError as exc:
                raise ConflictoComprasError(f"El NCF {ncf} de ese proveedor ya está registrado en el 606") from exc
            await conn.execute(
                """
                INSERT INTO compras.facturas
                    (registro606_id, proveedor_nombre, contacto_id, compra_inventario_id, concepto, vence)
                VALUES ($1, $2, $3, $4, $5, $6)
                """,
                registro_id,
                campos.get("proveedor_nombre"),
                campos.get("contacto_id"),
                campos.get("compra_inventario_id"),
                campos.get("concepto"),
                campos.get("vence"),
            )
            return await _factura_compra(conn, registro_id)


_CAMPOS_GESTION = ("proveedor_nombre", "contacto_id", "compra_inventario_id", "concepto", "vence")


async def actualizar_factura_compra(
    pool: asyncpg.Pool, registro_id: uuid.UUID, campos: dict[str, Any]
) -> dict[str, Any]:
    """Actualiza solo los campos enviados: los de gestión (compras.facturas)
    y la clasificación del 606 `tipo_bienes_servicios` -- p. ej. al
    registrar como factura de compra un e-CF de la bandeja. Una vez
    clasificado no se puede volver a null (422), y cambiarlo exige que el
    606 de su período no esté presentado (409). Los montos y el pago no se
    editan aquí (montos: en el 606; pago: con los pagos)."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            registro = await conn.fetchrow(
                "SELECT fecha_comprobante, periodo, tipo_bienes_servicios FROM compras606.registros"
                " WHERE id = $1 FOR UPDATE",
                registro_id,
            )
            if registro is None:
                raise CompraNoEncontradaError(str(registro_id))
            await _validar_contacto_y_compra(conn, campos)
            if campos.get("vence") and campos["vence"].strftime("%Y%m%d") < registro["fecha_comprobante"]:
                raise DatosInvalidosError("El vencimiento no puede ser anterior a la fecha del comprobante")
            if "tipo_bienes_servicios" in campos:
                tipo = campos["tipo_bienes_servicios"]
                if tipo is None and registro["tipo_bienes_servicios"] is not None:
                    raise DatosInvalidosError(
                        "El comprobante ya está clasificado en el 606: indique otro tipo de bienes y servicios (1-11)"
                    )
                if tipo is not None and tipo not in TIPOS_BIENES_SERVICIOS:
                    raise DatosInvalidosError("Tipo de bienes y servicios inválido (1-11)")
                if tipo != registro["tipo_bienes_servicios"]:
                    await _exigir_no_presentada(conn, "606", registro["periodo"])
                    await conn.execute(
                        """
                        UPDATE compras606.registros SET tipo_bienes_servicios = $2, actualizado_en = now()
                        WHERE id = $1
                        """,
                        registro_id,
                        tipo,
                    )
            await conn.execute(
                "INSERT INTO compras.facturas (registro606_id) VALUES ($1) ON CONFLICT DO NOTHING", registro_id
            )
            asignaciones = []
            valores: list[Any] = [registro_id]
            for nombre in _CAMPOS_GESTION:
                if nombre in campos:
                    valores.append(campos[nombre])
                    asignaciones.append(f"{nombre} = ${len(valores)}")
            if asignaciones:
                await conn.execute(
                    f"UPDATE compras.facturas SET {', '.join(asignaciones)}, actualizado_en = now()"
                    " WHERE registro606_id = $1",
                    *valores,
                )
            factura = await _documento(conn, registro_id)
            if factura is None:
                raise ConflictoComprasError("Este comprobante no es una factura por pagar (E43 o e-CF rechazado)")
            factura["pagos"] = await _pagos(conn, registro606_id=registro_id)
    return factura


async def eliminar_factura_compra(pool: asyncpg.Pool, registro_id: uuid.UUID) -> None:
    """Solo las registradas desde Compras (origen 'compras') y sin pagos
    vigentes: las de un e-CF (recibido o autoemitido) se corrigen en su
    origen. Quitar el renglón cambia el 606 y el ITBIS adelantado del IT-1
    de su período (y, si tenía retenciones clasificadas a mano, el IT-1 /
    IR-17 del mes de pago): 409 si alguno está marcado como presentado."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            registro = await conn.fetchrow("SELECT * FROM compras606.registros WHERE id = $1 FOR UPDATE", registro_id)
            if registro is None:
                raise CompraNoEncontradaError(str(registro_id))
            if registro["origen"] != "compras":
                raise ConflictoComprasError(
                    "Solo se eliminan facturas registradas desde Compras; las que vienen de un e-CF se corrigen en su origen"
                )
            pagos = await conn.fetchval(
                "SELECT count(*) FROM compras.pagos WHERE registro606_id = $1 AND NOT anulado", registro_id
            )
            if pagos:
                raise ConflictoComprasError("La factura tiene pagos: anúlelos antes de eliminarla")
            afectadas = {("606", registro["periodo"])}
            if registro["itbis_facturado"] or registro["itbis_costo"] or registro["itbis_proporcionalidad"]:
                afectadas.add(("it1", registro["periodo"]))
            afectadas |= compras_calculos.declaraciones_afectadas(
                CamposPago606.desde(registro), CamposPago606(fecha_pago=None, forma_pago=None)
            )
            for formulario, periodo in sorted(afectadas):
                await _exigir_no_presentada(conn, formulario, periodo)
            await conn.execute("DELETE FROM compras.pagos WHERE registro606_id = $1", registro_id)
            await conn.execute("DELETE FROM compras.facturas WHERE registro606_id = $1", registro_id)
            await conn.execute("DELETE FROM compras606.registros WHERE id = $1", registro_id)


# --- Pagos a proveedores ---------------------------------------------------------

_SELECT_PAGOS = f"""
    SELECT pg.id, pg.registro606_id, r.ncf, r.rnc_cedula AS proveedor_rnc,
           {_NOMBRE_PROVEEDOR} AS proveedor_nombre,
           pg.fecha, pg.monto, pg.metodo, pg.cuenta_origen, pg.cuenta_bancaria_id, pg.referencia, pg.notas,
           pg.tipo_retencion_isr, pg.retencion_isr, pg.retencion_itbis,
           pg.anulado, pg.anulado_en, pg.creado_en
    FROM compras.pagos pg
    LEFT JOIN compras606.registros r ON r.id = pg.registro606_id
    {_JOINS_PROVEEDOR}
"""


def _fila_pago(fila: asyncpg.Record) -> dict[str, Any]:
    datos = dict(fila)
    datos["id"] = str(datos["id"])
    datos["registro606_id"] = str(datos["registro606_id"])
    datos["proveedor_nombre"] = datos["proveedor_nombre"] or ""
    datos["tipo_retencion_isr_label"] = compras_calculos.etiqueta_retencion_isr(datos["tipo_retencion_isr"])
    return datos


async def _pagos(
    conn: asyncpg.Connection,
    *,
    registro606_id: uuid.UUID | None = None,
    periodo: str | None = None,
    limite: int = 500,
    offset: int = 0,
) -> list[dict[str, Any]]:
    condiciones = []
    valores: list[Any] = []
    if registro606_id is not None:
        valores.append(registro606_id)
        condiciones.append(f"pg.registro606_id = ${len(valores)}")
    if periodo:
        valores.append(validar_periodo_mensual(periodo))
        condiciones.append(f"to_char(pg.fecha, 'YYYYMM') = ${len(valores)}")
    where = f"WHERE {' AND '.join(condiciones)}" if condiciones else ""
    valores.extend([limite, offset])
    filas = await conn.fetch(
        f"""
        {_SELECT_PAGOS}
        {where}
        ORDER BY pg.fecha DESC, pg.creado_en DESC
        LIMIT ${len(valores) - 1} OFFSET ${len(valores)}
        """,
        *valores,
    )
    return [_fila_pago(fila) for fila in filas]


async def listar_pagos(
    pool: asyncpg.Pool,
    *,
    registro606_id: uuid.UUID | None = None,
    periodo: str | None = None,
    limite: int = 200,
    offset: int = 0,
) -> list[dict[str, Any]]:
    async with pool.acquire() as conn:
        return await _pagos(conn, registro606_id=registro606_id, periodo=periodo, limite=limite, offset=offset)


async def _pagos_vigentes(conn: asyncpg.Connection, registro_id: uuid.UUID) -> list[Pago]:
    filas = await conn.fetch(
        """
        SELECT fecha, metodo, monto, retencion_isr, retencion_itbis, tipo_retencion_isr
        FROM compras.pagos WHERE registro606_id = $1 AND NOT anulado
        ORDER BY fecha, creado_en
        """,
        registro_id,
    )
    return [Pago(**dict(f)) for f in filas]


async def _base_606(conn: asyncpg.Connection, registro: asyncpg.Record, hay_pagos: bool) -> CamposPago606:
    """Valores de pago del 606 antes del primer pago. Se guardan (en
    compras.facturas) cuando se registra un pago sobre una factura sin pagos
    vigentes, y se restauran cuando se anulan todos."""
    if not hay_pagos:
        await conn.execute(
            """
            INSERT INTO compras.facturas
                (registro606_id, base_guardada, base_fecha_pago, base_forma_pago, base_itbis_retenido,
                 base_tipo_retencion_isr, base_monto_retencion_renta)
            VALUES ($1, true, $2, $3, $4, $5, $6)
            ON CONFLICT (registro606_id) DO UPDATE
            SET base_guardada = true, base_fecha_pago = EXCLUDED.base_fecha_pago,
                base_forma_pago = EXCLUDED.base_forma_pago, base_itbis_retenido = EXCLUDED.base_itbis_retenido,
                base_tipo_retencion_isr = EXCLUDED.base_tipo_retencion_isr,
                base_monto_retencion_renta = EXCLUDED.base_monto_retencion_renta, actualizado_en = now()
            """,
            registro["id"],
            registro["fecha_pago"],
            registro["forma_pago"],
            registro["itbis_retenido"],
            registro["tipo_retencion_isr"],
            registro["monto_retencion_renta"],
        )
        return CamposPago606.desde(registro)
    fila = await conn.fetchrow(
        "SELECT * FROM compras.facturas WHERE registro606_id = $1 AND base_guardada", registro["id"]
    )
    if fila is None:
        # Pagos de antes de guardar la base (no debería pasar): sin retenciones previas.
        return CamposPago606(fecha_pago=None, forma_pago=registro["forma_pago"])
    return CamposPago606.desde(fila, "base_")


async def _aplicar_606(
    conn: asyncpg.Connection, registro: asyncpg.Record, nuevo: CamposPago606
) -> None:
    """Escribe los campos de pago del renglón del 606. 409 si cambian y el
    606 de su período (fecha de pago, forma de pago, retenciones son
    casillas del formato) o el IT-1/IR-17 cuyo total cambia ya están
    marcados como presentados."""
    anterior = CamposPago606.desde(registro)
    if anterior == nuevo:
        return
    afectadas = {("606", registro["periodo"])} | compras_calculos.declaraciones_afectadas(anterior, nuevo)
    for formulario, periodo in sorted(afectadas):
        await _exigir_no_presentada(conn, formulario, periodo)
    await conn.execute(
        """
        UPDATE compras606.registros
        SET fecha_pago = $2, forma_pago = $3, itbis_retenido = $4, tipo_retencion_isr = $5,
            monto_retencion_renta = $6, actualizado_en = now()
        WHERE id = $1
        """,
        registro["id"],
        nuevo.fecha_pago,
        nuevo.forma_pago,
        nuevo.itbis_retenido,
        nuevo.tipo_retencion_isr,
        nuevo.monto_retencion_renta,
    )


async def _obtener_pago(conn: asyncpg.Connection, pago_id: uuid.UUID) -> dict[str, Any]:
    fila = await conn.fetchrow(f"{_SELECT_PAGOS} WHERE pg.id = $1", pago_id)
    if fila is None:
        raise CompraNoEncontradaError(str(pago_id))
    return _fila_pago(fila)


async def registrar_pago(
    pool: asyncpg.Pool,
    *,
    registro606_id: uuid.UUID,
    fecha: date | None,
    monto: Decimal,
    metodo: str,
    cuenta_origen: str | None,
    referencia: str | None,
    notas: str | None,
    tipo_retencion_isr: int | None,
    retencion_isr: Decimal,
    retencion_itbis: Decimal,
    cuenta_bancaria_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """Registra un pago a proveedor y actualiza su renglón del 606. Con
    `cuenta_bancaria_id`, el pago (lo que sale, neto de retenciones) es un
    movimiento de esa cuenta de Bancos.

    `monto` es lo que se paga (neto de retenciones). Las retenciones se
    aplican una sola vez por factura (en un solo pago); con ellas, la fecha
    de pago del 606 es la de ese pago y el IT-1/IR-17 de ese mes las
    incluyen. Valida que monto + retenciones no supere lo pendiente y que
    la fecha no sea futura (hora RD). Mientras quede saldo, la forma de
    pago del 606 sigue siendo 04 (compra a crédito).
    """
    monto = _redondear(monto)
    retencion_isr = _redondear(retencion_isr)
    retencion_itbis = _redondear(retencion_itbis)
    if monto < 0 or retencion_isr < 0 or retencion_itbis < 0:
        raise DatosInvalidosError("El monto y las retenciones no pueden ser negativos")
    if retencion_isr > 0 and tipo_retencion_isr is None:
        raise DatosInvalidosError("Indique el tipo de retención de ISR (1-9)")
    if tipo_retencion_isr is not None and tipo_retencion_isr not in TIPOS_RETENCION_ISR:
        raise DatosInvalidosError("Tipo de retención en ISR debe ser 1-9")
    if retencion_isr == 0:
        tipo_retencion_isr = None
    if monto + retencion_isr + retencion_itbis <= 0:
        raise DatosInvalidosError("El monto del pago debe ser mayor que cero")
    fecha = fecha or hoy()
    if fecha > hoy():
        raise DatosInvalidosError("La fecha de pago no puede ser futura: registre el pago cuando se haga")
    fecha_pago = fecha.strftime("%Y%m%d")
    if not validar_fecha(fecha_pago):
        raise DatosInvalidosError("Fecha de pago inválida")
    pago = Pago(
        fecha=fecha,
        metodo=metodo,
        monto=monto,
        retencion_isr=retencion_isr,
        retencion_itbis=retencion_itbis,
        tipo_retencion_isr=tipo_retencion_isr,
    )
    async with pool.acquire() as conn:
        async with conn.transaction():
            await validar_cuenta_bancaria(conn, cuenta_bancaria_id, fecha, documento="pago")
            registro = await conn.fetchrow(
                "SELECT * FROM compras606.registros WHERE id = $1 FOR UPDATE", registro606_id
            )
            if registro is None:
                raise CompraNoEncontradaError(str(registro606_id))
            cuenta = await _cuenta(conn, registro606_id)
            if cuenta is None:
                raise DatosInvalidosError(
                    "Este comprobante no es una factura por pagar (nota de crédito, gasto menor E43 o e-CF rechazado)"
                )
            rechazo_en_envio = await conn.fetchval(
                "SELECT 1 FROM recepcion.ecf_recibidos WHERE compra606_id = $1 AND estado_aprobacion = 'enviando'"
                " AND decision_en_envio = 'rechazado'",
                registro606_id,
            )
            if rechazo_en_envio:
                raise ConflictoComprasError("Se está enviando el rechazo comercial de este e-CF: no se puede pagar")
            if fecha_pago < registro["fecha_comprobante"]:
                raise DatosInvalidosError("La fecha de pago no puede ser anterior a la fecha del comprobante")
            if retencion_itbis > registro["itbis_facturado"]:
                raise DatosInvalidosError(
                    f"El ITBIS retenido ({retencion_itbis}) supera el ITBIS facturado ({registro['itbis_facturado']})"
                )
            if retencion_isr > cuenta["monto_facturado"]:
                raise DatosInvalidosError(
                    f"El ISR retenido ({retencion_isr}) supera el monto facturado ({cuenta['monto_facturado']})"
                )
            vigentes = await _pagos_vigentes(conn, registro606_id)
            if pago.tiene_retenciones and any(p.tiene_retenciones for p in vigentes):
                raise ConflictoComprasError(
                    "La factura ya tiene retenciones aplicadas en otro pago: anúlelo para cambiarlas"
                )
            base = await _base_606(conn, registro, hay_pagos=bool(vigentes))
            pagos = [*vigentes, pago]
            retenciones = compras_calculos.campos_pago_606(base, pagos).retenciones
            disponible = cuenta["total"] - cuenta["notas_credito"] - cuenta["pagado"]
            if disponible - CamposPago606.desde(registro).retenciones <= 0:
                raise DatosInvalidosError("La factura ya está pagada por completo")
            maximo = disponible - retenciones
            if monto > maximo:
                raise DatosInvalidosError(
                    f"El pago ({monto}) más las retenciones ({retenciones}) supera lo pendiente de la "
                    f"factura ({disponible}); el máximo a pagar es {max(maximo, _CERO)}"
                )
            nuevo = compras_calculos.campos_pago_606(
                base, pagos, saldada=monto >= maximo, con_notas_credito=cuenta["notas_credito"] > 0
            )
            await _aplicar_606(conn, registro, nuevo)
            pago_id = await conn.fetchval(
                """
                INSERT INTO compras.pagos
                    (registro606_id, fecha, monto, metodo, cuenta_origen, referencia, notas,
                     tipo_retencion_isr, retencion_isr, retencion_itbis, cuenta_bancaria_id)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                RETURNING id
                """,
                registro606_id,
                fecha,
                monto,
                metodo,
                cuenta_origen,
                referencia,
                notas,
                tipo_retencion_isr,
                retencion_isr,
                retencion_itbis,
                cuenta_bancaria_id,
            )
            return await _obtener_pago(conn, pago_id)


async def anular_pago(pool: asyncpg.Pool, pago_id: uuid.UUID) -> dict[str, Any]:
    """Anula el pago y recalcula el 606 con los pagos que quedan (sin pagos:
    vuelve a los valores de antes del primer pago). 409 si ya estaba
    anulado, si cambia un 606/IT-1/IR-17 ya marcado como presentado o si
    está conciliado en una conciliación bancaria cerrada (con la línea del
    extracto en el período abierto, se desconcilia: ver
    `repository.liberar_conciliacion`)."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            actual = await conn.fetchrow(
                "SELECT registro606_id, anulado, cuenta_bancaria_id FROM compras.pagos WHERE id = $1", pago_id
            )
            if actual is None:
                raise CompraNoEncontradaError(str(pago_id))
            # Primero el candado de la cuenta bancaria (mismo orden que Bancos).
            if actual["cuenta_bancaria_id"] is not None:
                await bloquear_cuenta_bancaria(conn, actual["cuenta_bancaria_id"])
            # Mismo orden de bloqueo que registrar_pago: primero el renglón del 606.
            registro = await conn.fetchrow(
                "SELECT * FROM compras606.registros WHERE id = $1 FOR UPDATE", actual["registro606_id"]
            )
            anulado = await conn.fetchval("SELECT anulado FROM compras.pagos WHERE id = $1 FOR UPDATE", pago_id)
            if anulado:
                raise ConflictoComprasError("El pago ya está anulado")
            motivo = await liberar_conciliacion(conn, "pago", pago_id, actual["cuenta_bancaria_id"])
            if motivo is not None:
                raise ConflictoComprasError(motivo)
            await conn.execute(
                "UPDATE compras.pagos SET anulado = true, anulado_en = now() WHERE id = $1", pago_id
            )
            if registro is not None:
                vigentes = await _pagos_vigentes(conn, registro["id"])
                base = await _base_606(conn, registro, hay_pagos=True)
                cuenta = await _cuenta(conn, registro["id"])
                saldada, con_notas = True, False
                if cuenta is not None:
                    retenciones = compras_calculos.campos_pago_606(base, vigentes).retenciones
                    saldada = cuenta["total"] - cuenta["notas_credito"] - cuenta["pagado"] - retenciones <= 0
                    con_notas = cuenta["notas_credito"] > 0
                await _aplicar_606(
                    conn,
                    registro,
                    compras_calculos.campos_pago_606(base, vigentes, saldada=saldada, con_notas_credito=con_notas),
                )
            return await _obtener_pago(conn, pago_id)


# --- Comprobantes de compras E41 y gastos menores E43 (facturacion.facturas) ---------------------------------------------------------

async def listar_comprobantes_compra(
    pool: asyncpg.Pool,
    *,
    tipo_ecf: int | None = None,
    estado: str | None = None,
    q: str | None = None,
    periodo: str | None = None,
    limite: int = 200,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """E41 (Compras, al proveedor informal: va en `Comprador`) y E43 (Gastos
    menores, sin contraparte) emitidos por facturas_api, con el concepto (la
    primera línea), sus retenciones según el e-CF y su renglón del 606."""
    if tipo_ecf is not None and tipo_ecf not in (41, 43):
        raise DatosInvalidosError("tipo_ecf debe ser 41 o 43")
    valores: list[Any] = []
    condiciones = ["f.tipo_ecf IN (41, 43)"]
    if tipo_ecf is not None:
        valores.append(tipo_ecf)
        condiciones.append(f"f.tipo_ecf = ${len(valores)}")
    if estado is not None:
        valores.append(estado)
        condiciones.append(f"f.estado = ${len(valores)}")
    if q:
        valores.append(f"%{q}%")
        n = len(valores)
        condiciones.append(
            f"(f.encf ILIKE ${n} OR d #>> '{{Encabezado,Comprador,RazonSocialComprador}}' ILIKE ${n}"
            f" OR d #>> '{{Encabezado,Comprador,RNCComprador}}' ILIKE ${n}"
            f" OR d #>> '{{DetallesItems,Item,0,NombreItem}}' ILIKE ${n})"
        )
    if periodo:
        valores.append(validar_periodo_mensual(periodo))
        condiciones.append(
            f"to_char(COALESCE(ventas.fecha_dgii_segura(d #>> '{{Encabezado,Emisor,FechaEmision}}'),"
            f" f.creado_en::date), 'YYYYMM') = ${len(valores)}"
        )
    valores.extend([limite, offset])
    total = _monto_dgii("{Encabezado,Totales,MontoTotal}")
    itbis = _monto_dgii("{Encabezado,Totales,TotalITBIS}")
    query = f"""
        SELECT
            f.id, f.tipo_ecf, f.encf, f.estado, f.dgii_codigo, f.error, f.creado_en,
            CASE WHEN f.tipo_ecf = 41 THEN NULLIF(regexp_replace(
                COALESCE(d #>> '{{Encabezado,Comprador,RNCComprador}}', ''), '[^0-9A-Za-z]', '', 'g'), '')
            END AS proveedor_rnc,
            CASE WHEN f.tipo_ecf = 41 THEN NULLIF(d #>> '{{Encabezado,Comprador,RazonSocialComprador}}', '') END
                AS proveedor_nombre,
            COALESCE(NULLIF(d #>> '{{DetallesItems,Item,0,NombreItem}}', ''),
                     NULLIF(d #>> '{{DetallesItems,Item,NombreItem}}', '')) AS concepto,
            CASE WHEN jsonb_typeof(d #> '{{DetallesItems,Item}}') = 'array'
                 THEN jsonb_array_length(d #> '{{DetallesItems,Item}}') ELSE 1 END AS lineas,
            ventas.fecha_dgii_segura(d #>> '{{Encabezado,Emisor,FechaEmision}}') AS fecha_emision,
            ({total} - {itbis})::numeric(14, 2) AS monto,
            {itbis} AS itbis,
            {total} AS total,
            {_monto_dgii("{Encabezado,Totales,TotalITBISRetenido}")} AS itbis_retenido,
            {_monto_dgii("{Encabezado,Totales,TotalISRRetencion}")} AS isr_retenido,
            r.id AS registro606_id,
            {_fecha_606("r.fecha_pago")} AS fecha_pago_606,
            r.itbis_retenido AS itbis_retenido_606,
            r.monto_retencion_renta AS isr_retenido_606
        FROM facturacion.facturas f
        CROSS JOIN LATERAL (SELECT f.datos::jsonb AS d) x
        LEFT JOIN compras606.registros r ON r.referencia_tipo = 'factura' AND r.referencia_id = f.id::text
        WHERE {' AND '.join(condiciones)}
        ORDER BY f.creado_en DESC
        LIMIT ${len(valores) - 1} OFFSET ${len(valores)}
    """
    async with pool.acquire() as conn:
        filas = await conn.fetch(query, *valores)
    resultado = []
    for fila in filas:
        datos = dict(fila)
        datos["id"] = str(datos["id"])
        if datos["registro606_id"] is not None:
            datos["registro606_id"] = str(datos["registro606_id"])
        resultado.append(datos)
    return resultado


# --- Retenciones del mes y certificado ---------------------------------------------------------

_SELECT_RETENCIONES = f"""
    SELECT r.id AS registro606_id, r.rnc_cedula AS proveedor_rnc, {_NOMBRE_PROVEEDOR} AS proveedor_nombre,
           COALESCE(clx.correo, cl.correo) AS proveedor_correo,
           COALESCE(clx.telefono, cl.telefono) AS proveedor_telefono,
           r.ncf, {_fecha_606("r.fecha_comprobante")} AS fecha_comprobante,
           {_fecha_606("r.fecha_pago")} AS fecha_pago,
           (r.monto_servicios + r.monto_bienes)::numeric(14, 2) AS monto_facturado,
           r.itbis_facturado, r.tipo_retencion_isr,
           r.monto_retencion_renta AS isr_retenido, r.itbis_retenido
    FROM compras606.registros r
    {_JOINS_PROVEEDOR}
    WHERE r.fecha_pago LIKE $1 || '%' AND (r.itbis_retenido > 0 OR r.monto_retencion_renta > 0)
"""


async def _filas_retencion(conn: asyncpg.Connection, periodo: str, rnc: str | None = None) -> list[dict[str, Any]]:
    valores: list[Any] = [periodo]
    filtro = ""
    if rnc is not None:
        valores.append(rnc)
        filtro = "AND r.rnc_cedula = $2"
    filas = await conn.fetch(
        f"{_SELECT_RETENCIONES} {filtro} ORDER BY 3, r.fecha_pago, r.ncf", *valores
    )
    resultado = []
    for fila in filas:
        datos = dict(fila)
        datos["registro606_id"] = str(datos["registro606_id"])
        datos["tipo_retencion_isr_label"] = compras_calculos.etiqueta_retencion_isr(datos["tipo_retencion_isr"])
        resultado.append(datos)
    return resultado


async def retenciones_del_mes(pool: asyncpg.Pool, periodo: str) -> dict[str, Any]:
    """Retenciones efectuadas a proveedores en el mes (por fecha de pago del
    606, igual que el IT-1 y el IR-17), por comprobante y por proveedor."""
    validar_periodo_mensual(periodo)
    async with pool.acquire() as conn:
        filas = await _filas_retencion(conn, periodo)
        presentadas = {
            f["formulario"]
            for f in await conn.fetch(
                "SELECT formulario FROM impuestos.declaraciones_presentadas"
                " WHERE periodo = $1 AND formulario IN ('it1', 'ir17')",
                periodo,
            )
        }
    proveedores: dict[str, dict[str, Any]] = {}
    for fila in filas:
        declaraciones = [f for f, monto in (("ir17", fila["isr_retenido"]), ("it1", fila["itbis_retenido"])) if monto > 0]
        fila["declaraciones"] = declaraciones
        fila["estado"] = "declarada" if all(d in presentadas for d in declaraciones) else "por_declarar"
        proveedor = proveedores.setdefault(
            fila["proveedor_rnc"],
            {
                "proveedor_rnc": fila["proveedor_rnc"],
                "proveedor_nombre": fila["proveedor_nombre"],
                "proveedor_correo": fila.pop("proveedor_correo"),
                "proveedor_telefono": fila.pop("proveedor_telefono"),
                "comprobantes": 0,
                "isr_retenido": _CERO,
                "itbis_retenido": _CERO,
            },
        )
        fila.pop("proveedor_correo", None)
        fila.pop("proveedor_telefono", None)
        proveedor["comprobantes"] += 1
        proveedor["isr_retenido"] += fila["isr_retenido"]
        proveedor["itbis_retenido"] += fila["itbis_retenido"]
    return {
        "periodo": periodo,
        "filas": filas,
        "proveedores": sorted(proveedores.values(), key=lambda p: p["proveedor_nombre"]),
        "totales": {
            "isr_retenido": sum((f["isr_retenido"] for f in filas), _CERO),
            "itbis_retenido": sum((f["itbis_retenido"] for f in filas), _CERO),
        },
        "it1_presentado": "it1" in presentadas,
        "ir17_presentado": "ir17" in presentadas,
    }


async def certificado_retencion(
    pool: asyncpg.Pool, periodo: str, rnc: str, *, agente_rnc: str, agente_nombre: str | None
) -> dict[str, Any]:
    """Datos del certificado de retención de un proveedor en un mes (para
    imprimir/enviar). 404 si no se le retuvo nada en ese mes."""
    validar_periodo_mensual(periodo)
    rnc = _rnc(rnc)
    async with pool.acquire() as conn:
        filas = await _filas_retencion(conn, periodo, rnc)
    if not filas:
        raise CompraNoEncontradaError(f"{rnc}/{periodo}")
    comprobantes = [
        {k: v for k, v in f.items() if k not in ("proveedor_rnc", "proveedor_nombre", "proveedor_correo", "proveedor_telefono")}
        for f in filas
    ]
    isr = sum((f["isr_retenido"] for f in filas), _CERO)
    itbis = sum((f["itbis_retenido"] for f in filas), _CERO)
    return {
        "periodo": periodo,
        "periodo_nombre": nombre_periodo(periodo),
        "fecha_emision": hoy(),
        "agente": {"rnc": agente_rnc, "nombre": agente_nombre or None},
        "proveedor": {
            "rnc": rnc,
            "tipo_identificacion": "cedula" if len(rnc) == 11 else "rnc",
            "nombre": filas[0]["proveedor_nombre"],
            "correo": filas[0]["proveedor_correo"],
        },
        "comprobantes": comprobantes,
        "totales": {
            "monto_facturado": sum((f["monto_facturado"] for f in filas), _CERO),
            "itbis_facturado": sum((f["itbis_facturado"] for f in filas), _CERO),
            "isr_retenido": isr,
            "itbis_retenido": itbis,
            "total_retenido": isr + itbis,
        },
    }
