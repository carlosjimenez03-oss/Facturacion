"""El DDL que inventario_api aplica al arrancar (Supabase) y db/init/004 y
011 (Postgres local) deben ser el mismo."""
from pathlib import Path

from services.inventario_api.app import migraciones

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_004_inventario_tiene_el_mismo_ddl_que_migraciones():
    archivo = (REPO_ROOT / "db" / "init" / "004_inventario.sql").read_text(encoding="utf-8")
    assert migraciones.DDL_BASE.strip() in archivo


def test_011_almacenes_lotes_tiene_el_mismo_ddl_que_migraciones():
    archivo = (REPO_ROOT / "db" / "init" / "011_inventario_almacenes_lotes.sql").read_text(encoding="utf-8")
    assert migraciones.DDL_FASE4.strip() in archivo


def test_ddl_es_idempotente():
    for ddl in (migraciones.DDL_BASE, migraciones.DDL_FASE4):
        for linea in ddl.splitlines():
            if linea.startswith("CREATE "):
                assert "IF NOT EXISTS" in linea or linea.startswith("CREATE OR REPLACE "), linea
            if linea.strip().startswith("ADD COLUMN"):
                assert "ADD COLUMN IF NOT EXISTS" in linea, linea
            if linea.startswith("DROP "):
                assert "IF EXISTS" in linea, linea


class _NullTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeConnection:
    def __init__(self, movimientos_existe: bool):
        self.execute_calls: list[tuple[str, tuple]] = []
        self._movimientos_existe = movimientos_existe

    def transaction(self):
        return _NullTransaction()

    async def execute(self, query, *args):
        self.execute_calls.append((query, args))

    async def fetchval(self, query, *args):
        assert "to_regclass('inventario.movimientos')" in query
        return "inventario.movimientos" if self._movimientos_existe else None


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


async def test_base_vacia_aplica_004_y_fase4_tras_el_lock():
    conn = _FakeConnection(movimientos_existe=False)

    await migraciones.aplicar_migraciones(_FakePool(conn))

    assert "pg_advisory_xact_lock" in conn.execute_calls[0][0]
    assert conn.execute_calls[1][0] == migraciones.DDL_BASE
    assert conn.execute_calls[2][0] == migraciones.DDL_FASE4


async def test_base_existente_no_vuelve_a_correr_004():
    """Re-ejecutar DDL_BASE recrearía el índice único por (factura,
    producto) que la Fase 4 elimina."""
    conn = _FakeConnection(movimientos_existe=True)

    await migraciones.aplicar_migraciones(_FakePool(conn))

    assert [c[0] for c in conn.execute_calls[1:]] == [migraciones.DDL_FASE4]
