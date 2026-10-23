"""Acceso a Postgres para el esquema `ventas` (clientes, cotizaciones,
cobros) y lectura de `facturacion.facturas` para las cuentas por cobrar, el
saldo de cada cliente y el resumen de documentos de venta.

`facturacion.facturas` es de `facturas_api`: aquí SOLO se lee, nunca se
escribe. Su columna `datos` es `json` (no jsonb, ver
db/init/003_datos_column_json.sql), por eso las consultas la convierten con
`datos::jsonb` antes de navegarla. Las fechas del e-CF vienen como
DD-MM-YYYY y se convierten a `date`.

Igual que `services/inventario_api/app/repository.py`, este repositorio SÍ
requiere una base de datos disponible.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import asyncpg

# República Dominicana: UTC-4 todo el año (sin horario de verano). "Hoy"
# se calcula en hora local, no en la del servidor de base de datos (UTC en
# Supabase) -- si no, de 8 p. m. a medianoche una cuenta vencería un día antes.
_ZONA_RD = timezone(timedelta(hours=-4), "America/Santo_Domingo")

_CENTAVO = Decimal("0.01")
_TASAS_ITBIS = {"18": Decimal("0.18"), "16": Decimal("0.16"), "0": Decimal("0"), "exento": Decimal("0")}
_DIAS_VENCE_COTIZACION = 15
_DIAS_CREDITO_POR_DEFECTO = 30


class ClienteNoEncontradoError(Exception):
    pass


class ClienteDuplicadoError(Exception):
    """Ya existe un contacto con ese RNC/cédula."""


class ClienteConCotizacionesError(Exception):
    """El cliente está referenciado por cotizaciones y no se puede borrar."""


class CotizacionNoEncontradaError(Exception):
    pass


class CotizacionEstadoInvalidoError(Exception):
    """La cotización no está en un estado que admita la operación pedida."""


class CobroNoEncontradoError(Exception):
    pass


class CobroEstadoInvalidoError(Exception):
    """El cobro ya está anulado."""


class DatosInvalidosError(Exception):
    """Datos que pasan la validación de forma (pydantic) pero no las reglas de
    negocio -- main.py lo traduce a 422 con el mensaje tal cual."""


def hoy() -> date:
    return datetime.now(_ZONA_RD).date()


def _redondear(valor: Decimal) -> Decimal:
    return valor.quantize(_CENTAVO, rounding=ROUND_HALF_UP)


def _con_id_str(fila: asyncpg.Record) -> dict[str, Any]:
    datos = dict(fila)
    datos["id"] = str(datos["id"])
    return datos


# --- Expresiones SQL sobre facturacion.facturas ---------------------------------------------------------

def _fecha_dgii(ruta: str) -> str:
    """Fecha DD-MM-YYYY del e-CF -> date (NULL si falta, no tiene ese formato
    o no existe, p. ej. 31-02-2026). `ventas.fecha_dgii_segura` la crea
    `app.migraciones` (DDL_FUNCIONES): un `to_date` directo lanzaría un error
    con una fecha imposible y haría fallar la consulta entera."""
    return f"ventas.fecha_dgii_segura(d #>> '{ruta}')"


def _monto_dgii(ruta: str) -> str:
    """Monto del e-CF -> numeric(14, 2) (0 si falta o no es numérico)."""
    return (
        f"CASE WHEN d #>> '{ruta}' ~ '^-?[0-9]+([.][0-9]+)?$' "
        f"THEN round((d #>> '{ruta}')::numeric, 2) ELSE 0 END::numeric(14, 2)"
    )


# Expresiones sobre `d` (= datos::jsonb de la factura). El RNC del comprador
# se normaliza igual que `ventas.clientes.rnc` para poder cruzarlos.
_RNC_COMPRADOR = (
    "NULLIF(upper(regexp_replace(COALESCE(d #>> '{Encabezado,Comprador,RNCComprador}', ''),"
    " '[^0-9A-Za-z]', '', 'g')), '')"
)
_NOMBRE_COMPRADOR = "NULLIF(d #>> '{Encabezado,Comprador,RazonSocialComprador}', '')"
_FECHA_EMISION = _fecha_dgii("{Encabezado,Emisor,FechaEmision}")

# Tipos de e-CF de venta que generan una cuenta por cobrar. Quedan fuera las
# notas de crédito (34, rebajan la factura que modifican) y los comprobantes
# de compra/pago (41, 43, 47), donde el "comprador" es el proveedor.
_TIPOS_CXC = "(31, 32, 33, 44, 45, 46)"

# Cuentas por cobrar: facturas enviadas/aceptadas a crédito (TipoPago 2) con
# lo cobrado (cobros no anulados) y lo pendiente, que además descuenta las
# notas de crédito enviadas/aceptadas que referencian la factura. Sin
# parámetros: el estado (vencida/pendiente/cobrada) depende de "hoy" y se
# calcula en cada consulta que usa este CTE.
_CTE_CXC = f"""
    cxc_base AS (
        SELECT f.id, f.encf, f.tipo_ecf, f.creado_en, f.datos::jsonb AS d
        FROM facturacion.facturas f
        WHERE f.estado IN ('aceptado', 'enviado')
          AND f.tipo_ecf IN {_TIPOS_CXC}
          AND f.datos::jsonb #>> '{{Encabezado,IdDoc,TipoPago}}' = '2'
    ),
    cxc_notas AS (
        SELECT d #>> '{{InformacionReferencia,NCFModificado}}' AS encf_afectado,
               sum({_monto_dgii("{Encabezado,Totales,MontoTotal}")}) AS acreditado
        FROM (
            SELECT f.datos::jsonb AS d
            FROM facturacion.facturas f
            WHERE f.estado IN ('aceptado', 'enviado') AND f.tipo_ecf = 34
        ) n
        GROUP BY 1
    ),
    cxc_cobrado AS (
        SELECT factura_id, sum(monto) AS cobrado
        FROM ventas.cobros
        WHERE NOT anulado
        GROUP BY factura_id
    ),
    cxc AS (
        SELECT
            b.id AS factura_id,
            b.encf,
            b.tipo_ecf,
            {_RNC_COMPRADOR} AS cliente_rnc,
            COALESCE({_NOMBRE_COMPRADOR}, '') AS cliente_nombre,
            e.fecha_emision,
            COALESCE(
                {_fecha_dgii("{Encabezado,IdDoc,FechaLimitePago}")},
                e.fecha_emision + {_DIAS_CREDITO_POR_DEFECTO}
            ) AS vence,
            t.total,
            COALESCE(c.cobrado, 0)::numeric(14, 2) AS cobrado,
            (t.total - COALESCE(nc.acreditado, 0) - COALESCE(c.cobrado, 0))::numeric(14, 2) AS pendiente,
            cl.telefono AS cliente_telefono
        FROM cxc_base b
        CROSS JOIN LATERAL (
            SELECT COALESCE({_FECHA_EMISION}, b.creado_en::date) AS fecha_emision
        ) e
        CROSS JOIN LATERAL (
            SELECT {_monto_dgii("{Encabezado,Totales,MontoTotal}")} AS total
        ) t
        LEFT JOIN cxc_cobrado c ON c.factura_id = b.id
        LEFT JOIN cxc_notas nc ON nc.encf_afectado = b.encf
        LEFT JOIN ventas.clientes cl ON cl.rnc = {_RNC_COMPRADOR}
    )
