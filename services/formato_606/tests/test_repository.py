import uuid
from decimal import Decimal

import pytest

from services.formato_606.app import repository


class FakeRecord(dict):
    """Simula asyncpg.Record: dict(record) debe funcionar."""


class FakeConnection:
    def __init__(self, fetchrow_result=None, fetch_result=None, fetchval_result=None):
        self.fetchrow_result = fetchrow_result
        self.fetch_result = fetch_result or []
        self.fetchval_result = fetchval_result
        self.queries: list[tuple[str, tuple]] = []
        self.executed: list[tuple[str, tuple]] = []

    async def fetchrow(self, query, *args):
        self.queries.append((query, args))
        return self.fetchrow_result

    async def fetch(self, query, *args):
        self.queries.append((query, args))
        return self.fetch_result

    async def fetchval(self, query, *args):
        self.queries.append((query, args))
        return self.fetchval_result

    async def execute(self, query, *args):
        self.executed.append((query, args))
        return "DELETE 1"


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


def _fila(**overrides) -> FakeRecord:
    base = {
        "id": uuid.uuid4(),
        "periodo": "202601",
        "origen": "manual",
        "referencia_tipo": None,
        "referencia_id": None,
        "rnc_cedula": "131234567",
        "tipo_id": 1,
        "tipo_bienes_servicios": 2,
        "ncf": "E310000000001",
        "ncf_modificado": None,
        "fecha_comprobante": "20260115",
        "fecha_pago": None,
        "monto_servicios": Decimal("400.00"),
        "monto_bienes": Decimal("600.00"),
        "itbis_facturado": Decimal("180.00"),
        "itbis_retenido": Decimal("0"),
        "itbis_proporcionalidad": Decimal("0"),
        "itbis_costo": Decimal("30.00"),
        "itbis_percibido": Decimal("0"),
        "tipo_retencion_isr": None,
        "monto_retencion_renta": Decimal("0"),
        "isr_percibido": Decimal("0"),
        "impuesto_selectivo_consumo": Decimal("0"),
        "otros_impuestos": Decimal("0"),
        "monto_propina_legal": Decimal("0"),
        "forma_pago": 2,
        "creado_en": "2026-01-15T00:00:00Z",
        "actualizado_en": "2026-01-15T00:00:00Z",
    }
    base.update(overrides)
    return FakeRecord(base)


async def test_listar_por_periodo_agrega_calculados():
    conn = FakeConnection(fetch_result=[_fila()])
    pool = FakePool(conn)

    resultado = await repository.listar_por_periodo(pool, "202601")

    assert len(resultado) == 1
    assert resultado[0]["monto_facturado"] == Decimal("1000.00")  # 400 + 600
    assert resultado[0]["itbis_adelantar"] == Decimal("150.00")  # 180 - 30
    assert isinstance(resultado[0]["id"], str)


async def test_obtener_no_encontrado():
    conn = FakeConnection(fetchrow_result=None)
    pool = FakePool(conn)

    with pytest.raises(repository.RegistroNoEncontradoError):
        await repository.obtener(pool, uuid.uuid4())


async def test_actualizar_clasificacion_construye_update_con_campos_dados():
    conn = FakeConnection(fetchrow_result=_fila())
    pool = FakePool(conn)

    await repository.actualizar_clasificacion(
        pool, uuid.uuid4(), {"tipo_bienes_servicios": 3, "forma_pago": 1}
    )

    query, args = conn.queries[0]
    assert "tipo_bienes_servicios = $1" in query
    assert "forma_pago = $2" in query
    assert args[0] == 3
    assert args[1] == 1


async def test_actualizar_clasificacion_no_encontrado():
    conn = FakeConnection(fetchrow_result=None)
    pool = FakePool(conn)

    with pytest.raises(repository.RegistroNoEncontradoError):
        await repository.actualizar_clasificacion(pool, uuid.uuid4(), {"forma_pago": 1})


async def test_crear_manual_inserta_con_periodo_derivado():
    conn = FakeConnection(fetchrow_result=_fila())
    pool = FakePool(conn)

    await repository.crear_manual(
        pool,
        {
            "rnc_cedula": "131234567",
            "ncf": "E310000000001",
            "fecha_comprobante": "20260115",
        },
    )

    query, args = conn.queries[0]
    assert "INSERT INTO compras606.registros" in query
    assert args[0] == "202601"  # periodo derivado de fecha_comprobante
    assert args[1] == "131234567"
    assert args[2] == 1  # tipo_id (RNC)


async def test_eliminar_manual_ok():
    conn = FakeConnection(fetchval_result="manual")
    pool = FakePool(conn)

    await repository.eliminar_manual(pool, uuid.uuid4())

    assert len(conn.executed) == 1


async def test_eliminar_manual_no_encontrado():
    conn = FakeConnection(fetchval_result=None)
    pool = FakePool(conn)

    with pytest.raises(repository.RegistroNoEncontradoError):
        await repository.eliminar_manual(pool, uuid.uuid4())


async def test_eliminar_manual_origen_no_editable():
    conn = FakeConnection(fetchval_result="receptor_recepcion")
    pool = FakePool(conn)

    with pytest.raises(repository.RegistroNoEditableError):
        await repository.eliminar_manual(pool, uuid.uuid4())

    assert conn.executed == []
