"""Acceso a Postgres para el esquema `inventario` (productos, proveedores,
compras, movimientos/kardex).

A diferencia de `libs.ecf_common.db` (auditoría best-effort, nunca lanza),
este repositorio SÍ requiere una base de datos disponible -- igual que
`services/facturas_api/app/repository.py`.
"""
from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import asyncpg


class ProductoNoEncontradoError(Exception):
    pass


class ProveedorNoEncontradoError(Exception):
    pass


class CompraNoEncontradaError(Exception):
    pass


class CompraEstadoInvalidoError(Exception):
    """La compra ya está confirmada (o en otro estado) y no admite la operación pedida."""


def _con_id_str(fila: asyncpg.Record) -> dict[str, Any]:
    datos = dict(fila)
    datos["id"] = str(datos["id"])
    return datos


# --- Productos ---------------------------------------------------------

async def crear_producto(
    pool: asyncpg.Pool,
    *,
    sku: str,
    nombre: str,
    descripcion: str | None,
    unidad_medida: str | None,
    precio_venta: Decimal,
    stock_inicial: Decimal,
) -> dict[str, Any]:
    async with pool.acquire() as conn:
        async with conn.transaction():
            fila = await conn.fetchrow(
                """
                INSERT INTO inventario.productos
                    (sku, nombre, descripcion, unidad_medida, precio_venta, stock_actual)
                VALUES ($1, $2, $3, $4, $5, $6)
                RETURNING *
                """,
                sku,
                nombre,
                descripcion,
                unidad_medida,
                precio_venta,
                stock_inicial,
            )
            if stock_inicial > 0:
                await conn.execute(
                    """
                    INSERT INTO inventario.movimientos
                        (producto_id, tipo, cantidad, motivo, referencia_tipo)
                    VALUES ($1, 'entrada', $2, 'Stock inicial', 'ajuste')
                    """,
                    fila["id"],
                    stock_inicial,
                )
    return _con_id_str(fila)


