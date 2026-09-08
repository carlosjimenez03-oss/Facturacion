import uuid

import pytest

from services.facturas_api.app import repository


class FakeRecord(dict):
    """Simula asyncpg.Record: dict(record) debe funcionar (repository.py hace eso)."""


class FakeConnection:
    def __init__(self, fetchrow_result=None, fetch_result=None, execute_result="UPDATE 1"):
        self.fetchrow_result = fetchrow_result
        self.fetch_result = fetch_result or []
        self.execute_result = execute_result
        self.queries: list[tuple[str, tuple]] = []

    async def fetchrow(self, query, *args):
        self.queries.append((query, args))
        return self.fetchrow_result

    async def fetch(self, query, *args):
        self.queries.append((query, args))
        return self.fetch_result

    async def execute(self, query, *args):
        self.queries.append((query, args))
        return self.execute_result


class FakePool:
    def __init__(self, conn: FakeConnection):
        self._conn = conn

    def acquire(self):
        return _AcquireCtx(self._conn)


class _AcquireCtx:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return False


def _fila_factura(**overrides) -> FakeRecord:
    base = {
        "id": uuid.uuid4(),
        "tipo_ecf": 31,
        "ambiente": "test",
        "rnc_emisor": "131234567",
        "encf": "E310000000001",
        "estado": "borrador",
        "datos": '{"Encabezado": {}}',
        "xml_generado": None,
        "xml_firmado": None,
        "track_id": None,
        "dgii_estado": None,
        "dgii_codigo": None,
        "dgii_respuesta": None,
        "error": None,
        "creado_en": "2026-09-02T00:00:00Z",
        "actualizado_en": "2026-09-02T00:00:00Z",
    }
    base.update(overrides)
    return FakeRecord(base)


async def test_crear_factura_inserta_y_devuelve_dict():
    conn = FakeConnection(fetchrow_result=_fila_factura())
    pool = FakePool(conn)

    resultado = await repository.crear_factura(
        pool, tipo_ecf=31, ambiente="test", rnc_emisor="131234567", encf="E310000000001", datos={"Encabezado": {}}
    )

    assert resultado["tipo_ecf"] == 31
    assert isinstance(resultado["id"], str)
    assert isinstance(resultado["datos"], dict)
    assert "INSERT INTO facturacion.facturas" in conn.queries[0][0]


async def test_obtener_factura_no_encontrada():
    conn = FakeConnection(fetchrow_result=None)
    pool = FakePool(conn)

    with pytest.raises(repository.FacturaNoEncontradaError):
        await repository.obtener_factura(pool, uuid.uuid4())


async def test_crear_factura_serializa_items_inventario():
    producto_id = uuid.uuid4()
    conn = FakeConnection(
        fetchrow_result=_fila_factura(items_inventario='[{"numero_linea": "1", "producto_id": "x", "cantidad": "2"}]')
    )
    pool = FakePool(conn)

    resultado = await repository.crear_factura(
        pool,
        tipo_ecf=31,
        ambiente="test",
        rnc_emisor="131234567",
        encf="E310000000001",
        datos={"Encabezado": {}},
        items_inventario=[{"numero_linea": "1", "producto_id": producto_id, "cantidad": "2"}],
    )

    assert isinstance(resultado["items_inventario"], list)
    query, args = conn.queries[0]
    assert "items_inventario" in query
    items_json = args[-1]
    assert str(producto_id) in items_json


async def test_crear_factura_sin_items_inventario_guarda_null():
    conn = FakeConnection(fetchrow_result=_fila_factura())
    pool = FakePool(conn)

    await repository.crear_factura(
        pool, tipo_ecf=31, ambiente="test", rnc_emisor="131234567", encf="E310000000001", datos={"Encabezado": {}}
    )

    _, args = conn.queries[0]
    assert args[-1] is None


async def test_obtener_factura_encontrada():
    fila = _fila_factura()
    conn = FakeConnection(fetchrow_result=fila)
    pool = FakePool(conn)

    resultado = await repository.obtener_factura(pool, fila["id"])

    assert resultado["id"] == str(fila["id"])
    assert resultado["rnc_emisor"] == "131234567"


async def test_actualizar_datos_no_encontrada_o_no_borrador():
    conn = FakeConnection(fetchrow_result=None)
    pool = FakePool(conn)

    with pytest.raises(repository.FacturaNoEncontradaError):
        await repository.actualizar_datos(pool, uuid.uuid4(), encf="X", datos={})


async def test_eliminar_factura_no_encontrada():
    conn = FakeConnection(execute_result="DELETE 0")
    pool = FakePool(conn)

    with pytest.raises(repository.FacturaNoEncontradaError):
        await repository.eliminar_factura(pool, uuid.uuid4())


async def test_eliminar_factura_ok():
    conn = FakeConnection(execute_result="DELETE 1")
    pool = FakePool(conn)

    await repository.eliminar_factura(pool, uuid.uuid4())  # no debe lanzar


async def test_guardar_xml_generado_actualiza_estado():
    fila = _fila_factura(estado="generado", xml_generado="<ECF/>")
    conn = FakeConnection(fetchrow_result=fila)
    pool = FakePool(conn)

    resultado = await repository.guardar_xml_generado(pool, fila["id"], "<ECF/>")

    assert resultado["estado"] == "generado"
    assert resultado["xml_generado"] == "<ECF/>"


async def test_registrar_resultado_envio_guarda_track_id():
    fila = _fila_factura(estado="enviado", track_id="track-123")
    conn = FakeConnection(fetchrow_result=fila)
    pool = FakePool(conn)

    resultado = await repository.registrar_resultado_envio(
        pool, fila["id"], estado="enviado", track_id="track-123", dgii_respuesta={"trackId": "track-123"}
    )

    assert resultado["estado"] == "enviado"
    assert resultado["track_id"] == "track-123"


async def test_listar_facturas_aplica_filtros():
    conn = FakeConnection(fetch_result=[_fila_factura()])
    pool = FakePool(conn)

    resultado = await repository.listar_facturas(pool, tipo_ecf=31, estado="borrador")

    assert len(resultado) == 1
    query, params = conn.queries[0]
    assert "tipo_ecf = $1" in query
    assert "estado = $2" in query
    assert params[:2] == (31, "borrador")