"""

# Estado calculado de cada cuenta por cobrar; $1 = hoy.
_SELECT_CXC_CON_ESTADO = f"""
    WITH {_CTE_CXC}
    SELECT * FROM (
        SELECT
            cxc.*,
            CASE
                WHEN pendiente <= 0 THEN 'cobrada'
                WHEN vence < $1::date THEN 'vencida'
                ELSE 'pendiente'
            END AS estado,
            CASE WHEN pendiente > 0 AND vence < $1::date THEN $1::date - vence ELSE 0 END AS dias_vencida
        FROM cxc
    ) cuentas
"""


# --- Clientes ---------------------------------------------------------

def normalizar_identificacion(
    tipo_identificacion: str | None, rnc: str | None
) -> tuple[str | None, str | None]:
    """Normaliza el RNC/cédula a solo dígitos y valida su largo.

    Pasaporte: se conservan letras y dígitos (en mayúsculas). Sin
    `tipo_identificacion`, se deduce del largo (9 = RNC, 11 = cédula).
    """
    if tipo_identificacion == "pasaporte":
        valor = re.sub(r"[^0-9A-Za-z]", "", rnc or "").upper() or None
        return tipo_identificacion, valor
    valor = re.sub(r"\D", "", rnc or "") or None
    if valor is None:
        return tipo_identificacion, None
    if tipo_identificacion == "rnc" and len(valor) != 9:
        raise DatosInvalidosError(f"El RNC debe tener 9 dígitos (tiene {len(valor)})")
    if tipo_identificacion == "cedula" and len(valor) != 11:
        raise DatosInvalidosError(f"La cédula debe tener 11 dígitos (tiene {len(valor)})")
    if tipo_identificacion is None:
        if len(valor) == 9:
            tipo_identificacion = "rnc"
        elif len(valor) == 11:
            tipo_identificacion = "cedula"
        else:
            raise DatosInvalidosError(
                f"El RNC/cédula debe tener 9 u 11 dígitos (tiene {len(valor)})"
            )
    return tipo_identificacion, valor


_SELECT_CLIENTES = f"""
    WITH {_CTE_CXC},
    saldos AS (
        SELECT cliente_rnc, sum(pendiente) AS saldo
        FROM cxc
        WHERE pendiente > 0 AND cliente_rnc IS NOT NULL
        GROUP BY cliente_rnc
    )
    SELECT c.*, COALESCE(s.saldo, 0)::numeric(14, 2) AS saldo
    FROM ventas.clientes c
    LEFT JOIN saldos s ON s.cliente_rnc = c.rnc
