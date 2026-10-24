"""El DDL que facturas_api aplica al arrancar (Supabase) y
db/init/013_configuracion.sql / 017_contingencia.sql /
018_pos_sin_conexion.sql (Postgres local) deben ser el mismo."""
from pathlib import Path

from libs.ecf_common import multiempresa_ddl
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


def test_021_tiene_el_mismo_ddl_de_multiempresa():
    archivo = (REPO_ROOT / "db" / "init" / "021_multiempresa.sql").read_text(encoding="utf-8")
    assert multiempresa_ddl.DDL_MULTIEMPRESA.strip() in archivo
    assert migraciones.DDL_MULTIEMPRESA.strip() in archivo


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
    def __init__(self, fallas=None):
        self.llamadas = []
        self.argumentos = []
        self._fallas = list(fallas or [])

    def transaction(self):
        return _Tx()

    async def execute(self, query, *args):
        if query == migraciones.DDL_FACTURAS and self._fallas:
            self.llamadas.clear()
            self.argumentos.clear()
            raise self._fallas.pop(0)
        self.llamadas.append(query)
        self.argumentos.append(args)


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
    # lock_timeout, el lock compartido de la multiempresa ANTES del propio y
    # de tocar ninguna tabla (mismo orden que erp_api e inventario_api).
    assert conn.llamadas[0] == "SET LOCAL lock_timeout = '5s'"
    assert conn.llamadas[1] == "SELECT pg_advisory_xact_lock($1)"
    assert conn.argumentos[1] == (multiempresa_ddl.LOCK_MULTIEMPRESA,)
    assert conn.argumentos[2] == (migraciones._LOCK_MIGRACIONES,)
    assert "vendedor_id" in conn.llamadas[3]
    assert conn.llamadas[4] == migraciones.DDL_CONTINGENCIA
    assert conn.llamadas[5] == migraciones.DDL_IDEMPOTENCIA
    assert conn.llamadas[6] == migraciones.DDL_FASE7


def test_ddl_de_facturas_consulta_el_catalogo_antes_del_alter():
    """En una base ya migrada no se toma ningún lock de facturacion.facturas
    (antes: ALTER TABLE ... ADD COLUMN IF NOT EXISTS en cada arranque, que
    pide ACCESS EXCLUSIVE aunque la columna exista)."""
    for ddl in (migraciones.DDL_FACTURAS, migraciones.DDL_CONTINGENCIA):
        for linea in ddl.splitlines():
            if linea.strip().startswith("--"):
                continue
            if "ALTER TABLE" in linea or "CREATE INDEX" in linea:
                assert linea.startswith("        "), linea
    assert "column_name = 'vendedor_id'" in migraciones.DDL_FACTURAS
    assert "to_regclass('facturacion.facturas_vendedor_idx')" in migraciones.DDL_FACTURAS


async def test_tabla_ocupada_reintenta_con_espera_creciente():
    """Como erp_api: con lock_timeout, si otra sesión tiene la tabla, suelta
    todo y reintenta (en lugar de esperar con los locks tomados)."""
    import asyncpg

    conn = _Conn(fallas=[asyncpg.LockNotAvailableError("ocupada"), asyncpg.DeadlockDetectedError("deadlock")])
    esperas: list[float] = []

    async def dormir(segundos):
        esperas.append(segundos)

    await migraciones.aplicar_migraciones(_Pool(conn), dormir=dormir)
    assert esperas == [1.0, 2.0]
    assert conn.llamadas.count(migraciones.DDL_FASE7) == 1


async def test_tabla_ocupada_siempre_se_rinde():
    import asyncpg
    import pytest

    conn = _Conn(fallas=[asyncpg.LockNotAvailableError("ocupada")] * 3)

    async def dormir(_segundos):
        return None

    with pytest.raises(asyncpg.LockNotAvailableError):
        await migraciones.aplicar_migraciones(_Pool(conn), reintentos=3, dormir=dormir)


def test_018_tiene_el_mismo_ddl_de_fase7():
    archivo = (REPO_ROOT / "db" / "init" / "020_fase7_fiscal.sql").read_text(encoding="utf-8")
    assert migraciones.DDL_FASE7.strip() in archivo


def test_fase7_no_toma_locks_de_facturas_en_cada_arranque():
    for linea in migraciones.DDL_FASE7.splitlines():
        if linea.strip().startswith("--"):
            continue
        if "facturacion.facturas" in linea and ("ALTER TABLE" in linea or "CREATE INDEX" in linea):
            assert linea.startswith("        "), linea
    assert "column_name = 'inventario_revertido_en'" in migraciones.DDL_FASE7
