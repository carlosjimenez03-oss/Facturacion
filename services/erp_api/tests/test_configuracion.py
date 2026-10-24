"""Configuración (fase 6): cálculo de las secuencias e-NCF y rutas de
empresa, secuencias y vendedores con el repositorio mockeado. Las consultas
se prueban contra Postgres real en test_configuracion_postgres.py."""
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from services.erp_api.app import configuracion_repository as repo
from services.erp_api.app import main
from services.erp_api.app.config import get_settings
from services.erp_api.app.repository import DatosInvalidosError

HOY = date(2026, 10, 24)
AHORA = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)


def _fila(**cambios) -> dict:
    fila = {"tipo_ecf": 31, "prefijo": "E31", "desde": 1, "hasta": 5000, "proximo": 1,
            "fecha_vencimiento": date(2027, 12, 31), "activa": True, "actualizado_en": AHORA}
    fila.update(cambios)
    return fila


# --- calcular_secuencia ---------------------------------------------------------

def test_sin_configurar_propone_el_siguiente_al_ultimo_usado():
    s = repo.calcular_secuencia(31, None, 130, HOY)
    assert s["estado"] == "sin_configurar"
    assert s["siguiente_encf"] == "E310000000131"
    assert s["configurada"] is False


def test_vigente_sigue_al_mayor_usado():
    s = repo.calcular_secuencia(31, _fila(), 130, HOY)
    assert s["estado"] == "vigente"
    assert s["siguiente"] == 131
    assert s["siguiente_encf"] == "E310000000131"
    assert s["usados"] == 130
    assert s["disponibles"] == 4870
    assert s["avisos"] == []


def test_proximo_configurado_salta_numeros_usados_fuera():
    s = repo.calcular_secuencia(31, _fila(proximo=500), 130, HOY)
    assert s["siguiente"] == 500
    assert s["usados"] == 499


def test_desde_manda_si_no_hay_nada_usado():
    s = repo.calcular_secuencia(32, _fila(tipo_ecf=32, desde=1001, hasta=2000, proximo=1001), None, HOY)
    assert s["siguiente_encf"] == "E320000001001"
    assert s["usados"] == 0


def test_agotada():
    s = repo.calcular_secuencia(31, _fila(hasta=100), 100, HOY)
    assert s["estado"] == "agotada"
    assert s["disponibles"] == 0
    assert s["siguiente_encf"] is None
    assert "agotada" in s["avisos"][0]


def test_por_agotarse():
    s = repo.calcular_secuencia(31, _fila(hasta=100), 92, HOY)
    assert s["estado"] == "por_agotarse"
    assert s["disponibles"] == 8
    assert "Quedan 8" in s["avisos"][0]


def test_vencida_gana_a_agotada():
    s = repo.calcular_secuencia(31, _fila(hasta=100, fecha_vencimiento=date(2026, 10, 1)), 100, HOY)
    assert s["estado"] == "vencida"
    assert len(s["avisos"]) == 2


def test_por_vencer():
    s = repo.calcular_secuencia(31, _fila(fecha_vencimiento=date(2026, 11, 3)), 10, HOY)
    assert s["estado"] == "por_vencer"
    assert "10 días" in s["avisos"][0]


def test_vence_hoy_todavia_sirve():
    s = repo.calcular_secuencia(31, _fila(fecha_vencimiento=HOY), 10, HOY)
    assert s["estado"] == "por_vencer"
    assert s["siguiente_encf"] == "E310000000011"


def test_sin_vencimiento_tipo_32():
    s = repo.calcular_secuencia(32, _fila(tipo_ecf=32, fecha_vencimiento=None), 10, HOY)
    assert s["estado"] == "vigente"


def test_inactiva():
    s = repo.calcular_secuencia(31, _fila(activa=False), 10, HOY)
    assert s["estado"] == "inactiva"
    assert s["avisos"][0] == "La secuencia está desactivada."


# --- rutas ---------------------------------------------------------