"""


async def _obtener_cliente(conn: asyncpg.Connection, cliente_id: uuid.UUID) -> dict[str, Any] | None:
    fila = await conn.fetchrow(f"{_SELECT_CLIENTES} WHERE c.id = $1", cliente_id)
    return _con_id_str(fila) if fila is not None else None


def _mensaje_duplicado(rnc: str | None) -> str:
    return f"Ya existe un contacto con el RNC/cédula {rnc}"


async def crear_cliente(pool: asyncpg.Pool, **campos: Any) -> dict[str, Any]:
    tipo_identificacion, rnc = normalizar_identificacion(campos["tipo_identificacion"], campos["rnc"])
    async with pool.acquire() as conn:
        try:
            cliente_id = await conn.fetchval(
                """
                INSERT INTO ventas.clientes
                    (tipo, tipo_identificacion, rnc, nombre, correo, telefono, direccion,
                     condicion_pago_dias, limite_credito, notas, activo)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                RETURNING id
                """,
                campos["tipo"],
                tipo_identificacion,
                rnc,
                campos["nombre"],
                campos["correo"],
                campos["telefono"],
                campos["direccion"],
                campos["condicion_pago_dias"],
                campos["limite_credito"],
                campos["notas"],
                campos["activo"],
            )
        except asyncpg.UniqueViolationError as exc:
            raise ClienteDuplicadoError(_mensaje_duplicado(rnc)) from exc
        cliente = await _obtener_cliente(conn, cliente_id)
    assert cliente is not None
    return cliente


async def listar_clientes(
    pool: asyncpg.Pool,
    *,
    q: str | None = None,
    tipo: str | None = None,
    activo: bool | None = None,
    limite: int = 200,
    offset: int = 0,
) -> list[dict[str, Any]]:
    condiciones = []
    valores: list[Any] = []
    if q:
        valores.append(f"%{q}%")
        condiciones.append(f"(c.nombre ILIKE ${len(valores)} OR c.rnc ILIKE ${len(valores)})")
    if tipo in ("cliente", "proveedor"):
        # Un contacto 'ambos' es cliente y proveedor a la vez.
        valores.append(tipo)
        condiciones.append(f"c.tipo IN (${len(valores)}, 'ambos')")
    elif tipo is not None:
        valores.append(tipo)
        condiciones.append(f"c.tipo = ${len(valores)}")
    if activo is not None:
        valores.append(activo)
        condiciones.append(f"c.activo = ${len(valores)}")
    where = f"WHERE {' AND '.join(condiciones)}" if condiciones else ""
    valores.extend([limite, offset])
    query = f"""
        {_SELECT_CLIENTES}
        {where}
        ORDER BY c.nombre
        LIMIT ${len(valores) - 1} OFFSET ${len(valores)}
    """
    async with pool.acquire() as conn:
        filas = await conn.fetch(query, *valores)
    return [_con_id_str(fila) for fila in filas]


async def obtener_cliente(pool: asyncpg.Pool, cliente_id: uuid.UUID) -> dict[str, Any]:
    async with pool.acquire() as conn:
        cliente = await _obtener_cliente(conn, cliente_id)
    if cliente is None:
        raise ClienteNoEncontradoError(str(cliente_id))
    return cliente


async def obtener_cliente_por_rnc(pool: asyncpg.Pool, rnc: str) -> dict[str, Any]:
    valor = re.sub(r"[^0-9A-Za-z]", "", rnc).upper()
    if not valor:
        raise ClienteNoEncontradoError(rnc)
    async with pool.acquire() as conn:
        fila = await conn.fetchrow(f"{_SELECT_CLIENTES} WHERE c.rnc = $1", valor)
    if fila is None:
        raise ClienteNoEncontradoError(rnc)
    return _con_id_str(fila)


async def actualizar_cliente(pool: asyncpg.Pool, cliente_id: uuid.UUID, **campos: Any) -> dict[str, Any]:
    tipo_identificacion, rnc = normalizar_identificacion(campos["tipo_identificacion"], campos["rnc"])
    async with pool.acquire() as conn:
        try:
            resultado = await conn.execute(
                """
                UPDATE ventas.clientes
                SET tipo = $2, tipo_identificacion = $3, rnc = $4, nombre = $5, correo = $6,
                    telefono = $7, direccion = $8, condicion_pago_dias = $9,
                    limite_credito = $10, notas = $11, activo = $12, actualizado_en = now()
                WHERE id = $1
                """,
                cliente_id,
                campos["tipo"],
                tipo_identificacion,
                rnc,
                campos["nombre"],
                campos["correo"],
                campos["telefono"],
                campos["direccion"],
                campos["condicion_pago_dias"],
                campos["limite_credito"],
                campos["notas"],
                campos["activo"],
            )
        except asyncpg.UniqueViolationError as exc:
            raise ClienteDuplicadoError(_mensaje_duplicado(rnc)) from exc
        if resultado == "UPDATE 0":
            raise ClienteNoEncontradoError(str(cliente_id))
        cliente = await _obtener_cliente(conn, cliente_id)
    assert cliente is not None
    return cliente


async def eliminar_cliente(pool: asyncpg.Pool, cliente_id: uuid.UUID) -> None:
    async with pool.acquire() as conn:
        try:
            resultado = await conn.execute("DELETE FROM ventas.clientes WHERE id = $1", cliente_id)
        except asyncpg.ForeignKeyViolationError as exc:
            raise ClienteConCotizacionesError(str(cliente_id)) from exc
    if resultado == "DELETE 0":
        raise ClienteNoEncontradoError(str(cliente_id))


# --- Cotizaciones ---------------------------------------------------------

def calcular_cotizacion(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Decimal]]:
    """Calcula el importe de cada línea y los totales de la cotización.

    importe = cantidad * precio * (1 - descuento_pct / 100), redondeado a 2
    decimales. El ITBIS se calcula como en los Totales del e-CF: sobre la
    suma de los importes gravados de cada tasa (18 % y 16 %); tasa 0 y
    exento no suman ITBIS. Devuelve (líneas listas para guardar en jsonb,
    totales).
    """
    lineas: list[dict[str, Any]] = []
    bruto_total = Decimal("0")
    importes_total = Decimal("0")
    gravado: dict[str, Decimal] = {"18": Decimal("0"), "16": Decimal("0")}
    for item in items:
        cantidad = Decimal(item["cantidad"])
        precio = Decimal(item["precio_unitario"])
        descuento_pct = Decimal(item.get("descuento_pct") or 0)
        bruto = cantidad * precio
        importe = _redondear(bruto * (1 - descuento_pct / 100))
        bruto_total += bruto
        importes_total += importe
        if item["itbis"] in gravado:
            gravado[item["itbis"]] += importe
        producto_id = item.get("producto_id")
        lineas.append(
            {
                "producto_id": str(producto_id) if producto_id is not None else None,
                "descripcion": item["descripcion"],
                "cantidad": str(cantidad),
                "precio_unitario": str(precio),
                "descuento_pct": str(descuento_pct),
                "itbis": item["itbis"],
                "importe": str(importe),
            }
        )
    subtotal = _redondear(bruto_total)
    itbis_total = sum(
        (_redondear(monto * _TASAS_ITBIS[tasa]) for tasa, monto in gravado.items()), Decimal("0.00")
    )
    totales = {
        "subtotal": subtotal,
        "descuentos": subtotal - importes_total,
        "itbis_total": itbis_total,
        "total": importes_total + itbis_total,
    }
    return lineas, totales


# Columnas de una cotización con el estado visible ('vencida' calculada);
# $1 = hoy.
_COLUMNAS_COTIZACION = """
    id, numero, cliente_id, cliente_nombre, cliente_rnc, fecha, vence, items, notas,
    subtotal, descuentos, itbis_total, total,
    CASE WHEN estado = 'abierta' AND vence < $1::date THEN 'vencida' ELSE estado END AS estado,
    factura_id, creado_en, actualizado_en
