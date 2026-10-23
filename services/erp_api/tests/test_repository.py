"""Tests de la lógica más riesgosa de repository.py: cálculo de totales de
una cotización (descuentos e ITBIS por tasa, redondeo), normalización del
RNC/cédula, validaciones de un cobro (factura a crédito, monto <= pendiente)
y las transiciones de estado de una cotización. Igual que en
services/inventario_api/tests/test_repository.py, no usan una base de datos
real -- un doble mínimo de asyncpg.Connection/Pool que devuelve resultados
pre-cargados en el mismo orden en que `repository.py` los pide."""
import uuid
from datetime import date
from decimal import Decimal

import asyncpg
import pytest

from services.erp_api.app import repository


class FakeRecord(dict):
    """Simula asyncpg.Record: dict(record) debe funcionar."""


class _NullTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeConnection:
    def __init__(self, fetchrow_results=None, fetch_results=None, fetchval_results=None, execute_error=None):
        self._fetchrow_queue = list(fetchrow_results or [])
        self._fetch_queue = list(fetch_results or [])
        self._fetchval_queue = list(fetchval_results or [])
        self._execute_error = execute_error
        self.execute_calls: list[tuple[str, tuple]] = []
        self.fetch_calls: list[tuple[str, tuple]] = []
        self.fetchval_calls: list[tuple[str, tuple]] = []

    def transaction(self):
        return _NullTransaction()

    async def fetchrow(self, query, *args):
        return self._fetchrow_queue.pop(0) if self._fetchrow_queue else None

    async def fetch(self, query, *args):
        self.fetch_calls.append((query, args))
        return self._fetch_queue.pop(0) if self._fetch_queue else []

    async def fetchval(self, query, *args):
        self.fetchval_calls.append((query, args))
        return self._fetchval_queue.pop(0) if self._fetchval_queue else None

    async def execute(self, query, *args):
        self.execute_calls.append((query, args))
        if self._execute_error is not None:
            raise self._execute_error
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


def _item(cantidad, precio, itbis, descuento="0"):
    return {
        "producto_id": None,
        "descripcion": "Línea",
        "cantidad": Decimal(cantidad),
        "precio_unitario": Decimal(precio),
        "descuento_pct": Decimal(descuento),
        "itbis": itbis,
    }


# --- calcular_cotizacion ---------------------------------------------------------

def test_calcular_cotizacion_con_descuento_y_tasas_mixtas():
    lineas, totales = repository.calcular_cotizacion(
        [
            _item("10", "450.00", "18", descuento="10"),  # 4500 - 10 % = 4050 -> ITBIS 729.00
            _item("3", "99.99", "16"),  # 299.97 -> ITBIS 47.9952 = 48.00
            _item("1", "500", "exento"),
            _item("2", "100", "0"),
        ]
    )
    assert [linea["importe"] for linea in lineas] == ["4050.00", "299.97", "500.00", "200.00"]
    assert totales == {
        "subtotal": Decimal("5499.97"),
        "descuentos": Decimal("450.00"),
        "itbis_total": Decimal("777.00"),
        "total": Decimal("5826.97"),
    }


def test_calcular_cotizacion_redondea_half_up():
    # 2.5 * 199.99 = 499.975 -> 499.98; con 5 % de descuento 474.97625 -> 474.98
    lineas, totales = repository.calcular_cotizacion([_item("2.5", "199.99", "18", descuento="5")])
    assert lineas[0]["importe"] == "474.98"
    assert totales["subtotal"] == Decimal("499.98")
    assert totales["descuentos"] == Decimal("25.00")
    assert totales["itbis_total"] == Decimal("85.50")  # 474.98 * 0.18 = 85.4964
    assert totales["total"] == Decimal("560.48")


def test_calcular_cotizacion_guarda_lineas_como_texto():
    producto_id = uuid.uuid4()
    item = _item("1", "10", "18") | {"producto_id": producto_id}
    lineas, _ = repository.calcular_cotizacion([item])
    assert lineas[0]["producto_id"] == str(producto_id)
    assert all(isinstance(lineas[0][campo], str) for campo in ("cantidad", "precio_unitario", "descuento_pct"))


# --- normalizar_identificacion ---------------------------------------------------------

def test_normalizar_identificacion_quita_guiones():
    assert repository.normalizar_identificacion("rnc", "1-31-23456-7") == ("rnc", "131234567")
    assert repository.normalizar_identificacion("cedula", "402-1234567-8") == ("cedula", "40212345678")


def test_normalizar_identificacion_deduce_el_tipo_por_largo():
    assert repository.normalizar_identificacion(None, "131234567") == ("rnc", "131234567")
    assert repository.normalizar_identificacion(None, "40212345678") == ("cedula", "40212345678")


