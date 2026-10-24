"""Lotes (número + vencimiento) por producto, su existencia por almacén, la
sugerencia FEFO y las alertas de "por vencer" / "vencidos" (pantalla
Lotes)."""
from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import asyncpg

from . import stock
from .errores import LoteNoEncontradoError, OperacionInvalidaError, ProductoNoEncontradoError

_SELECT_LOTE = """
    SELECT l.*, p.sku AS producto_sku, p.nombre AS producto_nombre,
           COALESCE((SELECT sum(el.cantidad) FROM inventario.existencias_lote el WHERE el.lote_id = l.id), 0)
               AS cantidad
    FROM inventario.lotes l
    JOIN inventario.productos p ON p.id = l.producto_id
"""


async def _con_existencias(
    conn: asyncpg.Connection, filas: list[asyncpg.Record], *, hoy: date, dias_alerta: int
) -> list[dict[str, Any]]:
    ids = [fila["id"] for fila in filas]
    detalle = await conn.fetch(
        """
        SELECT el.lote_id, el.almacen_id, a.nombre AS almacen_nombre, el.cantidad
        FROM inventario.existencias_lote el
        JOIN inventario.almacenes a ON a.id = el.almacen_id
        WHERE el.lote_id = ANY($1::uuid[]) AND el.cantidad <> 0
        ORDER BY a.es_principal DESC, a.nombre
        """,
        ids,
    )
    resultado = []
    for fila in filas:
        lote = stock.con_id_str(fila)
        vence = lote["fecha_vencimiento"]
        lote["estado"] = stock.estado_lote(
            fecha_vencimiento=vence, bloqueado=lote["bloqueado"], hoy=hoy, dias_alerta=dias_alerta
        )
        lote["dias_para_vencer"] = (vence - hoy).days if vence else None
        lote["existencias"] = [
            {"almacen_id": d["almacen_id"], "almacen_nombre": d["almacen_nombre"], "cantidad": d["cantidad"]}
            for d in detalle
            if d["lote_id"] == fila["id"]
        ]
        resultado.append(lote)
    return resultado


def _condicion_estado(estado: str, valores: list[Any], hoy: date, dias_alerta: int) -> str:
    """Traduce el estado calculado (ver `stock.estado_lote`) a SQL."""
    if estado == "bloqueado":
        return "l.bloqueado"

    def parametro(valor: date) -> str:
        valores.append(valor)
        return f"${len(valores)}"

    limite = hoy + timedelta(days=dias_alerta)
    if estado == "vencido":
        return f"(NOT l.bloqueado AND l.fecha_vencimiento < {parametro(hoy)})"
    if estado == "por_vencer":
        return (
            f"(NOT l.bloqueado AND l.fecha_vencimiento >= {parametro(hoy)} "
            f"AND l.fecha_vencimiento <= {parametro(limite)})"
        )
    # vigente
    return f"(NOT l.bloqueado AND (l.fecha_vencimiento IS NULL OR l.fecha_vencimiento > {parametro(limite)}))"


