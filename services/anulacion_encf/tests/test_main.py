import io

import pytest
from fastapi.testclient import TestClient

from libs.ecf_common.auth.schemas import TokenResponse
from libs.ecf_common.http_client import DGIIServiceError
from services.anulacion_encf.app import main
from services.anulacion_encf.app.schemas import AnulacionRangoResponse


@pytest.fixture
def client() -> TestClient:
    return TestClient(main.app)


class FakeTokenProvider:
    async def obtener_token(self, forzar_renovacion: bool = False) -> TokenResponse:
        return TokenResponse(token="tok-123", expira="2099-01-01T00:00:00Z", expedido="2026-01-01T00:00:00Z")


def test_health(client):
    response = client.get("/health")

    assert response.status_code == 200


def test_anular_rango_ok(client, monkeypatch, credencial_prueba):
    monkeypatch.setattr(main, "cargar_certificado_p12", lambda path, password: credencial_prueba)
    monkeypatch.setattr(main, "firmar_xml", lambda xml_bytes, credencial: b"<ANECF>firmado</ANECF>")
    monkeypatch.setattr(main, "_token_provider", FakeTokenProvider())

    class FakeDGIIClient:
        def __init__(self, ambiente):
            pass

        async def anular_rango(self, xml_firmado, nombre_archivo, token):
            assert token == "tok-123"
            return AnulacionRangoResponse(rnc="101672919", codigo="1")

    monkeypatch.setattr(main, "AnulacionDGIIClient", FakeDGIIClient)

    response = client.post("/anulacion", files={"xml": ("anulacion.xml", io.BytesIO(b"<ANECF/>"), "text/xml")})

    assert response.status_code == 200
    assert response.json()["rnc"] == "101672919"


def test_anular_rango_error_dgii(client, monkeypatch, credencial_prueba):
    monkeypatch.setattr(main, "cargar_certificado_p12", lambda path, password: credencial_prueba)
    monkeypatch.setattr(main, "firmar_xml", lambda xml_bytes, credencial: b"<ANECF>firmado</ANECF>")
    monkeypatch.setattr(main, "_token_provider", FakeTokenProvider())

    class FakeDGIIClientConError:
        def __init__(self, ambiente):
            pass

        async def anular_rango(self, xml_firmado, nombre_archivo, token):
            raise DGIIServiceError("rechazado")

    monkeypatch.setattr(main, "AnulacionDGIIClient", FakeDGIIClientConError)

    response = client.post("/anulacion", files={"xml": ("anulacion.xml", io.BytesIO(b"<ANECF/>"), "text/xml")})

    assert response.status_code == 502
