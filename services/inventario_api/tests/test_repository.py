"""Tests de la lógica más riesgosa de repository.py: el costo promedio
ponderado al confirmar una compra y la idempotencia de la salida de
mercancía por factura (evitar descontar stock dos veces ante un reintento
de facturas_api). No usan una base de datos real -- un doble mínimo de
asyncpg.Connection/Pool que devuelve resultados pre-cargados en el mismo
orden en que `repository.py` los pide."""
import uuid
from decimal import Decimal

import pytest

from services.inventario_api.app import repository


class FakeRecord(dict):
    """Simula asyncpg.Record: dict(record) debe funcionar."""


class _NullTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeConnection:
    def __init__(self, fetchrow_results=None, fetch_results=None):
        self._fetchrow_queue = list(fetchrow_results or [])
        self._fetch_queue = list(fetch_results or [])
        self.execute_calls: list[tuple[str, tuple]] = []
        self.fetchrow_calls: list[tuple[str, tuple]] = []

    def transaction(self):
        return _NullTransaction()

    async def fetchrow(self, query, *args):
        self.fetchrow_calls.append((query, args))
        return self._fetchrow_queue.pop(0) if self._fetchrow_queue else None

    async def fetch(self, query, *args):
        return self._fetch_queue.pop(0) if self._fetch_queue else []

    async def execute(self, query, *args):
        self.execute_calls.append((query, args))
        return "UPDATE 1"


class _AcquireCtx:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return False


class FakePool:
    def __init__(self, conn: FakeConnection):
        self._conn = conn

    def acquire(self):
        return _AcquireCtx(self._conn)


def _compra_record(compra_id, estado="confirmada") -> FakeRecord:
    return FakeRecord(
        id=compra_id,
        proveedor_id=None,
        numero_documento=None,
        estado=estado,
        notas=None,
        creado_en="2026-09-08T00:00:00Z",
        actualizado_en="2026-09-08T00:00:00Z",
        confirmada_en="2026-09-08T00:00:00Z",
    )


async def test_confirmar_compra_stock_en_cero_toma_el_costo_de_la_compra():
    """Con stock_actual=0, el costo promedio ponderado no puede dividir por
    cero -- el nuevo costo promedio debe ser directamente el costo de esta
    compra (ver el `if nuevo_stock > 0 else costo_unitario` en repository.py)."""
    compra_id = uuid.uuid4()
    producto_id = uuid.uuid4()
    item = FakeRecord(
        id=uuid.uuid4(), compra_id=compra_id, producto_id=producto_id,
        cantidad=Decimal("10"), costo_unitario=Decimal("5"),
    )
    conn = FakeConnection(
        fetchrow_results=[
            FakeRecord(estado="borrador"),  # estado actual (FOR UPDATE)
            FakeRecord(stock_actual=Decimal("0"), costo_promedio=Decimal("0")),  # producto (FOR UPDATE)
            _compra_record(compra_id),  # _compra_con_items: SELECT compra
        ],
        fetch_results=[[item], [item]],  # compra_items (una vez para confirmar, otra para la respuesta)
    )
    pool = FakePool(conn)

    resultado = await repository.confirmar_compra(pool, compra_id)

    assert resultado["estado"] == "confirmada"
    actualizaciones_stock = [c for c in conn.execute_calls if "SET stock_actual = $2" in c[0]]
    assert len(actualizaciones_stock) == 1
    _, args = actualizaciones_stock[0]
    assert args == (producto_id, Decimal("10"), Decimal("5"))


async def test_confirmar_compra_promedia_costo_ponderado_con_stock_existente():
    """stock_previo=10 a costo 4, se compran 10 más a costo 6 ->
    nuevo_stock=20, nuevo_costo=(10*4 + 10*6)/20=5."""
    compra_id = uuid.uuid4()
    producto_id = uuid.uuid4()
    item = FakeRecord(
        id=uuid.uuid4(), compra_id=compra_id, producto_id=producto_id,
        cantidad=Decimal("10"), costo_unitario=Decimal("6"),
    )
    conn = FakeConnection(
        fetchrow_results=[
            FakeRecord(estado="borrador"),
            FakeRecord(stock_actual=Decimal("10"), costo_promedio=Decimal("4")),
            _compra_record(compra_id),
        ],
        fetch_results=[[item], [item]],
    )
    pool = FakePool(conn)

    await repository.confirmar_compra(pool, compra_id)

    actualizaciones_stock = [c for c in conn.execute_calls if "SET stock_actual = $2" in c[0]]
    _, args = actualizaciones_stock[0]
    assert args == (producto_id, Decimal("20"), Decimal("5"))


