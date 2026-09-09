from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from lxml import etree

from libs.ecf_common.auth.jwt_tokens import emitir_token
from services.receptor_recepcion.app import main

XML_ECF = (
    b'<?xml version="1.0" encoding="UTF-8"?>'
    b"<ECF><Encabezado>"
    b"<Emisor><RNCEmisor>131234567</RNCEmisor></Emisor>"
    b"<Comprador><RNCComprador>101672919</RNCComprador></Comprador>"
    b"<IdDoc><eNCF>E310000000001</eNCF></IdDoc>"
    b"</Encabezado></ECF>"
)

XML_ECF_COMPLETO = (
    b'<?xml version="1.0" encoding="UTF-8"?>'
    b"<ECF><Encabezado>"
    b"<Emisor><RNCEmisor>131234567</RNCEmisor><FechaEmision>15-01-2026</FechaEmision></Emisor>"
    b"<Comprador><RNCComprador>101672919</RNCComprador></Comprador>"
    b"<IdDoc><eNCF>E310000000001</eNCF></IdDoc>"
    b"<Totales><TotalITBIS>180.00</TotalITBIS></Totales>"
    b"</Encabezado>"
    b"<DetallesItems>"
    b"<Item><IndicadorBienoServicio>1</IndicadorBienoServicio><MontoItem>600.00</MontoItem></Item>"
    b"<Item><IndicadorBienoServicio>2</IndicadorBienoServicio><MontoItem>400.00</MontoItem></Item>"
    b"</DetallesItems>"
    b"</ECF>"
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
    monkeypatch.setattr(main.settings, "emisor_rnc", "101672919")
    with TestClient(main.app) as c:
        yield c


def _token_valido(credencial_prueba) -> str:
    return emitir_token(credencial_prueba, "131234567").token


def test_health(client):
    assert client.get("/health").json()["status"] == "ok"


def test_recibir_ecf_sin_token_falla_401(client):
    respuesta = client.post("/fe/recepcion/api/ecf", files={"xml": ("e-cf.xml", XML_ECF, "text/xml")})

    assert respuesta.status_code == 401


def test_recibir_ecf_token_invalido_falla_401(client):
    respuesta = client.post(
        "/fe/recepcion/api/ecf",
        files={"xml": ("e-cf.xml", XML_ECF, "text/xml")},
        headers={"authorization": "Bearer token-invalido"},
    )

    assert respuesta.status_code == 401


def test_recibir_ecf_ok_devuelve_arecf_firmado(client, credencial_prueba):
    token = _token_valido(credencial_prueba)

    respuesta = client.post(
        "/fe/recepcion/api/ecf",
        files={"xml": ("e-cf.xml", XML_ECF, "text/xml")},
        headers={"authorization": f"Bearer {token}"},
    )

    assert respuesta.status_code == 200
    assert respuesta.headers["content-type"].startswith("text/xml")
    root = etree.fromstring(respuesta.content)
    assert root.tag == "ARECF"
    assert root.findtext(".//RNCEmisor") == "131234567"
    assert root.findtext(".//RNCComprador") == "101672919"
    assert root.findtext(".//eNCF") == "E310000000001"
    assert root.findtext(".//Estado") == "0"
    assert root.find(".//{http://www.w3.org/2000/09/xmldsig#}Signature") is not None


def test_recibir_ecf_sin_autenticacion_requerida_no_exige_bearer(client, monkeypatch):
    monkeypatch.setattr(main.settings, "requiere_autenticacion", False)

    respuesta = client.post("/fe/recepcion/api/ecf", files={"xml": ("e-cf.xml", XML_ECF, "text/xml")})

    assert respuesta.status_code == 200


def test_recibir_ecf_xml_mal_formado_falla_422(client, monkeypatch):
    monkeypatch.setattr(main.settings, "requiere_autenticacion", False)

    respuesta = client.post("/fe/recepcion/api/ecf", files={"xml": ("e-cf.xml", b"no es xml", "text/xml")})

    assert respuesta.status_code == 422


def test_recibir_ecf_dirigido_a_otro_rnc_falla_422(client, monkeypatch, credencial_prueba):
    monkeypatch.setattr(main.settings, "emisor_rnc", "999999999")
    token = _token_valido(credencial_prueba)

    respuesta = client.post(
        "/fe/recepcion/api/ecf",
        files={"xml": ("e-cf.xml", XML_ECF, "text/xml")},
        headers={"authorization": f"Bearer {token}"},
    )

    assert respuesta.status_code == 422


def test_recibir_ecf_registra_compra_606(client, monkeypatch, credencial_prueba):
    llamadas = []

    async def fake_registrar_compra_606(pool, **kwargs):
        llamadas.append(kwargs)

    monkeypatch.setattr(main, "registrar_compra_606", fake_registrar_compra_606)
    token = _token_valido(credencial_prueba)

    respuesta = client.post(
        "/fe/recepcion/api/ecf",
        files={"xml": ("e-cf.xml", XML_ECF_COMPLETO, "text/xml")},
        headers={"authorization": f"Bearer {token}"},
    )

    assert respuesta.status_code == 200
    assert len(llamadas) == 1
    llamada = llamadas[0]
    assert llamada["origen"] == "receptor_recepcion"
    assert llamada["rnc_cedula"] == "131234567"
    assert llamada["ncf"] == "E310000000001"
    assert llamada["fecha_comprobante"] == "20260115"
    assert llamada["monto_bienes"] == Decimal("600.00")
    assert llamada["monto_servicios"] == Decimal("400.00")
    assert llamada["itbis_facturado"] == Decimal("180.00")


def test_recibir_ecf_sin_fecha_emision_no_registra_compra_606(client, monkeypatch, credencial_prueba):
    llamadas = []

    async def fake_registrar_compra_606(pool, **kwargs):
        llamadas.append(kwargs)

    monkeypatch.setattr(main, "registrar_compra_606", fake_registrar_compra_606)
    token = _token_valido(credencial_prueba)

    respuesta = client.post(
        "/fe/recepcion/api/ecf",
        files={"xml": ("e-cf.xml", XML_ECF, "text/xml")},  # sin FechaEmision
        headers={"authorization": f"Bearer {token}"},
    )

    assert respuesta.status_code == 200
    assert llamadas == []


def test_recibir_ecf_ok_aunque_falle_registrar_compra_606(client, monkeypatch, credencial_prueba):
    async def fake_registrar_compra_606(pool, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(main, "registrar_compra_606", fake_registrar_compra_606)
    token = _token_valido(credencial_prueba)

    respuesta = client.post(
        "/fe/recepcion/api/ecf",
        files={"xml": ("e-cf.xml", XML_ECF_COMPLETO, "text/xml")},
        headers={"authorization": f"Bearer {token}"},
    )

    assert respuesta.status_code == 200
