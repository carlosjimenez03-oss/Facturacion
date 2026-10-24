"""Multiempresa sin base de datos: la empresa del contexto, la cabecera de
las llamadas internas, el certificado y la caché de ambiente / token de la
DGII por empresa. Lo que necesita Postgres (RLS, membresías) está en
services/erp_api/tests/test_multiempresa_postgres.py."""
import asyncio

import pytest

from libs.ecf_common import ambiente, empresa
from libs.ecf_common.auth import certificado_activo
from libs.ecf_common.auth.sesion_supabase import cabeceras_internas
from libs.ecf_common.environments import DGIIEnvironment

TOKEN = "t" * 40


def test_sin_indicar_es_la_predeterminada_y_no_viaja_cabecera():
    assert empresa.empresa_actual() == 1
    assert empresa.cabeceras_empresa() == {}
    assert cabeceras_internas(TOKEN) == {"X-Kontara-Interno": TOKEN}


def test_la_empresa_viaja_en_las_llamadas_internas():
    with empresa.en_empresa(3):
        assert empresa.empresa_actual() == 3
        assert cabeceras_internas(TOKEN) == {"X-Kontara-Interno": TOKEN, "X-Kontara-Empresa": "3"}
        assert cabeceras_internas("") == {"X-Kontara-Empresa": "3"}
        with empresa.en_empresa(None):
            assert empresa.empresa_actual() == 1
    assert empresa.empresa_actual() == 1


def test_todas_las_empresas_no_tiene_una_actual():
    with empresa.todas_las_empresas():
        with pytest.raises(RuntimeError):
            empresa.empresa_actual()
        assert empresa.cabeceras_empresa() == {}


@pytest.mark.parametrize(
    ("texto", "esperado"), [("2", 2), (" 15 ", 15), ("0", None), ("-1", None), ("1;DROP", None), ("", None),
                            ("1234567890", None)]
)
def test_empresa_de_texto(texto, esperado):
    assert empresa.empresa_de_texto(texto) == esperado


def test_las_tareas_copian_la_empresa_de_quien_las_lanza():
    async def correr():
        with empresa.en_empresa(7):
            tarea = asyncio.create_task(asyncio.sleep(0, result=None))
            vista = asyncio.create_task(_empresa_en_tarea())
            await tarea
        return await vista

    async def _empresa_en_tarea():
        return empresa.empresa_actual()

    assert asyncio.run(correr()) == 7


def test_certificado_de_cada_empresa_en_su_archivo(monkeypatch, tmp_path):
    monkeypatch.setenv("CERTIFICADO_ACTIVO_PATH", str(tmp_path / "activo.json"))
    assert certificado_activo.ruta_activo() == tmp_path / "activo.json"
    with empresa.en_empresa(4):
        assert certificado_activo.ruta_activo() == tmp_path / "empresas" / "4.json"
        assert not certificado_activo.usa_certificado_de_variables()
        estado = certificado_activo.estado_certificado("certs/no-existe.p12", "", "101000004")
        assert estado["estado"] == "no_configurado" and "Esta empresa" in estado["error"]


def test_empresa_nueva_sin_certificado_no_firma_con_el_de_las_variables(monkeypatch, tmp_path, p12_prueba):
    from libs.ecf_common.auth.signing import cargar_certificado_p12

    monkeypatch.setenv("CERTIFICADO_ACTIVO_PATH", str(tmp_path / "activo.json"))
    ruta, password = p12_prueba
    assert cargar_certificado_p12(str(ruta), password).certificate is not None  # la 1, como siempre
    with empresa.en_empresa(2), pytest.raises(FileNotFoundError, match="Esta empresa no tiene certificado"):
        cargar_certificado_p12(str(ruta), password)


def test_ambiente_y_token_dgii_por_empresa():
    activo = ambiente.AmbienteDGII("test")

    class _Pool:
        def __init__(self, valores):
            self.valores = valores

        async def fetchval(self, query, *args):
            if "to_regclass" in query:
                return "configuracion.ambiente_dgii"
            return self.valores[empresa.empresa_actual()]

    pool = _Pool({1: "production", 2: "qa"})

    async def leer():
        uno = await activo.actual(pool)
        with empresa.en_empresa(2):
            dos = await activo.actual(pool)
            activo.fijar("test")
            dos_fijado = await activo.actual(pool)
        return uno, dos, dos_fijado, await activo.actual(pool)

    assert asyncio.run(leer()) == ("production", "qa", "test", "production")

    tokens = ambiente.TokenProviderPorAmbiente("certs/x.p12", "")
    uno = tokens.para(DGIIEnvironment.TESTECF)
    with empresa.en_empresa(2):
        dos = tokens.para(DGIIEnvironment.TESTECF)
    assert uno is not dos
    assert tokens.para(DGIIEnvironment.TESTECF) is uno


def test_resolver_empresa_sin_sesion_confia_en_nginx():
    async def resolver(pedida, interna=False):
        return await empresa.resolver_empresa(None, None, pedida, interna=interna)

    assert asyncio.run(resolver(None))[0] is None
    assert asyncio.run(resolver(3))[0] == 3
    assert asyncio.run(resolver(-1))[2].status_code == 422
