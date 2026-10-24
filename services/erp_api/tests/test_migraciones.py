"""El DDL que erp_api aplica al arrancar (Supabase) y db/init/007_ventas.sql,
008_impuestos.sql, 009_fecha_dgii_segura.sql, 010_compras.sql y
012_bancos_contabilidad.sql (Postgres local) deben ser el mismo."""
from pathlib import Path

import asyncpg
import pytest

from services.erp_api.app import migraciones

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_007_ventas_tiene_el_mismo_ddl_que_migraciones():
    archivo = (REPO_ROOT / "db" / "init" / "007_ventas.sql").read_text(encoding="utf-8")
    assert migraciones.DDL_VENTAS.strip() in archivo


def test_008_impuestos_tiene_el_mismo_ddl_que_migraciones():
    archivo = (REPO_ROOT / "db" / "init" / "008_impuestos.sql").read_text(encoding="utf-8")
    assert migraciones.DDL_IMPUESTOS.strip() in archivo


def test_009_fecha_dgii_segura_tiene_el_mismo_ddl_que_migraciones():
    archivo = (REPO_ROOT / "db" / "init" / "009_fecha_dgii_segura.sql").read_text(encoding="utf-8")
    assert migraciones.DDL_FUNCIONES.strip() in archivo


def test_010_compras_tiene_el_mismo_ddl_que_migraciones():
    archivo = (REPO_ROOT / "db" / "init" / "010_compras.sql").read_text(encoding="utf-8")
    assert migraciones.DDL_COMPRAS.strip() in archivo


def test_012_bancos_contabilidad_tiene_el_mismo_ddl_que_migraciones():
    archivo = (REPO_ROOT / "db" / "init" / "012_bancos_contabilidad.sql").read_text(encoding="utf-8")
    assert migraciones.DDL_CONTABILIDAD.strip() in archivo
    assert migraciones.DDL_BANCOS.strip() in archivo
    # Contabilidad antes que Bancos: bancos.cuentas referencia contabilidad.cuentas.
    assert archivo.index(migraciones.DDL_CONTABILIDAD.strip()) < archivo.index(migraciones.DDL_BANCOS.strip())


def test_la_semilla_del_catalogo_solo_corre_con_el_catalogo_vacio():
    assert "IF NOT EXISTS (SELECT 1 FROM contabilidad.cuentas) THEN" in migraciones.DDL_CONTABILIDAD


def test_ddl_es_idempotente():
    for ddl in (
        migraciones.DDL_VENTAS, migraciones.DDL_IMPUESTOS, migraciones.DDL_FUNCIONES, migraciones.DDL_COMPRAS,
        migraciones.DDL_CONTABILIDAD, migraciones.DDL_BANCOS,
    ):
        for linea in ddl.splitlines():
            if linea.startswith("CREATE "):
                assert "IF NOT EXISTS" in linea or linea.startswith("CREATE OR REPLACE "), linea
            if linea.startswith("ALTER TABLE") or linea.strip().startswith("ADD COLUMN"):
                assert "ADD COLUMN" not in linea or "IF NOT EXISTS" in linea, linea


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

    assert conn.execute_calls[0][0].startswith("SET LOCAL lock_timeout")
    assert "pg_advisory_xact_lock" in conn.execute_calls[1][0]
    assert conn.execute_calls[2][0] == migraciones.DDL_VENTAS
    assert conn.execute_calls[3][0] == migraciones.DDL_IMPUESTOS
    assert conn.execute_calls[4][0] == migraciones.DDL_FUNCIONES
    assert conn.execute_calls[5][0] == migraciones.DDL_COMPRAS
    assert conn.execute_calls[6][0] == migraciones.DDL_CONTABILIDAD
    assert conn.execute_calls[7][0] == migraciones.DDL_BANCOS


class _ConexionOcupada(_FakeConnection):
    """Falla con lock_timeout las primeras `fallos` veces que corre DDL_BANCOS."""

    def __init__(self, fallos):
        super().__init__()
        self.fallos = fallos

    async def execute(self, query, *args):
        await super().execute(query, *args)
        if query == migraciones.DDL_BANCOS and self.fallos:
            self.fallos -= 1
            raise asyncpg.LockNotAvailableError("canceling statement due to lock timeout")


async def test_aplicar_migraciones_reintenta_si_una_tabla_esta_ocupada():
    conn = _ConexionOcupada(fallos=2)
    esperas = []

    async def dormir(segundos):
        esperas.append(segundos)

    await migraciones.aplicar_migraciones(_FakePool(conn), dormir=dormir)
    assert esperas == [1.0, 2.0]
    assert [q for q, _ in conn.execute_calls].count(migraciones.DDL_BANCOS) == 3

    with pytest.raises(asyncpg.LockNotAvailableError):
        await migraciones.aplicar_migraciones(_FakePool(_ConexionOcupada(fallos=9)), reintentos=2, dormir=dormir)


def test_las_columnas_nuevas_de_tablas_con_escrituras_solo_se_agregan_si_faltan():
    """ventas.cobros / compras.pagos: ningún ALTER TABLE ni CREATE INDEX fuera
    del bloque DO que consulta el catálogo (si no, cada arranque toma sus locks)."""
    for tabla in ("ventas.cobros", "compras.pagos"):
        for linea in migraciones.DDL_BANCOS.splitlines():
            if tabla in linea and ("ALTER TABLE" in linea or "CREATE INDEX" in linea):
                assert linea.startswith("        "), linea
    assert "column_name = 'cuenta_bancaria_id'" in migraciones.DDL_BANCOS
    assert "to_regclass('ventas.cobros_cuenta_bancaria_idx') IS NULL" in migraciones.DDL_BANCOS
