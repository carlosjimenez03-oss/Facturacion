"""Primitivas de stock compartidas por compras, ventas (facturas), ajustes,
lotes y transferencias. Todas reciben una conexión YA dentro de una
transacción: quien llama decide qué se aplica junto (todo o nada).

Reglas que concentran estas funciones (y que ninguna otra parte del
servicio debe tocar a mano):

- `productos.stock_actual` es el TOTAL del producto y `existencias` su
  reparto por almacén; `existencias_lote` el reparto por lote dentro de cada
  almacén (lo que no está en ningún lote es mercancía "sin lote"). Se
  actualizan juntos.
- Concurrencia: cada movimiento bloquea primero la fila del producto
  (`SELECT ... FOR NO KEY UPDATE`), así que dos salidas simultáneas del
  mismo producto se serializan y la segunda ve la existencia que dejó la
  primera -- no hay sobreventa en las salidas que validan stock. Las
  operaciones de varias líneas bloquean antes todos sus productos en orden
  de id (`bloquear_productos`) para no caer en un deadlock. Es NO KEY
  UPDATE y no FOR UPDATE a propósito: insertar una línea que referencia al
  producto (ajuste_items, compra_items...) toma un FOR KEY SHARE sobre su
  fila, y dos transacciones que ya tienen ese lock y piden FOR UPDATE se
  bloquean mutuamente (deadlock); NO KEY UPDATE no choca con KEY SHARE.
- Costo: promedio ponderado global por producto. Las entradas lo
  recalculan; las salidas (y las transferencias) salen al costo promedio
  vigente, que queda grabado en `movimientos.costo_unitario` para el Kardex.
  La excepción es la anulación de una entrada: sale al costo con que entró
  y el promedio se recalcula sin ella (`costo_promedio_sin_entrada`).
- Lotes: toda entrada a un lote activa `maneja_lotes` del producto, y
  `maneja_lotes` no se puede apagar mientras haya lotes con existencia
  (repository.actualizar_producto). Así lo "sin lote" de un producto sin
  lotes es toda su existencia.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import asyncpg

from .errores import (
    AlmacenNoEncontradoError,
    LoteNoEncontradoError,
    OperacionInvalidaError,
    ProductoNoEncontradoError,
    StockInsuficienteError,
)

# Igual que erp_api: la República Dominicana no tiene horario de verano.
ZONA_RD = timezone(timedelta(hours=-4), "America/Santo_Domingo")


def hoy_rd() -> date:
    return datetime.now(ZONA_RD).date()


def con_id_str(fila: asyncpg.Record) -> dict[str, Any]:
    datos = dict(fila)
    datos["id"] = str(datos["id"])
    return datos


# --- Cálculos puros ---------------------------------------------------------

def costo_promedio_ponderado(
    stock_previo: Decimal, costo_previo: Decimal, cantidad: Decimal, costo_unitario: Decimal
) -> Decimal:
    """Nuevo costo promedio tras una entrada. Sin existencia previa
    positiva (cero o negativa por ventas forzadas) el promedio anterior no
    significa nada: el nuevo costo es directamente el de esta entrada."""
    nuevo_stock = stock_previo + cantidad
    if stock_previo <= 0 or nuevo_stock <= 0:
        return costo_unitario
    return (stock_previo * costo_previo + cantidad * costo_unitario) / nuevo_stock


def costo_promedio_sin_entrada(
    stock_previo: Decimal, costo_previo: Decimal, cantidad: Decimal, costo_unitario: Decimal
) -> Decimal:
    """Costo promedio tras sacar `cantidad` al `costo_unitario` con que
    entró (anulación de esa entrada): deshace el promedio ponderado,
    (existencia·promedio − cantidad·costo) / (existencia − cantidad). Si no
    queda existencia positiva, el costo queda en el de la entrada anulada
    (igual que `costo_promedio_ponderado` sin existencia previa)."""
    nuevo_stock = stock_previo - cantidad
    if nuevo_stock <= 0:
        return costo_unitario
    return (stock_previo * costo_previo - cantidad * costo_unitario) / nuevo_stock


def estado_lote(
    *, fecha_vencimiento: date | None, bloqueado: bool, hoy: date, dias_alerta: int
) -> str:
    """'bloqueado' | 'vencido' | 'por_vencer' | 'vigente'. Un lote vence al
    terminar su fecha de vencimiento (ese día todavía está vigente/por
    vencer)."""
    if bloqueado:
        return "bloqueado"
    if fecha_vencimiento is None:
        return "vigente"
    if fecha_vencimiento < hoy:
        return "vencido"
    if fecha_vencimiento <= hoy + timedelta(days=dias_alerta):
        return "por_vencer"
    return "vigente"


def plan_fefo(
    lotes: list[dict[str, Any]], cantidad: Decimal
) -> tuple[list[tuple[Any, Decimal]], Decimal]:
    """Reparte `cantidad` entre `lotes` (ya filtrados y en orden FEFO:
    primero el que vence antes) -> ([(lote_id, cantidad_a_tomar)], faltante)."""
    tomas: list[tuple[Any, Decimal]] = []
    restante = cantidad
    for lote in lotes:
        if restante <= 0:
            break
        disponible = lote["cantidad"]
        if disponible <= 0:
            continue
        tomar = min(disponible, restante)
        tomas.append((lote["lote_id"], tomar))
        restante -= tomar
    return tomas, max(restante, Decimal("0"))


# --- Almacenes / lotes ---------------------------------------------------------

# El almacén "Principal" de la empresa 1 lo creó la migración; el de cada
# empresa nueva se crea al primer uso (multiempresa: el índice único del
# principal es por empresa, y la política RLS pone la empresa del request).
_SQL_CREAR_PRINCIPAL = """
INSERT INTO inventario.almacenes (nombre, es_principal)
SELECT 'Principal', true
WHERE NOT EXISTS (SELECT 1 FROM inventario.almacenes WHERE es_principal)
ON CONFLICT DO NOTHING
"""


async def asegurar_almacen_principal(conn: asyncpg.Connection) -> None:
    await conn.execute(_SQL_CREAR_PRINCIPAL)


async def almacen_principal_id(conn: asyncpg.Connection) -> uuid.UUID:
    almacen_id = await conn.fetchval("SELECT id FROM inventario.almacenes WHERE es_principal")
    if almacen_id is None:
        await asegurar_almacen_principal(conn)
        almacen_id = await conn.fetchval("SELECT id FROM inventario.almacenes WHERE es_principal")
    if almacen_id is None:  # solo si alguien borró la fila a mano
        raise AlmacenNoEncontradoError("No hay almacén principal")
    return almacen_id


async def obtener_almacen(
    conn: asyncpg.Connection, almacen_id: uuid.UUID | None, *, exigir_activo: bool = True
) -> asyncpg.Record:
    """El almacén indicado o, si es None, el principal. Con `exigir_activo`
    (toda operación que mete o saca mercancía salvo la salida por factura y
    las anulaciones) además bloquea la fila FOR SHARE hasta el fin de la
    transacción: inactivar el almacén (FOR UPDATE en
    `almacenes_repository.actualizar_almacen`) espera a que termine y ve la
    existencia que dejó, o la operación espera y lo ve inactivo."""
    bloqueo = " FOR SHARE" if exigir_activo else ""
    if almacen_id is None:
        fila = await conn.fetchrow(f"SELECT * FROM inventario.almacenes WHERE es_principal{bloqueo}")
        if fila is None:
            await asegurar_almacen_principal(conn)
            fila = await conn.fetchrow(f"SELECT * FROM inventario.almacenes WHERE es_principal{bloqueo}")
    else:
        fila = await conn.fetchrow(f"SELECT * FROM inventario.almacenes WHERE id = $1{bloqueo}", almacen_id)
    if fila is None:
        raise AlmacenNoEncontradoError(str(almacen_id) if almacen_id else "principal")
    if exigir_activo and not fila["activo"]:
        raise OperacionInvalidaError(f"El almacén '{fila['nombre']}' está inactivo")
    return fila


async def obtener_lote_de_producto(
    conn: asyncpg.Connection, lote_id: uuid.UUID, producto_id: uuid.UUID
) -> asyncpg.Record:
    lote = await conn.fetchrow("SELECT * FROM inventario.lotes WHERE id = $1", lote_id)
    if lote is None:
        raise LoteNoEncontradoError(str(lote_id))
    if lote["producto_id"] != producto_id:
        raise OperacionInvalidaError(f"El lote '{lote['numero']}' no es del producto indicado")
    return lote


async def obtener_o_crear_lote(
    conn: asyncpg.Connection, producto_id: uuid.UUID, numero: str, fecha_vencimiento: date | None
) -> uuid.UUID:
    """Lote (producto, número); si no existe lo crea. Si ya existía sin
    vencimiento y ahora llega uno, se completa."""
    return await conn.fetchval(
        """
        INSERT INTO inventario.lotes (producto_id, numero, fecha_vencimiento)
        VALUES ($1, $2, $3)
        ON CONFLICT (producto_id, numero) DO UPDATE
            SET fecha_vencimiento = COALESCE(inventario.lotes.fecha_vencimiento, EXCLUDED.fecha_vencimiento)
        RETURNING id
        """,
        producto_id,
        numero,
        fecha_vencimiento,
    )


async def lotes_fefo(
    conn: asyncpg.Connection, producto_id: uuid.UUID, almacen_id: uuid.UUID, hoy: date
) -> list[dict[str, Any]]:
    """Lotes del producto con existencia en el almacén que se pueden vender:
    ni bloqueados ni vencidos, primero el que vence antes (los que no
    vencen al final)."""
    filas = await conn.fetch(
        """
        SELECT l.id AS lote_id, l.numero, l.fecha_vencimiento, el.cantidad
        FROM inventario.existencias_lote el
        JOIN inventario.lotes l ON l.id = el.lote_id
        WHERE l.producto_id = $1 AND el.almacen_id = $2 AND el.cantidad > 0
            AND NOT l.bloqueado AND (l.fecha_vencimiento IS NULL OR l.fecha_vencimiento >= $3)
        ORDER BY l.fecha_vencimiento NULLS LAST, l.creado_en, l.numero
        """,
        producto_id,
        almacen_id,
        hoy,
    )
    return [dict(fila) for fila in filas]


# --- Existencias ---------------------------------------------------------

async def bloquear_productos(conn: asyncpg.Connection, producto_ids: list[uuid.UUID]) -> None:
    """Bloquea (FOR NO KEY UPDATE) todos los productos de una operación de varias
    líneas, siempre en el mismo orden, antes de tocar ninguno."""
    unicos = sorted(set(producto_ids))
    if not unicos:
        return
    encontrados = await conn.fetch(
        "SELECT id FROM inventario.productos WHERE id = ANY($1::uuid[]) ORDER BY id FOR NO KEY UPDATE",
        unicos,
    )
    faltan = set(unicos) - {fila["id"] for fila in encontrados}
    if faltan:
        raise ProductoNoEncontradoError(str(sorted(faltan)[0]))


async def _producto_bloqueado(conn: asyncpg.Connection, producto_id: uuid.UUID) -> asyncpg.Record:
    producto = await conn.fetchrow(
        """
        SELECT id, nombre, tipo, maneja_lotes, stock_actual, costo_promedio
        FROM inventario.productos WHERE id = $1 FOR NO KEY UPDATE
        """,
        producto_id,
    )
    if producto is None:
        raise ProductoNoEncontradoError(str(producto_id))
    if producto["tipo"] == "servicio":
        raise OperacionInvalidaError(f"'{producto['nombre']}' es un servicio: no lleva existencia")
    return producto


async def existencia(conn: asyncpg.Connection, producto_id: uuid.UUID, almacen_id: uuid.UUID) -> Decimal:
    valor = await conn.fetchval(
        "SELECT cantidad FROM inventario.existencias WHERE producto_id = $1 AND almacen_id = $2",
        producto_id,
        almacen_id,
    )
    return valor if valor is not None else Decimal("0")


async def existencia_lote(conn: asyncpg.Connection, lote_id: uuid.UUID, almacen_id: uuid.UUID) -> Decimal:
    valor = await conn.fetchval(
        "SELECT cantidad FROM inventario.existencias_lote WHERE lote_id = $1 AND almacen_id = $2",
        lote_id,
        almacen_id,
    )
    return valor if valor is not None else Decimal("0")


async def cantidad_en_lotes(conn: asyncpg.Connection, producto_id: uuid.UUID, almacen_id: uuid.UUID) -> Decimal:
    """Suma de todos los lotes del producto en el almacén (vencidos y
    bloqueados incluidos). Existencia del almacén menos esto = "sin lote"."""
    return await conn.fetchval(
        """
        SELECT COALESCE(sum(el.cantidad), 0)
        FROM inventario.existencias_lote el
        JOIN inventario.lotes l ON l.id = el.lote_id
        WHERE l.producto_id = $1 AND el.almacen_id = $2
        """,
        producto_id,
        almacen_id,
    )


async def _sumar_existencia(
    conn: asyncpg.Connection, producto_id: uuid.UUID, almacen_id: uuid.UUID, delta: Decimal
) -> None:
    await conn.execute(
        """
        INSERT INTO inventario.existencias (producto_id, almacen_id, cantidad)
        VALUES ($1, $2, $3)
        ON CONFLICT (producto_id, almacen_id)
        DO UPDATE SET cantidad = inventario.existencias.cantidad + EXCLUDED.cantidad, actualizado_en = now()
        """,
        producto_id,
        almacen_id,
        delta,
    )


async def _sumar_existencia_lote(
    conn: asyncpg.Connection, lote_id: uuid.UUID, almacen_id: uuid.UUID, delta: Decimal
) -> None:
    await conn.execute(
        """
        INSERT INTO inventario.existencias_lote (lote_id, almacen_id, cantidad)
        VALUES ($1, $2, $3)
        ON CONFLICT (lote_id, almacen_id)
        DO UPDATE SET cantidad = inventario.existencias_lote.cantidad + EXCLUDED.cantidad, actualizado_en = now()
        """,
        lote_id,
        almacen_id,
        delta,
    )


async def _insertar_movimiento(
    conn: asyncpg.Connection,
    *,
    producto_id: uuid.UUID,
    almacen_id: uuid.UUID,
    tipo: str,
    cantidad: Decimal,
    costo_unitario: Decimal,
    lote_id: uuid.UUID | None,
    motivo: str | None,
    referencia_tipo: str | None,
    referencia_id: str | None,
    referencia_linea: str | None,
    documento: str | None,
) -> dict[str, Any]:
    fila = await conn.fetchrow(
        """
        INSERT INTO inventario.movimientos
            (producto_id, almacen_id, lote_id, tipo, cantidad, costo_unitario, motivo,
             referencia_tipo, referencia_id, referencia_linea, documento)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
        RETURNING *
        """,
        producto_id,
        almacen_id,
        lote_id,
        tipo,
        cantidad,
        costo_unitario,
        motivo,
        referencia_tipo,
        referencia_id,
        referencia_linea,
        documento,
    )
    return con_id_str(fila)


async def registrar_entrada(
    conn: asyncpg.Connection,
    *,
    producto_id: uuid.UUID,
    almacen_id: uuid.UUID,
    cantidad: Decimal,
    costo_unitario: Decimal | None = None,
    lote_id: uuid.UUID | None = None,
    motivo: str | None,
    referencia_tipo: str,
    referencia_id: str | None = None,
    referencia_linea: str | None = None,
    documento: str | None = None,
    es_transferencia: bool = False,
) -> dict[str, Any]:
    """Entrada de mercancía a un almacén (y lote). `costo_unitario` None =
    al costo promedio vigente. Una transferencia no cambia el total del
    producto ni su costo (es la mitad de un par salida/entrada)."""
    producto = await _producto_bloqueado(conn, producto_id)
    if lote_id is not None:
        await obtener_lote_de_producto(conn, lote_id, producto_id)
        if not producto["maneja_lotes"]:
            await conn.execute(
                "UPDATE inventario.productos SET maneja_lotes = true, actualizado_en = now() WHERE id = $1",
                producto_id,
            )
    costo: Decimal = costo_unitario if costo_unitario is not None else producto["costo_promedio"]

    if not es_transferencia:
        nuevo_costo = costo_promedio_ponderado(
            producto["stock_actual"], producto["costo_promedio"], cantidad, costo
        )
        await conn.execute(
            """
            UPDATE inventario.productos
            SET stock_actual = stock_actual + $2, costo_promedio = $3, actualizado_en = now()
            WHERE id = $1
            """,
            producto_id,
            cantidad,
            nuevo_costo,
        )
    await _sumar_existencia(conn, producto_id, almacen_id, cantidad)
    if lote_id is not None:
        await _sumar_existencia_lote(conn, lote_id, almacen_id, cantidad)
    return await _insertar_movimiento(
        conn,
        producto_id=producto_id,
        almacen_id=almacen_id,
        tipo="entrada",
        cantidad=cantidad,
        costo_unitario=costo,
        lote_id=lote_id,
        motivo=motivo,
        referencia_tipo=referencia_tipo,
        referencia_id=referencia_id,
        referencia_linea=referencia_linea,
        documento=documento,
    )


async def registrar_salida(
    conn: asyncpg.Connection,
    *,
    producto_id: uuid.UUID,
    almacen: asyncpg.Record,
    cantidad: Decimal,
    lote_id: uuid.UUID | None = None,
    forzar: bool = False,
    fefo: bool = True,
    costo_entrada: Decimal | None = None,
    motivo: str | None,
    referencia_tipo: str,
    referencia_id: str | None = None,
    referencia_linea: str | None = None,
    documento: str | None = None,
    es_transferencia: bool = False,
) -> list[dict[str, Any]]:
    """Salida de mercancía de un almacén, al costo promedio vigente.

    - Con `lote_id`: sale de ese lote (aunque esté vencido o bloqueado: un
      ajuste por vencimiento lo necesita).
    - Sin lote y el producto `maneja_lotes`: se toma por FEFO de los lotes
      vendibles y, si no alcanzan, el resto de la mercancía "sin lote".
      Puede generar varios movimientos (uno por lote). Con `fefo` False
      sale directo de lo "sin lote" (la anulación de una entrada sin lote).
    - Sin `forzar` y en un almacén que no `permite_negativo`, lanza
      `StockInsuficienteError` si no hay existencia suficiente: del lote,
      o de lo "sin lote" (existencia del almacén menos sus lotes) si la
      salida no lleva lote ni FEFO -- no basta con el total del almacén, o
      lo "sin lote" quedaría negativo con los lotes en positivo. `forzar`
      lo usa la salida por factura: el e-CF ya se envió y no se puede
      deshacer.
    - `costo_entrada`: solo la anulación de una entrada. Sale a ese costo
      (el de la entrada) en vez del promedio vigente, y el promedio se
      recalcula como si la entrada no hubiera ocurrido.
    """
    almacen_id = almacen["id"]
    producto = await _producto_bloqueado(conn, producto_id)
    valida = not forzar and not almacen["permite_negativo"]
    disponible = await existencia(conn, producto_id, almacen_id)
    if valida and disponible < cantidad:
        raise StockInsuficienteError(
            producto_id=str(producto_id),
            nombre=producto["nombre"],
            almacen_id=str(almacen_id),
            solicitado=cantidad,
            disponible=disponible,
        )

    tomas: list[tuple[Any, Decimal]]
    if lote_id is not None:
        await obtener_lote_de_producto(conn, lote_id, producto_id)
        disponible_lote = await existencia_lote(conn, lote_id, almacen_id)
        if valida and disponible_lote < cantidad:
            raise StockInsuficienteError(
                producto_id=str(producto_id),
                nombre=producto["nombre"],
                almacen_id=str(almacen_id),
                lote_id=str(lote_id),
                solicitado=cantidad,
                disponible=disponible_lote,
            )
        tomas = [(lote_id, cantidad)]
    elif producto["maneja_lotes"] and fefo:
        vendibles = await lotes_fefo(conn, producto_id, almacen_id, hoy_rd())
        tomas, resto = plan_fefo(vendibles, cantidad)
        if resto > 0:
            sin_lote = max(disponible - await cantidad_en_lotes(conn, producto_id, almacen_id), Decimal("0"))
            if valida and resto > sin_lote:
                raise StockInsuficienteError(
                    producto_id=str(producto_id),
                    nombre=producto["nombre"],
                    almacen_id=str(almacen_id),
                    solicitado=cantidad,
                    disponible=cantidad - resto + sin_lote,
                )
            tomas.append((None, resto))
    else:
        if valida:
            sin_lote = disponible - await cantidad_en_lotes(conn, producto_id, almacen_id)
            if sin_lote < cantidad:
                raise StockInsuficienteError(
                    producto_id=str(producto_id),
                    nombre=producto["nombre"],
                    almacen_id=str(almacen_id),
                    solicitado=cantidad,
                    disponible=max(sin_lote, Decimal("0")),
                )
        tomas = [(None, cantidad)]

    costo: Decimal = producto["costo_promedio"] if costo_entrada is None else costo_entrada
    movimientos = []
    for lote_toma, cantidad_toma in tomas:
        if lote_toma is not None:
            await _sumar_existencia_lote(conn, lote_toma, almacen_id, -cantidad_toma)
        movimientos.append(
            await _insertar_movimiento(
                conn,
                producto_id=producto_id,
                almacen_id=almacen_id,
                tipo="salida",
                cantidad=cantidad_toma,
                costo_unitario=costo,
                lote_id=lote_toma,
                motivo=motivo,
                referencia_tipo=referencia_tipo,
                referencia_id=referencia_id,
                referencia_linea=referencia_linea,
                documento=documento,
            )
        )
    await _sumar_existencia(conn, producto_id, almacen_id, -cantidad)
    if not es_transferencia:
        nuevo_costo = producto["costo_promedio"]
        if costo_entrada is not None:
            nuevo_costo = costo_promedio_sin_entrada(
                producto["stock_actual"], producto["costo_promedio"], cantidad, costo_entrada
            )
        await conn.execute(
            """
            UPDATE inventario.productos
            SET stock_actual = stock_actual - $2, costo_promedio = $3, actualizado_en = now()
            WHERE id = $1
            """,
            producto_id,
            cantidad,
            nuevo_costo,
        )
    return movimientos
