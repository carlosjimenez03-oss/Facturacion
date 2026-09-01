import pytest
from fastapi.testclient import TestClient

from libs.ecf_common.auth.dgii_client import DGIIAutenticacionError
from libs.ecf_common.auth.schemas import TokenResponse
from services.autenticacion.app import main


class FakeTokenProvider:
    def __init__(self, token: TokenResponse | None = None, error: Exception | None = None):
        self.token = token
        self.error = error
        self.llamadas: list[bool] = []

    async def obtener_token(self, forzar_renovacion: bool = False) -> TokenResponse:
        self.llamadas.append(forzar_renovacion)
        if self.error is not None:
            raise self.error
        assert self.token is not None
        return self.token


@pytest.fixture
def client() -> TestClient:
    return TestClient(main.app)


def test_health(client):
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body == {"status": "ok", "app_env": "test", "dgii_environment": "testecf"}


def test_obtener_token_delega_al_token_provider(client, monkeypatch):
    fake = FakeTokenProvider(
        token=TokenResponse(
            token="token-123", expira="2099-01-01T00:00:00Z", expedido="2026-01-01T00:00:00Z"
        )
    )
    monkeypatch.setattr(main, "_token_provider", fake)

    response = client.get("/token")

    assert response.status_code == 200
    assert response.json()["token"] == "token-123"
    assert fake.llamadas == [False]


def test_obtener_token_forzar_renovacion(client, monkeypatch):
    fake = FakeTokenProvider(
        token=TokenResponse(
            token="token-123", expira="2099-01-01T00:00:00Z", expedido="2026-01-01T00:00:00Z"
        )
    )
    monkeypatch.setattr(main, "_token_provider", fake)

    client.get("/token?forzar_renovacion=true")

    assert fake.llamadas == [True]


def test_obtener_token_propaga_error_dgii(client, monkeypatch):
    fake = FakeTokenProvider(error=DGIIAutenticacionError("la DGII rechazó la solicitud"))
    monkeypatch.setattr(main, "_token_provider", fake)

    response = client.get("/token")

    assert response.status_code == 502


def test_obtener_token_propaga_error_certificado(client, monkeypatch):
    fake = FakeTokenProvider(error=ValueError("certificado inválido"))
    monkeypatch.setattr(main, "_token_provider", fake)

    response = client.get("/token")

    assert response.status_code == 500
