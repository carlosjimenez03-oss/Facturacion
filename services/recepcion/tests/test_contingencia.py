"""Recepción en modo contingencia: XML ya firmado por facturas_api (se envía
tal cual), 503 cuando la DGII no responde (vs. 502 si rechaza) y la sonda
GET /disponibilidad."""
import io

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from libs.ecf_common.auth.signing import firmar_xml
from libs.ecf_common.http_client import DGIIServiceError
from services.recepcion.app import main
from services.recepcion.app.schemas import RecepcionResponse
from services.recepcion.tests.test_main import FakeTokenProvider

BASE_RECEPCION = "https://ecf.dgii.gov.do/testecf/recepcion"


@pytest.fixture
def client() -> TestClient:
    return TestClient(main.app)


@pytest.fixture(autouse=True)
def _sin_auditoria(monkeypatch):
    llamadas = []

    async def registrar(pool, **kwargs):
        llamadas.append(kwargs)

    monkeypatch.setattr(main, "registrar_envio", registrar)
    monkeypatch.setattr(main, "_token_provider", FakeTokenProvider())
    return llamadas


def _cliente_dgii(monkeypatch, comportamiento):
    enviados = []

    class FakeDGIIClient:
        def __init__(self, ambiente):
            pass

        async def enviar_ecf(self, xml_firmado, nombre_archivo, token):
            enviados.append(xml_firmado)
            return comportamiento()

    monkeypatch.setattr(main, "RecepcionDGIIClient", FakeDGIIClient)
    return enviados


def _post(client, xml: bytes, **data):
    return client.post(
        "/ecf",
        data={"encf": "E310000000001", **data},
        files={"xml": ("factura.xml", io.BytesIO(xml), "text/xml")},
    )


def test_xml_ya_firmado_se_envia_byte_a_byte(client, monkeypatch, credencial_prueba):
    xml_firmado = firmar_xml(b"<ECF><Encabezado/></ECF>", credencial_prueba)

    def no_debe_firmar(*a, **k):
        raise AssertionError("no debe volver a firmar")

    monkeypatch.setattr(main, "firmar_xml", no_debe_firmar)
    monkeypatch.setattr(main, "cargar_certificado_p12", no_debe_firmar)
    enviados = _cliente_dgii(monkeypatch, lambda: RecepcionResponse(trackId="trk-1"))
    r = _post(client, xml_firmado, firmado="true")
    assert r.status_code == 200 and r.json()["trackId"] == "trk-1"
    assert enviados == [xml_firmado]


def test_xml_indicado_como_firmado_sin_firma(client, monkeypatch):
    enviados = _cliente_dgii(monkeypatch, lambda: RecepcionResponse(trackId="x"))
    r = _post(client, b"<ECF/>", firmado="true")
    assert r.status_code == 422 and enviados == []


def test_dgii_sin_conexion_es_503(client, monkeypatch, credencial_prueba, _sin_auditoria):
    def caida():
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(main, "cargar_certificado_p12", lambda path, password: credencial_prueba)
    _cliente_dgii(monkeypatch, caida)
    r = _post(client, b"<ECF>sin firmar</ECF>")
    assert r.status_code == 503 and "La DGII no responde" in r.json()["detail"]
    assert "ConnectError" in _sin_auditoria[0]["error"]


def test_dgii_5xx_es_503_y_4xx_es_502(client, monkeypatch, credencial_prueba):
    monkeypatch.setattr(main, "cargar_certificado_p12", lambda path, password: credencial_prueba)

    def error(codigo):
        def _f():
            raise DGIIServiceError(f"HTTP {codigo}", status_code=codigo)
        return _f

    _cliente_dgii(monkeypatch, error(503))
    assert _post(client, b"<ECF/>").status_code == 503
    _cliente_dgii(monkeypatch, error(400))
    assert _post(client, b"<ECF/>").status_code == 502


def test_token_sin_conexion_es_503(client, monkeypatch, credencial_prueba):
    class TokenCaido:
        async def obtener_token(self, forzar_renovacion=False, **_):
            raise httpx.ConnectTimeout("timeout")

    monkeypatch.setattr(main, "cargar_certificado_p12", lambda path, password: credencial_prueba)
    monkeypatch.setattr(main, "_token_provider", TokenCaido())
    assert _post(client, b"<ECF/>").status_code == 503


@pytest.mark.parametrize(
    ("mock", "disponible"),
    [
        ({"return_value": httpx.Response(405)}, True),
        ({"return_value": httpx.Response(200)}, True),
        ({"return_value": httpx.Response(503)}, False),
        ({"side_effect": httpx.ConnectError("refused")}, False),
    ],
)
@respx.mock
def test_disponibilidad(client, mock, disponible):
    respx.get(f"{BASE_RECEPCION}/api/facturaselectronicas").mock(**mock)
    r = client.get("/disponibilidad")
    assert r.status_code == 200 and r.json()["disponible"] is disponible
    assert (r.json()["detalle"] is None) is disponible
