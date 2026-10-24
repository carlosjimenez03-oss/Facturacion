"""Ajustes de inventario (pantalla Ajustes): documentos con líneas de
entrada/salida por conteo físico, merma, daño, vencimiento u otro motivo.

Ciclo: 'borrador' (editable, sin efecto) -> `aplicar` ('aplicado': genera
los movimientos; las salidas validan existencia y salen al costo promedio,
las entradas recalculan el promedio) -> `anular` ('anulado': genera el
movimiento inverso de cada uno, en orden inverso). Un borrador se puede
borrar; uno aplicado o anulado, no.
"""
from __future__ import annotations

import uuid
from datetime import date
from typing import Any

import asyncpg

from . import stock
from .errores import AjusteNoEncontradoError, OperacionInvalidaError, ProductoNoEncontradoError
from .stock import con_id_str as _con_id_str

MOTIVOS_AJUSTE = {
    "conteo_fisico": "Conteo físico",
    "merma": "Merma",
    "dano": "Daño",
    "vencimiento": "Vencimiento",
    "otro": "Otro",
}


async def _ajuste_con_items(conn: asyncpg.Connection, ajuste_id: uuid.UUID) -> dict[str, Any] | None:
    fila = await conn.fetchrow(
        """
        SELECT aj.*, a.nombre AS almacen_nombre
        FROM inventario.ajustes aj
        JOIN inventario.almacenes a ON a.id = aj.almacen_id
        WHERE aj.id = $1
        """,
        ajuste_id,
    )
    if fila is None:
        return None
    items = await conn.fetch(
        """
        SELECT ai.*, p.sku AS producto_sku, p.nombre AS producto_nombre, l.numero AS lote_numero
        FROM inventario.ajuste_items ai
        JOIN inventario.productos p ON p.id = ai.producto_id
        LEFT JOIN inventario.lotes l ON l.id = ai.lote_id
        WHERE ai.ajuste_id = $1
        ORDER BY ai.linea
        """,
        ajuste_id,
    )
    ajuste = _con_id_str(fila)
    ajuste["items"] = [_con_id_str(item) for item in items]
    return ajuste


async def _validar_items(conn: asyncpg.Connection, items: list[dict[str, Any]]) -> None:
    if not items:
        raise OperacionInvalidaError("El ajuste no tiene líneas")
    for item in items:
        tipo = await conn.fetchval("SELECT tipo FROM inventario.productos WHERE id = $1", item["producto_id"])
        if tipo is None:
            raise ProductoNoEncontradoError(str(item["producto_id"]))
        if tipo == "servicio":
            raise OperacionInvalidaError("Un servicio no lleva existencia: no se ajusta")
        if item.get("lote_id") is not None:
            await stock.obtener_lote_de_producto(conn, item["lote_id"], item["producto_id"])


