import pytest
from fastapi.testclient import TestClient

from libs.ecf_common.auth.schemas import TokenResponse
from libs.ecf_common.http_client import DGIIServiceError
from services.consulta_directorio.app import main
from services.consulta_directorio.app.schemas import ContribuyenteDirectorio


@pytest.fixture
def client() -> TestClient:
    return TestClient(main.app)


class FakeTokenProvider:
    async def obtener_token(self, forzar_renovacion: bool = False) -> TokenResponse:
        return TokenResponse(token="tok-123", expira="2099-01-01T00:00:00Z", expedido="2026-01-01T00:00:00Z")


ITEM = ContribuyenteDirectorio(
    nombre="Empresa Prueba SRL",
    rnc="101672919",
    urlRecepcion="https://ejemplo.do/recepcion",
    urlAceptacion="https://ejemplo.do/aprobacioncomercial",
)


def test_health(client):
    response = client.get("/health")

    assert response.status_code == 200


def test_listado_directorio_ok(client, monkeypatch):
    monkeypatch.setattr(main, "_token_provider", FakeTokenProvider())

    class FakeDGIIClient:
        def __init__(self, ambiente):
            pass

        async def listado(self, token):
            assert token == "tok-123"
            return [ITEM]

    monkeypatch.setattr(main, "ConsultaDirectorioDGIIClient", FakeDGIIClient)

    response = client.get("/directorio")

    assert response.status_code == 200
    assert response.json()[0]["rnc"] == "101672919"


def test_directorio_por_rnc_ok(client, monkeypatch):
    monkeypatch.setattr(main, "_token_provider", FakeTokenProvider())

    class FakeDGIIClient:
        def __init__(self, ambiente):
            pass

        async def obtener_por_rnc(self, rnc, token):
            assert rnc == "101672919"
            return [ITEM]

    monkeypatch.setattr(main, "ConsultaDirectorioDGIIClient", FakeDGIIClient)

    response = client.get("/directorio/101672919")

    assert response.status_code == 200


def test_listado_directorio_error_dgii(client, monkeypatch):
    monkeypatch.setattr(main, "_token_provider", FakeTokenProvider())

    class FakeDGIIClientConError:
        def __init__(self, ambiente):
            pass

        async def listado(self, token):
            raise DGIIServiceError("error")

    monkeypatch.setattr(main, "ConsultaDirectorioDGIIClient", FakeDGIIClientConError)

    response = client.get("/directorio")

    assert response.status_code == 502