async def listar_productos(
    pool: asyncpg.Pool,
    *,
    q: str | None = None,
    activo: bool | None = None,
    limite: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    condiciones = []
    valores: list[Any] = []
    if q:
        valores.append(f"%{q}%")
        condiciones.append(f"(nombre ILIKE ${len(valores)} OR sku ILIKE ${len(valores)})")
    if activo is not None:
        valores.append(activo)
        condiciones.append(f"activo = ${len(valores)}")
    where = f"WHERE {' AND '.join(condiciones)}" if condiciones else ""
    valores.extend([limite, offset])
    query = f"""
        SELECT * FROM inventario.productos
        {where}
        ORDER BY nombre
        LIMIT ${len(valores) - 1} OFFSET ${len(valores)}
    """
    async with pool.acquire() as conn:
        filas = await conn.fetch(query, *valores)
    return [_con_id_str(fila) for fila in filas]


async def obtener_producto(pool: asyncpg.Pool, producto_id: uuid.UUID) -> dict[str, Any]:
    async with pool.acquire() as conn:
        fila = await conn.fetchrow("SELECT * FROM inventario.productos WHERE id = $1", producto_id)
    if fila is None:
        raise ProductoNoEncontradoError(str(producto_id))
    return _con_id_str(fila)


async def actualizar_producto(
    pool: asyncpg.Pool,
    producto_id: uuid.UUID,
    *,
    sku: str,
    nombre: str,
    descripcion: str | None,
    unidad_medida: str | None,
    precio_venta: Decimal,
    activo: bool,
) -> dict[str, Any]:
    async with pool.acquire() as conn:
        fila = await conn.fetchrow(
            """
            UPDATE inventario.productos
            SET sku = $2, nombre = $3, descripcion = $4, unidad_medida = $5,
                precio_venta = $6, activo = $7, actualizado_en = now()
            WHERE id = $1
            RETURNING *
            """,
            producto_id,
            sku,
            nombre,
            descripcion,
            unidad_medida,
            precio_venta,
            activo,
        )
    if fila is None:
        raise ProductoNoEncontradoError(str(producto_id))
    return _con_id_str(fila)


# --- Proveedores ---------------------------------------------------------

async def crear_proveedor(
    pool: asyncpg.Pool,
    *,
    nombre: str,
    rnc: str | None,
    telefono: str | None,
    correo: str | None,
    direccion: str | None,
) -> dict[str, Any]:
    async with pool.acquire() as conn:
        fila = await conn.fetchrow(
            """
            INSERT INTO inventario.proveedores (nombre, rnc, telefono, correo, direccion)
            VALUES ($1, $2, $3, $4, $5)
            RETURNING *
            """,
            nombre,
            rnc,
            telefono,
            correo,
            direccion,
        )
    return _con_id_str(fila)


async def listar_proveedores(
    pool: asyncpg.Pool, *, activo: bool | None = None, limite: int = 50, offset: int = 0
) -> list[dict[str, Any]]:
    condiciones = []
    valores: list[Any] = []
    if activo is not None:
        valores.append(activo)
        condiciones.append(f"activo = ${len(valores)}")
    where = f"WHERE {' AND '.join(condiciones)}" if condiciones else ""
    valores.extend([limite, offset])
    query = f"""
        SELECT * FROM inventario.proveedores
        {where}
        ORDER BY nombre
        LIMIT ${len(valores) - 1} OFFSET ${len(valores)}
    """
    async with pool.acquire() as conn:
        filas = await conn.fetch(query, *valores)
    return [_con_id_str(fila) for fila in filas]


async def obtener_proveedor(pool: asyncpg.Pool, proveedor_id: uuid.UUID) -> dict[str, Any]:
    async with pool.acquire() as conn:
        fila = await conn.fetchrow("SELECT * FROM inventario.proveedores WHERE id = $1", proveedor_id)
    if fila is None:
        raise ProveedorNoEncontradoError(str(proveedor_id))
    return _con_id_str(fila)


async def actualizar_proveedor(
    pool: asyncpg.Pool,
    proveedor_id: uuid.UUID,
    *,
    nombre: str,
    rnc: str | None,
    telefono: str | None,
    correo: str | None,
    direccion: str | None,
    activo: bool,
) -> dict[str, Any]:
    async with pool.acquire() as conn:
        fila = await conn.fetchrow(
            """
            UPDATE inventario.proveedores
            SET nombre = $2, rnc = $3, telefono = $4, correo = $5, direccion = $6,
                activo = $7, actualizado_en = now()
            WHERE id = $1
            RETURNING *
            """,
            proveedor_id,
            nombre,
            rnc,
            telefono,
            correo,
            direccion,
            activo,
        )
    if fila is None:
        raise ProveedorNoEncontradoError(str(proveedor_id))
    return _con_id_str(fila)


# --- Compras ---------------------------------------------------------

async def _compra_con_items(conn: asyncpg.Connection, compra_id: uuid.UUID) -> dict[str, Any] | None:
    fila = await conn.fetchrow("SELECT * FROM inventario.compras WHERE id = $1", compra_id)
    if fila is None:
        return None
    items = await conn.fetch(
        "SELECT * FROM inventario.compra_items WHERE compra_id = $1 ORDER BY id", compra_id
    )
    compra = _con_id_str(fila)
    compra["items"] = [_con_id_str(item) for item in items]
    return compra


async def crear_compra(
    pool: asyncpg.Pool,
    *,
    proveedor_id: uuid.UUID | None,
    numero_documento: str | None,
    notas: str | None,
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    async with pool.acquire() as conn:
        async with conn.transaction():
            fila = await conn.fetchrow(
                """
                INSERT INTO inventario.compras (proveedor_id, numero_documento, notas)
                VALUES ($1, $2, $3)
                RETURNING id
                """,
                proveedor_id,
                numero_documento,
                notas,
            )
            compra_id = fila["id"]
            await _reemplazar_items_compra(conn, compra_id, items)
            compra = await _compra_con_items(conn, compra_id)
    assert compra is not None
    return compra


async def _reemplazar_items_compra(
    conn: asyncpg.Connection, compra_id: uuid.UUID, items: list[dict[str, Any]]
) -> None:
    await conn.execute("DELETE FROM inventario.compra_items WHERE compra_id = $1", compra_id)
    for item in items:
        await conn.execute(
            """
            INSERT INTO inventario.compra_items (compra_id, producto_id, cantidad, costo_unitario)
            VALUES ($1, $2, $3, $4)
            """,
            compra_id,
            item["producto_id"],
            item["cantidad"],
            item["costo_unitario"],
        )


async def listar_compras(
    pool: asyncpg.Pool, *, estado: str | None = None, limite: int = 50, offset: int = 0
) -> list[dict[str, Any]]:
    condiciones = []
    valores: list[Any] = []
    if estado is not None:
        valores.append(estado)
        condiciones.append(f"estado = ${len(valores)}")
    where = f"WHERE {' AND '.join(condiciones)}" if condiciones else ""
    valores.extend([limite, offset])
    query = f"""
        SELECT * FROM inventario.compras
        {where}
        ORDER BY creado_en DESC
        LIMIT ${len(valores) - 1} OFFSET ${len(valores)}
    """
    async with pool.acquire() as conn:
        filas = await conn.fetch(query, *valores)
    return [_con_id_str(fila) | {"items": []} for fila in filas]


async def obtener_compra(pool: asyncpg.Pool, compra_id: uuid.UUID) -> dict[str, Any]:
    async with pool.acquire() as conn:
        compra = await _compra_con_items(conn, compra_id)
    if compra is None:
        raise CompraNoEncontradaError(str(compra_id))
    return compra


async def actualizar_compra(
    pool: asyncpg.Pool,
    compra_id: uuid.UUID,
    *,
    proveedor_id: uuid.UUID | None,
    numero_documento: str | None,
    notas: str | None,
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    async with pool.acquire() as conn:
        async with conn.transaction():
            actual = await conn.fetchrow(
                "SELECT estado FROM inventario.compras WHERE id = $1 FOR UPDATE", compra_id
            )
            if actual is None:
                raise CompraNoEncontradaError(str(compra_id))
            if actual["estado"] != "borrador":
                raise CompraEstadoInvalidoError(
                    f"Solo se pueden editar compras en estado 'borrador' (estado actual: {actual['estado']})"
                )
            await conn.execute(
                """
                UPDATE inventario.compras
                SET proveedor_id = $2, numero_documento = $3, notas = $4, actualizado_en = now()
                WHERE id = $1
                """,
                compra_id,
                proveedor_id,
                numero_documento,
                notas,
            )
            await _reemplazar_items_compra(conn, compra_id, items)
            compra = await _compra_con_items(conn, compra_id)
    assert compra is not None
    return compra


async def eliminar_compra(pool: asyncpg.Pool, compra_id: uuid.UUID) -> None:
    async with pool.acquire() as conn:
        resultado = await conn.execute(
            "DELETE FROM inventario.compras WHERE id = $1 AND estado = 'borrador'", compra_id
        )
    if resultado == "DELETE 0":
        raise CompraNoEncontradaError(str(compra_id))


async def confirmar_compra(pool: asyncpg.Pool, compra_id: uuid.UUID) -> dict[str, Any]:
    """Confirma una compra en 'borrador': genera un movimiento 'entrada' por
    cada línea, sube `stock_actual` y recalcula `costo_promedio` (promedio
    ponderado) de cada producto involucrado. Todo en una sola transacción --
    o se aplica completo o no se aplica nada."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            actual = await conn.fetchrow(
                "SELECT estado FROM inventario.compras WHERE id = $1 FOR UPDATE", compra_id
            )
            if actual is None:
                raise CompraNoEncontradaError(str(compra_id))
            if actual["estado"] != "borrador":
                raise CompraEstadoInvalidoError(
                    f"La compra ya está en estado '{actual['estado']}'"
                )

            items = await conn.fetch(
                "SELECT * FROM inventario.compra_items WHERE compra_id = $1 ORDER BY id", compra_id
            )
            for item in items:
                producto = await conn.fetchrow(
                    "SELECT stock_actual, costo_promedio FROM inventario.productos WHERE id = $1 FOR UPDATE",
                    item["producto_id"],
                )
                if producto is None:
                    raise ProductoNoEncontradoError(str(item["producto_id"]))

                stock_previo: Decimal = producto["stock_actual"]
                costo_previo: Decimal = producto["costo_promedio"]
                cantidad: Decimal = item["cantidad"]
                costo_unitario: Decimal = item["costo_unitario"]

                nuevo_stock = stock_previo + cantidad
                nuevo_costo_promedio = (
                    (stock_previo * costo_previo + cantidad * costo_unitario) / nuevo_stock
                    if nuevo_stock > 0
                    else costo_unitario
                )

                await conn.execute(
                    """
                    UPDATE inventario.productos
                    SET stock_actual = $2, costo_promedio = $3, actualizado_en = now()
                    WHERE id = $1
                    """,
                    item["producto_id"],
                    nuevo_stock,
                    nuevo_costo_promedio,
                )
                await conn.execute(
                    """
                    INSERT INTO inventario.movimientos
                        (producto_id, tipo, cantidad, costo_unitario, motivo, referencia_tipo, referencia_id)
                    VALUES ($1, 'entrada', $2, $3, 'Compra confirmada', 'compra', $4)
                    """,
                    item["producto_id"],
                    cantidad,
                    costo_unitario,
                    str(compra_id),
                )

            await conn.execute(
                """
                UPDATE inventario.compras
                SET estado = 'confirmada', confirmada_en = now(), actualizado_en = now()
                WHERE id = $1
                """,
                compra_id,
            )
            compra = await _compra_con_items(conn, compra_id)
    assert compra is not None
    return compra


# --- Movimientos (kardex) ---------------------------------------------------------

async def listar_movimientos(
    pool: asyncpg.Pool,
    *,
    producto_id: uuid.UUID | None = None,
    tipo: str | None = None,
    limite: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    condiciones = []
    valores: list[Any] = []
    if producto_id is not None:
        valores.append(producto_id)
        condiciones.append(f"producto_id = ${len(valores)}")
    if tipo is not None:
        valores.append(tipo)
        condiciones.append(f"tipo = ${len(valores)}")
    where = f"WHERE {' AND '.join(condiciones)}" if condiciones else ""
    valores.extend([limite, offset])
    query = f"""
        SELECT * FROM inventario.movimientos
        {where}
        ORDER BY creado_en DESC
        LIMIT ${len(valores) - 1} OFFSET ${len(valores)}
    """
    async with pool.acquire() as conn:
        filas = await conn.fetch(query, *valores)
    return [_con_id_str(fila) for fila in filas]


# --- Integración con ventas (facturas_api) ---------------------------------------------------------

async def validar_disponibilidad(
    pool: asyncpg.Pool, items: list[dict[str, Any]]
) -> tuple[bool, list[dict[str, Any]]]:
    faltantes: list[dict[str, Any]] = []
    async with pool.acquire() as conn:
        for item in items:
            producto = await conn.fetchrow(
                "SELECT nombre, stock_actual FROM inventario.productos WHERE id = $1",
                item["producto_id"],
            )
            if producto is None:
                raise ProductoNoEncontradoError(str(item["producto_id"]))
            if producto["stock_actual"] < item["cantidad"]:
                faltantes.append(
                    {
                        "producto_id": str(item["producto_id"]),
                        "nombre": producto["nombre"],
                        "solicitado": item["cantidad"],
                        "disponible": producto["stock_actual"],
                    }
                )
    return (len(faltantes) == 0, faltantes)


async def registrar_salida_por_factura(
    pool: asyncpg.Pool, factura_id: str, items: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Registra la salida de mercancía de una factura ya enviada exitosamente
    a la DGII. Idempotente por (factura_id, producto_id): si ya existe un
    movimiento de salida para ese par (reintento de facturas_api), no se
    vuelve a descontar stock -- se devuelve el movimiento ya existente."""
    movimientos: list[dict[str, Any]] = []
    async with pool.acquire() as conn:
        async with conn.transaction():
            for item in items:
                nuevo = await conn.fetchrow(
                    """
                    INSERT INTO inventario.movimientos
                        (producto_id, tipo, cantidad, motivo, referencia_tipo, referencia_id)
                    VALUES ($1, 'salida', $2, 'Venta (factura enviada a la DGII)', 'factura', $3)
                    ON CONFLICT (referencia_id, producto_id) WHERE referencia_tipo = 'factura' AND tipo = 'salida'
                    DO NOTHING
                    RETURNING *
                    """,
                    item["producto_id"],
                    item["cantidad"],
                    factura_id,
                )
                if nuevo is not None:
                    await conn.execute(
                        """
                        UPDATE inventario.productos
                        SET stock_actual = stock_actual - $2, actualizado_en = now()
                        WHERE id = $1
                        """,
                        item["producto_id"],
                        item["cantidad"],
                    )
                    movimientos.append(_con_id_str(nuevo))
                else:
                    existente = await conn.fetchrow(
                        """
                        SELECT * FROM inventario.movimientos
                        WHERE referencia_tipo = 'factura' AND referencia_id = $1 AND producto_id = $2
                            AND tipo = 'salida'
                        """,
                        factura_id,
                        item["producto_id"],
                    )
                    if existente is not None:
                        movimientos.append(_con_id_str(existente))
    return movimientos
