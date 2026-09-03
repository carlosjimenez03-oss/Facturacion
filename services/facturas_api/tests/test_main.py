import uuid

import pytest
import respx
from fastapi.testclient import TestClient
from httpx import Response

from services.facturas_api.app import main
from services.facturas_api.app import repository


async def _sin_espera(segundos):
    return None


@pytest.fixture
def client(monkeypatch):
    async def fake_crear_pool(database_url):
        return object()  # pool "dummy": las funciones de repository se mockean por test

    async def fake_cerrar_pool(pool):
        return None

    monkeypatch.setattr(main, "crear_pool", fake_crear_pool)
    monkeypatch.setattr(main, "cerrar_pool", fake_cerrar_pool)
    with TestClient(main.app) as c:
        yield c


def _factura(**overrides) -> dict:
    base = {
        "id": str(uuid.uuid4()),
        "tipo_ecf": 31,
        "ambiente": "test",
        "rnc_emisor": "131234567",
        "encf": "E310000000001",
        "estado": "borrador",
        "datos": {
            "Encabezado": {
                "Version": "1.0",
                "IdDoc": {
                    "TipoeCF": "31",
                    "eNCF": "E310000000001",
                    "FechaVencimientoSecuencia": "31-12-2026",
                    "TipoIngresos": "01",
                    "TipoPago": "1",
                },
                "Emisor": {
                    "RNCEmisor": "131234567",
                    "RazonSocialEmisor": "Empresa Demo SRL",
                    "DireccionEmisor": "Calle Falsa 123",
                    "FechaEmision": "02-09-2026",
                },
                "Comprador": {"RNCComprador": "101672919", "RazonSocialComprador": "Cliente Demo SRL"},
                "Totales": {"MontoTotal": "1180.00"},
            },
            "DetallesItems": {
                "Item": [
                    {
                        "NumeroLinea": "1",
                        "IndicadorFacturacion": "1",
                        "NombreItem": "Producto X",
                        "IndicadorBienoServicio": "1",
                        "CantidadItem": "1.00",
                        "PrecioUnitarioItem": "1000.0000",
                        "MontoItem": "1000.00",
                    }
                ]
            },
        },
        "xml_generado": None,
        "xml_firmado": None,
        "track_id": None,
        "dgii_estado": None,
        "dgii_codigo": None,
        "dgii_respuesta": None,
        "error": None,
        "creado_en": "2026-09-02T00:00:00Z",
        "actualizado_en": "2026-09-02T00:00:00Z",
    }
    base.update(overrides)
    return base


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "app_env": "test", "dgii_environment": "testecf"}


def test_crear_factura_tipo_invalido(client):
    response = client.post("/facturas", json={"tipo_ecf": 99, "datos": {}})
    assert response.status_code == 422


def test_crear_factura_ok(client, monkeypatch):
    creada = _factura()

    async def fake_crear(pool, **kwargs):
        assert kwargs["tipo_ecf"] == 31
        return creada

    monkeypatch.setattr(repository, "crear_factura", fake_crear)

    response = client.post(
        "/facturas",
        json={"tipo_ecf": 31, "encf": "E310000000001", "datos": creada["datos"]},
    )

    assert response.status_code == 201
    assert response.json()["id"] == creada["id"]


def test_obtener_factura_404(client, monkeypatch):
    async def fake_obtener(pool, fid):
        raise repository.FacturaNoEncontradaError(str(fid))

    monkeypatch.setattr(repository, "obtener_factura", fake_obtener)

    response = client.get(f"/facturas/{uuid.uuid4()}")
    assert response.status_code == 404


def test_actualizar_factura_estado_no_borrador(client, monkeypatch):
    factura = _factura(estado="enviado")

    async def fake_obtener(pool, fid):
        return factura

    monkeypatch.setattr(repository, "obtener_factura", fake_obtener)

    response = client.put(f"/facturas/{factura['id']}", json={"datos": factura["datos"]})
    assert response.status_code == 409


def test_eliminar_factura_ok(client, monkeypatch):
    factura = _factura(estado="borrador")

    async def fake_obtener(pool, fid):
        return factura

    async def fake_eliminar(pool, fid):
        return None

    monkeypatch.setattr(repository, "obtener_factura", fake_obtener)
    monkeypatch.setattr(repository, "eliminar_factura", fake_eliminar)

    response = client.delete(f"/facturas/{factura['id']}")
    assert response.status_code == 204


def test_generar_xml_ok(client, monkeypatch):
    factura = _factura()

    async def fake_obtener(pool, fid):
        return factura

    async def fake_guardar(pool, fid, xml_generado):
        return {**factura, "estado": "generado", "xml_generado": xml_generado}

    monkeypatch.setattr(repository, "obtener_factura", fake_obtener)
    monkeypatch.setattr(repository, "guardar_xml_generado", fake_guardar)

    response = client.post(f"/facturas/{factura['id']}/generar-xml")

    assert response.status_code == 200
    cuerpo = response.json()
    assert cuerpo["errores"] == []
    assert cuerpo["estado"] == "generado"
    assert "<ECF>" in cuerpo["xml_generado"]