def test_normalizar_identificacion_vacio_es_none():
    assert repository.normalizar_identificacion(None, "") == (None, None)
    assert repository.normalizar_identificacion("rnc", None) == ("rnc", None)


@pytest.mark.parametrize(
    ("tipo", "valor"), [("rnc", "1312345"), ("cedula", "131234567"), (None, "12345")]
)
def test_normalizar_identificacion_largo_invalido(tipo, valor):
    with pytest.raises(repository.DatosInvalidosError):
        repository.normalizar_identificacion(tipo, valor)


def test_normalizar_identificacion_pasaporte_conserva_letras():
    assert repository.normalizar_identificacion("pasaporte", "ab 12-3456") == ("pasaporte", "AB123456")


# --- Clientes ---------------------------------------------------------

async def test_eliminar_cliente_con_cotizaciones_lanza_error():
    conn = FakeConnection(execute_error=asyncpg.ForeignKeyViolationError("referenciado"))

    with pytest.raises(repository.ClienteConCotizacionesError):
        await repository.eliminar_cliente(FakePool(conn), uuid.uuid4())


async def test_listar_clientes_tipo_cliente_incluye_ambos():
    conn = FakeConnection(fetch_results=[[]])

    await repository.listar_clientes(FakePool(conn), tipo="cliente")

    query, args = conn.fetch_calls[0]
    assert "c.tipo IN ($1, 'ambos')" in query
    assert args[0] == "cliente"


# --- Cotizaciones ---------------------------------------------------------

async def test_listar_cotizaciones_vencida_filtra_abiertas_con_vence_pasado(monkeypatch):
    monkeypatch.setattr(repository, "hoy", lambda: date(2026, 10, 23))
    conn = FakeConnection(fetch_results=[[]])

    await repository.listar_cotizaciones(FakePool(conn), estado="vencida")

    query, args = conn.fetch_calls[0]
    assert "estado = 'abierta' AND vence < $1::date" in query
    assert args[0] == date(2026, 10, 23)


async def test_crear_cotizacion_sin_cliente_lanza_error():
    conn = FakeConnection()

    with pytest.raises(repository.DatosInvalidosError):
        await repository.crear_cotizacion(
            FakePool(conn), cliente_id=None, cliente_nombre=None, cliente_rnc=None,
            fecha=None, vence=None, items=[_item("1", "1", "18")], notas=None,
        )


async def test_crear_cotizacion_vence_por_defecto_y_correlativo(monkeypatch):
    monkeypatch.setattr(repository, "hoy", lambda: date(2026, 10, 23))
    cotizacion_id = uuid.uuid4()
    conn = FakeConnection(
        fetchval_results=[7, cotizacion_id],  # nextval, INSERT ... RETURNING id
        fetchrow_results=[FakeRecord(id=cotizacion_id, items="[]")],
    )

    await repository.crear_cotizacion(
        FakePool(conn), cliente_id=None, cliente_nombre="Cliente de paso", cliente_rnc="001-0000000-1",
        fecha=None, vence=None, items=[_item("1", "100", "18")], notas=None,
    )

    _, args = conn.fetchval_calls[1]
    assert args[0] == "COT-0007"
    assert args[3] == "00100000001"  # cliente_rnc normalizado
    assert args[4] == date(2026, 10, 23) and args[5] == date(2026, 11, 7)
    assert args[11] == Decimal("118.00")  # total


async def test_crear_cotizacion_vence_anterior_a_fecha_lanza_error():
    conn = FakeConnection()

    with pytest.raises(repository.DatosInvalidosError):
        await repository.crear_cotizacion(
            FakePool(conn), cliente_id=None, cliente_nombre="X", cliente_rnc=None,
            fecha=date(2026, 10, 23), vence=date(2026, 10, 1), items=[_item("1", "1", "18")], notas=None,
        )


@pytest.mark.parametrize("estado", ["aceptada", "rechazada", "facturada"])
async def test_actualizar_cotizacion_no_abierta_lanza_error(estado):
    conn = FakeConnection(fetchval_results=[estado])

    with pytest.raises(repository.CotizacionEstadoInvalidoError):
        await repository.actualizar_cotizacion(
            FakePool(conn), uuid.uuid4(), cliente_id=None, cliente_nombre="X", cliente_rnc=None,
            fecha=None, vence=None, items=[_item("1", "1", "18")], notas=None,
        )


async def test_eliminar_cotizacion_no_abierta_lanza_error():
    conn = FakeConnection(fetchval_results=["aceptada"])

    with pytest.raises(repository.CotizacionEstadoInvalidoError):
        await repository.eliminar_cotizacion(FakePool(conn), uuid.uuid4())
    assert not any("DELETE" in query for query, _ in conn.execute_calls)


