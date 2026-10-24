"""El DDL que inventario_api aplica al arrancar (Supabase) y db/init/004 y
011 (Postgres local) deben ser el mismo."""
import logging
from pathlib import Path

import asyncpg
import pytest

from libs.ecf_common import multiempresa_ddl
from services.inventario_api.app import migraciones

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_004_inventario_tiene_el_mismo_ddl_que_migraciones():
    archivo = (REPO_ROOT / "db" / "init" / "004_inventario.sql").read_text(encoding="utf-8")
    assert migraciones.DDL_BASE.strip() in archivo


def test_011_almacenes_lotes_tiene_el_mismo_ddl_que_migraciones():
    archivo = (REPO_ROOT / "db" / "init" / "011_inventario_almacenes_lotes.sql").read_text(encoding="utf-8")
    assert migraciones.DDL_FASE4.strip() in archivo


def test_021_tiene_el_mismo_ddl_de_multiempresa():
    archivo = (REPO_ROOT / "db" / "init" / "021_multiempresa.sql").read_text(encoding="utf-8")
    assert multiempresa_ddl.DDL_MULTIEMPRESA.strip() in archivo
    assert migraciones.DDL_MULTIEMPRESA.strip() in archivo


def test_ddl_es_idempotente():
    for ddl in (migraciones.DDL_BASE, migraciones.DDL_FASE4):
        for linea in ddl.splitlines():
            if linea.startswith("CREATE "):
                assert "IF NOT EXISTS" in linea or linea.startswith("CREATE OR REPLACE "), linea
            if linea.strip().startswith("ADD COLUMN"):
                assert "ADD COLUMN IF NOT EXISTS" in linea, linea
            if linea.startswith("DROP "):
                assert "IF EXISTS" in linea, linea


def test_cada_migracion_anota_su_version():
    """Si el DDL no anotara su versión, se volvería a correr en cada arranque."""
    for migracion in migraciones.MIGRACIONES:
        assert (
            f"INSERT INTO inventario.schema_version (version, descripcion)\nVALUES ({migracion.version}, "
            in migracion.ddl
        )
    versiones = [m.version for m in migraciones.MIGRACIONES]
    assert versiones == sorted(set(versiones))
    assert migraciones.VERSION_ACTUAL == versiones[-1]


class _NullTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeConnection:
    """Responde lo mínimo: si existen `movimientos` y `schema_version`, la
    versión anotada, `deadlock_timeout` y (opcional) un error en el LOCK
    TABLE de los primeros intentos."""

    def __init__(self, *, movimientos_existe: bool, version: int | None, fallas_lock: list[Exception] | None = None):
        self.execute_calls: list[str] = []
        self._movimientos_existe = movimientos_existe
        self._version = version
        self._fallas_lock = list(fallas_lock or [])
        self.descuadres: list[dict] = []

    def transaction(self):
        return _NullTransaction()

    async def execute(self, query, *args):
        if query.startswith("LOCK TABLE") and self._fallas_lock:
            self.execute_calls.append(query)
            raise self._fallas_lock.pop(0)
        self.execute_calls.append(query)
        if query == migraciones.DDL_BASE:
            self._movimientos_existe = True
        if query in [m.ddl for m in migraciones.MIGRACIONES]:
            self._version = migraciones.VERSION_ACTUAL

    async def fetchval(self, query, *args):
        if "to_regclass('inventario.movimientos')" in query:
            return "inventario.movimientos" if self._movimientos_existe else None
        if "deadlock_timeout" in query:
            return 800
        if "to_regclass('inventario.schema_version')" in query:
            return None if self._version is None else "inventario.schema_version"
        assert "FROM inventario.schema_version" in query
        return self._version

    async def fetch(self, query, *args):
        assert "inventario.existencias" in query  # verificar_existencias
        return self.descuadres


class _AcquireCtx:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return False


class _FakePool:
    def __init__(self, conn):
        self._conn = conn

    def acquire(self):
        return _AcquireCtx(self._conn)


def _sin_sql_de_control(calls: list[str]) -> list[str]:
    return [
        c
        for c in calls
        if not c.startswith(("SET LOCAL", "SELECT pg_advisory_xact_lock", "LOCK TABLE", "SELECT set_config"))
    ]