"""


def _fila_cotizacion(fila: asyncpg.Record) -> dict[str, Any]:
    datos = _con_id_str(fila)
    if isinstance(datos.get("items"), str):
        datos["items"] = json.loads(datos["items"])
    return datos


async def _obtener_cotizacion(conn: asyncpg.Connection, cotizacion_id: uuid.UUID) -> dict[str, Any] | None:
    fila = await conn.fetchrow(
        f"SELECT {_COLUMNAS_COTIZACION} FROM ventas.cotizaciones WHERE id = $2", hoy(), cotizacion_id
    )
    return _fila_cotizacion(fila) if fila is not None else None


async def _datos_cotizacion(
    conn: asyncpg.Connection,
    *,
    cliente_id: uuid.UUID | None,
    cliente_nombre: str | None,
    cliente_rnc: str | None,
    fecha: date | None,
    vence: date | None,
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    """Resuelve el cliente (copia nombre/RNC del contacto si viene
    `cliente_id`), las fechas por defecto y los totales."""
    if cliente_id is not None:
        cliente = await conn.fetchrow(
            "SELECT nombre, rnc FROM ventas.clientes WHERE id = $1", cliente_id
        )
        if cliente is None:
            raise DatosInvalidosError("El cliente indicado no existe")
        cliente_nombre, cliente_rnc = cliente["nombre"], cliente["rnc"]
    else:
        if not cliente_nombre:
            raise DatosInvalidosError("Indique el cliente (cliente_id o cliente_nombre)")
        cliente_rnc = re.sub(r"[^0-9A-Za-z]", "", cliente_rnc or "").upper() or None
    fecha = fecha or hoy()
    vence = vence or fecha + timedelta(days=_DIAS_VENCE_COTIZACION)
    if vence < fecha:
        raise DatosInvalidosError("La fecha de vencimiento no puede ser anterior a la fecha de la cotización")
    lineas, totales = calcular_cotizacion(items)
    return {
        "cliente_id": cliente_id,
        "cliente_nombre": cliente_nombre,
        "cliente_rnc": cliente_rnc,
        "fecha": fecha,
        "vence": vence,
        "items": json.dumps(lineas),
        **totales,
    }


async def crear_cotizacion(
    pool: asyncpg.Pool,
    *,
    cliente_id: uuid.UUID | None,
    cliente_nombre: str | None,
    cliente_rnc: str | None,
    fecha: date | None,
    vence: date | None,
    items: list[dict[str, Any]],
    notas: str | None,
) -> dict[str, Any]:
    async with pool.acquire() as conn:
        async with conn.transaction():
            datos = await _datos_cotizacion(
                conn,
                cliente_id=cliente_id,
                cliente_nombre=cliente_nombre,
                cliente_rnc=cliente_rnc,
                fecha=fecha,
                vence=vence,
                items=items,
            )
            correlativo = await conn.fetchval("SELECT nextval('ventas.cotizaciones_numero_seq')")
            cotizacion_id = await conn.fetchval(
                """
                INSERT INTO ventas.cotizaciones
                    (numero, cliente_id, cliente_nombre, cliente_rnc, fecha, vence, items, notas,
                     subtotal, descuentos, itbis_total, total)
                VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8, $9, $10, $11, $12)
                RETURNING id
                """,
                f"COT-{correlativo:04d}",
                datos["cliente_id"],
                datos["cliente_nombre"],
                datos["cliente_rnc"],
                datos["fecha"],
                datos["vence"],
                datos["items"],
                notas,
                datos["subtotal"],
                datos["descuentos"],
                datos["itbis_total"],
                datos["total"],
            )
            cotizacion = await _obtener_cotizacion(conn, cotizacion_id)
    assert cotizacion is not None
    return cotizacion


async def listar_cotizaciones(
    pool: asyncpg.Pool,
    *,
    q: str | None = None,
    estado: str | None = None,
    limite: int = 200,
    offset: int = 0,
) -> list[dict[str, Any]]:
    valores: list[Any] = [hoy()]
    condiciones = []
    if q:
        valores.append(f"%{q}%")
        condiciones.append(
            f"(numero ILIKE ${len(valores)} OR cliente_nombre ILIKE ${len(valores)}"
            f" OR cliente_rnc ILIKE ${len(valores)})"
        )
    if estado == "vencida":
        condiciones.append("estado = 'abierta' AND vence < $1::date")
    elif estado == "abierta":
        condiciones.append("estado = 'abierta' AND vence >= $1::date")
    elif estado is not None:
        valores.append(estado)
        condiciones.append(f"estado = ${len(valores)}")
    where = f"WHERE {' AND '.join(condiciones)}" if condiciones else ""
    valores.extend([limite, offset])
    query = f"""
        SELECT {_COLUMNAS_COTIZACION} FROM ventas.cotizaciones
        {where}
        ORDER BY creado_en DESC
        LIMIT ${len(valores) - 1} OFFSET ${len(valores)}
    """
    async with pool.acquire() as conn:
        filas = await conn.fetch(query, *valores)
    return [_fila_cotizacion(fila) for fila in filas]


async def obtener_cotizacion(pool: asyncpg.Pool, cotizacion_id: uuid.UUID) -> dict[str, Any]:
    async with pool.acquire() as conn:
        cotizacion = await _obtener_cotizacion(conn, cotizacion_id)
    if cotizacion is None:
        raise CotizacionNoEncontradaError(str(cotizacion_id))
    return cotizacion


async def _estado_guardado_para_actualizar(conn: asyncpg.Connection, cotizacion_id: uuid.UUID) -> str:
    estado = await conn.fetchval(
        "SELECT estado FROM ventas.cotizaciones WHERE id = $1 FOR UPDATE", cotizacion_id
    )
    if estado is None:
        raise CotizacionNoEncontradaError(str(cotizacion_id))
    return estado


async def actualizar_cotizacion(
    pool: asyncpg.Pool,
    cotizacion_id: uuid.UUID,
    *,
    cliente_id: uuid.UUID | None,
    cliente_nombre: str | None,
    cliente_rnc: str | None,
    fecha: date | None,
    vence: date | None,
    items: list[dict[str, Any]],
    notas: str | None,
) -> dict[str, Any]:
    async with pool.acquire() as conn:
        async with conn.transaction():
            estado = await _estado_guardado_para_actualizar(conn, cotizacion_id)
            if estado != "abierta":
                raise CotizacionEstadoInvalidoError(
                    f"Solo se pueden editar cotizaciones abiertas (estado actual: {estado})"
                )
            datos = await _datos_cotizacion(
                conn,
                cliente_id=cliente_id,
                cliente_nombre=cliente_nombre,
                cliente_rnc=cliente_rnc,
                fecha=fecha,
                vence=vence,
                items=items,
            )
            await conn.execute(
                """
                UPDATE ventas.cotizaciones
                SET cliente_id = $2, cliente_nombre = $3, cliente_rnc = $4, fecha = $5, vence = $6,
                    items = $7::jsonb, notas = $8, subtotal = $9, descuentos = $10,
                    itbis_total = $11, total = $12, actualizado_en = now()
                WHERE id = $1
                """,
                cotizacion_id,
                datos["cliente_id"],
                datos["cliente_nombre"],
                datos["cliente_rnc"],
                datos["fecha"],
                datos["vence"],
                datos["items"],
                notas,
                datos["subtotal"],
                datos["descuentos"],
                datos["itbis_total"],
                datos["total"],
            )
            cotizacion = await _obtener_cotizacion(conn, cotizacion_id)
    assert cotizacion is not None
    return cotizacion


async def cambiar_estado_cotizacion(
    pool: asyncpg.Pool, cotizacion_id: uuid.UUID, estado: str
) -> dict[str, Any]:
    """abierta | aceptada | rechazada. Una cotización facturada ya no cambia
    de estado."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            actual = await _estado_guardado_para_actualizar(conn, cotizacion_id)
            if actual == "facturada":
                raise CotizacionEstadoInvalidoError("La cotización ya fue facturada y no cambia de estado")
            await conn.execute(
                "UPDATE ventas.cotizaciones SET estado = $2, actualizado_en = now() WHERE id = $1",
                cotizacion_id,
                estado,
            )
            cotizacion = await _obtener_cotizacion(conn, cotizacion_id)
    assert cotizacion is not None
    return cotizacion


