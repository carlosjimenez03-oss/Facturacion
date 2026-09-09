import pytest
from fastapi.testclient import TestClient
from lxml import etree

from libs.ecf_common.auth.signing import firmar_xml
from services.receptor_autenticacion.app import main


@pytest.fixture
def client(monkeypatch):
    async def fake_crear_pool(database_url):
        return None

    async def fake_cerrar_pool(pool):
        return None

    monkeypatch.setattr(main, "crear_pool", fake_crear_pool)
    monkeypatch.setattr(main, "cerrar_pool", fake_cerrar_pool)
    with TestClient(main.app) as c:
        yield c


def _obtener_y_firmar_semilla(client, credencial):
    respuesta = client.get("/fe/autenticacion/api/semilla")
    assert respuesta.status_code == 200
    firmado = firmar_xml(respuesta.content, credencial)
    return firmado


def test_health(client):
    assert client.get("/health").json()["status"] == "ok"


def test_obtener_semilla_devuelve_xml_con_valor_y_fecha(client):
    respuesta = client.get("/fe/autenticacion/api/semilla")

    assert respuesta.status_code == 200
    assert respuesta.headers["content-type"].startswith("text/xml")
    root = etree.fromstring(respuesta.content)
    assert root.tag == "SemillaModel"
    assert root.findtext("valor")
    assert root.findtext("fecha")


def test_validar_certificado_ok_emite_token(client, monkeypatch, credencial_con_rnc, credencial_prueba):
    llamante = credencial_con_rnc("101672919")
    firmado = _obtener_y_firmar_semilla(client, llamante)

    monkeypatch.setattr(main, "cargar_certificado_p12", lambda path, password: credencial_prueba)

    respuesta = client.post(
        "/fe/autenticacion/api/validacioncertificado",
        files={"xml": ("semilla.xml", firmado, "text/xml")},
    )

    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["token"]
    assert cuerpo["expira"].endswith("Z")
    assert cuerpo["expedido"].endswith("Z")


def test_validar_certificado_responde_xml_si_accept_lo_pide(
    client, monkeypatch, credencial_con_rnc, credencial_prueba
):
    llamante = credencial_con_rnc("101672919")
    firmado = _obtener_y_firmar_semilla(client, llamante)

    monkeypatch.setattr(main, "cargar_certificado_p12", lambda path, password: credencial_prueba)

    respuesta = client.post(
        "/fe/autenticacion/api/validacioncertificado",
        files={"xml": ("semilla.xml", firmado, "text/xml")},
        headers={"accept": "application/xml"},
    )

    assert respuesta.status_code == 200
    root = etree.fromstring(respuesta.content)
    assert root.tag == "RespuestaAutenticacion"
    assert root.findtext("token")


def test_validar_certificado_semilla_reutilizada_falla(
    client, monkeypatch, credencial_con_rnc, credencial_prueba
):
    llamante = credencial_con_rnc("101672919")
    firmado = _obtener_y_firmar_semilla(client, llamante)
    monkeypatch.setattr(main, "cargar_certificado_p12", lambda path, password: credencial_prueba)

    primera = client.post(
        "/fe/autenticacion/api/validacioncertificado", files={"xml": ("semilla.xml", firmado, "text/xml")}
    )
    segunda = client.post(
        "/fe/autenticacion/api/validacioncertificado", files={"xml": ("semilla.xml", firmado, "text/xml")}
    )

    assert primera.status_code == 200
    assert segunda.status_code == 400


def test_validar_certificado_firma_invalida_falla(client, credencial_con_rnc):
    llamante = credencial_con_rnc("101672919")
    firmado = _obtener_y_firmar_semilla(client, llamante)
    alterado = firmado.replace(b"<valor>", b"<valor>x")

    respuesta = client.post(
        "/fe/autenticacion/api/validacioncertificado",
        files={"xml": ("semilla.xml", alterado, "text/xml")},
    )

    assert respuesta.status_code == 400


def test_validar_certificado_sin_firma_falla(client):
    respuesta = client.post(
        "/fe/autenticacion/api/validacioncertificado",
        files={"xml": ("semilla.xml", b"<SemillaModel><valor>x</valor></SemillaModel>", "text/xml")},
    )

    assert respuesta.status_code == 400


def test_validar_certificado_semilla_desconocida_falla(client, credencial_con_rnc):
    """Firmar un SemillaModel con un <valor> inventado (no emitido por
    nuestro propio GET /semilla) debe rechazarse igual que uno reusado."""
    llamante = credencial_con_rnc("101672919")
    xml_falso = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b"<SemillaModel><valor>no-emitido-por-nosotros</valor><fecha>2026-01-01T00:00:00Z</fecha></SemillaModel>"
    )
    firmado = firmar_xml(xml_falso, llamante)

    respuesta = client.post(
        "/fe/autenticacion/api/validacioncertificado",
        files={"xml": ("semilla.xml", firmado, "text/xml")},
    )

    assert respuesta.status_code == 400
