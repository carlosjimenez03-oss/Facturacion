import pytest
from fastapi.testclient import TestClient

from libs.ecf_common.auth.schemas import TokenResponse
from libs.ecf_common.http_client import DGIIServiceError
from services.consulta_rfce.app import main
from services.consulta_rfce.app.schemas import ConsultaRFCEResponse


@pytest.fixture
def client() -> TestClient:
    return TestClient(main.app)


class FakeTokenProvider:
    async def obtener_token(self, forzar_renovacion: bool = False, **_) -> TokenResponse:
        return TokenResponse(token="tok-123", expira="2099-01-01T00:00:00Z", expedido="2026-01-01T00:00:00Z")


def test_health(client):
    response = client.get("/health")

    assert response.status_code == 200


def test_consultar_rfce_ok(client, monkeypatch):
    monkeypatch.setattr(main, "_token_provider", FakeTokenProvider())

    class FakeDGIIClient:
        def __init__(self, ambiente):
            pass

        async def consultar(self, rnc_emisor, encf, codigo_seguridad, token):
            assert token == "tok-123"
            return ConsultaRFCEResponse(estado="Aceptado", rnc=rnc_emisor, encf=encf)

    monkeypatch.setattr(main, "ConsultaRFCEDGIIClient", FakeDGIIClient)

    response = client.get(
        "/rfce",
        params={"rnc_emisor": "131880738", "encf": "E320000000064", "codigo_seguridad": "uabnyh"},
    )

    assert response.status_code == 200
    assert response.json()["estado"] == "Aceptado"


def test_consultar_rfce_error_dgii(client, monkeypatch):
    monkeypatch.setattr(main, "_token_provider", FakeTokenProvider())

    class FakeDGIIClientConError:
        def __init__(self, ambiente):
            pass

        async def consultar(self, rnc_emisor, encf, codigo_seguridad, token):
            raise DGIIServiceError("error")

    monkeypatch.setattr(main, "ConsultaRFCEDGIIClient", FakeDGIIClientConError)

    response = client.get(
        "/rfce",
        params={"rnc_emisor": "131880738", "encf": "E320000000064", "codigo_seguridad": "uabnyh"},
    )

    assert response.status_code == 502
