"""Ambiente DGII leído en tiempo de ejecución (libs/ecf_common/ambiente.py):
la fila de Configuración manda sobre APP_ENV, con caché corta, y cualquier
problema al leerla deja APP_ENV."""
import pytest

from libs.ecf_common import ambiente as amb
from libs.ecf_common.environments import DGIIEnvironment


class _Pool:
    """Responde a los fetchval de `leer_ambiente_guardado`."""

    def __init__(self, valor="production", tabla=True, error=None):
        self.valor, self.tabla, self.error = valor, tabla, error
        self.consultas = 0

    async def fetchval(self, query, *args):
        if self.error:
            raise self.error
        if "to_regclass" in query:
            return "configuracion.ambiente_dgii" if self.tabla else None
        self.consultas += 1
        return self.valor


class _Reloj:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_validar_ambiente_acepta_app_env_y_nombre_dgii():
    assert amb.validar_ambiente("production") == "production"
    assert amb.validar_ambiente(" eCF ") == "production"
    assert amb.validar_ambiente("CerteCF") == "qa"
    assert amb.validar_ambiente("testecf") == "test"
    for malo in ("", None, "staging", "prod"):
        with pytest.raises(amb.AmbienteInvalidoError):
            amb.validar_ambiente(malo)
    assert amb.nombre("production") == "Producción (eCF)"


async def test_la_fila_manda_sobre_app_env_con_cache():
    reloj = _Reloj()
    resolvedor = amb.AmbienteDGII("test", ttl_segundos=15, reloj=reloj)
    pool = _Pool("production")
    assert await resolvedor.actual(pool) == "production"
    assert await resolvedor.origen(pool) == "configuracion"
    assert await resolvedor.dgii(pool) == DGIIEnvironment.ECF
    pool.valor = "qa"
    reloj.t = 10
    assert await resolvedor.actual(pool) == "production"  # todavía en caché
    assert pool.consultas == 1
    reloj.t = 16
    assert await resolvedor.actual(pool) == "qa"
    # Lo pedido explícitamente (el ambiente de la factura) gana sobre el activo.
    assert await resolvedor.resolver(pool, "test") == "test"
    with pytest.raises(amb.AmbienteInvalidoError):
        await resolvedor.resolver(pool, "otro")


@pytest.mark.parametrize(
    "pool",
    [None, _Pool(tabla=False), _Pool(valor=None), _Pool(error=OSError("base caída"))],
    ids=["sin_base", "sin_tabla", "sin_fila", "base_caida"],
)
async def test_sin_fila_o_sin_base_usa_app_env(pool):
    resolvedor = amb.AmbienteDGII("qa")
    assert await resolvedor.actual(pool) == "qa"
    assert await resolvedor.origen(pool) == "variables"


async def test_fijar_e_invalidar():
    reloj = _Reloj()
    resolvedor = amb.AmbienteDGII("test", reloj=reloj)
    pool = _Pool("test")
    resolvedor.fijar("ecf")
    assert await resolvedor.actual(pool) == "production" and pool.consultas == 0
    resolvedor.invalidar()
    assert await resolvedor.actual(pool) == "test" and pool.consultas == 1


async def test_un_token_provider_por_ambiente(monkeypatch):
    creados = []

    class FakeTokenProvider:
        def __init__(self, ambiente, certificado_path, certificado_password, margen_segundos):
            creados.append((ambiente, margen_segundos))
            self.ambiente = ambiente

        async def obtener_token(self, forzar_renovacion=False):
            return (self.ambiente, forzar_renovacion)

    from libs.ecf_common.auth import token_provider

    monkeypatch.setattr(token_provider, "TokenProvider", FakeTokenProvider)
    proveedores = amb.TokenProviderPorAmbiente("certs/x.p12", "clave", margen_segundos=30)
    assert await proveedores.obtener_token(ambiente=DGIIEnvironment.TESTECF) == (DGIIEnvironment.TESTECF, False)
    assert await proveedores.obtener_token(True, ambiente=DGIIEnvironment.ECF) == (DGIIEnvironment.ECF, True)
    await proveedores.obtener_token(ambiente=DGIIEnvironment.TESTECF)
    # Uno por ambiente, reutilizado: el token de TesteCF no sirve en eCF.
    assert creados == [(DGIIEnvironment.TESTECF, 30), (DGIIEnvironment.ECF, 30)]