async def _reemplazar_items(conn: asyncpg.Connection, ajuste_id: uuid.UUID, items: list[dict[str, Any]]) -> None:
    await conn.execute("DELETE FROM inventario.ajuste_items WHERE ajuste_id = $1", ajuste_id)
    for linea, item in enumerate(items, start=1):
        await conn.execute(
            """
            INSERT INTO inventario.ajuste_items
                (ajuste_id, linea, producto_id, tipo, cantidad, costo_unitario, lote_id)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
            ajuste_id,
            linea,
            item["producto_id"],
            item["tipo"],
            item["cantidad"],
            item.get("costo_unitario") if item["tipo"] == "entrada" else None,
            item.get("lote_id"),
        )


async def crear_ajuste(
    pool: asyncpg.Pool,
    *,
    almacen_id: uuid.UUID | None,
    motivo: str,
    fecha: date | None,
    notas: str | None,
    items: list[dict[str, Any]],
    aplicar: bool = False,
) -> dict[str, Any]:
    """Crea el ajuste en 'borrador'; con `aplicar` además lo aplica en la
    misma transacción (si no se puede aplicar, tampoco se crea)."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            almacen = await stock.obtener_almacen(conn, almacen_id)
            await _validar_items(conn, items)
            correlativo = await conn.fetchval("SELECT nextval('inventario.ajustes_numero_seq')")
            ajuste_id = await conn.fetchval(
                """
                INSERT INTO inventario.ajustes (numero, fecha, almacen_id, motivo, notas)
                VALUES ($1, COALESCE($2, current_date), $3, $4, $5)
                RETURNING id
                """,
                f"AJ-{correlativo:04d}",
                fecha,
                almacen["id"],
                motivo,
                notas,
            )
            await _reemplazar_items(conn, ajuste_id, items)
            if aplicar:
                await _aplicar(conn, ajuste_id)
            ajuste = await _ajuste_con_items(conn, ajuste_id)
    assert ajuste is not None
    return ajuste


async def _bloquear_ajuste(conn: asyncpg.Connection, ajuste_id: uuid.UUID, estado_esperado: str) -> asyncpg.Record:
    actual = await conn.fetchrow("SELECT * FROM inventario.ajustes WHERE id = $1 FOR UPDATE", ajuste_id)
    if actual is None:
        raise AjusteNoEncontradoError(str(ajuste_id))
    if actual["estado"] != estado_esperado:
        raise OperacionInvalidaError(
            f"El ajuste {actual['numero']} está '{actual['estado']}' (se esperaba '{estado_esperado}')"
        )
    return actual


async def actualizar_ajuste(
    pool: asyncpg.Pool,
    ajuste_id: uuid.UUID,
    *,
    almacen_id: uuid.UUID | None,
    motivo: str,
    fecha: date | None,
    notas: str | None,
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    async with pool.acquire() as conn:
        async with conn.transaction():
            await _bloquear_ajuste(conn, ajuste_id, "borrador")
            almacen = await stock.obtener_almacen(conn, almacen_id)
            await _validar_items(conn, items)
            await conn.execute(
                """
                UPDATE inventario.ajustes
                SET almacen_id = $2, motivo = $3, fecha = COALESCE($4, fecha), notas = $5, actualizado_en = now()
                WHERE id = $1
                """,
                ajuste_id,
                almacen["id"],
                motivo,
                fecha,
                notas,
            )
            await _reemplazar_items(conn, ajuste_id, items)
            ajuste = await _ajuste_con_items(conn, ajuste_id)
    assert ajuste is not None
    return ajuste


async def eliminar_ajuste(pool: asyncpg.Pool, ajuste_id: uuid.UUID) -> None:
    async with pool.acquire() as conn:
        async with conn.transaction():
            await _bloquear_ajuste(conn, ajuste_id, "borrador")
            await conn.execute("DELETE FROM inventario.ajustes WHERE id = $1", ajuste_id)


async def listar_ajustes(
    pool: asyncpg.Pool,
    *,
    estado: str | None = None,
    almacen_id: uuid.UUID | None = None,
    producto_id: uuid.UUID | None = None,
    desde: date | None = None,
    hasta: date | None = None,
    limite: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    condiciones = []
    valores: list[Any] = []
    for columna, operador, valor in (
        ("aj.estado", "=", estado),
        ("aj.almacen_id", "=", almacen_id),
        ("aj.fecha", ">=", desde),
        ("aj.fecha", "<=", hasta),
    ):
        if valor is not None:
            valores.append(valor)
            condiciones.append(f"{columna} {operador} ${len(valores)}")
    if producto_id is not None:
        valores.append(producto_id)
        condiciones.append(
            f"EXISTS (SELECT 1 FROM inventario.ajuste_items ai WHERE ai.ajuste_id = aj.id AND ai.producto_id = ${len(valores)})"
        )
    where = f"WHERE {' AND '.join(condiciones)}" if condiciones else ""
    valores.extend([limite, offset])
    async with pool.acquire() as conn:
        ids = await conn.fetch(
            f"""
            SELECT aj.id FROM inventario.ajustes aj
            {where}
            ORDER BY aj.creado_en DESC
            LIMIT ${len(valores) - 1} OFFSET ${len(valores)}
            """,
            *valores,
        )
        resultado = []
        for fila in ids:
            ajuste = await _ajuste_con_items(conn, fila["id"])
            assert ajuste is not None
            resultado.append(ajuste)
    return resultado


async def obtener_ajuste(pool: asyncpg.Pool, ajuste_id: uuid.UUID) -> dict[str, Any]:
    async with pool.acquire() as conn:
        ajuste = await _ajuste_con_items(conn, ajuste_id)
    if ajuste is None:
        raise AjusteNoEncontradoError(str(ajuste_id))
    return ajuste


async def aplicar_ajuste(pool: asyncpg.Pool, ajuste_id: uuid.UUID) -> dict[str, Any]:
    """Genera los movimientos del ajuste. Si una salida no tiene existencia
    (almacén sin `permite_negativo`) lanza `StockInsuficienteError` y no se
    aplica ninguna línea."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            await _aplicar(conn, ajuste_id)
            ajuste = await _ajuste_con_items(conn, ajuste_id)
    assert ajuste is not None
    return ajuste


async def _aplicar(conn: asyncpg.Connection, ajuste_id: uuid.UUID) -> None:
    actual = await _bloquear_ajuste(conn, ajuste_id, "borrador")
    almacen = await stock.obtener_almacen(conn, actual["almacen_id"])
    items = await conn.fetch(
        "SELECT * FROM inventario.ajuste_items WHERE ajuste_id = $1 ORDER BY linea", ajuste_id
    )
    if not items:
        raise OperacionInvalidaError("El ajuste no tiene líneas")
    await stock.bloquear_productos(conn, [item["producto_id"] for item in items])
    motivo = f"Ajuste: {MOTIVOS_AJUSTE[actual['motivo']]}"
    for item in items:
        comunes = {
            "producto_id": item["producto_id"],
            "lote_id": item["lote_id"],
            "motivo": motivo,
            "referencia_tipo": "ajuste",
            "referencia_id": str(ajuste_id),
            "referencia_linea": str(item["id"]),
            "documento": actual["numero"],
        }
        if item["tipo"] == "entrada":
            await stock.registrar_entrada(
                conn,
                almacen_id=almacen["id"],
                cantidad=item["cantidad"],
                costo_unitario=item["costo_unitario"],
                **comunes,
            )
        else:
            await stock.registrar_salida(conn, almacen=almacen, cantidad=item["cantidad"], **comunes)
    await conn.execute(
        """
        UPDATE inventario.ajustes
        SET estado = 'aplicado', aplicado_en = now(), actualizado_en = now()
        WHERE id = $1
        """,
        ajuste_id,
    )


async def anular_ajuste(pool: asyncpg.Pool, ajuste_id: uuid.UUID) -> dict[str, Any]:
    """Revierte un ajuste aplicado con el movimiento inverso de cada uno de
    sus movimientos (mismo almacén y lote; `referencia_tipo`
    'anulacion_ajuste'). Revertir una entrada es una salida al costo con
    que entró (el promedio se recalcula sin ella) y valida existencia --
    del lote, o de lo "sin lote" si entró sin lote --: si esa mercancía ya
    se vendió o transfirió, la anulación falla con `StockInsuficienteError`
    y el ajuste queda aplicado."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            actual = await _bloquear_ajuste(conn, ajuste_id, "aplicado")
            movimientos = await conn.fetch(
                """
                SELECT * FROM inventario.movimientos
                WHERE referencia_tipo = 'ajuste' AND referencia_id = $1
                ORDER BY secuencia DESC
                """,
                str(ajuste_id),
            )
            await stock.bloquear_productos(conn, [mov["producto_id"] for mov in movimientos])
            almacenes: dict[uuid.UUID, asyncpg.Record] = {}
            motivo = f"Anulación del ajuste {actual['numero']}"
            for mov in movimientos:
                if mov["almacen_id"] not in almacenes:
                    almacenes[mov["almacen_id"]] = await stock.obtener_almacen(
                        conn, mov["almacen_id"], exigir_activo=False
                    )
                comunes = {
                    "producto_id": mov["producto_id"],
                    "lote_id": mov["lote_id"],
                    "motivo": motivo,
                    "referencia_tipo": "anulacion_ajuste",
                    "referencia_id": str(ajuste_id),
                    "referencia_linea": mov["referencia_linea"],
                    "documento": actual["numero"],
                }
                if mov["tipo"] == "salida":
                    await stock.registrar_entrada(
                        conn,
                        almacen_id=mov["almacen_id"],
                        cantidad=mov["cantidad"],
                        costo_unitario=mov["costo_unitario"],
                        **comunes,
                    )
                else:
                    # Sale al costo con que entró (no al promedio de hoy) y
                    # el promedio vuelve a ser el de antes de la entrada.
                    await stock.registrar_salida(
                        conn,
                        almacen=almacenes[mov["almacen_id"]],
                        cantidad=mov["cantidad"],
                        fefo=False,
                        costo_entrada=mov["costo_unitario"],
                        **comunes,
                    )
            await conn.execute(
                """
                UPDATE inventario.ajustes
                SET estado = 'anulado', anulado_en = now(), actualizado_en = now()
                WHERE id = $1
                """,
                ajuste_id,
            )
            ajuste = await _ajuste_con_items(conn, ajuste_id)
    assert ajuste is not None
    return ajuste

