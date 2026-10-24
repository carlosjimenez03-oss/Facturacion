"""facturas_api, fase 6: vendedor de la factura (vendedor_id fuera de
`datos`) y GET /certificado (sin llave ni contraseña)."""
import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from libs.ecf_common.tests.test_certificado_activo import crear_p12
from services.facturas_api.app import main, repository, secuencias_encf
from services.facturas_api.app.config import get_settings

AHORA = datetime(2026, 10, 24, tzinfo=timezone.utc)


@pytest.fixture
def client(monkeypatch):
    async def fake_crear_pool(database_url):
        return object()

    async def nada(pool):
        return None

    monkeypatch.setattr(main.settings, "sincronizar_estado_cada_segundos", 0)
    monkeypatch.setattr(main, "crear_pool", fake_crear_pool)
    monkeypatch.setattr(main, "cerrar_pool", nada)
    monkeypatch.setattr(main, "aplicar_migraciones", nada)
    with TestClient(main.app) as c:
        yield c


def _factura(**cambios):
    f = {"id": str(uuid.uuid4()), "tipo_ecf": 31, "ambiente": "test", "rnc_emisor": "131897542",
         "encf": "E310000000001", "estado": "borrador", "datos": {}, "creado_en": AHORA, "actualizado_en": AHORA,
         "vendedor_id": None}
    f.update(cambios)
    return f


def test_crear_y_actualizar_con_vendedor(client, monkeypatch):
    vendedor = str(uuid.uuid4())
    recibido = {}

    async def fake_crear(pool, **kw):
        recibido["crear"] = kw["vendedor_id"]
        return _factura(vendedor_id=kw["vendedor_id"])

    async def fake_obtener(pool, fid):
        return _factura(id=str(fid))

    async def fake_actualizar(pool, fid, **kw):
        recibido["actualizar"] = kw["vendedor_id"]
        return _factura(id=str(fid), vendedor_id=kw["vendedor_id"])

    async def fake_validar(pool, fid, rnc, encf):
        recibido["validar"] = encf

    monkeypatch.setattr(secuencias_encf, "crear_factura", fake_crear)
    monkeypatch.setattr(secuencias_encf, "validar_encf_para_actualizar", fake_validar)
    monkeypatch.setattr(repository, "obtener_factura", fake_obtener)
    monkeypatch.setattr(repository, "actualizar_datos", fake_actualizar)

    r = client.post("/facturas", json={"tipo_ecf": 31, "encf": "E310000000001", "datos": {}, "vendedor_id": vendedor})
    assert r.status_code == 201
    assert r.json()["vendedor_id"] == vendedor
    assert str(recibido["crear"]) == vendedor

    fid = r.json()["id"]
    r = client.put(f"/facturas/{fid}", json={"encf": "E310000000001", "datos": {}, "vendedor_id": None})
    assert r.status_code == 200
    assert recibido["actualizar"] is None
    assert recibido["validar"] == "E310000000001"
    assert client.post("/facturas", json={"tipo_ecf": 31, "datos": {}, "vendedor_id": "no-uuid"}).status_code == 422


def test_actualizar_con_e_ncf_de_otra_factura_409(client, monkeypatch):
    async def fake_obtener(pool, fid):
        return _factura(id=str(fid))

    async def fake_validar(pool, fid, rnc, encf):
        raise secuencias_encf.EncfNoDisponibleError(f"El e-NCF {encf} ya está usado en otra factura")

    async def no_debe(pool, fid, **kw):
        raise AssertionError("no debe actualizar")

    monkeypatch.setattr(repository, "obtener_factura", fake_obtener)
    monkeypatch.setattr(secuencias_encf, "validar_encf_para_actualizar", fake_validar)
    monkeypatch.setattr(repository, "actualizar_datos", no_debe)
    r = client.put(f"/facturas/{uuid.uuid4()}", json={"encf": "E310000000007", "datos": {}})
    assert r.status_code == 409
    assert "ya está usado" in r.json()["detail"]


def test_certificado_no_configurado(client, monkeypatch, tmp_path):
    monkeypatch.setenv("CERTIFICADO_ACTIVO_PATH", str(tmp_path / "activo.json"))
    monkeypatch.setattr(get_settings(), "certificado_path", str(tmp_path / "no-existe.p12"))
    r = client.get("/certificado")
    assert r.status_code == 200
    assert r.json()["estado"] == "no_configurado"


def test_certificado_configurado_sin_secretos(client, monkeypatch, tmp_path):
    p12 = tmp_path / "emisor.p12"
    p12.write_bytes(crear_p12("clave-del-p12", rnc="131897542"))
    monkeypatch.setenv("CERTIFICADO_ACTIVO_PATH", str(tmp_path / "activo.json"))
    monkeypatch.setattr(get_settings(), "certificado_path", str(p12))
    monkeypatch.setattr(get_settings(), "certificado_password", "clave-del-p12")
    monkeypatch.setattr(get_settings(), "emisor_rnc", "131897542")
    r = client.get("/certificado")
    cuerpo = r.json()
    assert cuerpo["estado"] == "vigente"
    assert cuerpo["archivo"] == "emisor.p12"
    assert cuerpo["coincide_rnc_emisor"] is True
    assert cuerpo["dias_para_vencer"] > 300
    assert "clave-del-p12" not in r.text
    assert "PRIVATE" not in r.text
