"""Almacenes, existencias por almacén, transferencias entre almacenes y la
valuación del inventario (pantallas Almacenes y Productos)."""
from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import asyncpg

from . import stock
from .errores import (
    AlmacenNoEncontradoError,
    OperacionInvalidaError,
    ProductoNoEncontradoError,
    TransferenciaNoEncontradaError,
)
from .stock import con_id_str as _con_id_str

# Estadísticas de cada almacén. El valor usa GREATEST(cantidad, 0) igual que
# la valuación del IR-2 en erp_api (una existencia negativa no resta valor).
_SELECT_ALMACEN = """
    SELECT a.*,
           COALESCE(s.productos, 0) AS productos,
           COALESCE(s.cantidad, 0) AS cantidad,
           COALESCE(s.valor, 0) AS valor
    FROM inventario.almacenes a
    LEFT JOIN (
        SELECT e.almacen_id,
               count(*) FILTER (WHERE e.cantidad <> 0) AS productos,
               sum(e.cantidad) AS cantidad,
               sum(GREATEST(e.cantidad, 0) * p.costo_promedio) AS valor
        FROM inventario.existencias e
        JOIN inventario.productos p ON p.id = e.producto_id
        GROUP BY e.almacen_id
    ) s ON s.almacen_id = a.id
"""


def _almacen(fila: asyncpg.Record) -> dict[str, Any]:
    datos = _con_id_str(fila)
    datos["valor"] = Decimal(datos["valor"]).quantize(Decimal("0.01"))
    return datos


# --- Almacenes ---------------------------------------------------------

async def listar_almacenes(pool: asyncpg.Pool, *, activo: bool | None = None) -> list[dict[str, Any]]:
    valores: list[Any] = []
    where = ""
    if activo is not None:
        valores.append(activo)
        where = "WHERE a.activo = $1"
    async with pool.acquire() as conn:
        await stock.asegurar_almacen_principal(conn)
        filas = await conn.fetch(
            f"{_SELECT_ALMACEN} {where} ORDER BY a.es_principal DESC, a.nombre", *valores
        )
    return [_almacen(fila) for fila in filas]


async def _obtener(conn: asyncpg.Connection, almacen_id: uuid.UUID) -> dict[str, Any]:
    fila = await conn.fetchrow(f"{_SELECT_ALMACEN} WHERE a.id = $1", almacen_id)
    if fila is None:
        raise AlmacenNoEncontradoError(str(almacen_id))
    return _almacen(fila)


async def obtener_almacen(pool: asyncpg.Pool, almacen_id: uuid.UUID) -> dict[str, Any]:
    async with pool.acquire() as conn:
        return await _obtener(conn, almacen_id)


async def crear_almacen(
    pool: asyncpg.Pool, *, nombre: str, direccion: str | None, permite_negativo: bool = False
) -> dict[str, Any]:
    async with pool.acquire() as conn:
        fila = await conn.fetchrow(
            """
            INSERT INTO inventario.almacenes (nombre, direccion, permite_negativo)
            VALUES ($1, $2, $3)
            RETURNING id
            """,
            nombre,
            direccion,
            permite_negativo,
        )
        return await _obtener(conn, fila["id"])


