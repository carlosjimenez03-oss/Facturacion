from decimal import Decimal

import pytest

from libs.ecf_common.compras606 import (
    periodo_de_fecha,
    registrar_compra_606,
    tipo_identificacion,
    validar_fecha,
    validar_ncf,
    validar_periodo,
    validar_rnc_cedula,
)


def test_tipo_identificacion_rnc():
    assert tipo_identificacion("131234567") == 1


def test_tipo_identificacion_cedula():
    assert tipo_identificacion("00112345678") == 2


@pytest.mark.parametrize(
    "valor,esperado",
    [
        ("131234567", True),  # RNC, 9 dígitos
        ("00112345678", True),  # Cédula, 11 dígitos
        ("12345", False),  # longitud inválida
        ("13123456A", False),  # no numérico
        ("", False),
    ],
)
def test_validar_rnc_cedula(valor, esperado):
    assert validar_rnc_cedula(valor) is esperado


@pytest.mark.parametrize(
    "valor,esperado",
    [
        ("202506", True),
        ("201805", True),  # mínimo permitido
        ("201804", False),  # antes del mínimo
        ("202513", False),  # mes inválido
        ("20256", False),  # longitud inválida
        ("abcdef", False),
    ],
)
def test_validar_periodo(valor, esperado):
    assert validar_periodo(valor) is esperado


@pytest.mark.parametrize(
    "valor,esperado",
    [
        ("20260115", True),
        ("20260230", False),  # 30 de febrero no existe
        ("20261301", False),  # mes 13 inválido
        ("2026011", False),  # longitud inválida
    ],
)
def test_validar_fecha(valor, esperado):
    assert validar_fecha(valor) is esperado


@pytest.mark.parametrize(
    "valor,esperado",
    [
        ("E310000000001", True),  # e-NCF 13 posiciones
        ("e450000000001", True),  # minúscula también válida
        ("B0100000001", True),  # NCF papel 11 posiciones
        ("A020000000101000001", True),  # NCF antiguo 19 posiciones
        ("E31000000001", False),  # e-NCF con longitud incorrecta
        ("B9900000001", False),  # tipo "99" no permitido en la serie B (11 posiciones)
        ("", False),
    ],
)
def test_validar_ncf(valor, esperado):
    assert validar_ncf(valor) is esperado


def test_periodo_de_fecha():
    assert periodo_de_fecha("20260115") == "202601"


class FakeConnection:
    def __init__(self):
        self.queries: list[tuple[str, tuple]] = []

    async def execute(self, query, *args):
        self.queries.append((query, args))
        return "INSERT 0 1"


class _AcquireCtx:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return False


class FakePool:
    def __init__(self, conn):
        self._conn = conn

    def acquire(self):
        return _AcquireCtx(self._conn)


async def test_registrar_compra_606_inserta():
    conn = FakeConnection()
    pool = FakePool(conn)

    await registrar_compra_606(
        pool,
        origen="receptor_recepcion",
        referencia_tipo="envio_dgii",
        referencia_id="123",
        rnc_cedula="131234567",
        ncf="E310000000001",
        ncf_modificado=None,
        fecha_comprobante="20260115",
        monto_servicios=Decimal("100.00"),
        monto_bienes=Decimal("0.00"),
        itbis_facturado=Decimal("18.00"),
    )

    assert len(conn.queries) == 1
    query, args = conn.queries[0]
    assert "INSERT INTO compras606.registros" in query
    assert args[0] == "202601"  # periodo derivado de la fecha
    assert args[5] == 1  # tipo_id (RNC)


async def test_registrar_compra_606_sin_pool_no_hace_nada():
    await registrar_compra_606(
        None,
        origen="auto_emitido",
        referencia_tipo="factura",
        referencia_id="1",
        rnc_cedula="131234567",
        ncf="E410000000001",
        ncf_modificado=None,
        fecha_comprobante="20260115",
        monto_servicios=Decimal("0"),
        monto_bienes=Decimal("100"),
        itbis_facturado=Decimal("18"),
    )
    # No debe lanzar ninguna excepción.


async def test_registrar_compra_606_datos_incompletos_no_inserta():
    conn = FakeConnection()
    pool = FakePool(conn)

    await registrar_compra_606(
        pool,
        origen="auto_emitido",
        referencia_tipo="factura",
        referencia_id="1",
        rnc_cedula="",
        ncf="E410000000001",
        ncf_modificado=None,
        fecha_comprobante="20260115",
        monto_servicios=Decimal("0"),
        monto_bienes=Decimal("100"),
        itbis_facturado=Decimal("18"),
    )

    assert conn.queries == []


async def test_registrar_compra_606_error_de_conexion_no_lanza():
    class FailingConnection(FakeConnection):
        async def execute(self, query, *args):
            raise RuntimeError("boom")

    pool = FakePool(FailingConnection())

    await registrar_compra_606(
        pool,
        origen="auto_emitido",
        referencia_tipo="factura",
        referencia_id="1",
        rnc_cedula="131234567",
        ncf="E410000000001",
        ncf_modificado=None,
        fecha_comprobante="20260115",
        monto_servicios=Decimal("0"),
        monto_bienes=Decimal("100"),
        itbis_facturado=Decimal("18"),
    )
    # No debe lanzar ninguna excepción -- best-effort.