# Lo que corren las migraciones pendientes (11 y la 21 de la multiempresa,
# precedida del DDL común) sin el SQL de control.
_DDL_PENDIENTE = [migraciones.DDL_FASE4, multiempresa_ddl.DDL_MULTIEMPRESA, migraciones.DDL_MULTIEMPRESA]


async def test_base_vacia_aplica_004_y_fase4_tras_el_lock():
    conn = _FakeConnection(movimientos_existe=False, version=None)

    aplicadas = await migraciones.aplicar_migraciones(_FakePool(conn))

    assert aplicadas == [11, 21]
    assert conn.execute_calls[0] == "SET LOCAL lock_timeout = '5s'"
    assert "pg_advisory_xact_lock" in conn.execute_calls[1]
    assert conn.execute_calls[2] == migraciones.DDL_BASE
    # Espera por tabla menor que deadlock_timeout, y 5 s de nuevo para el DDL.
    assert conn.execute_calls[3] == "SET LOCAL lock_timeout = '800ms'"
    assert conn.execute_calls[4].startswith("LOCK TABLE inventario.productos, inventario.movimientos")
    assert conn.execute_calls[4].endswith("IN ACCESS EXCLUSIVE MODE")
    assert conn.execute_calls[5] == "SET LOCAL lock_timeout = '5s'"
    assert conn.execute_calls[6] == migraciones.DDL_FASE4
    assert _sin_sql_de_control(conn.execute_calls[7:]) == _DDL_PENDIENTE[1:]


async def test_base_existente_no_vuelve_a_correr_004():
    """Re-ejecutar DDL_BASE recrearía el índice único por (factura,
    producto) que la Fase 4 elimina."""
    conn = _FakeConnection(movimientos_existe=True, version=None)

    await migraciones.aplicar_migraciones(_FakePool(conn))

    assert _sin_sql_de_control(conn.execute_calls) == _DDL_PENDIENTE


async def test_base_al_dia_no_ejecuta_ddl_ni_toma_locks():
    """El caso de todos los arranques después del primero: ni ALTER TABLE
    ni advisory lock ni LOCK TABLE (no frena las ventas en curso)."""
    conn = _FakeConnection(movimientos_existe=True, version=migraciones.VERSION_ACTUAL)

    aplicadas = await migraciones.aplicar_migraciones(_FakePool(conn))

    assert aplicadas == []
    assert conn.execute_calls == []


async def test_tabla_ocupada_reintenta_con_espera_creciente():
    ocupada = asyncpg.LockNotAvailableError("could not obtain lock on relation")
    conn = _FakeConnection(
        movimientos_existe=True, version=None, fallas_lock=[ocupada, asyncpg.DeadlockDetectedError("deadlock")]
    )
    esperas: list[float] = []

    async def dormir(segundos):
        esperas.append(segundos)

    aplicadas = await migraciones.aplicar_migraciones(_FakePool(conn), dormir=dormir)

    assert aplicadas == [11, 21]
    assert esperas == [0.5, 1.0]
    assert _sin_sql_de_control(conn.execute_calls) == _DDL_PENDIENTE


async def test_tabla_ocupada_en_todos_los_intentos_propaga_el_error():
    fallas = [asyncpg.LockNotAvailableError("ocupada") for _ in range(3)]
    conn = _FakeConnection(movimientos_existe=True, version=None, fallas_lock=fallas)

    async def dormir(segundos):
        pass

    with pytest.raises(asyncpg.LockNotAvailableError):
        await migraciones.aplicar_migraciones(_FakePool(conn), reintentos=3, dormir=dormir)


async def test_descuadre_de_existencias_solo_se_reporta(caplog):
    conn = _FakeConnection(movimientos_existe=True, version=migraciones.VERSION_ACTUAL)
    conn.descuadres = [{"sku": "A-1", "stock_actual": 5, "en_almacenes": 3}]

    with caplog.at_level(logging.WARNING, logger=migraciones.__name__):
        await migraciones.aplicar_migraciones(_FakePool(conn))

    assert "A-1 (stock_actual 5, almacenes 3)" in caplog.text
    assert conn.execute_calls == []  # no se corrige nada