async def actualizar_almacen(
    pool: asyncpg.Pool,
    almacen_id: uuid.UUID,
    *,
    nombre: str,
    direccion: str | None,
    permite_negativo: bool,
    activo: bool,
    es_principal: bool | None = None,
) -> dict[str, Any]:
    """`es_principal` True lo vuelve el principal (y deja de serlo el
    anterior); no se puede quitar la marca sin designar otro. No se inactiva
    el principal ni un almacén que todavía tiene existencia (la fila queda
    bloqueada FOR UPDATE: una transferencia, compra o ajuste en curso hacia
    este almacén termina antes y su existencia cuenta)."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Este almacén (y, si pasa a ser el principal, el principal
            # actual), en orden de id: las transferencias bloquean sus dos
            # almacenes FOR SHARE en ese mismo orden.
            ids = [almacen_id]
            if es_principal:
                ids.append(await conn.fetchval("SELECT id FROM inventario.almacenes WHERE es_principal"))
            filas = await conn.fetch(
                "SELECT * FROM inventario.almacenes WHERE id = ANY($1::uuid[]) ORDER BY id FOR UPDATE",
                [i for i in ids if i is not None],
            )
            actual = next((fila for fila in filas if fila["id"] == almacen_id), None)
            if actual is None:
                raise AlmacenNoEncontradoError(str(almacen_id))
            principal = actual["es_principal"] if es_principal is None else es_principal
            if actual["es_principal"] and not principal:
                raise OperacionInvalidaError(
                    "Para quitarle la marca de principal, designe otro almacén como principal"
                )
            if principal and not activo:
                raise OperacionInvalidaError("El almacén principal no se puede inactivar")
            if actual["activo"] and not activo:
                con_existencia = await conn.fetchval(
                    "SELECT count(*) FROM inventario.existencias WHERE almacen_id = $1 AND cantidad <> 0",
                    almacen_id,
                )
                if con_existencia:
                    raise OperacionInvalidaError(
                        f"El almacén todavía tiene existencia de {con_existencia} producto(s): "
                        "transfiérala antes de inactivarlo"
                    )
            if principal and not actual["es_principal"]:
                await conn.execute(
                    "UPDATE inventario.almacenes SET es_principal = false, actualizado_en = now() WHERE es_principal"
                )
            await conn.execute(
                """
                UPDATE inventario.almacenes
                SET nombre = $2, direccion = $3, permite_negativo = $4, activo = $5, es_principal = $6,
                    actualizado_en = now()
                WHERE id = $1
                """,
                almacen_id,
                nombre,
                direccion,
                permite_negativo,
                activo,
                principal,
            )
            return await _obtener(conn, almacen_id)


async def existencias_almacen(
    pool: asyncpg.Pool, almacen_id: uuid.UUID, *, q: str | None = None, incluir_cero: bool = False
) -> list[dict[str, Any]]:
    """Productos con existencia en el almacén (con `incluir_cero`, también
    los que tuvieron y quedaron en cero)."""
    valores: list[Any] = [almacen_id]
    condiciones = ["e.almacen_id = $1"]
    if not incluir_cero:
        condiciones.append("e.cantidad <> 0")
    if q:
        valores.append(f"%{q}%")
        condiciones.append(f"(p.nombre ILIKE ${len(valores)} OR p.sku ILIKE ${len(valores)})")
    async with pool.acquire() as conn:
        await _obtener(conn, almacen_id)
        filas = await conn.fetch(
            f"""
            SELECT p.id AS producto_id, p.sku, p.nombre, p.unidad_medida, p.categoria, p.stock_minimo,
                   e.cantidad, p.costo_promedio,
                   round(GREATEST(e.cantidad, 0) * p.costo_promedio, 2) AS valor
            FROM inventario.existencias e
            JOIN inventario.productos p ON p.id = e.producto_id
            WHERE {' AND '.join(condiciones)}
            ORDER BY p.nombre
            """,
            *valores,
        )
    return [dict(fila) for fila in filas]


async def existencias_producto(pool: asyncpg.Pool, producto_id: uuid.UUID, *, dias_alerta: int) -> dict[str, Any]:
    """Existencia del producto en cada almacén, con el detalle por lote y
    lo que queda "sin lote"."""
    hoy = stock.hoy_rd()
    async with pool.acquire() as conn:
        producto = await conn.fetchrow(
            "SELECT id, stock_actual, costo_promedio FROM inventario.productos WHERE id = $1", producto_id
        )
        if producto is None:
            raise ProductoNoEncontradoError(str(producto_id))
        almacenes = await conn.fetch(
            """
            SELECT a.id AS almacen_id, a.nombre AS almacen_nombre, a.activo, e.cantidad
            FROM inventario.existencias e
            JOIN inventario.almacenes a ON a.id = e.almacen_id
            WHERE e.producto_id = $1
            ORDER BY a.es_principal DESC, a.nombre
            """,
            producto_id,
        )
        lotes = await conn.fetch(
            """
            SELECT el.almacen_id, l.id AS lote_id, l.numero, l.fecha_vencimiento, l.bloqueado, el.cantidad
            FROM inventario.existencias_lote el
            JOIN inventario.lotes l ON l.id = el.lote_id
            WHERE l.producto_id = $1 AND el.cantidad <> 0
            ORDER BY l.fecha_vencimiento NULLS LAST, l.numero
            """,
            producto_id,
        )
    por_almacen = []
    for almacen in almacenes:
        suyos = [
            {
                "lote_id": lote["lote_id"],
                "numero": lote["numero"],
                "fecha_vencimiento": lote["fecha_vencimiento"],
                "cantidad": lote["cantidad"],
                "estado": stock.estado_lote(
                    fecha_vencimiento=lote["fecha_vencimiento"],
                    bloqueado=lote["bloqueado"],
                    hoy=hoy,
                    dias_alerta=dias_alerta,
                ),
            }
            for lote in lotes
            if lote["almacen_id"] == almacen["almacen_id"]
        ]
        por_almacen.append(
            {
                "almacen_id": almacen["almacen_id"],
                "almacen_nombre": almacen["almacen_nombre"],
                "activo": almacen["activo"],
                "cantidad": almacen["cantidad"],
                "sin_lote": almacen["cantidad"] - sum((lote["cantidad"] for lote in suyos), Decimal("0")),
                "valor": (max(almacen["cantidad"], Decimal("0")) * producto["costo_promedio"]).quantize(
                    Decimal("0.01")
                ),
                "lotes": suyos,
            }
        )
    return {
        "producto_id": producto["id"],
        "stock_actual": producto["stock_actual"],
        "costo_promedio": producto["costo_promedio"],
        "almacenes": por_almacen,
    }


# --- Transferencias ---------------------------------------------------------

async def _transferencia_con_items(conn: asyncpg.Connection, transferencia_id: uuid.UUID) -> dict[str, Any] | None:
    fila = await conn.fetchrow("SELECT * FROM inventario.transferencias WHERE id = $1", transferencia_id)
    if fila is None:
        return None
    items = await conn.fetch(
        """
        SELECT ti.*, p.sku AS producto_sku, p.nombre AS producto_nombre
        FROM inventario.transferencia_items ti
        JOIN inventario.productos p ON p.id = ti.producto_id
        WHERE ti.transferencia_id = $1
        ORDER BY ti.linea
        """,
        transferencia_id,
    )
    transferencia = _con_id_str(fila)
    transferencia["items"] = [_con_id_str(item) for item in items]
    return transferencia


async def crear_transferencia(
    pool: asyncpg.Pool,
    *,
    almacen_origen_id: uuid.UUID,
    almacen_destino_id: uuid.UUID,
    notas: str | None,
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    """Mueve mercancía de un almacén a otro y la deja aplicada. Por cada
    línea: salida del origen (valida existencia salvo que el origen
    `permite_negativo`; sin lote y producto con lotes -> FEFO) y entrada al
    destino en los MISMOS lotes, al costo promedio vigente. No cambia el
    total del producto ni su costo. Una sola transacción: si una línea no
    tiene existencia no se mueve nada."""
    if almacen_origen_id == almacen_destino_id:
        raise OperacionInvalidaError("El almacén de origen y el de destino deben ser distintos")
    if not items:
        raise OperacionInvalidaError("La transferencia no tiene líneas")
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Los dos activos y bloqueados (FOR SHARE) en orden de id, para
            # que no se inactiven a mitad de la transferencia.
            almacenes = {
                almacen_id: await stock.obtener_almacen(conn, almacen_id)
                for almacen_id in sorted((almacen_origen_id, almacen_destino_id))
            }
            origen = almacenes[almacen_origen_id]
            destino = almacenes[almacen_destino_id]
            correlativo = await conn.fetchval("SELECT nextval('inventario.transferencias_numero_seq')")
            numero = f"TR-{correlativo:04d}"
            fila = await conn.fetchrow(
                """
                INSERT INTO inventario.transferencias (numero, almacen_origen_id, almacen_destino_id, notas)
                VALUES ($1, $2, $3, $4)
                RETURNING id
                """,
                numero,
                origen["id"],
                destino["id"],
                notas,
            )
            transferencia_id = fila["id"]
            await stock.bloquear_productos(conn, [item["producto_id"] for item in items])
            for linea, item in enumerate(items, start=1):
                await conn.execute(
                    """
                    INSERT INTO inventario.transferencia_items
                        (transferencia_id, linea, producto_id, cantidad, lote_id)
                    VALUES ($1, $2, $3, $4, $5)
                    """,
                    transferencia_id,
                    linea,
                    item["producto_id"],
                    item["cantidad"],
                    item.get("lote_id"),
                )
                comunes = {
                    "producto_id": item["producto_id"],
                    "referencia_tipo": "transferencia",
                    "referencia_id": str(transferencia_id),
                    "documento": numero,
                    "es_transferencia": True,
                }
                salidas = await stock.registrar_salida(
                    conn,
                    almacen=origen,
                    cantidad=item["cantidad"],
                    lote_id=item.get("lote_id"),
                    motivo=f"Transferencia a {destino['nombre']}",
                    **comunes,
                )
                for salida in salidas:
                    await stock.registrar_entrada(
                        conn,
                        almacen_id=destino["id"],
                        cantidad=salida["cantidad"],
                        costo_unitario=salida["costo_unitario"],
                        lote_id=salida["lote_id"],
                        motivo=f"Transferencia desde {origen['nombre']}",
                        **comunes,
                    )
            transferencia = await _transferencia_con_items(conn, transferencia_id)
    assert transferencia is not None
    return transferencia


async def listar_transferencias(
    pool: asyncpg.Pool, *, almacen_id: uuid.UUID | None = None, limite: int = 50, offset: int = 0
) -> list[dict[str, Any]]:
    valores: list[Any] = []
    where = ""
    if almacen_id is not None:
        valores.append(almacen_id)
        where = "WHERE almacen_origen_id = $1 OR almacen_destino_id = $1"
    valores.extend([limite, offset])
    async with pool.acquire() as conn:
        filas = await conn.fetch(
            f"""
            SELECT * FROM inventario.transferencias
            {where}
            ORDER BY creado_en DESC
            LIMIT ${len(valores) - 1} OFFSET ${len(valores)}
            """,
            *valores,
        )
        resultado = []
        for fila in filas:
            transferencia = await _transferencia_con_items(conn, fila["id"])
            assert transferencia is not None
            resultado.append(transferencia)
    return resultado


async def obtener_transferencia(pool: asyncpg.Pool, transferencia_id: uuid.UUID) -> dict[str, Any]:
    async with pool.acquire() as conn:
        transferencia = await _transferencia_con_items(conn, transferencia_id)
    if transferencia is None:
        raise TransferenciaNoEncontradaError(str(transferencia_id))
    return transferencia


# --- Valuación ---------------------------------------------------------

async def valuacion(pool: asyncpg.Pool) -> dict[str, Any]:
    """Valor del inventario (existencia × costo promedio) total, por almacén
    y por categoría, y los contadores de alertas de stock. `valor_total` es
    exactamente lo que el IR-2 de erp_api toma como inventario final
    (`sum(GREATEST(stock_actual, 0) * costo_promedio)` de productos), igual
    que `por_categoria`. `por_almacen` aplica el GREATEST a la existencia
    de cada almacén: con algún almacén en negativo, la suma por almacén es
    MAYOR que `valor_total` (ver docs/INVENTARIO.md, "Valuación con
    existencia negativa")."""
    async with pool.acquire() as conn:
        totales = await conn.fetchrow(
            """
            SELECT COALESCE(sum(GREATEST(stock_actual, 0) * costo_promedio), 0) AS valor_total,
                   count(*) FILTER (WHERE tipo = 'bien' AND activo) AS productos,
                   count(*) FILTER (WHERE tipo = 'bien' AND activo AND stock_actual > 0) AS con_existencia,
                   count(*) FILTER (WHERE tipo = 'bien' AND activo AND stock_actual <= 0) AS sin_existencia,
                   count(*) FILTER (
                       WHERE tipo = 'bien' AND activo AND stock_actual > 0
                           AND stock_minimo > 0 AND stock_actual <= stock_minimo
                   ) AS bajo_minimo
            FROM inventario.productos
            """
        )
        almacenes = await conn.fetch(f"{_SELECT_ALMACEN} ORDER BY a.es_principal DESC, a.nombre")
        categorias = await conn.fetch(
            """
            SELECT categoria,
                   count(*) FILTER (WHERE stock_actual <> 0) AS productos,
                   sum(stock_actual) AS cantidad,
                   sum(GREATEST(stock_actual, 0) * costo_promedio) AS valor
            FROM inventario.productos
            WHERE tipo = 'bien'
            GROUP BY categoria
            ORDER BY categoria NULLS LAST
            """
        )
    centavos = Decimal("0.01")
    return {
        "valor_total": Decimal(totales["valor_total"]).quantize(centavos),
        "productos": totales["productos"],
        "con_existencia": totales["con_existencia"],
        "sin_existencia": totales["sin_existencia"],
        "bajo_minimo": totales["bajo_minimo"],
        "por_almacen": [
            {
                "almacen_id": fila["id"],
                "nombre": fila["nombre"],
                "activo": fila["activo"],
                "productos": fila["productos"],
                "cantidad": fila["cantidad"],
                "valor": Decimal(fila["valor"]).quantize(centavos),
            }
            for fila in almacenes
        ],
        "por_categoria": [
            {
                "categoria": fila["categoria"],
                "productos": fila["productos"],
                "cantidad": fila["cantidad"],
                "valor": Decimal(fila["valor"]).quantize(centavos),
            }
            for fila in categorias
        ],
    }
