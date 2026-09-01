import pytest
from fastapi.testclient import TestClient

from libs.ecf_common.auth.schemas import TokenResponse
from libs.ecf_common.http_client import DGIIServiceError
from services.consulta_resultado.app import main
from services.consulta_resultado.app.schemas import ConsultaResultadoResponse


@pytest.fixture
def client() -> TestClient:
    return TestClient(main.app)


class FakeTokenProvider:
    async def obtener_token(self, forzar_renovacion: bool = False) -> TokenResponse:
        return TokenResponse(token="tok-123", expira="2099-01-01T00:00:00Z", expedido="2026-01-01T00:00:00Z")


def test_health(client):
    response = client.get("/health")

    assert response.status_code == 200


def test_consultar_resultado_ok(client, monkeypatch):
    monkeypatch.setattr(main, "_token_provider", FakeTokenProvider())

    class FakeDGIIClient:
        def __init__(self, ambiente):
            pass

        async def consultar(self, track_id, token):
            assert token == "tok-123"
            return ConsultaResultadoResponse(trackId=track_id, estado="Aceptado")

    monkeypatch.setattr(main, "ConsultaResultadoDGIIClient", FakeDGIIClient)

    response = client.get("/resultado/abc-123")

    assert response.status_code == 200
    assert response.json()["estado"] == "Aceptado"


def test_consultar_resultado_error_dgii(client, monkeypatch):
    monkeypatch.setattr(main, "_token_provider", FakeTokenProvider())

    class FakeDGIIClientConError:
        def __init__(self, ambiente):
            pass

        async def consultar(self, track_id, token):
            raise DGIIServiceError("error")

    monkeypatch.setattr(main, "ConsultaResultadoDGIIClient", FakeDGIIClientConError)

    response = client.get("/resultado/abc-123")

    assert response.status_code == 502
