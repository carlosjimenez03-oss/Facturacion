import pytest
from fastapi.testclient import TestClient

from libs.ecf_common.auth.jwt_tokens import emitir_token
from services.receptor_aprobacion_comercial.app import main

XML_ACECF = (
    b'<?xml version="1.0" encoding="UTF-8"?>'
    b"<ACECF><DetalleAprobacionComercial>"
    b"<RNCEmisor>131234567</RNCEmisor><eNCF>E310000000001</eNCF><Estado>1</Estado>"
    b"</DetalleAprobacionComercial></ACECF>"
)


@pytest.fixture
def client(monkeypatch, credencial_prueba):
    async def fake_crear_pool(database_url):
        return None

    async def fake_cerrar_pool(pool):
        return None

    monkeypatch.setattr(main, "crear_pool", fake_crear_pool)
    monkeypatch.setattr(main, "cerrar_pool", fake_cerrar_pool)
    monkeypatch.setattr(main, "cargar_certificado_p12", lambda path, password: credencial_prueba)
    with TestClient(main.app) as c:
        yield c


def test_health(client):
    assert client.get("/health").json()["status"] == "ok"


def test_recibir_sin_token_falla_401(client):
    respuesta = client.post(
        "/fe/aprobacioncomercial/api/ecf", files={"xml": ("acecf.xml", XML_ACECF, "text/xml")}
    )

    assert respuesta.status_code == 401


def test_recibir_token_invalido_falla_401(client):
    respuesta = client.post(
        "/fe/aprobacioncomercial/api/ecf",
        files={"xml": ("acecf.xml", XML_ACECF, "text/xml")},
        headers={"authorization": "Bearer invalido"},
    )

    assert respuesta.status_code == 401


def test_recibir_ok_con_token_valido(client, credencial_prueba):
    token = emitir_token(credencial_prueba, "131234567").token

    respuesta = client.post(
        "/fe/aprobacioncomercial/api/ecf",
        files={"xml": ("acecf.xml", XML_ACECF, "text/xml")},
        headers={"authorization": f"Bearer {token}"},
    )

    assert respuesta.status_code == 200
    assert respuesta.json()["status"] == "ok"


def test_recibir_sin_autenticacion_requerida_no_exige_bearer(client, monkeypatch):
    monkeypatch.setattr(main.settings, "requiere_autenticacion", False)

    respuesta = client.post(
        "/fe/aprobacioncomercial/api/ecf", files={"xml": ("acecf.xml", XML_ACECF, "text/xml")}
    )

    assert respuesta.status_code == 200


def test_recibir_xml_mal_formado_falla_400(client, monkeypatch):
    monkeypatch.setattr(main.settings, "requiere_autenticacion", False)

    respuesta = client.post(
        "/fe/aprobacioncomercial/api/ecf", files={"xml": ("acecf.xml", b"no es xml", "text/xml")}
    )

    assert respuesta.status_code == 400
