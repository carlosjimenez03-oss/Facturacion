"""El DDL que facturas_api aplica al arrancar (Supabase) y
db/init/013_configuracion.sql / 017_contingencia.sql /
018_pos_sin_conexion.sql (Postgres local) deben ser el mismo."""
from pathlib import Path

from services.facturas_api.app import migraciones

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_013_tiene_el_mismo_ddl_que_migraciones():
    archivo = (REPO_ROOT / "db" / "init" / "013_configuracion.sql").read_text(encoding="utf-8")
    assert migraciones.DDL_FACTURAS.strip() in archivo


def test_017_tiene_el_mismo_ddl_de_contingencia():
    archivo = (REPO_ROOT / "db" / "init" / "017_contingencia.sql").read_text(encoding="utf-8")
    assert migraciones.DDL_CONTINGENCIA.strip() in archivo


def test_018_tiene_el_mismo_ddl_de_idempotencia():
    archivo = (REPO_ROOT / "db" / "init" / "018_pos_sin_conexion.sql").read_text(encoding="utf-8")
    assert migraciones.DDL_IDEMPOTENCIA.strip() in archivo


def test_ddl_es_idempotente():
    for ddl in (migraciones.DDL_FACTURAS, migraciones.DDL_CONTINGENCIA, migraciones.DDL_IDEMPOTENCIA):
        for linea in ddl.splitlines():
            if linea.strip().startswith(("CREATE ", "ALTER ", "ADD COLUMN")):
                assert "IF NOT EXISTS" in linea or linea.strip().startswith("ALTER TABLE facturacion.facturas"), linea


def test_contingencia_no_toma_locks_de_facturas_en_cada_arranque():
    """Los ALTER TABLE / CREATE INDEX sobre facturacion.facturas van dentro
    del bloque DO que consulta el catálogo antes."""
    for ddl in (migraciones.DDL_CONTINGENCIA, migraciones.DDL_IDEMPOTENCIA):
        for linea in ddl.splitlines():
            if linea.startswith("--"):
                continue
            if "facturacion.facturas" in linea and ("ALTER TABLE" in linea or "CREATE INDEX" in linea):
                assert linea.startswith("        "), linea
    assert "column_name = 'ultimo_intento_envio'" in migraciones.DDL_CONTINGENCIA
    assert "column_name = 'clave_idempotencia'" in migraciones.DDL_IDEMPOTENCIA


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


async def test_aplicar_migraciones_toma_el_lock_y_agrega_vendedor_id_contingencia_e_idempotencia():
    conn = _Conn()
    await migraciones.aplicar_migraciones(_Pool(conn))
    assert "pg_advisory_xact_lock" in conn.llamadas[0]
    assert "vendedor_id" in conn.llamadas[1]
    assert conn.llamadas[2] == migraciones.DDL_CONTINGENCIA
    assert conn.llamadas[3] == migraciones.DDL_IDEMPOTENCIA
