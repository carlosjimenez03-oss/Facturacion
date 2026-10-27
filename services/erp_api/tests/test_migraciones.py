"""El DDL que erp_api aplica al arrancar (Supabase) y db/init/007_ventas.sql,
008_impuestos.sql, 009_fecha_dgii_segura.sql, 010_compras.sql,
012_bancos_contabilidad.sql, 013_configuracion.sql, 014_operacion.sql y
015_nomina.sql ... 019_pagos_en_linea.sql (Postgres local) deben ser el mismo."""
from pathlib import Path

import asyncpg
import pytest

from libs.ecf_common import multiempresa_ddl
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


def test_014_operacion_tiene_el_mismo_ddl_que_migraciones():
    archivo = (REPO_ROOT / "db" / "init" / "014_operacion.sql").read_text(encoding="utf-8")
    assert migraciones.DDL_OPERACION.strip() in archivo


def test_013_configuracion_tiene_el_mismo_ddl_que_migraciones():
    archivo = (REPO_ROOT / "db" / "init" / "013_configuracion.sql").read_text(encoding="utf-8")
    assert migraciones.DDL_CONFIGURACION.strip() in archivo


def test_015_nomina_tiene_el_mismo_ddl_que_migraciones():
    archivo = (REPO_ROOT / "db" / "init" / "015_nomina.sql").read_text(encoding="utf-8")
    assert migraciones.DDL_NOMINA.strip() in archivo


def test_016_soluciones_tiene_el_mismo_ddl_que_migraciones():
    archivo = (REPO_ROOT / "db" / "init" / "016_soluciones.sql").read_text(encoding="utf-8")
    assert migraciones.DDL_SOLUCIONES.strip() in archivo


def test_017_contingencia_tiene_el_mismo_ddl_que_migraciones():
    archivo = (REPO_ROOT / "db" / "init" / "017_contingencia.sql").read_text(encoding="utf-8")
    assert migraciones.DDL_CONTINGENCIA.strip() in archivo
    assert "'factura_papel'" in migraciones.DDL_CONTINGENCIA


def test_019_pagos_en_linea_tiene_el_mismo_ddl_que_migraciones():
    archivo = (REPO_ROOT / "db" / "init" / "019_pagos_en_linea.sql").read_text(encoding="utf-8")
    assert migraciones.DDL_PAGOS.strip() in archivo
    # Un cobro por intento aprobado (idempotencia de los retornos del banco).
    assert "pagos_en_linea_cobro_idx" in migraciones.DDL_PAGOS


