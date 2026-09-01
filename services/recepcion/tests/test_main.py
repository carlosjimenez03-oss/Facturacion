import io

import pytest
from fastapi.testclient import TestClient

from libs.ecf_common.auth.schemas import TokenResponse
from libs.ecf_common.http_client import DGIIServiceError
from services.recepcion.app import main
from services.recepcion.app.schemas import RecepcionResponse


@pytest.fixture
def client() -> TestClient:
    return TestClient(main.app)


class FakeTokenProvider:
    async def obtener_token(self, forzar_renovacion: bool = False) -> TokenResponse:
        return TokenResponse(token="tok-123", expira="2099-01-01T00:00:00Z", expedido="2026-01-01T00:00:00Z")


def test_health(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "app_env": "test", "dgii_environment": "testecf"}


def test_enviar_ecf_ok(client, monkeypatch, credencial_prueba):
    monkeypatch.setattr(main, "cargar_certificado_p12", lambda path, password: credencial_prueba)
    monkeypatch.setattr(main, "firmar_xml", lambda xml_bytes, credencial: b"<ECF>firmado</ECF>")
    monkeypatch.setattr(main, "_token_provider", FakeTokenProvider())

    class FakeDGIIClient:
        def __init__(self, ambiente):
            self.ambiente = ambiente

        async def enviar_ecf(self, xml_firmado, nombre_archivo, token):
            assert xml_firmado == b"<ECF>firmado</ECF>"
            assert nombre_archivo.endswith("E310000000001.xml")
            assert token == "tok-123"
            return RecepcionResponse(trackId="track-1")

    monkeypatch.setattr(main, "RecepcionDGIIClient", FakeDGIIClient)

    response = client.post(
        "/ecf",
        data={"encf": "E310000000001"},
        files={"xml": ("factura.xml", io.BytesIO(b"<ECF>sin firmar</ECF>"), "text/xml")},
    )

    assert response.status_code == 200
    assert response.json()["trackId"] == "track-1"


def test_enviar_ecf_error_dgii(client, monkeypatch, credencial_prueba):
    monkeypatch.setattr(main, "cargar_certificado_p12", lambda path, password: credencial_prueba)
    monkeypatch.setattr(main, "firmar_xml", lambda xml_bytes, credencial: b"<ECF>firmado</ECF>")
    monkeypatch.setattr(main, "_token_provider", FakeTokenProvider())

    class FakeDGIIClientConError:
        def __init__(self, ambiente):
            pass

        async def enviar_ecf(self, xml_firmado, nombre_archivo, token):
            raise DGIIServiceError("rechazado por la DGII")

    monkeypatch.setattr(main, "RecepcionDGIIClient", FakeDGIIClientConError)

    response = client.post(
        "/ecf",
        data={"encf": "E310000000001"},
        files={"xml": ("factura.xml", io.BytesIO(b"<ECF>sin firmar</ECF>"), "text/xml")},
    )

    assert response.status_code == 502
