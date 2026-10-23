"""El DDL que erp_api aplica al arrancar (Supabase) y db/init/007_ventas.sql
y 008_impuestos.sql (Postgres local) deben ser el mismo."""
from pathlib import Path

from services.erp_api.app import migraciones

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_007_ventas_tiene_el_mismo_ddl_que_migraciones():
    archivo = (REPO_ROOT / "db" / "init" / "007_ventas.sql").read_text(encoding="utf-8")
    assert migraciones.DDL_VENTAS.strip() in archivo


def test_008_impuestos_tiene_el_mismo_ddl_que_migraciones():
    archivo = (REPO_ROOT / "db" / "init" / "008_impuestos.sql").read_text(encoding="utf-8")
    assert migraciones.DDL_IMPUESTOS.strip() in archivo


def test_ddl_es_idempotente():
    for ddl in (migraciones.DDL_VENTAS, migraciones.DDL_IMPUESTOS):
        for linea in ddl.splitlines():
            if linea.startswith("CREATE "):
                assert "IF NOT EXISTS" in linea, linea


class _NullTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeConnection:
    def __init__(self):
        self.execute_calls: list[tuple[str, tuple]] = []

    def transaction(self):
        return _NullTransaction()

    async def execute(self, query, *args):
        self.execute_calls.append((query, args))


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


async def test_aplicar_migraciones_toma_el_lock_antes_del_ddl():
    conn = _FakeConnection()

    await migraciones.aplicar_migraciones(_FakePool(conn))

    assert "pg_advisory_xact_lock" in conn.execute_calls[0][0]
    assert conn.execute_calls[1][0] == migraciones.DDL_VENTAS
    assert conn.execute_calls[2][0] == migraciones.DDL_IMPUESTOS