def test_ddl_es_idempotente():
    for ddl in (
        migraciones.DDL_VENTAS, migraciones.DDL_IMPUESTOS, migraciones.DDL_FUNCIONES, migraciones.DDL_COMPRAS,
        migraciones.DDL_CONTABILIDAD, migraciones.DDL_BANCOS, migraciones.DDL_CONFIGURACION,
        migraciones.DDL_OPERACION, migraciones.DDL_NOMINA, migraciones.DDL_SOLUCIONES,
        migraciones.DDL_CONTINGENCIA, migraciones.DDL_PAGOS, migraciones.DDL_PADRON,
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
    # El lock compartido de la multiempresa ANTES que el propio y que
    # cualquier tabla (todos los servicios en el mismo orden: sin deadlocks).
    assert conn.execute_calls[1] == ("SELECT pg_advisory_xact_lock($1)", (multiempresa_ddl.LOCK_MULTIEMPRESA,))
    assert "pg_advisory_xact_lock" in conn.execute_calls[2][0]
    assert conn.execute_calls[3][0] == migraciones.DDL_VENTAS
    assert conn.execute_calls[4][0] == migraciones.DDL_IMPUESTOS
    assert conn.execute_calls[5][0] == migraciones.DDL_FUNCIONES
    assert conn.execute_calls[6][0] == migraciones.DDL_COMPRAS
    assert conn.execute_calls[7][0] == migraciones.DDL_CONTABILIDAD
    assert conn.execute_calls[8][0] == migraciones.DDL_BANCOS
    assert conn.execute_calls[9][0] == migraciones.DDL_CONFIGURACION
    assert conn.execute_calls[10][0] == migraciones.DDL_OPERACION
    assert conn.execute_calls[11][0] == migraciones.DDL_NOMINA
    # Pagos en línea después de soluciones (necesita soluciones.portal_enlaces
    # y ventas.cobros); el ambiente de las secuencias y, al final, la
    # multiempresa (DDL común con su lock compartido y el de erp-api).
    llamadas = [c[0] for c in conn.execute_calls]
    assert llamadas.index(migraciones.DDL_PAGOS) > llamadas.index(migraciones.DDL_SOLUCIONES)
    assert llamadas.index(migraciones.DDL_AMBIENTE) < llamadas.index(multiempresa_ddl.DDL_MULTIEMPRESA)
    assert ("SELECT pg_advisory_xact_lock($1)", (multiempresa_ddl.LOCK_MULTIEMPRESA,)) in conn.execute_calls
    assert llamadas[-2:] == [multiempresa_ddl.DDL_MULTIEMPRESA, migraciones.DDL_MULTIEMPRESA]


def test_022_padron_rnc_tiene_el_mismo_ddl_que_migraciones():
    archivo = (REPO_ROOT / "db" / "init" / "022_padron_rnc.sql").read_text(encoding="utf-8")
    assert migraciones.DDL_PADRON.strip() in archivo
    # Global: no se aísla por empresa (ni columna empresa_id ni política).
    assert "padron_rnc" not in migraciones.DDL_MULTIEMPRESA
    assert "empresa_id integer" not in migraciones.DDL_PADRON and "ROW LEVEL SECURITY" not in migraciones.DDL_PADRON
    # Los permisos de kontara_app / kontara_todas: kontara_conceder del esquema.
    assert "kontara_conceder(ARRAY['configuracion'])" in archivo


async def test_el_padron_se_crea_antes_de_la_multiempresa():
    conn = _FakeConnection()
    await migraciones.aplicar_migraciones(_FakePool(conn))
    llamadas = [c[0] for c in conn.execute_calls]
    assert llamadas.index(migraciones.DDL_PADRON) < llamadas.index(multiempresa_ddl.DDL_MULTIEMPRESA)


def test_021_tiene_el_mismo_ddl_de_multiempresa():
    archivo = (REPO_ROOT / "db" / "init" / "021_multiempresa.sql").read_text(encoding="utf-8")
    assert multiempresa_ddl.DDL_MULTIEMPRESA.strip() in archivo
    assert migraciones.DDL_MULTIEMPRESA.strip() in archivo


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


def test_018_ambiente_tiene_el_mismo_ddl_que_migraciones():
    from services.erp_api.app.migraciones_configuracion import DDL_AMBIENTE

    archivo = (REPO_ROOT / "db" / "init" / "020_fase7_fiscal.sql").read_text(encoding="utf-8")
    assert DDL_AMBIENTE.strip() in archivo
    for linea in DDL_AMBIENTE.splitlines():
        if linea.startswith("CREATE "):
            assert "IF NOT EXISTS" in linea, linea
    # Las secuencias que ya existían quedan en el ambiente de la instalación.
    assert "current_setting('kontara.ambiente_defecto', true)" in DDL_AMBIENTE


async def test_aplicar_migraciones_pone_el_ambiente_de_las_secuencias_antes_del_ddl():
    from services.erp_api.app.migraciones_configuracion import DDL_AMBIENTE

    conn = _FakeConnection()
    await migraciones.aplicar_migraciones(_FakePool(conn), ambiente_defecto="production")
    consultas = [q for q, _ in conn.execute_calls]
    i = consultas.index(DDL_AMBIENTE)
    assert "set_config('kontara.ambiente_defecto'" in consultas[i - 1]
    assert conn.execute_calls[i - 1][1] == ("production",)
    # Un APP_ENV desconocido no rompe el CHECK: queda en 'test'.
    conn = _FakeConnection()
    await migraciones.aplicar_migraciones(_FakePool(conn), ambiente_defecto="staging")
    assert ("test",) in [a for _, a in conn.execute_calls]
