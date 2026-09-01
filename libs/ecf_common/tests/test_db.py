import pytest

from libs.ecf_common.db import crear_pool, registrar_envio


class _FakeConnection:
    def __init__(self, llamadas: list):
        self._llamadas = llamadas

    async def execute(self, query, *args):
        self._llamadas.append((query, args))


class _FakeAcquireContext:
    def __init__(self, conn: _FakeConnection):
        self._conn = conn

    async def __aenter__(self) -> _FakeConnection:
        return self._conn

    async def __aexit__(self, *exc_info):
        return False


class _FakePool:
    def __init__(self):
        self.llamadas: list = []

    def acquire(self):
        return _FakeAcquireContext(_FakeConnection(self.llamadas))


class _FakePoolQueFalla(_FakePool):
    def acquire(self):
        raise RuntimeError("conexión perdida")


@pytest.mark.asyncio
async def test_crear_pool_sin_database_url_retorna_none():
    assert await crear_pool("") is None


@pytest.mark.asyncio
async def test_crear_pool_url_invalida_no_lanza():
    pool = await crear_pool("postgresql://usuario:clave@host-inexistente:5432/db")
    assert pool is None


@pytest.mark.asyncio
async def test_registrar_envio_sin_pool_es_noop():
    # No debe lanzar excepción aunque el pool sea None.
    await registrar_envio(
        None, modulo="recepcion", ambiente="test", rnc_emisor="101672919", encf="E310000000001"
    )


@pytest.mark.asyncio
async def test_registrar_envio_inserta_fila():
    pool = _FakePool()

    await registrar_envio(
        pool,
        modulo="recepcion",
        ambiente="test",
        rnc_emisor="101672919",
        encf="E310000000001",
        track_id="track-1",
        estado="Aceptado",
        codigo="1",
        respuesta={"trackId": "track-1"},
    )

    assert len(pool.llamadas) == 1
    query, args = pool.llamadas[0]
    assert "INSERT INTO facturacion.envios_dgii" in query
    assert args[0] == "recepcion"
    assert args[3] == "E310000000001"


@pytest.mark.asyncio
async def test_registrar_envio_no_lanza_si_falla_la_insercion():
    pool = _FakePoolQueFalla()

    # No debe propagar la excepción del pool.
    await registrar_envio(pool, modulo="recepcion", ambiente="test", rnc_emisor="101672919")
