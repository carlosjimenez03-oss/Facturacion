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
    async def obtener_token(self, forzar_renovacion: bool = False, **_) -> TokenResponse:
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


def test_consulta_en_el_ambiente_pedido(client, monkeypatch):
    """facturas_api consulta cada e-CF en el ambiente donde se envió."""
    from libs.ecf_common.environments import DGIIEnvironment

    vistos = []

    class TokenPorAmbiente:
        async def obtener_token(self, forzar_renovacion=False, *, ambiente):
            vistos.append(("token", ambiente))
            return TokenResponse(token="tok", expira="2099-01-01T00:00:00Z", expedido="2026-01-01T00:00:00Z")

    class FakeDGIIClient:
        def __init__(self, ambiente):
            vistos.append(("cliente", ambiente))

        async def consultar(self, track_id, token):
            return ConsultaResultadoResponse(trackId=track_id, codigo=1, estado="Aceptado")

    monkeypatch.setattr(main, "_token_provider", TokenPorAmbiente())
    monkeypatch.setattr(main, "ConsultaResultadoDGIIClient", FakeDGIIClient)
    assert client.get("/resultado/t-1", params={"ambiente": "test"}).status_code == 200
    assert client.get("/resultado/t-2", params={"ambiente": "production"}).status_code == 200
    assert vistos == [
        ("token", DGIIEnvironment.TESTECF), ("cliente", DGIIEnvironment.TESTECF),
        ("token", DGIIEnvironment.ECF), ("cliente", DGIIEnvironment.ECF),
    ]
    assert client.get("/resultado/t-3", params={"ambiente": "nada"}).status_code == 422
