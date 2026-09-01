import pytest
from fastapi.testclient import TestClient

from libs.ecf_common.auth.schemas import TokenResponse
from libs.ecf_common.http_client import DGIIServiceError
from services.consulta_estado.app import main
from services.consulta_estado.app.schemas import ConsultaEstadoResponse


@pytest.fixture
def client() -> TestClient:
    return TestClient(main.app)


class FakeTokenProvider:
    async def obtener_token(self, forzar_renovacion: bool = False) -> TokenResponse:
        return TokenResponse(token="tok-123", expira="2099-01-01T00:00:00Z", expedido="2026-01-01T00:00:00Z")


def test_health(client):
    response = client.get("/health")

    assert response.status_code == 200


def test_consultar_estado_ok(client, monkeypatch):
    monkeypatch.setattr(main, "_token_provider", FakeTokenProvider())

    class FakeDGIIClient:
        def __init__(self, ambiente):
            pass

        async def consultar(self, rnc_emisor, ncf_electronico, token, rnc_comprador=None, codigo_seguridad=None):
            assert token == "tok-123"
            return ConsultaEstadoResponse(estado="Aceptado", rncEmisor=rnc_emisor)

    monkeypatch.setattr(main, "ConsultaEstadoDGIIClient", FakeDGIIClient)

    response = client.get("/estado", params={"rnc_emisor": "131880738", "ncf_electronico": "e3100000000"})

    assert response.status_code == 200
    assert response.json()["estado"] == "Aceptado"


def test_consultar_estado_error_dgii(client, monkeypatch):
    monkeypatch.setattr(main, "_token_provider", FakeTokenProvider())

    class FakeDGIIClientConError:
        def __init__(self, ambiente):
            pass

        async def consultar(self, rnc_emisor, ncf_electronico, token, rnc_comprador=None, codigo_seguridad=None):
            raise DGIIServiceError("error")

    monkeypatch.setattr(main, "ConsultaEstadoDGIIClient", FakeDGIIClientConError)

    response = client.get("/estado", params={"rnc_emisor": "131880738", "ncf_electronico": "e3100000000"})

    assert response.status_code == 502
