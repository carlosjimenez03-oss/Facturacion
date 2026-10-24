"""Acceso a Postgres para el esquema `inventario` (productos, proveedores,
compras, movimientos/kardex y la integración con ventas).

A diferencia de `libs.ecf_common.db` (auditoría best-effort, nunca lanza),
este repositorio SÍ requiere una base de datos disponible -- igual que
`services/facturas_api/app/repository.py`.

Todo cambio de existencia pasa por `stock.registrar_entrada` /
`stock.registrar_salida` (existencias por almacén y lote, total en
`productos.stock_actual`, costo promedio). Almacenes, lotes, ajustes,
transferencias, Kardex y valuación viven en sus propios módulos
(`almacenes_repository`, `lotes_repository`, `ajustes_repository`,
`kardex`).
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

import asyncpg

from . import stock
from .errores import (  # noqa: F401 -- re-exportados: main.py y los tests los usan como repository.X
    AlmacenNoEncontradoError,
    CompraEstadoInvalidoError,
    CompraNoEncontradaError,
    LoteNoEncontradoError,
    OperacionInvalidaError,
    ProductoNoEncontradoError,
    ProveedorNoEncontradoError,
    StockInsuficienteError,
)
from .stock import con_id_str as _con_id_str

# Clave fija (primer argumento) del advisory lock por factura de
# `registrar_salida_por_factura`; el segundo es hashtext(factura_id).
_LOCK_SALIDA_FACTURA = 7_004_002


def rango_fechas(desde: date | None, hasta: date | None) -> tuple[datetime | None, datetime | None]:
    """[desde 00:00, hasta+1 00:00) en hora de RD, para filtrar `creado_en`
    (timestamptz) por días calendario sin castear la columna."""
    inicio = datetime.combine(desde, time.min, tzinfo=stock.ZONA_RD) if desde else None
    fin = datetime.combine(hasta + timedelta(days=1), time.min, tzinfo=stock.ZONA_RD) if hasta else None
    return inicio, fin


# --- Productos ---------------------------------------------------------

def estado_stock(producto: dict[str, Any]) -> str:
    """'no_aplica' (servicio) | 'sin_existencia' | 'bajo_minimo' | 'disponible'."""
    if producto.get("tipo") == "servicio":
        return "no_aplica"
    existencia = producto["stock_actual"]
    minimo = producto.get("stock_minimo") or Decimal("0")
    if existencia <= 0:
        return "sin_existencia"
    if minimo > 0 and existencia <= minimo:
        return "bajo_minimo"
    return "disponible"


def normalizar_codigo_barras(valor: str | None) -> str | None:
    """Sin espacios alrededor; vacío = sin código (NULL). Si no, el índice
    único de `codigo_barras` solo admitiría un producto con ''."""
    if valor is None:
        return None
    return valor.strip() or None


def _producto(fila: asyncpg.Record) -> dict[str, Any]:
    datos = _con_id_str(fila)
    datos["estado_stock"] = estado_stock(datos)
    return datos


async def crear_producto(
    pool: asyncpg.Pool,
    *,
    sku: str,
    nombre: str,
    descripcion: str | None,
    unidad_medida: str | None,
    precio_venta: Decimal,
    stock_inicial: Decimal,
    tipo: str = "bien",
    tasa_itbis: str = "18",
    codigo_barras: str | None = None,
    categoria: str | None = None,
    stock_minimo: Decimal = Decimal("0"),
    maneja_lotes: bool = False,
    costo_unitario_inicial: Decimal | None = None,
    almacen_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """Crea el producto y, si trae `stock_inicial`, lo ingresa al almacén
    indicado (o al principal) al `costo_unitario_inicial` (0 si no se da)."""
    if tipo == "servicio" and stock_inicial != 0:
        raise OperacionInvalidaError("Un servicio no lleva existencia: no admite stock inicial")
    async with pool.acquire() as conn:
        async with conn.transaction():
            fila = await conn.fetchrow(
                """
                INSERT INTO inventario.productos
                    (sku, nombre, descripcion, unidad_medida, precio_venta, tipo, tasa_itbis,
                     codigo_barras, categoria, stock_minimo, maneja_lotes)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                RETURNING *
                """,
                sku,
                nombre,
                descripcion,
                unidad_medida,
                precio_venta,
                tipo,
                tasa_itbis,
                normalizar_codigo_barras(codigo_barras),
                categoria,
                stock_minimo,
                maneja_lotes,
            )
            if stock_inicial > 0:
                almacen = await stock.obtener_almacen(conn, almacen_id)
                await stock.registrar_entrada(
                    conn,
                    producto_id=fila["id"],
                    almacen_id=almacen["id"],
                    cantidad=stock_inicial,
                    costo_unitario=costo_unitario_inicial or Decimal("0"),
                    motivo="Stock inicial",
                    referencia_tipo="ajuste",
                )
                fila = await conn.fetchrow("SELECT * FROM inventario.productos WHERE id = $1", fila["id"])
    return _producto(fila)


async def listar_productos(
    pool: asyncpg.Pool,
    *,
    q: str | None = None,
    activo: bool | None = None,
    categoria: str | None = None,
    tipo: str | None = None,
    alerta_stock: bool | None = None,
    limite: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    condiciones = []
    valores: list[Any] = []
    if q:
        valores.append(f"%{q}%")
        condiciones.append(
            f"(nombre ILIKE ${len(valores)} OR sku ILIKE ${len(valores)} OR codigo_barras ILIKE ${len(valores)})"
        )
    if activo is not None:
        valores.append(activo)
        condiciones.append(f"activo = ${len(valores)}")
    if categoria:
        valores.append(categoria)
        condiciones.append(f"categoria = ${len(valores)}")
    if tipo:
        valores.append(tipo)
        condiciones.append(f"tipo = ${len(valores)}")
    if alerta_stock is not None:
        alerta = "(tipo = 'bien' AND (stock_actual <= 0 OR (stock_minimo > 0 AND stock_actual <= stock_minimo)))"
        condiciones.append(alerta if alerta_stock else f"NOT {alerta}")
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
    return [_producto(fila) for fila in filas]


async def listar_categorias(pool: asyncpg.Pool) -> list[str]:
    async with pool.acquire() as conn:
        filas = await conn.fetch(
            "SELECT DISTINCT categoria FROM inventario.productos WHERE categoria IS NOT NULL ORDER BY categoria"
        )
    return [fila["categoria"] for fila in filas]


async def obtener_producto(pool: asyncpg.Pool, producto_id: uuid.UUID) -> dict[str, Any]:
    async with pool.acquire() as conn:
        fila = await conn.fetchrow("SELECT * FROM inventario.productos WHERE id = $1", producto_id)
    if fila is None:
        raise ProductoNoEncontradoError(str(producto_id))
    return _producto(fila)


# Campos de la Fase 4 que `actualizar_producto` solo cambia si vienen en el
# payload (un cliente de antes que no los manda no los borra).
CAMPOS_PRODUCTO_OPCIONALES = (
    "tipo",
    "tasa_itbis",
    "codigo_barras",
    "categoria",
    "stock_minimo",
    "maneja_lotes",
)


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
    **opcionales: Any,
) -> dict[str, Any]:
    """`opcionales`: solo los de `CAMPOS_PRODUCTO_OPCIONALES` que el cliente
    mandó. Pasar a 'servicio' exige existencia cero; apagar `maneja_lotes`,
    que ningún lote tenga existencia (si no, las salidas sin lote dejarían
    lo "sin lote" en negativo con los lotes en positivo)."""
    cambios: dict[str, Any] = {
        "sku": sku,
        "nombre": nombre,
        "descripcion": descripcion,
        "unidad_medida": unidad_medida,
        "precio_venta": precio_venta,
        "activo": activo,
    }
    cambios.update({k: v for k, v in opcionales.items() if k in CAMPOS_PRODUCTO_OPCIONALES})
    if "codigo_barras" in cambios:
        cambios["codigo_barras"] = normalizar_codigo_barras(cambios["codigo_barras"])
    asignaciones = ", ".join(f"{campo} = ${i}" for i, campo in enumerate(cambios, start=2))
    async with pool.acquire() as conn:
        async with conn.transaction():
            actual = await conn.fetchrow(
                "SELECT tipo, stock_actual, maneja_lotes FROM inventario.productos WHERE id = $1 FOR UPDATE",
                producto_id,
            )
            if actual is None:
                raise ProductoNoEncontradoError(str(producto_id))
            if cambios.get("maneja_lotes") is False and actual["maneja_lotes"]:
                en_lotes = await conn.fetchval(
                    """
                    SELECT count(DISTINCT l.id)
                    FROM inventario.existencias_lote el
                    JOIN inventario.lotes l ON l.id = el.lote_id
                    WHERE l.producto_id = $1 AND el.cantidad <> 0
                    """,
                    producto_id,
                )
                if en_lotes:
                    raise OperacionInvalidaError(
                        f"El producto tiene existencia en {en_lotes} lote(s): sáquela o ajústela "
                        "antes de dejar de manejar lotes"
                    )
            if cambios.get("tipo") == "servicio" and actual["tipo"] != "servicio" and actual["stock_actual"] != 0:
                raise OperacionInvalidaError(
                    "Solo se puede convertir en servicio un producto sin existencia "
                    f"(existencia actual: {actual['stock_actual']})"
                )
            fila = await conn.fetchrow(
                f"""
                UPDATE inventario.productos
                SET {asignaciones}, actualizado_en = now()
                WHERE id = $1
                RETURNING *
                """,
                producto_id,
                *cambios.values(),
            )
    return _producto(fila)


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


async def _validar_almacen_compra(conn: asyncpg.Connection, almacen_id: uuid.UUID | None) -> None:
    if almacen_id is not None:
        await stock.obtener_almacen(conn, almacen_id)


async def crear_compra(
    pool: asyncpg.Pool,
    *,
    proveedor_id: uuid.UUID | None,
    numero_documento: str | None,
    notas: str | None,
    items: list[dict[str, Any]],
    almacen_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    async with pool.acquire() as conn:
        async with conn.transaction():
            await _validar_almacen_compra(conn, almacen_id)
            fila = await conn.fetchrow(
                """
                INSERT INTO inventario.compras (proveedor_id, numero_documento, notas, almacen_id)
                VALUES ($1, $2, $3, $4)
                RETURNING id
                """,
                proveedor_id,
                numero_documento,
                notas,
                almacen_id,
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
            INSERT INTO inventario.compra_items
                (compra_id, producto_id, cantidad, costo_unitario, lote_numero, lote_vencimiento)
            VALUES ($1, $2, $3, $4, $5, $6)
            """,
            compra_id,
            item["producto_id"],
            item["cantidad"],
            item["costo_unitario"],
            item.get("lote_numero"),
            item.get("lote_vencimiento"),
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
    almacen_id: uuid.UUID | None = None,
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
            await _validar_almacen_compra(conn, almacen_id)
            await conn.execute(
                """
                UPDATE inventario.compras
                SET proveedor_id = $2, numero_documento = $3, notas = $4, almacen_id = $5,
                    actualizado_en = now()
                WHERE id = $1
                """,
                compra_id,
                proveedor_id,
                numero_documento,
                notas,
                almacen_id,
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
    """Confirma una compra en 'borrador': cada línea entra al almacén de la
    compra (o al principal) -- y a su lote, que se crea si no existe --,
    sube `stock_actual` y recalcula `costo_promedio` (promedio ponderado).
    Las líneas de servicios no mueven inventario. Todo en una sola
    transacción -- o se aplica completo o no se aplica nada."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            actual = await conn.fetchrow(
                "SELECT estado, almacen_id, numero_documento FROM inventario.compras WHERE id = $1 FOR UPDATE",
                compra_id,
            )
            if actual is None:
                raise CompraNoEncontradaError(str(compra_id))
            if actual["estado"] != "borrador":
                raise CompraEstadoInvalidoError(
                    f"La compra ya está en estado '{actual['estado']}'"
                )

            items = await conn.fetch(
                """
                SELECT ci.*, p.tipo AS producto_tipo
                FROM inventario.compra_items ci
                JOIN inventario.productos p ON p.id = ci.producto_id
                WHERE ci.compra_id = $1
                ORDER BY ci.id
                """,
                compra_id,
            )
            almacen = await stock.obtener_almacen(conn, actual["almacen_id"])
            bienes = [item for item in items if item["producto_tipo"] != "servicio"]
            await stock.bloquear_productos(conn, [item["producto_id"] for item in bienes])
            for item in bienes:
                lote_id = None
                if item["lote_numero"]:
                    lote_id = await stock.obtener_o_crear_lote(
                        conn, item["producto_id"], item["lote_numero"], item["lote_vencimiento"]
                    )
                await stock.registrar_entrada(
                    conn,
                    producto_id=item["producto_id"],
                    almacen_id=almacen["id"],
                    cantidad=item["cantidad"],
                    costo_unitario=item["costo_unitario"],
                    lote_id=lote_id,
                    motivo="Compra confirmada",
                    referencia_tipo="compra",
                    referencia_id=str(compra_id),
                    documento=actual["numero_documento"],
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
    almacen_id: uuid.UUID | None = None,
    lote_id: uuid.UUID | None = None,
    referencia_tipo: str | None = None,
    referencia_id: str | None = None,
    desde: date | None = None,
    hasta: date | None = None,
    limite: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    condiciones = []
    valores: list[Any] = []
    inicio, fin = rango_fechas(desde, hasta)
    for columna, operador, valor in (
        ("producto_id", "=", producto_id),
        ("tipo", "=", tipo),
        ("almacen_id", "=", almacen_id),
        ("lote_id", "=", lote_id),
        ("referencia_tipo", "=", referencia_tipo),
        ("referencia_id", "=", referencia_id),
        ("creado_en", ">=", inicio),
        ("creado_en", "<", fin),
    ):
        if valor is not None:
            valores.append(valor)
            condiciones.append(f"{columna} {operador} ${len(valores)}")
    where = f"WHERE {' AND '.join(condiciones)}" if condiciones else ""
    valores.extend([limite, offset])
    query = f"""
        SELECT * FROM inventario.movimientos
        {where}
        ORDER BY creado_en DESC, secuencia DESC
        LIMIT ${len(valores) - 1} OFFSET ${len(valores)}
    """
    async with pool.acquire() as conn:
        filas = await conn.fetch(query, *valores)
    return [_con_id_str(fila) for fila in filas]


# --- Integración con ventas (facturas_api) ---------------------------------------------------------

async def validar_disponibilidad(
    pool: asyncpg.Pool, items: list[dict[str, Any]]
) -> tuple[bool, list[dict[str, Any]]]:
    """¿Alcanza la existencia para facturar `items`? Cada ítem puede traer
    `almacen_id` (None = principal) y `lote_id`. Se suma lo pedido por
    (producto, almacén) y por lote antes de comparar (dos líneas del mismo
    producto cuentan juntas). Los servicios siempre están disponibles. Un
    lote vencido o bloqueado, o un almacén inactivo, se reporta como
    faltante (`motivo`). Lo pedido sin lote se compara con lo que se le
    puede dar: lotes vendibles (FEFO) + "sin lote" si el producto maneja
    lotes, o solo lo "sin lote" (existencia − lotes) si no -- igual que
    valida `stock.registrar_salida`.

    Un almacén o lote que no existe lanza `AlmacenNoEncontradoError` /
    `LoteNoEncontradoError` (422 en main.py); un lote de otro producto,
    `OperacionInvalidaError` (409)."""
    faltantes: list[dict[str, Any]] = []
    por_almacen: dict[tuple[uuid.UUID, uuid.UUID], Decimal] = {}
    sin_lote: dict[tuple[uuid.UUID, uuid.UUID], Decimal] = {}
    por_lote: dict[tuple[uuid.UUID, uuid.UUID, uuid.UUID], Decimal] = {}
    hoy = stock.hoy_rd()
    async with pool.acquire() as conn:
        productos: dict[uuid.UUID, asyncpg.Record] = {}
        almacenes: dict[uuid.UUID, asyncpg.Record] = {}
        for item in items:
            producto_id = item["producto_id"]
            if producto_id not in productos:
                producto = await conn.fetchrow(
                    "SELECT id, nombre, tipo, maneja_lotes FROM inventario.productos WHERE id = $1",
                    producto_id,
                )
                if producto is None:
                    raise ProductoNoEncontradoError(str(producto_id))
                productos[producto_id] = producto
            if productos[producto_id]["tipo"] == "servicio":
                continue
            almacen = await stock.obtener_almacen(conn, item.get("almacen_id"), exigir_activo=False)
            almacenes[almacen["id"]] = almacen
            clave = (producto_id, almacen["id"])
            por_almacen[clave] = por_almacen.get(clave, Decimal("0")) + item["cantidad"]
            if item.get("lote_id") is not None:
                clave_lote = (producto_id, almacen["id"], item["lote_id"])
                por_lote[clave_lote] = por_lote.get(clave_lote, Decimal("0")) + item["cantidad"]
            else:
                sin_lote[clave] = sin_lote.get(clave, Decimal("0")) + item["cantidad"]

        def _faltante(producto_id, almacen_id, solicitado, disponible, *, lote_id=None, motivo="stock"):
            faltantes.append(
                {
                    "producto_id": str(producto_id),
                    "nombre": productos[producto_id]["nombre"],
                    "solicitado": solicitado,
                    "disponible": disponible,
                    "almacen_id": str(almacen_id),
                    "lote_id": str(lote_id) if lote_id else None,
                    "motivo": motivo,
                }
            )

        for (producto_id, almacen_id), solicitado in por_almacen.items():
            if not almacenes[almacen_id]["activo"]:
                # No se vende desde un almacén inactivo (la salida por factura
                # no lo rechaza: para entonces el e-CF ya se envió).
                disponible = await stock.existencia(conn, producto_id, almacen_id)
                _faltante(producto_id, almacen_id, solicitado, disponible, motivo="almacen_inactivo")
                continue
            if almacenes[almacen_id]["permite_negativo"]:
                continue
            disponible = await stock.existencia(conn, producto_id, almacen_id)
            if disponible < solicitado:
                _faltante(producto_id, almacen_id, solicitado, disponible)
                continue
            pedido_sin_lote = sin_lote.get((producto_id, almacen_id), Decimal("0"))
            if not productos[producto_id]["maneja_lotes"] and pedido_sin_lote > 0:
                sin_lote_disponible = disponible - await stock.cantidad_en_lotes(conn, producto_id, almacen_id)
                if sin_lote_disponible < pedido_sin_lote:
                    _faltante(producto_id, almacen_id, pedido_sin_lote, max(sin_lote_disponible, Decimal("0")))
            elif pedido_sin_lote > 0:
                # Sin lote indicado se venderá por FEFO: solo cuentan los lotes
                # vendibles (ni vencidos ni bloqueados) y lo que está sin lote.
                # Lo pedido con lote explícito de esos mismos lotes ya no está
                # disponible para el FEFO (la salida procesa primero esas líneas).
                vendibles = await stock.lotes_fefo(conn, producto_id, almacen_id, hoy)
                en_lotes = await stock.cantidad_en_lotes(conn, producto_id, almacen_id)
                vendible = sum(
                    (
                        max(lote["cantidad"] - por_lote.get((producto_id, almacen_id, lote["lote_id"]), Decimal("0")),
                            Decimal("0"))
                        for lote in vendibles
                    ),
                    Decimal("0"),
                ) + max(disponible - en_lotes, Decimal("0"))
                if vendible < pedido_sin_lote:
                    _faltante(producto_id, almacen_id, pedido_sin_lote, vendible)

        for (producto_id, almacen_id, lote_id), solicitado in por_lote.items():
            lote = await stock.obtener_lote_de_producto(conn, lote_id, producto_id)
            if not almacenes[almacen_id]["activo"]:
                continue  # ya reportado arriba
            estado = stock.estado_lote(
                fecha_vencimiento=lote["fecha_vencimiento"], bloqueado=lote["bloqueado"], hoy=hoy, dias_alerta=0
            )
            disponible = await stock.existencia_lote(conn, lote_id, almacen_id)
            if estado in ("vencido", "bloqueado"):
                _faltante(producto_id, almacen_id, solicitado, disponible, lote_id=lote_id, motivo=f"lote_{estado}")
            elif disponible < solicitado and not almacenes[almacen_id]["permite_negativo"]:
                _faltante(producto_id, almacen_id, solicitado, disponible, lote_id=lote_id)
    return (len(faltantes) == 0, faltantes)


async def registrar_salida_por_factura(
    pool: asyncpg.Pool, factura_id: str, items: list[dict[str, Any]], *, documento: str | None = None
) -> list[dict[str, Any]]:
    """Registra la salida de mercancía de una factura ya enviada
    exitosamente a la DGII: cada ítem sale de su `almacen_id` (o del
    principal) y de su `lote_id` (o por FEFO si el producto maneja lotes).
    NO valida existencia: el e-CF ya es irreversible, así que puede dejar
    el stock negativo (la validación es `validar_disponibilidad`, antes de
    enviar).

    Idempotente por factura: un advisory lock por `factura_id` serializa
    los reintentos simultáneos y, si ya hay salidas de esta factura
    (reintento de facturas_api tras un timeout), no se descuenta nada -- se
    devuelven los movimientos ya registrados. Como toda la salida es una
    sola transacción, "ya hay alguna" equivale a "ya está completa"."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT pg_advisory_xact_lock($1, hashtext($2))", _LOCK_SALIDA_FACTURA, factura_id
            )
            existentes = await conn.fetch(
                """
                SELECT * FROM inventario.movimientos
                WHERE referencia_tipo = 'factura' AND referencia_id = $1 AND tipo = 'salida'
                ORDER BY secuencia
                """,
                factura_id,
            )
            if existentes:
                return [_con_id_str(fila) for fila in existentes]

            ids = list({item["producto_id"] for item in items})
            tipos = {
                fila["id"]: fila["tipo"]
                for fila in await conn.fetch(
                    "SELECT id, tipo FROM inventario.productos WHERE id = ANY($1::uuid[])", ids
                )
            }
            faltan = [pid for pid in ids if pid not in tipos]
            if faltan:
                raise ProductoNoEncontradoError(str(faltan[0]))
            bienes = [item for item in items if tipos[item["producto_id"]] != "servicio"]
            # Primero las líneas con lote explícito: si no, el FEFO de otra
            # línea del mismo producto podría tomar justo esas unidades.
            bienes.sort(key=lambda item: item.get("lote_id") is None)
            await stock.bloquear_productos(conn, [item["producto_id"] for item in bienes])

            movimientos: list[dict[str, Any]] = []
            for item in bienes:
                almacen = await stock.obtener_almacen(conn, item.get("almacen_id"), exigir_activo=False)
                movimientos.extend(
                    await stock.registrar_salida(
                        conn,
                        producto_id=item["producto_id"],
                        almacen=almacen,
                        cantidad=item["cantidad"],
                        lote_id=item.get("lote_id"),
                        forzar=True,
                        motivo="Venta (factura enviada a la DGII)",
                        referencia_tipo="factura",
                        referencia_id=factura_id,
                        referencia_linea=item.get("numero_linea"),
                        documento=documento,
                    )
                )
    return movimientos
