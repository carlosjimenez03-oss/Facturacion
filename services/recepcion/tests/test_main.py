import io

import pytest
from fastapi.testclient import TestClient

from libs.ecf_common.auth.schemas import TokenResponse
from libs.ecf_common.http_client import DGIIServiceError
from services.recepcion.app import main
from services.recepcion.app.schemas import RecepcionResponse


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


def test_enviar_ecf_ok(client, monkeypatch, credencial_prueba):
    monkeypatch.setattr(main, "cargar_certificado_p12", lambda path, password: credencial_prueba)
    monkeypatch.setattr(main, "firmar_xml", lambda xml_bytes, credencial: b"<ECF>firmado</ECF>")
    monkeypatch.setattr(main, "_token_provider", FakeTokenProvider())

    class FakeDGIIClient:
        def __init__(self, ambiente):
            self.ambiente = ambiente

        async def enviar_ecf(self, xml_firmado, nombre_archivo, token):
            assert xml_firmado == b"<ECF>firmado</ECF>"
            assert nombre_archivo.endswith("E310000000001.xml")
            assert token == "tok-123"
            return RecepcionResponse(trackId="track-1")

    monkeypatch.setattr(main, "RecepcionDGIIClient", FakeDGIIClient)

    response = client.post(
        "/ecf",
        data={"encf": "E310000000001"},
        files={"xml": ("factura.xml", io.BytesIO(b"<ECF>sin firmar</ECF>"), "text/xml")},
    )

    assert response.status_code == 200
    assert response.json()["trackId"] == "track-1"
    assert len(_fake_registrar_envio.llamadas) == 1
    assert _fake_registrar_envio.llamadas[0]["track_id"] == "track-1"
    assert _fake_registrar_envio.llamadas[0]["modulo"] == "recepcion"


def test_enviar_ecf_error_dgii(client, monkeypatch, credencial_prueba):
    monkeypatch.setattr(main, "cargar_certificado_p12", lambda path, password: credencial_prueba)
    monkeypatch.setattr(main, "firmar_xml", lambda xml_bytes, credencial: b"<ECF>firmado</ECF>")
    monkeypatch.setattr(main, "_token_provider", FakeTokenProvider())

    class FakeDGIIClientConError:
        def __init__(self, ambiente):
            pass

        async def enviar_ecf(self, xml_firmado, nombre_archivo, token):
            raise DGIIServiceError("rechazado por la DGII")

    monkeypatch.setattr(main, "RecepcionDGIIClient", FakeDGIIClientConError)

    response = client.post(
        "/ecf",
        data={"encf": "E310000000001"},
        files={"xml": ("factura.xml", io.BytesIO(b"<ECF>sin firmar</ECF>"), "text/xml")},
    )

    assert response.status_code == 502
    assert len(_fake_registrar_envio.llamadas) == 1
    assert _fake_registrar_envio.llamadas[0]["error"] == "rechazado por la DGII"


def test_enviar_ecf_en_el_ambiente_de_la_factura(client, monkeypatch):
    """facturas_api manda el ambiente de la factura: se usa ese (token y
    servicio de la DGII), aunque el activo sea otro."""
    from libs.ecf_common.environments import DGIIEnvironment

    pedidos = {}

    class TokenPorAmbiente:
        async def obtener_token(self, forzar_renovacion=False, *, ambiente):
            pedidos["token"] = ambiente
            return TokenResponse(token="tok", expira="2099-01-01T00:00:00Z", expedido="2026-01-01T00:00:00Z")

    class FakeDGIIClient:
        def __init__(self, ambiente):
            pedidos["cliente"] = ambiente

        async def enviar_ecf(self, xml_firmado, nombre_archivo, token):
            return RecepcionResponse(trackId="track-prod")

    monkeypatch.setattr(main, "_token_provider", TokenPorAmbiente())
    monkeypatch.setattr(main, "RecepcionDGIIClient", FakeDGIIClient)
    firmado = b'<ECF><Signature xmlns="http://www.w3.org/2000/09/xmldsig#"/></ECF>'
    r = client.post(
        "/ecf",
        data={"encf": "E310000000001", "firmado": "true", "ambiente": "production"},
        files={"xml": ("f.xml", io.BytesIO(firmado), "text/xml")},
    )
    assert r.status_code == 200
    assert pedidos == {"token": DGIIEnvironment.ECF, "cliente": DGIIEnvironment.ECF}
    assert _fake_registrar_envio.llamadas[-1]["ambiente"] == "production"

    r = client.post(
        "/ecf",
        data={"encf": "E310000000001", "firmado": "true", "ambiente": "staging"},
        files={"xml": ("f.xml", io.BytesIO(firmado), "text/xml")},
    )
    assert r.status_code == 422


def test_health_con_el_ambiente_de_configuracion(client, monkeypatch):
    async def guardado(pool, pedido=None):
        return pedido or "qa"

    monkeypatch.setattr(main._ambiente, "resolver", guardado)
    assert client.get("/health").json() == {"status": "ok", "app_env": "qa", "dgii_environment": "certecf"}