async def test_confirmar_compra_ya_confirmada_lanza_error():
    compra_id = uuid.uuid4()
    conn = FakeConnection(fetchrow_results=[FakeRecord(estado="confirmada")])
    pool = FakePool(conn)

    with pytest.raises(repository.CompraEstadoInvalidoError):
        await repository.confirmar_compra(pool, compra_id)


async def test_confirmar_compra_inexistente_lanza_error():
    conn = FakeConnection(fetchrow_results=[None])
    pool = FakePool(conn)

    with pytest.raises(repository.CompraNoEncontradaError):
        await repository.confirmar_compra(pool, uuid.uuid4())


async def test_registrar_salida_por_factura_descuenta_stock():
    producto_id = uuid.uuid4()
    movimiento_insertado = FakeRecord(
        id=uuid.uuid4(), producto_id=producto_id, tipo="salida", cantidad=Decimal("2"),
        costo_unitario=None, motivo="Venta (factura enviada a la DGII)",
        referencia_tipo="factura", referencia_id="factura-1", creado_en="2026-09-08T00:00:00Z",
    )
    conn = FakeConnection(fetchrow_results=[movimiento_insertado])
    pool = FakePool(conn)

    movimientos = await repository.registrar_salida_por_factura(
        pool, "factura-1", [{"producto_id": producto_id, "cantidad": Decimal("2")}]
    )

    assert len(movimientos) == 1
    descuentos = [c for c in conn.execute_calls if "stock_actual - $2" in c[0]]
    assert len(descuentos) == 1
    assert descuentos[0][1] == (producto_id, Decimal("2"))


async def test_registrar_salida_por_factura_es_idempotente():
    """Si ya existe un movimiento de salida para esta factura+producto (el
    INSERT ON CONFLICT no inserta nada), NO debe volver a descontar stock."""
    producto_id = uuid.uuid4()
    movimiento_existente = FakeRecord(
        id=uuid.uuid4(), producto_id=producto_id, tipo="salida", cantidad=Decimal("2"),
        costo_unitario=None, motivo="Venta (factura enviada a la DGII)",
        referencia_tipo="factura", referencia_id="factura-1", creado_en="2026-09-08T00:00:00Z",
    )
    conn = FakeConnection(fetchrow_results=[None, movimiento_existente])
    pool = FakePool(conn)

    movimientos = await repository.registrar_salida_por_factura(
        pool, "factura-1", [{"producto_id": producto_id, "cantidad": Decimal("2")}]
    )

    assert len(movimientos) == 1
    assert conn.execute_calls == []  # ningún descuento de stock adicional


async def test_validar_disponibilidad_detecta_faltante():
    producto_id = uuid.uuid4()
    conn = FakeConnection(
        fetchrow_results=[FakeRecord(nombre="Producto X", stock_actual=Decimal("1"))]
    )
    pool = FakePool(conn)

    disponible, faltantes = await repository.validar_disponibilidad(
        pool, [{"producto_id": producto_id, "cantidad": Decimal("5")}]
    )

    assert disponible is False
    assert faltantes == [
        {"producto_id": str(producto_id), "nombre": "Producto X", "solicitado": Decimal("5"), "disponible": Decimal("1")}
    ]


async def test_validar_disponibilidad_ok_cuando_alcanza_el_stock():
    producto_id = uuid.uuid4()
    conn = FakeConnection(
        fetchrow_results=[FakeRecord(nombre="Producto X", stock_actual=Decimal("10"))]
    )
    pool = FakePool(conn)

    disponible, faltantes = await repository.validar_disponibilidad(
        pool, [{"producto_id": producto_id, "cantidad": Decimal("5")}]
    )

    assert disponible is True
    assert faltantes == []


async def test_validar_disponibilidad_producto_inexistente_lanza_error():
    conn = FakeConnection(fetchrow_results=[None])
    pool = FakePool(conn)

    with pytest.raises(repository.ProductoNoEncontradoError):
        await repository.validar_disponibilidad(pool, [{"producto_id": uuid.uuid4(), "cantidad": Decimal("1")}])
