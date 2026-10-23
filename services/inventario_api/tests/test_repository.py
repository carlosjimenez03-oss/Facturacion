"""Tests de la lógica más riesgosa sin base de datos: costo promedio
ponderado, reparto FEFO, estados de lote/stock, el Kardex (promedio y
PEPS) y la idempotencia de la salida de mercancía por factura (evitar
descontar stock dos veces ante un reintento de facturas_api). Usan un doble
mínimo de asyncpg.Connection/Pool que devuelve resultados pre-cargados en
el mismo orden en que `repository.py` los pide. Los flujos completos
(existencias, lotes, ajustes, transferencias, concurrencia) se prueban
contra Postgres real en test_postgres.py."""
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from services.inventario_api.app import kardex, repository, stock


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



# --- Cálculos puros (stock.py / repository.py) ---------------------------------------------------------

def test_costo_promedio_stock_en_cero_toma_el_costo_de_la_entrada():
    """Con stock 0 el promedio no puede dividir por cero: el nuevo costo es
    directamente el de esta entrada."""
    assert stock.costo_promedio_ponderado(Decimal("0"), Decimal("0"), Decimal("10"), Decimal("5")) == Decimal("5")


def test_costo_promedio_pondera_con_stock_existente():
    """stock_previo=10 a costo 4, entran 10 más a costo 6 -> (10*4 + 10*6)/20 = 5."""
    assert stock.costo_promedio_ponderado(Decimal("10"), Decimal("4"), Decimal("10"), Decimal("6")) == Decimal("5")


def test_costo_promedio_con_stock_negativo_toma_el_costo_de_la_entrada():
    """Stock negativo (ventas forzadas tras enviar el e-CF): el promedio
    anterior no significa nada."""
    assert stock.costo_promedio_ponderado(Decimal("-5"), Decimal("10"), Decimal("10"), Decimal("12")) == Decimal("12")


def test_plan_fefo_reparte_en_orden_y_reporta_faltante():
    lotes = [
        {"lote_id": "A", "cantidad": Decimal("4")},
        {"lote_id": "B", "cantidad": Decimal("0")},
        {"lote_id": "C", "cantidad": Decimal("10")},
    ]
    assert stock.plan_fefo(lotes, Decimal("6")) == ([("A", Decimal("4")), ("C", Decimal("2"))], Decimal("0"))
    assert stock.plan_fefo(lotes, Decimal("20")) == ([("A", Decimal("4")), ("C", Decimal("10"))], Decimal("6"))
    assert stock.plan_fefo([], Decimal("1")) == ([], Decimal("1"))


@pytest.mark.parametrize(
    ("dias", "bloqueado", "esperado"),
    [(-1, False, "vencido"), (0, False, "por_vencer"), (30, False, "por_vencer"), (31, False, "vigente"),
     (None, False, "vigente"), (-10, True, "bloqueado")],
)
def test_estado_lote(dias, bloqueado, esperado):
    hoy = date(2026, 10, 23)
    vence = hoy + timedelta(days=dias) if dias is not None else None
    assert stock.estado_lote(fecha_vencimiento=vence, bloqueado=bloqueado, hoy=hoy, dias_alerta=30) == esperado


@pytest.mark.parametrize(
    ("producto", "esperado"),
    [
        ({"tipo": "servicio", "stock_actual": Decimal("0")}, "no_aplica"),
        ({"tipo": "bien", "stock_actual": Decimal("0"), "stock_minimo": Decimal("0")}, "sin_existencia"),
        ({"tipo": "bien", "stock_actual": Decimal("18"), "stock_minimo": Decimal("50")}, "bajo_minimo"),
        ({"tipo": "bien", "stock_actual": Decimal("18"), "stock_minimo": Decimal("0")}, "disponible"),
        ({"stock_actual": Decimal("3")}, "disponible"),  # fila de antes de la Fase 4
    ],
)
def test_estado_stock(producto, esperado):
    assert repository.estado_stock(producto) == esperado


def test_rango_fechas_en_hora_de_rd():
    inicio, fin = repository.rango_fechas(date(2026, 10, 1), date(2026, 10, 23))
    assert inicio == datetime(2026, 10, 1, 4, 0, tzinfo=timezone.utc)
    assert fin == datetime(2026, 10, 24, 4, 0, tzinfo=timezone.utc)
    assert repository.rango_fechas(None, None) == (None, None)


# --- Kardex ---------------------------------------------------------

def _mov(dia, tipo, cantidad, costo, referencia_tipo="compra", **extra):
    return {
        "id": uuid.uuid4(),
        "creado_en": datetime(2026, 10, dia, 14, 0, tzinfo=timezone.utc),
        "tipo": tipo,
        "cantidad": Decimal(cantidad),
        "costo_unitario": Decimal(costo) if costo is not None else None,
        "referencia_tipo": referencia_tipo,
        **extra,
    }


# Mismo ejemplo de docs/diseno/prototipo-kontara/pantallas/Kardex.dc.html
# (las salidas se graban al costo promedio vigente, como hace stock.py).
_KARDEX = [
    _mov(1, "entrada", "200", "410", "ajuste", motivo="Stock inicial"),
    _mov(3, "entrada", "500", "425"),
    _mov(8, "salida", "350", "420.7143", "factura", documento="E310000000118"),
    _mov(15, "entrada", "400", "438"),
]


