"""El DDL que facturas_api aplica al arrancar (Supabase) y
db/init/013_configuracion.sql (Postgres local) deben ser el mismo."""
from pathlib import Path

from services.facturas_api.app import migraciones

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_013_tiene_el_mismo_ddl_que_migraciones():
    archivo = (REPO_ROOT / "db" / "init" / "013_configuracion.sql").read_text(encoding="utf-8")
    assert migraciones.DDL_FACTURAS.strip() in archivo


def test_ddl_es_idempotente():
    for linea in migraciones.DDL_FACTURAS.splitlines():
        if linea.startswith(("CREATE ", "ALTER ")):
            assert "IF NOT EXISTS" in linea, linea


class _Tx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Conn:
    def __init__(self):
        self.llamadas = []

    def transaction(self):
        return _Tx()

    async def execute(self, query, *args):
        self.llamadas.append(query)


class _Acq:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *exc):
        return False


class _Pool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        return _Acq(self.conn)


async def test_aplicar_migraciones_toma_el_lock_y_agrega_vendedor_id():
    conn = _Conn()
    await migraciones.aplicar_migraciones(_Pool(conn))
    assert "pg_advisory_xact_lock" in conn.llamadas[0]
    assert "vendedor_id" in conn.llamadas[1]