def test_generar_xml_con_errores_no_guarda(client, monkeypatch):
    factura = _factura()
    factura["datos"]["Encabezado"]["Totales"] = {}  # falta MontoTotal

    async def fake_obtener(pool, fid):
        return factura

    guardado_llamado = {"si": False}

    async def fake_guardar(pool, fid, xml_generado):
        guardado_llamado["si"] = True
        return factura

    monkeypatch.setattr(repository, "obtener_factura", fake_obtener)
    monkeypatch.setattr(repository, "guardar_xml_generado", fake_guardar)

    response = client.post(f"/facturas/{factura['id']}/generar-xml")

    assert response.status_code == 200
    assert response.json()["errores"], "debía reportar errores de validación"
    assert guardado_llamado["si"] is False


def test_firmar_factura_sin_xml_generado(client, monkeypatch):
    factura = _factura(xml_generado=None)

    async def fake_obtener(pool, fid):
        return factura

    monkeypatch.setattr(repository, "obtener_factura", fake_obtener)

    response = client.post(f"/facturas/{factura['id']}/firmar")
    assert response.status_code == 409


def test_firmar_factura_ok(client, monkeypatch, credencial_prueba):
    factura = _factura(estado="generado", xml_generado="<ECF><Encabezado/></ECF>")

    async def fake_obtener(pool, fid):
        return factura

    async def fake_guardar_firmado(pool, fid, xml_firmado):
        return {**factura, "estado": "firmado", "xml_firmado": xml_firmado}

    monkeypatch.setattr(repository, "obtener_factura", fake_obtener)
    monkeypatch.setattr(repository, "guardar_xml_firmado", fake_guardar_firmado)
    monkeypatch.setattr(main, "cargar_certificado_p12", lambda path, password: credencial_prueba)
    monkeypatch.setattr(main, "firmar_xml", lambda xml_bytes, credencial: b"<ECF>firmado</ECF>")

    response = client.post(f"/facturas/{factura['id']}/firmar")

    assert response.status_code == 200
    assert response.json()["xml_firmado"] == "<ECF>firmado</ECF>"


def test_enviar_factura_sin_encf(client, monkeypatch):
    factura = _factura(xml_generado="<ECF/>", encf=None)

    async def fake_obtener(pool, fid):
        return factura

    monkeypatch.setattr(repository, "obtener_factura", fake_obtener)

    response = client.post(f"/facturas/{factura['id']}/enviar")
    assert response.status_code == 422


@respx.mock
def test_enviar_factura_ok(client, monkeypatch):
    factura = _factura(xml_generado="<ECF><Encabezado/></ECF>")

    async def fake_obtener(pool, fid):
        return factura

    async def fake_registrar(pool, fid, **kwargs):
        return {**factura, **kwargs}

    monkeypatch.setattr(repository, "obtener_factura", fake_obtener)
    monkeypatch.setattr(repository, "registrar_resultado_envio", fake_registrar)

    respx.post(f"{main.settings.recepcion_service_url}/ecf").mock(
        return_value=Response(200, json={"trackId": "track-abc"})
    )

    response = client.post(f"/facturas/{factura['id']}/enviar")

    assert response.status_code == 200
    cuerpo = response.json()
    assert cuerpo["track_id"] == "track-abc"
    assert cuerpo["estado"] == "enviado"


@respx.mock
def test_enviar_factura_error_dgii(client, monkeypatch):
    factura = _factura(xml_generado="<ECF><Encabezado/></ECF>")

    async def fake_obtener(pool, fid):
        return factura

    async def fake_registrar(pool, fid, **kwargs):
        return {**factura, **kwargs}

    monkeypatch.setattr(repository, "obtener_factura", fake_obtener)
    monkeypatch.setattr(repository, "registrar_resultado_envio", fake_registrar)

    respx.post(f"{main.settings.recepcion_service_url}/ecf").mock(
        return_value=Response(502, text="rechazado por la DGII")
    )

    response = client.post(f"/facturas/{factura['id']}/enviar")

    assert response.status_code == 502


def test_lifespan_reintenta_conexion_hasta_lograrla(monkeypatch):
    intentos = []

    async def fake_crear_pool(database_url):
        intentos.append(database_url)
        if len(intentos) < 3:
            return None
        return object()

    monkeypatch.setattr(main, "crear_pool", fake_crear_pool)
    monkeypatch.setattr(main, "cerrar_pool", lambda pool: _sin_espera(None))
    monkeypatch.setattr(main.asyncio, "sleep", _sin_espera)

    with TestClient(main.app):
        pass

    assert len(intentos) == 3


def test_lifespan_falla_tras_agotar_reintentos(monkeypatch):
    async def fake_crear_pool(database_url):
        return None

    monkeypatch.setattr(main, "crear_pool", fake_crear_pool)
    monkeypatch.setattr(main.asyncio, "sleep", _sin_espera)

    with pytest.raises(RuntimeError, match="requiere DATABASE_URL"):
        with TestClient(main.app):
            pass