@pytest.fixture
def client(monkeypatch):
    async def fake_crear_pool(database_url):
        return object()

    async def nada(pool):
        return None

    monkeypatch.setattr(main, "crear_pool", fake_crear_pool)
    monkeypatch.setattr(main, "cerrar_pool", nada)
    monkeypatch.setattr(main, "aplicar_migraciones", nada)
    monkeypatch.setattr(get_settings(), "emisor_rnc", "1-31-89754-2")
    monkeypatch.setattr(get_settings(), "emisor_razon_social", "Caribe Verde, SRL")
    with TestClient(main.app) as c:
        yield c


def test_empresa_sin_guardar_usa_las_variables_de_entorno(client, monkeypatch):
    async def fake(pool):
        return None

    monkeypatch.setattr(repo, "obtener_empresa", fake)
    cuerpo = client.get("/configuracion/empresa").json()
    assert cuerpo["configurada"] is False
    assert cuerpo["razon_social"] == "Caribe Verde, SRL"
    assert cuerpo["rnc"] == "131897542"
    assert cuerpo["ambiente"] == get_settings().app_env
    assert cuerpo["ambiente_dgii"] == get_settings().dgii_environment.value


def test_guardar_empresa(client, monkeypatch):
    recibido = {}

    async def fake(pool, **kw):
        recibido.update(kw)
        return {**kw, "id": 1, "actualizado_en": AHORA, "rnc": "131897542"}

    monkeypatch.setattr(repo, "guardar_empresa", fake)
    r = client.put("/configuracion/empresa", json={"razon_social": "  Caribe Verde, SRL ", "rnc": "1-31-89754-2"})
    assert r.status_code == 200
    assert recibido["razon_social"] == "Caribe Verde, SRL"
    assert r.json()["configurada"] is True


def test_guardar_empresa_invalida_422(client, monkeypatch):
    async def fake(pool, **kw):
        raise DatosInvalidosError("El RNC debe tener 9 dígitos")

    monkeypatch.setattr(repo, "guardar_empresa", fake)
    r = client.put("/configuracion/empresa", json={"razon_social": "X", "rnc": "12"})
    assert r.status_code == 422
    assert client.put("/configuracion/empresa", json={"razon_social": ""}).status_code == 422


def test_listar_y_guardar_secuencias(client, monkeypatch):
    async def fake_listar(pool, hoy):
        return [repo.calcular_secuencia(t, None, None, hoy) for t in repo.NOMBRES_TIPO]

    recibido = {}

    async def fake_guardar(pool, tipo, **kw):
        recibido.update(kw, tipo=tipo)
        return repo.calcular_secuencia(tipo, _fila(desde=kw["desde"], hasta=kw["hasta"], proximo=kw["desde"]), None, kw["hoy"])

    monkeypatch.setattr(repo, "listar_secuencias", fake_listar)
    monkeypatch.setattr(repo, "guardar_secuencia", fake_guardar)
    lista = client.get("/configuracion/secuencias").json()
    assert [s["tipo_ecf"] for s in lista] == [31, 32, 33, 34, 41, 43, 44, 45, 46, 47]
    r = client.put("/configuracion/secuencias/31", json={"desde": 1, "hasta": 5000, "fecha_vencimiento": "2027-12-31"})
    assert r.status_code == 200
    assert recibido["tipo"] == 31 and recibido["proximo"] is None
    assert r.json()["siguiente_encf"] == "E310000000001"
    assert client.put("/configuracion/secuencias/31", json={"desde": 0, "hasta": 5}).status_code == 422


def test_secuencia_tipo_invalido(client):
    assert client.get("/configuracion/secuencias/99").status_code == 422
    assert client.put("/configuracion/secuencias/99", json={"desde": 1, "hasta": 5}).status_code == 422


def test_eliminar_secuencia_inexistente(client, monkeypatch):
    async def fake(pool, tipo):
        raise repo.SecuenciaNoEncontradaError(str(tipo))

    monkeypatch.setattr(repo, "eliminar_secuencia", fake)
    assert client.delete("/configuracion/secuencias/31").status_code == 404