async def listar_lotes(
    pool: asyncpg.Pool,
    *,
    dias_alerta: int,
    producto_id: uuid.UUID | None = None,
    almacen_id: uuid.UUID | None = None,
    estado: str | None = None,
    q: str | None = None,
    con_existencia: bool | None = None,
    limite: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    hoy = stock.hoy_rd()
    condiciones: list[str] = []
    valores: list[Any] = []
    if producto_id is not None:
        valores.append(producto_id)
        condiciones.append(f"l.producto_id = ${len(valores)}")
    if almacen_id is not None:
        valores.append(almacen_id)
        condiciones.append(
            "EXISTS (SELECT 1 FROM inventario.existencias_lote el "
            f"WHERE el.lote_id = l.id AND el.almacen_id = ${len(valores)} AND el.cantidad <> 0)"
        )
    if estado:
        condiciones.append(_condicion_estado(estado, valores, hoy, dias_alerta))
    if q:
        valores.append(f"%{q}%")
        condiciones.append(
            f"(l.numero ILIKE ${len(valores)} OR p.nombre ILIKE ${len(valores)} OR p.sku ILIKE ${len(valores)})"
        )
    if con_existencia is not None:
        existe = "EXISTS (SELECT 1 FROM inventario.existencias_lote el WHERE el.lote_id = l.id AND el.cantidad <> 0)"
        condiciones.append(existe if con_existencia else f"NOT {existe}")
    where = f"WHERE {' AND '.join(condiciones)}" if condiciones else ""
    valores.extend([limite, offset])
    async with pool.acquire() as conn:
        filas = await conn.fetch(
            f"""
            {_SELECT_LOTE}
            {where}
            ORDER BY l.fecha_vencimiento NULLS LAST, p.nombre, l.numero
            LIMIT ${len(valores) - 1} OFFSET ${len(valores)}
            """,
            *valores,
        )
        return await _con_existencias(conn, filas, hoy=hoy, dias_alerta=dias_alerta)


async def obtener_lote(pool: asyncpg.Pool, lote_id: uuid.UUID, *, dias_alerta: int) -> dict[str, Any]:
    async with pool.acquire() as conn:
        return await _obtener(conn, lote_id, dias_alerta=dias_alerta)


async def _obtener(conn: asyncpg.Connection, lote_id: uuid.UUID, *, dias_alerta: int) -> dict[str, Any]:
    fila = await conn.fetchrow(f"{_SELECT_LOTE} WHERE l.id = $1", lote_id)
    if fila is None:
        raise LoteNoEncontradoError(str(lote_id))
    return (await _con_existencias(conn, [fila], hoy=stock.hoy_rd(), dias_alerta=dias_alerta))[0]


async def crear_lote(
    pool: asyncpg.Pool,
    *,
    producto_id: uuid.UUID,
    numero: str,
    fecha_vencimiento: date | None,
    notas: str | None,
    dias_alerta: int,
    cantidad_inicial: Decimal = Decimal("0"),
    almacen_id: uuid.UUID | None = None,
    costo_unitario: Decimal | None = None,
) -> dict[str, Any]:
    """Registra un lote del producto (y le activa `maneja_lotes`). Con
    `cantidad_inicial` > 0 además ingresa esa mercancía al almacén indicado
    (o al principal) al `costo_unitario` (None = costo promedio vigente);
    si no, el lote queda listo para la próxima entrada (compra o ajuste)."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            producto = await conn.fetchrow(
                "SELECT tipo FROM inventario.productos WHERE id = $1 FOR NO KEY UPDATE", producto_id
            )
            if producto is None:
                raise ProductoNoEncontradoError(str(producto_id))
            if producto["tipo"] == "servicio":
                raise OperacionInvalidaError("Un servicio no lleva lotes")
            existe = await conn.fetchval(
                "SELECT 1 FROM inventario.lotes WHERE producto_id = $1 AND numero = $2", producto_id, numero
            )
            if existe:
                raise OperacionInvalidaError(f"El producto ya tiene un lote '{numero}'")
            lote_id = await conn.fetchval(
                """
                INSERT INTO inventario.lotes (producto_id, numero, fecha_vencimiento, notas)
                VALUES ($1, $2, $3, $4)
                RETURNING id
                """,
                producto_id,
                numero,
                fecha_vencimiento,
                notas,
            )
            await conn.execute(
                "UPDATE inventario.productos SET maneja_lotes = true, actualizado_en = now() "
                "WHERE id = $1 AND NOT maneja_lotes",
                producto_id,
            )
            if cantidad_inicial > 0:
                almacen = await stock.obtener_almacen(conn, almacen_id)
                await stock.registrar_entrada(
                    conn,
                    producto_id=producto_id,
                    almacen_id=almacen["id"],
                    cantidad=cantidad_inicial,
                    costo_unitario=costo_unitario,
                    lote_id=lote_id,
                    motivo="Entrada manual de lote",
                    referencia_tipo="lote",
                    referencia_id=str(lote_id),
                    documento=numero,
                )
            return await _obtener(conn, lote_id, dias_alerta=dias_alerta)


async def actualizar_lote(
    pool: asyncpg.Pool,
    lote_id: uuid.UUID,
    *,
    numero: str,
    fecha_vencimiento: date | None,
    bloqueado: bool,
    notas: str | None,
    dias_alerta: int,
) -> dict[str, Any]:
    async with pool.acquire() as conn:
        async with conn.transaction():
            actual = await conn.fetchrow("SELECT producto_id FROM inventario.lotes WHERE id = $1 FOR UPDATE", lote_id)
            if actual is None:
                raise LoteNoEncontradoError(str(lote_id))
            repetido = await conn.fetchval(
                "SELECT 1 FROM inventario.lotes WHERE producto_id = $1 AND numero = $2 AND id <> $3",
                actual["producto_id"],
                numero,
                lote_id,
            )
            if repetido:
                raise OperacionInvalidaError(f"El producto ya tiene un lote '{numero}'")
            await conn.execute(
                """
                UPDATE inventario.lotes
                SET numero = $2, fecha_vencimiento = $3, bloqueado = $4, notas = $5, actualizado_en = now()
                WHERE id = $1
                """,
                lote_id,
                numero,
                fecha_vencimiento,
                bloqueado,
                notas,
            )
            return await _obtener(conn, lote_id, dias_alerta=dias_alerta)


async def sugerir_fefo(
    pool: asyncpg.Pool, *, producto_id: uuid.UUID, almacen_id: uuid.UUID | None, cantidad: Decimal
) -> dict[str, Any]:
    """De qué lotes conviene sacar `cantidad` (primero el que vence antes;
    ni vencidos ni bloqueados), cuánto saldría de lo "sin lote" y cuánto
    falta. Es lo mismo que hace una salida sin lote de un producto que
    maneja lotes (ver `stock.registrar_salida`), sin mover nada."""
    hoy = stock.hoy_rd()
    async with pool.acquire() as conn:
        existe = await conn.fetchval("SELECT 1 FROM inventario.productos WHERE id = $1", producto_id)
        if not existe:
            raise ProductoNoEncontradoError(str(producto_id))
        almacen = await stock.obtener_almacen(conn, almacen_id, exigir_activo=False)
        vendibles = await stock.lotes_fefo(conn, producto_id, almacen["id"], hoy)
        disponible = await stock.existencia(conn, producto_id, almacen["id"])
        en_lotes = await stock.cantidad_en_lotes(conn, producto_id, almacen["id"])
    tomas, resto = stock.plan_fefo(vendibles, cantidad)
    por_lote = {lote["lote_id"]: lote for lote in vendibles}
    sin_lote_disponible = max(disponible - en_lotes, Decimal("0"))
    sin_lote = min(resto, sin_lote_disponible)
    return {
        "producto_id": producto_id,
        "almacen_id": almacen["id"],
        "cantidad": cantidad,
        "tomas": [
            {
                "lote_id": lote_id,
                "numero": por_lote[lote_id]["numero"],
                "fecha_vencimiento": por_lote[lote_id]["fecha_vencimiento"],
                "disponible": por_lote[lote_id]["cantidad"],
                "cantidad": tomar,
            }
            for lote_id, tomar in tomas
        ],
        "sin_lote": sin_lote,
        "faltante": resto - sin_lote,
    }


async def alertas_lotes(pool: asyncpg.Pool, *, dias: int) -> dict[str, Any]:
    """Lotes con existencia que vencen dentro de `dias` días y los ya
    vencidos (los bloqueados no se alertan: ya se apartaron)."""
    por_vencer = await listar_lotes(
        pool, dias_alerta=dias, estado="por_vencer", con_existencia=True, limite=500
    )
    vencidos = await listar_lotes(pool, dias_alerta=dias, estado="vencido", con_existencia=True, limite=500)
    return {"dias": dias, "por_vencer": por_vencer, "vencidos": vencidos}
