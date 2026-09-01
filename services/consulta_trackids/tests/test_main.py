import pytest
from fastapi.testclient import TestClient

from libs.ecf_common.auth.schemas import TokenResponse
from libs.ecf_common.http_client import DGIIServiceError
from services.consulta_trackids.app import main
from services.consulta_trackids.app.schemas import TrackingDetalle


@pytest.fixture
def client() -> TestClient:
    return TestClient(main.app)


class FakeTokenProvider:
    async def obtener_token(self, forzar_renovacion: bool = False) -> TokenResponse:
        return TokenResponse(token="tok-123", expira="2099-01-01T00:00:00Z", expedido="2026-01-01T00:00:00Z")


def test_health(client):
    response = client.get("/health")

    assert response.status_code == 200


def test_consultar_trackids_ok(client, monkeypatch):
    monkeypatch.setattr(main, "_token_provider", FakeTokenProvider())

    class FakeDGIIClient:
        def __init__(self, ambiente):
            pass

        async def consultar(self, rnc_emisor, encf, token):
            assert token == "tok-123"
            return [TrackingDetalle(trackId="t1", estado="Aceptado", fechaRecepcion="2026-01-01T00:00:00Z")]

    monkeypatch.setattr(main, "ConsultaTrackIdsDGIIClient", FakeDGIIClient)

    response = client.get("/trackids", params={"rnc_emisor": "101672919", "encf": "e3100000000"})

    assert response.status_code == 200
    assert response.json()[0]["trackId"] == "t1"


def test_consultar_trackids_error_dgii(client, monkeypatch):
    monkeypatch.setattr(main, "_token_provider", FakeTokenProvider())

    class FakeDGIIClientConError:
        def __init__(self, ambiente):
            pass

        async def consultar(self, rnc_emisor, encf, token):
            raise DGIIServiceError("error")

    monkeypatch.setattr(main, "ConsultaTrackIdsDGIIClient", FakeDGIIClientConError)

    response = client.get("/trackids", params={"rnc_emisor": "101672919", "encf": "e3100000000"})

    assert response.status_code == 502
