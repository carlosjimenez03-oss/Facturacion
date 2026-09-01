import pytest
from fastapi.testclient import TestClient

from services.autenticacion.app import main
from services.autenticacion.app.schemas import TokenResponse
from services.autenticacion.app.token_cache import TokenCache


@pytest.fixture(autouse=True)
def reset_cache():
    main._cache = TokenCache()
    yield
    main._cache = TokenCache()


@pytest.fixture
def client() -> TestClient:
    return TestClient(main.app)


def test_health(client):
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body == {"status": "ok", "app_env": "test", "dgii_environment": "testecf"}


def test_obtener_token_llama_a_dgii_y_cachea(client, monkeypatch, credencial_prueba):
    llamadas = {"semilla": 0, "validar": 0}

    def fake_cargar_certificado_p12(path, password):
        return credencial_prueba

    def fake_firmar_xml(xml_bytes, credencial):
        return b"<SemillaModel>firmada</SemillaModel>"

    class FakeDGIIClient:
        def __init__(self, ambiente):
            self.ambiente = ambiente

        async def obtener_semilla(self):
            llamadas["semilla"] += 1
            return b"<SemillaModel><valor>abc</valor></SemillaModel>"

        async def validar_semilla(self, semilla_firmada):
            llamadas["validar"] += 1
            return TokenResponse(
                token="token-123",
                expira="2099-01-01T00:00:00Z",
                expedido="2026-01-01T00:00:00Z",
            )

    monkeypatch.setattr(main, "cargar_certificado_p12", fake_cargar_certificado_p12)
    monkeypatch.setattr(main, "firmar_xml", fake_firmar_xml)
    monkeypatch.setattr(main, "AutenticacionDGIIClient", FakeDGIIClient)

    primera = client.get("/token")
    segunda = client.get("/token")

    assert primera.status_code == 200
    assert primera.json()["token"] == "token-123"
    assert segunda.json()["token"] == "token-123"
    # La segunda llamada debe servirse desde el cache, sin volver a llamar a la DGII.
    assert llamadas == {"semilla": 1, "validar": 1}


def test_obtener_token_propaga_error_dgii(client, monkeypatch, credencial_prueba):
    from services.autenticacion.app.dgii_client import DGIIAutenticacionError

    def fake_cargar_certificado_p12(path, password):
        return credencial_prueba

    class FakeDGIIClientConError:
        def __init__(self, ambiente):
            pass

        async def obtener_semilla(self):
            raise DGIIAutenticacionError("la DGII rechazó la solicitud")

        async def validar_semilla(self, semilla_firmada):
            raise AssertionError("no debería llegar aquí")

    monkeypatch.setattr(main, "cargar_certificado_p12", fake_cargar_certificado_p12)
    monkeypatch.setattr(main, "AutenticacionDGIIClient", FakeDGIIClientConError)

    response = client.get("/token")

    assert response.status_code == 502