def _vendedor(**cambios) -> dict:
    v = {"id": str(uuid.uuid4()), "nombre": "María Rosario", "correo": None, "comision_pct": Decimal("2.5"),
         "activo": True, "creado_en": AHORA, "actualizado_en": AHORA}
    v.update(cambios)
    return v


def test_vendedores_crud(client, monkeypatch):
    v = _vendedor()

    async def fake_crear(pool, **kw):
        assert kw["comision_pct"] == Decimal("2.5")
        return v

    async def fake_actualizar(pool, vid, **kw):
        if str(vid) != v["id"]:
            raise repo.VendedorNoEncontradoError(str(vid))
        return {**v, **kw}

    async def fake_eliminar(pool, vid):
        raise repo.VendedorEnUsoError(str(vid))

    monkeypatch.setattr(repo, "crear_vendedor", fake_crear)
    monkeypatch.setattr(repo, "actualizar_vendedor", fake_actualizar)
    monkeypatch.setattr(repo, "eliminar_vendedor", fake_eliminar)
    r = client.post("/configuracion/vendedores", json={"nombre": "María Rosario", "comision_pct": "2.5"})
    assert r.status_code == 201
    assert r.json()["comision_pct"] == "2.50"
    r = client.put(f"/configuracion/vendedores/{v['id']}", json={"nombre": "María R.", "activo": False})
    assert r.json()["activo"] is False
    assert client.put(f"/configuracion/vendedores/{uuid.uuid4()}", json={"nombre": "X"}).status_code == 404
    assert client.put("/configuracion/vendedores/abc", json={"nombre": "X"}).status_code == 422
    assert client.delete(f"/configuracion/vendedores/{v['id']}").status_code == 409
    assert client.post("/configuracion/vendedores", json={"nombre": "X", "comision_pct": "150"}).status_code == 422


# --- validaciones del repositorio (sin base) ---------------------------------------------------------

class _Conn:
    def __init__(self):
        self.args = None

    async def fetchrow(self, query, *args):
        self.args = args
        return {"id": 1, "razon_social": args[0], "rnc": args[2]}


class _Acq:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *e):
        return False


class _Pool:
    def __init__(self):
        self.conn = _Conn()

    def acquire(self):
        return _Acq(self.conn)


async def test_guardar_empresa_normaliza_y_valida():
    pool = _Pool()
    campos = {"nombre_comercial": "", "direccion": None, "telefono": None, "correo": "facturacion@caribe.do", "logo": None}
    await repo.guardar_empresa(pool, razon_social="Caribe", rnc="1-31-89754-2", **campos)
    assert pool.conn.args[2] == "131897542"
    assert pool.conn.args[1] is None  # nombre comercial vacío -> NULL
    with pytest.raises(DatosInvalidosError, match="RNC"):
        await repo.guardar_empresa(pool, razon_social="Caribe", rnc="1234", **campos)
    with pytest.raises(DatosInvalidosError, match="correo"):
        await repo.guardar_empresa(pool, razon_social="Caribe", rnc=None, **{**campos, "correo": "no-es-correo"})
    with pytest.raises(DatosInvalidosError, match="logo"):
        await repo.guardar_empresa(pool, razon_social="Caribe", rnc=None, **{**campos, "logo": "javascript:alert(1)"})
    await repo.guardar_empresa(pool, razon_social="Caribe", rnc=None, **{**campos, "logo": "data:image/png;base64,iVBORw0KGgo="})


async def test_guardar_secuencia_valida_rango():
    with pytest.raises(DatosInvalidosError, match="hasta"):
        await repo.guardar_secuencia(object(), 31, desde=10, hasta=5, proximo=None, fecha_vencimiento=None, activa=True, hoy=HOY)
    with pytest.raises(DatosInvalidosError, match="próximo"):
        await repo.guardar_secuencia(object(), 31, desde=10, hasta=50, proximo=60, fecha_vencimiento=None, activa=True, hoy=HOY)
    with pytest.raises(DatosInvalidosError, match="tipo_ecf"):
        await repo.guardar_secuencia(object(), 30, desde=1, hasta=5, proximo=None, fecha_vencimiento=None, activa=True, hoy=HOY)
