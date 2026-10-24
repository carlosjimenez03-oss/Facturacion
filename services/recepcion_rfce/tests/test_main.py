import io

import pytest
from fastapi.testclient import TestClient

from libs.ecf_common.auth.schemas import TokenResponse
from libs.ecf_common.http_client import DGIIServiceError
from services.recepcion_rfce.app import main
from services.recepcion_rfce.app.schemas import RecepcionRFCEResponse


@pytest.fixture
def client() -> TestClient:
    return TestClient(main.app)


class FakeTokenProvider:
    async def obtener_token(self, forzar_renovacion: bool = False, **_) -> TokenResponse:
        return TokenResponse(token="tok-123", expira="2099-01-01T00:00:00Z", expedido="2026-01-01T00:00:00Z")


def test_health(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "app_env": "test", "dgii_environment": "testecf"}


async def _fake_registrar_envio(pool, **kwargs):
    _fake_registrar_envio.llamadas.append(kwargs)


_fake_registrar_envio.llamadas = []


@pytest.fixture(autouse=True)
def _reset_auditoria(monkeypatch):
    _fake_registrar_envio.llamadas = []
    monkeypatch.setattr(main, "registrar_envio", _fake_registrar_envio)
    yield


def test_enviar_rfce_ok(client, monkeypatch, credencial_prueba):
    monkeypatch.setattr(main, "cargar_certificado_p12", lambda path, password: credencial_prueba)
    monkeypatch.setattr(main, "firmar_xml", lambda xml_bytes, credencial: b"<RFCE>firmado</RFCE>")
    monkeypatch.setattr(main, "_token_provider", FakeTokenProvider())

    class FakeDGIIClient:
        def __init__(self, ambiente):
            pass

        async def enviar_rfce(self, xml_firmado, nombre_archivo, token):
            assert token == "tok-123"
            return RecepcionRFCEResponse(codigo=1, estado="Aceptado")

    monkeypatch.setattr(main, "RecepcionRFCEDGIIClient", FakeDGIIClient)

    response = client.post(
        "/rfce",
        data={"encf": "E320000000001"},
        files={"xml": ("resumen.xml", io.BytesIO(b"<RFCE/>"), "text/xml")},
    )

    assert response.status_code == 200
    assert response.json()["estado"] == "Aceptado"
    assert len(_fake_registrar_envio.llamadas) == 1
    assert _fake_registrar_envio.llamadas[0]["modulo"] == "recepcion_rfce"


def test_enviar_rfce_error_dgii(client, monkeypatch, credencial_prueba):
    monkeypatch.setattr(main, "cargar_certificado_p12", lambda path, password: credencial_prueba)
    monkeypatch.setattr(main, "firmar_xml", lambda xml_bytes, credencial: b"<RFCE>firmado</RFCE>")
    monkeypatch.setattr(main, "_token_provider", FakeTokenProvider())

    class FakeDGIIClientConError:
        def __init__(self, ambiente):
            pass

        async def enviar_rfce(self, xml_firmado, nombre_archivo, token):
            raise DGIIServiceError("rechazado")

    monkeypatch.setattr(main, "RecepcionRFCEDGIIClient", FakeDGIIClientConError)

    response = client.post(
        "/rfce",
        data={"encf": "E320000000001"},
        files={"xml": ("resumen.xml", io.BytesIO(b"<RFCE/>"), "text/xml")},
    )

    assert response.status_code == 502
    assert _fake_registrar_envio.llamadas[0]["error"] == "rechazado"