async def test_eliminar_cotizacion_inexistente_lanza_error():
    conn = FakeConnection(fetchval_results=[None])

    with pytest.raises(repository.CotizacionNoEncontradaError):
        await repository.eliminar_cotizacion(FakePool(conn), uuid.uuid4())


async def test_cambiar_estado_desde_facturada_lanza_error():
    conn = FakeConnection(fetchval_results=["facturada"])

    with pytest.raises(repository.CotizacionEstadoInvalidoError):
        await repository.cambiar_estado_cotizacion(FakePool(conn), uuid.uuid4(), "abierta")
    assert conn.execute_calls == []


async def test_marcar_facturada_con_otra_factura_lanza_error():
    conn = FakeConnection(fetchrow_results=[FakeRecord(estado="facturada", factura_id=uuid.uuid4())])

    with pytest.raises(repository.CotizacionEstadoInvalidoError):
        await repository.marcar_cotizacion_facturada(FakePool(conn), uuid.uuid4(), uuid.uuid4())


async def test_marcar_facturada_factura_inexistente_lanza_error():
    conn = FakeConnection(
        fetchrow_results=[FakeRecord(estado="aceptada", factura_id=None)],
        fetchval_results=[None],  # SELECT 1 FROM facturacion.facturas
    )

    with pytest.raises(repository.DatosInvalidosError):
        await repository.marcar_cotizacion_facturada(FakePool(conn), uuid.uuid4(), uuid.uuid4())
    assert conn.execute_calls == []


# --- Cobros ---------------------------------------------------------

async def _registrar(conn, monto):
    return await repository.registrar_cobro(
        FakePool(conn), factura_id=uuid.uuid4(), fecha=None, monto=Decimal(monto), metodo="efectivo",
        cuenta_destino=None, referencia=None, notas=None,
    )


async def test_registrar_cobro_factura_no_a_credito_lanza_error():
    conn = FakeConnection(fetchrow_results=[None])  # no está en el CTE de cuentas por cobrar

    with pytest.raises(repository.DatosInvalidosError, match="no es a crédito"):
        await _registrar(conn, "100")


async def test_registrar_cobro_monto_cero_lanza_error():
    conn = FakeConnection()

    with pytest.raises(repository.DatosInvalidosError, match="mayor que cero"):
        await _registrar(conn, "0")


async def test_registrar_cobro_que_supera_lo_pendiente_lanza_error():
    conn = FakeConnection(fetchrow_results=[FakeRecord(pendiente=Decimal("500.00"))])

    with pytest.raises(repository.DatosInvalidosError, match="supera lo pendiente"):
        await _registrar(conn, "500.01")
    assert conn.fetchval_calls == []  # no se insertó nada


async def test_registrar_cobro_por_lo_pendiente_exacto(monkeypatch):
    monkeypatch.setattr(repository, "hoy", lambda: date(2026, 10, 23))
    cobro_id = uuid.uuid4()
    factura_id = uuid.uuid4()
    conn = FakeConnection(
        fetchrow_results=[
            FakeRecord(pendiente=Decimal("500.00")),
            FakeRecord(id=cobro_id, factura_id=factura_id, monto=Decimal("500.00")),
        ],
        fetchval_results=[cobro_id],
    )

    cobro = await _registrar(conn, "500")

    assert cobro["id"] == str(cobro_id) and cobro["factura_id"] == str(factura_id)
    assert "pg_advisory_xact_lock" in conn.execute_calls[0][0]
    _, args = conn.fetchval_calls[0]
    assert args[1] == date(2026, 10, 23)  # fecha por defecto = hoy
    assert args[2] == Decimal("500.00")


async def test_anular_cobro_ya_anulado_lanza_error():
    conn = FakeConnection(fetchval_results=[True])

    with pytest.raises(repository.CobroEstadoInvalidoError):
        await repository.anular_cobro(FakePool(conn), uuid.uuid4())


async def test_listar_cuentas_por_cobrar_por_defecto_excluye_cobradas(monkeypatch):
    monkeypatch.setattr(repository, "hoy", lambda: date(2026, 10, 23))
    conn = FakeConnection(fetch_results=[[]])

    await repository.listar_cuentas_por_cobrar(FakePool(conn), cliente_rnc="131-23456-7")

    query, args = conn.fetch_calls[0]
    assert "estado <> 'cobrada'" in query
    assert args == (date(2026, 10, 23), "131234567")


async def test_listar_cuentas_por_cobrar_todas_no_filtra_estado():
    conn = FakeConnection(fetch_results=[[]])

    await repository.listar_cuentas_por_cobrar(FakePool(conn), estado="todas")

    query, args = conn.fetch_calls[0]
    assert "estado <> 'cobrada'" not in query
    assert len(args) == 1