async def marcar_cotizacion_facturada(
    pool: asyncpg.Pool, cotizacion_id: uuid.UUID, factura_id: uuid.UUID
) -> dict[str, Any]:
    """La marca como facturada y la vincula a la factura creada desde ella.
    Repetir la llamada con la misma factura no falla (reintento del
    frontend); con otra factura distinta, 409."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            actual = await conn.fetchrow(
                "SELECT estado, factura_id FROM ventas.cotizaciones WHERE id = $1 FOR UPDATE",
                cotizacion_id,
            )
            if actual is None:
                raise CotizacionNoEncontradaError(str(cotizacion_id))
            if actual["estado"] == "facturada" and actual["factura_id"] != factura_id:
                raise CotizacionEstadoInvalidoError("La cotización ya está vinculada a otra factura")
            existe = await conn.fetchval("SELECT 1 FROM facturacion.facturas WHERE id = $1", factura_id)
            if existe is None:
                raise DatosInvalidosError("La factura indicada no existe")
            await conn.execute(
                """
                UPDATE ventas.cotizaciones
                SET estado = 'facturada', factura_id = $2, actualizado_en = now()
                WHERE id = $1
                """,
                cotizacion_id,
                factura_id,
            )
            cotizacion = await _obtener_cotizacion(conn, cotizacion_id)
    assert cotizacion is not None
    return cotizacion


async def eliminar_cotizacion(pool: asyncpg.Pool, cotizacion_id: uuid.UUID) -> None:
    async with pool.acquire() as conn:
        async with conn.transaction():
            estado = await _estado_guardado_para_actualizar(conn, cotizacion_id)
            if estado != "abierta":
                raise CotizacionEstadoInvalidoError(
                    f"Solo se pueden eliminar cotizaciones abiertas (estado actual: {estado})"
                )
            await conn.execute("DELETE FROM ventas.cotizaciones WHERE id = $1", cotizacion_id)


# --- Cuentas por cobrar ---------------------------------------------------------

async def listar_cuentas_por_cobrar(
    pool: asyncpg.Pool, *, estado: str | None = None, cliente_rnc: str | None = None
) -> list[dict[str, Any]]:
    """`estado` None = lo que falta por cobrar (pendiente + vencida);
    'todas' = también las cobradas."""
    valores: list[Any] = [hoy()]
    condiciones = []
    if estado is None:
        condiciones.append("estado <> 'cobrada'")
    elif estado != "todas":
        valores.append(estado)
        condiciones.append(f"estado = ${len(valores)}")
    if cliente_rnc:
        valores.append(re.sub(r"[^0-9A-Za-z]", "", cliente_rnc).upper())
        condiciones.append(f"cliente_rnc = ${len(valores)}")
    where = f"WHERE {' AND '.join(condiciones)}" if condiciones else ""
    query = f"""
        {_SELECT_CXC_CON_ESTADO}
        {where}
        ORDER BY vence, fecha_emision, encf
    """
    async with pool.acquire() as conn:
        filas = await conn.fetch(query, *valores)
    return [dict(fila) | {"factura_id": str(fila["factura_id"])} for fila in filas]


# --- Cobros ---------------------------------------------------------

_SELECT_COBROS = """
    SELECT co.id, co.factura_id, f.encf,
           COALESCE(f.datos::jsonb #>> '{Encabezado,Comprador,RazonSocialComprador}', '') AS cliente_nombre,
           co.fecha, co.monto, co.metodo, co.cuenta_destino, co.referencia, co.notas,
           co.anulado, co.creado_en
    FROM ventas.cobros co
    LEFT JOIN facturacion.facturas f ON f.id = co.factura_id
"""


def _fila_cobro(fila: asyncpg.Record) -> dict[str, Any]:
    return _con_id_str(fila) | {"factura_id": str(fila["factura_id"])}


async def listar_cobros(
    pool: asyncpg.Pool, *, factura_id: uuid.UUID | None = None, limite: int = 200, offset: int = 0
) -> list[dict[str, Any]]:
    condiciones = []
    valores: list[Any] = []
    if factura_id is not None:
        valores.append(factura_id)
        condiciones.append(f"co.factura_id = ${len(valores)}")
    where = f"WHERE {' AND '.join(condiciones)}" if condiciones else ""
    valores.extend([limite, offset])
    query = f"""
        {_SELECT_COBROS}
        {where}
        ORDER BY co.fecha DESC, co.creado_en DESC
        LIMIT ${len(valores) - 1} OFFSET ${len(valores)}
    """
    async with pool.acquire() as conn:
        filas = await conn.fetch(query, *valores)
    return [_fila_cobro(fila) for fila in filas]


async def registrar_cobro(
    pool: asyncpg.Pool,
    *,
    factura_id: uuid.UUID,
    fecha: date | None,
    monto: Decimal,
    metodo: str,
    cuenta_destino: str | None,
    referencia: str | None,
    notas: str | None,
) -> dict[str, Any]:
    """Registra un cobro de una factura a crédito, validando que no supere
    lo pendiente. El advisory lock por factura evita que dos cobros
    simultáneos de la misma factura pasen ambos la validación (no se puede
    usar FOR UPDATE: facturacion.facturas es de solo lectura para este
    servicio)."""
    monto = _redondear(monto)
    if monto <= 0:
        raise DatosInvalidosError("El monto del cobro debe ser mayor que cero")
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1, 0))", str(factura_id))
            cuenta = await conn.fetchrow(
                f"WITH {_CTE_CXC} SELECT pendiente FROM cxc WHERE factura_id = $1", factura_id
            )
            if cuenta is None:
                raise DatosInvalidosError("La factura no es a crédito o no fue enviada")
            pendiente: Decimal = cuenta["pendiente"]
            if pendiente <= 0:
                raise DatosInvalidosError("La factura ya está cobrada por completo")
            if monto > pendiente:
                raise DatosInvalidosError(
                    f"El monto del cobro ({monto}) supera lo pendiente de la factura ({pendiente})"
                )
            cobro_id = await conn.fetchval(
                """
                INSERT INTO ventas.cobros
                    (factura_id, fecha, monto, metodo, cuenta_destino, referencia, notas)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                RETURNING id
                """,
                factura_id,
                fecha or hoy(),
                monto,
                metodo,
                cuenta_destino,
                referencia,
                notas,
            )
            fila = await conn.fetchrow(f"{_SELECT_COBROS} WHERE co.id = $1", cobro_id)
    return _fila_cobro(fila)


async def anular_cobro(pool: asyncpg.Pool, cobro_id: uuid.UUID) -> dict[str, Any]:
    async with pool.acquire() as conn:
        async with conn.transaction():
            anulado = await conn.fetchval(
                "SELECT anulado FROM ventas.cobros WHERE id = $1 FOR UPDATE", cobro_id
            )
            if anulado is None:
                raise CobroNoEncontradoError(str(cobro_id))
            if anulado:
                raise CobroEstadoInvalidoError("El cobro ya está anulado")
            await conn.execute(
                "UPDATE ventas.cobros SET anulado = true, anulado_en = now() WHERE id = $1", cobro_id
            )
            fila = await conn.fetchrow(f"{_SELECT_COBROS} WHERE co.id = $1", cobro_id)
    return _fila_cobro(fila)


# --- Resumen de documentos de venta ---------------------------------------------------------

async def listar_documentos_venta(
    pool: asyncpg.Pool,
    *,
    tipo_ecf: int | None = None,
    estado: str | None = None,
    q: str | None = None,
    limite: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Cualquier factura de facturacion.facturas con los datos del e-CF que
    el listado de facturas_api no trae (cliente, total, referencia)."""
    condiciones = []
    valores: list[Any] = []
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
            f" OR d #>> '{{Encabezado,Comprador,RNCComprador}}' ILIKE ${n} OR {_RNC_COMPRADOR} ILIKE ${n})"
        )
    where = f"WHERE {' AND '.join(condiciones)}" if condiciones else ""
    valores.extend([limite, offset])
    query = f"""
        SELECT
            f.id, f.tipo_ecf, f.encf, f.estado,
            {_RNC_COMPRADOR} AS cliente_rnc,
            {_NOMBRE_COMPRADOR} AS cliente_nombre,
            {_FECHA_EMISION} AS fecha_emision,
            {_monto_dgii("{Encabezado,Totales,MontoTotal}")} AS total,
            NULLIF(d #>> '{{InformacionReferencia,NCFModificado}}', '') AS encf_afectado,
            NULLIF(d #>> '{{InformacionReferencia,CodigoModificacion}}', '') AS codigo_modificacion,
            f.dgii_codigo, f.error, f.creado_en
        FROM facturacion.facturas f
        CROSS JOIN LATERAL (SELECT f.datos::jsonb AS d) x
        {where}
        ORDER BY f.creado_en DESC
        LIMIT ${len(valores) - 1} OFFSET ${len(valores)}
    """
    async with pool.acquire() as conn:
        filas = await conn.fetch(query, *valores)
    return [_con_id_str(fila) for fila in filas]