def test_kardex_promedio_saldo_acumulado():
    resultado = kardex.calcular_kardex(_KARDEX)
    filas = resultado["filas"]
    assert [f["concepto"] for f in filas] == ["Stock inicial", "Compra", "Venta", "Compra"]
    assert [f["saldo_cantidad"] for f in filas] == [Decimal("200"), Decimal("700"), Decimal("350"), Decimal("750")]
    assert filas[1]["costo_promedio"] == Decimal("420.7143")
    assert filas[2]["cantidad"] == Decimal("-350") and filas[2]["costo_total"] == Decimal("-147250.01")
    assert resultado["saldo_final"]["valor"] == Decimal("322450.00")  # 322449.995, redondeo comercial
    assert resultado["entradas"] == Decimal("1100") and resultado["salidas"] == Decimal("350")


def test_kardex_peps_consume_las_capas_mas_antiguas():
    resultado = kardex.calcular_kardex(_KARDEX, metodo="peps")
    venta = resultado["filas"][2]
    assert venta["costo_total"] == -(Decimal("200") * 410 + Decimal("150") * 425)
    assert resultado["saldo_final"]["valor"] == Decimal("350") * 425 + Decimal("400") * 438


def test_kardex_periodo_calcula_saldo_inicial_con_lo_anterior():
    inicio = datetime(2026, 10, 5, tzinfo=timezone.utc)
    resultado = kardex.calcular_kardex(_KARDEX, inicio=inicio)
    assert resultado["saldo_inicial"] == {
        "cantidad": Decimal("700"),
        "costo_promedio": Decimal("420.7143"),
        "valor": Decimal("294500.00"),
    }
    assert len(resultado["filas"]) == 2
    assert resultado["entradas"] == Decimal("400")


def test_kardex_movimientos_sin_costo_se_valuan_al_promedio():
    """Movimientos de antes de la Fase 4 (sin costo_unitario)."""
    movs = [_mov(1, "entrada", "10", "5"), _mov(2, "salida", "4", None, "factura"), _mov(3, "entrada", "2", None)]
    filas = kardex.calcular_kardex(movs)["filas"]
    assert filas[1]["costo_unitario"] == Decimal("5.0000")
    assert filas[2]["saldo_valor"] == Decimal("40.00")


def test_kardex_csv_para_excel():
    resultado = kardex.calcular_kardex(_KARDEX)
    resultado.update({"desde": date(2026, 10, 1), "almacen_nombre": None})
    csv = kardex.kardex_csv(resultado)
    lineas = csv.lstrip("﻿").split("\r\n")
    assert csv.startswith("﻿")
    assert lineas[0].startswith("Fecha,Documento,Tipo de movimiento")
    assert lineas[1].startswith("01/10/2026,,Saldo inicial")
    assert lineas[4].startswith("08/10/2026,E310000000118,Venta,,,-350,")


# --- Compras ---------------------------------------------------------

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


# --- Integración con ventas ---------------------------------------------------------

async def test_registrar_salida_por_factura_es_idempotente():
    """Si ya hay movimientos de salida de esta factura (reintento de
    facturas_api), se devuelven tal cual y NO se descuenta stock otra vez.
    Lo primero que hace es tomar el advisory lock de la factura."""
    producto_id = uuid.uuid4()
    movimiento_existente = FakeRecord(
        id=uuid.uuid4(), producto_id=producto_id, tipo="salida", cantidad=Decimal("2"),
        costo_unitario=Decimal("5"), motivo="Venta (factura enviada a la DGII)",
        referencia_tipo="factura", referencia_id="factura-1", creado_en="2026-09-08T00:00:00Z",
    )
    conn = FakeConnection(fetch_results=[[movimiento_existente]])
    pool = FakePool(conn)

    movimientos = await repository.registrar_salida_por_factura(
        pool, "factura-1", [{"producto_id": producto_id, "cantidad": Decimal("2")}]
    )

    assert len(movimientos) == 1
    assert len(conn.execute_calls) == 1  # solo el advisory lock
    assert "pg_advisory_xact_lock" in conn.execute_calls[0][0]
    assert conn.execute_calls[0][1][1] == "factura-1"


async def test_validar_disponibilidad_producto_inexistente_lanza_error():
    conn = FakeConnection(fetchrow_results=[None])
    pool = FakePool(conn)

    with pytest.raises(repository.ProductoNoEncontradoError):
        await repository.validar_disponibilidad(pool, [{"producto_id": uuid.uuid4(), "cantidad": Decimal("1")}])


async def test_validar_disponibilidad_ignora_servicios():
    """Un servicio no lleva existencia: no se consulta nada más."""
    conn = FakeConnection(
        fetchrow_results=[FakeRecord(id="x", nombre="Instalación", tipo="servicio", maneja_lotes=False)]
    )
    pool = FakePool(conn)

    disponible, faltantes = await repository.validar_disponibilidad(
        pool, [{"producto_id": uuid.uuid4(), "cantidad": Decimal("5")}]
    )

    assert (disponible, faltantes) == (True, [])
    assert len(conn.fetchrow_calls) == 1
