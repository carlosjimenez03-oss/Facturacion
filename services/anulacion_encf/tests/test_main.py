import io

import pytest
from fastapi.testclient import TestClient

from libs.ecf_common.auth.schemas import TokenResponse
from libs.ecf_common.http_client import DGIIServiceError
from services.anulacion_encf.app import main
from services.anulacion_encf.app.schemas import AnulacionRangoResponse


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


def test_anular_rango_ok(client, monkeypatch, credencial_prueba):
    monkeypatch.setattr(main, "cargar_certificado_p12", lambda path, password: credencial_prueba)
    monkeypatch.setattr(main, "firmar_xml", lambda xml_bytes, credencial: b"<ANECF>firmado</ANECF>")
    monkeypatch.setattr(main, "_token_provider", FakeTokenProvider())

    class FakeDGIIClient:
        def __init__(self, ambiente):
            pass

        async def anular_rango(self, xml_firmado, nombre_archivo, token):
            assert token == "tok-123"
            return AnulacionRangoResponse(rnc="101672919", codigo="1")

    monkeypatch.setattr(main, "AnulacionDGIIClient", FakeDGIIClient)

    response = client.post("/anulacion", files={"xml": ("anulacion.xml", io.BytesIO(b"<ANECF/>"), "text/xml")})

    assert response.status_code == 200
    assert response.json()["rnc"] == "101672919"
    assert len(_fake_registrar_envio.llamadas) == 1
    assert _fake_registrar_envio.llamadas[0]["modulo"] == "anulacion_encf"
    # Además de la respuesta de la DGII se guarda el detalle del ANECF
    # (aquí vacío: el XML de prueba no trae rangos).
    assert _fake_registrar_envio.llamadas[0]["respuesta"]["rangos"] == []


def test_anular_rango_ok_aunque_falle_el_detalle(client, monkeypatch, credencial_prueba):
    """La DGII ya aplicó la anulación: un fallo al leer el detalle del XML
    no debe convertirse en un 500 ni dejar la anulación sin auditar."""
    monkeypatch.setattr(main, "cargar_certificado_p12", lambda path, password: credencial_prueba)
    monkeypatch.setattr(main, "firmar_xml", lambda xml_bytes, credencial: b"<ANECF>firmado</ANECF>")
    monkeypatch.setattr(main, "_token_provider", FakeTokenProvider())

    class FakeDGIIClient:
        def __init__(self, ambiente):
            pass

        async def anular_rango(self, xml_firmado, nombre_archivo, token):
            return AnulacionRangoResponse(rnc="101672919", codigo="1")

    def detalle_que_falla(xml_bytes):
        raise ValueError("estructura inesperada")

    monkeypatch.setattr(main, "AnulacionDGIIClient", FakeDGIIClient)
    monkeypatch.setattr(main, "extraer_detalle_anulacion", detalle_que_falla)

    response = client.post("/anulacion", files={"xml": ("anulacion.xml", io.BytesIO(b"<ANECF/>"), "text/xml")})

    assert response.status_code == 200
    assert len(_fake_registrar_envio.llamadas) == 1
    assert _fake_registrar_envio.llamadas[0]["codigo"] == "1"
    assert "rangos" not in _fake_registrar_envio.llamadas[0]["respuesta"]


def test_anular_rango_error_dgii(client, monkeypatch, credencial_prueba):
    monkeypatch.setattr(main, "cargar_certificado_p12", lambda path, password: credencial_prueba)
    monkeypatch.setattr(main, "firmar_xml", lambda xml_bytes, credencial: b"<ANECF>firmado</ANECF>")
    monkeypatch.setattr(main, "_token_provider", FakeTokenProvider())

    class FakeDGIIClientConError:
        def __init__(self, ambiente):
            pass

        async def anular_rango(self, xml_firmado, nombre_archivo, token):
            raise DGIIServiceError("rechazado")

    monkeypatch.setattr(main, "AnulacionDGIIClient", FakeDGIIClientConError)

    response = client.post("/anulacion", files={"xml": ("anulacion.xml", io.BytesIO(b"<ANECF/>"), "text/xml")})

    assert response.status_code == 502
    assert _fake_registrar_envio.llamadas[0]["error"] == "rechazado"
