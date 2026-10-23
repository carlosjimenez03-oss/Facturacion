import io

import pytest
from fastapi.testclient import TestClient

from libs.ecf_common.auth.schemas import TokenResponse
from libs.ecf_common.http_client import DGIIServiceError
from services.aprobacion_comercial.app import main
from services.aprobacion_comercial.app.schemas import AprobacionComercialResponse


@pytest.fixture
def client() -> TestClient:
    return TestClient(main.app)


class FakeTokenProvider:
    async def obtener_token(self, forzar_renovacion: bool = False) -> TokenResponse:
        return TokenResponse(token="tok-123", expira="2099-01-01T00:00:00Z", expedido="2026-01-01T00:00:00Z")


def test_health(client):
    response = client.get("/health")

    assert response.status_code == 200


async def _fake_registrar_envio(pool, **kwargs):
    _fake_registrar_envio.llamadas.append(kwargs)


_fake_registrar_envio.llamadas = []


@pytest.fixture(autouse=True)
def _reset_auditoria(monkeypatch):
    _fake_registrar_envio.llamadas = []
    monkeypatch.setattr(main, "registrar_envio", _fake_registrar_envio)
    yield


def test_enviar_aprobacion_ok(client, monkeypatch, credencial_prueba):
    monkeypatch.setattr(main, "cargar_certificado_p12", lambda path, password: credencial_prueba)
    monkeypatch.setattr(main, "firmar_xml", lambda xml_bytes, credencial: b"<ACECF>firmado</ACECF>")
    monkeypatch.setattr(main, "_token_provider", FakeTokenProvider())

    class FakeDGIIClient:
        def __init__(self, ambiente):
            pass

        async def enviar_aprobacion(self, xml_firmado, nombre_archivo, token):
            assert token == "tok-123"
            return AprobacionComercialResponse(estado="Aprobada")

    monkeypatch.setattr(main, "AprobacionComercialDGIIClient", FakeDGIIClient)

    response = client.post(
        "/aprobacion",
        data={"encf": "E310000000001"},
        files={"xml": ("aprobacion.xml", io.BytesIO(b"<ACECF/>"), "text/xml")},
    )

    assert response.status_code == 200
    assert response.json()["estado"] == "Aprobada"
    # Devuelve el ACECF firmado (erp-api lo entrega al emisor), pero no lo audita.
    assert response.json()["xml_firmado"] == "<ACECF>firmado</ACECF>"
    assert len(_fake_registrar_envio.llamadas) == 1
    assert _fake_registrar_envio.llamadas[0]["modulo"] == "aprobacion_comercial"
    assert "xml_firmado" not in _fake_registrar_envio.llamadas[0]["respuesta"]


def test_enviar_aprobacion_error_dgii(client, monkeypatch, credencial_prueba):
    monkeypatch.setattr(main, "cargar_certificado_p12", lambda path, password: credencial_prueba)
    monkeypatch.setattr(main, "firmar_xml", lambda xml_bytes, credencial: b"<ACECF>firmado</ACECF>")
    monkeypatch.setattr(main, "_token_provider", FakeTokenProvider())

    class FakeDGIIClientConError:
        def __init__(self, ambiente):
            pass

        async def enviar_aprobacion(self, xml_firmado, nombre_archivo, token):
            raise DGIIServiceError("rechazado")

    monkeypatch.setattr(main, "AprobacionComercialDGIIClient", FakeDGIIClientConError)

    response = client.post(
        "/aprobacion",
        data={"encf": "E310000000001"},
        files={"xml": ("aprobacion.xml", io.BytesIO(b"<ACECF/>"), "text/xml")},
    )

    assert response.status_code == 502
    assert _fake_registrar_envio.llamadas[0]["error"] == "rechazado"
