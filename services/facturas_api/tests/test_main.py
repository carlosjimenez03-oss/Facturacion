import uuid
from datetime import datetime, timezone

import pytest
import respx
from fastapi.testclient import TestClient
from httpx import ConnectError, Response

from services.facturas_api.app import envio, main
from services.facturas_api.app import repository


async def _sin_espera(segundos):
    return None


@pytest.fixture(autouse=True)
def _sin_sincronizacion_de_fondo(monkeypatch):
    # El lifespan corre con TestClient: sin esto lanzaría la tarea de fondo
    # de app.estado_dgii contra el pool de mentira (y con asyncio.sleep
    # parcheado en algunos tests, en un bucle sin pausa).
    monkeypatch.setattr(main.settings, "sincronizar_estado_cada_segundos", 0)
    monkeypatch.setattr(main, "_ESPERA_CONSULTA_TRAS_ENVIO_SEGUNDOS", 0)


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


@pytest.mark.parametrize(
    ("estado", "track_id"),
    [("aceptado", "abc"), ("enviado", "abc"), ("rechazado", "abc"), ("error", "abc")],
)
def test_generar_xml_rechazado_si_ya_se_envio(client, monkeypatch, estado, track_id):
    factura = _factura(estado=estado, track_id=track_id)

    async def fake_obtener(pool, fid):
        return factura

    async def fake_guardar(pool, fid, xml_generado):
        raise AssertionError("no debía regenerar el XML de una factura enviada")

    monkeypatch.setattr(repository, "obtener_factura", fake_obtener)
    monkeypatch.setattr(repository, "guardar_xml_generado", fake_guardar)

    response = client.post(f"/facturas/{factura['id']}/generar-xml")

    assert response.status_code == 409


def test_generar_xml_permitido_si_el_envio_fallo_sin_track_id(client, monkeypatch):
    factura = _factura(estado="error", track_id=None)

    async def fake_obtener(pool, fid):
        return factura

    async def fake_guardar(pool, fid, xml_generado):
        return {**factura, "estado": "generado", "xml_generado": xml_generado}

    monkeypatch.setattr(repository, "obtener_factura", fake_obtener)
    monkeypatch.setattr(repository, "guardar_xml_generado", fake_guardar)

    response = client.post(f"/facturas/{factura['id']}/generar-xml")

    assert response.status_code == 200


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


def test_representacion_impresa_sin_firmar(client, monkeypatch):
    factura = _factura(xml_firmado=None)

    async def fake_obtener(pool, fid):
        return factura

    monkeypatch.setattr(repository, "obtener_factura", fake_obtener)

    response = client.get(f"/facturas/{factura['id']}/representacion-impresa")
    assert response.status_code == 409


def test_representacion_impresa_ok(client, monkeypatch, credencial_prueba):
    from libs.ecf_common.auth.signing import firmar_xml
    from services.facturas_api.app.xml_builder import construir_xml_ecf

    factura = _factura(estado="firmado", xml_generado="<ECF><Encabezado/></ECF>")
    xml_sin_firmar = construir_xml_ecf(factura["tipo_ecf"], factura["datos"])
    factura["xml_firmado"] = firmar_xml(xml_sin_firmar, credencial_prueba).decode("utf-8")

    async def fake_obtener(pool, fid):
        return factura

    monkeypatch.setattr(repository, "obtener_factura", fake_obtener)

    response = client.get(f"/facturas/{factura['id']}/representacion-impresa")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content.startswith(b"%PDF")


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


def _items_inventario():
    return [{"numero_linea": "1", "producto_id": str(uuid.uuid4()), "cantidad": "2.00"}]


@respx.mock
def test_enviar_factura_bloquea_por_stock_insuficiente(client, monkeypatch):
    factura = _factura(xml_generado="<ECF><Encabezado/></ECF>", items_inventario=_items_inventario())

    async def fake_obtener(pool, fid):
        return factura

    monkeypatch.setattr(repository, "obtener_factura", fake_obtener)

    respx.post(f"{main.settings.inventario_api_url}/movimientos/validar-disponibilidad").mock(
        return_value=Response(
            200,
            json={
                "disponible": False,
                "faltantes": [
                    {"producto_id": str(uuid.uuid4()), "nombre": "Producto X", "solicitado": "2", "disponible": "0"}
                ],
            },
        )
    )
    ruta_recepcion = respx.post(f"{main.settings.recepcion_service_url}/ecf")

    response = client.post(f"/facturas/{factura['id']}/enviar")

    assert response.status_code == 409
    assert "Producto X" in response.json()["detail"]
    assert not ruta_recepcion.called  # nunca debe llegar a enviarse a la DGII


@respx.mock
def test_enviar_factura_502_si_inventario_api_no_responde(client, monkeypatch):
    factura = _factura(xml_generado="<ECF><Encabezado/></ECF>", items_inventario=_items_inventario())

    async def fake_obtener(pool, fid):
        return factura

    monkeypatch.setattr(repository, "obtener_factura", fake_obtener)

    respx.post(f"{main.settings.inventario_api_url}/movimientos/validar-disponibilidad").mock(
        side_effect=ConnectError("connection refused")
    )
    ruta_recepcion = respx.post(f"{main.settings.recepcion_service_url}/ecf")

    response = client.post(f"/facturas/{factura['id']}/enviar")

    assert response.status_code == 502
    assert not ruta_recepcion.called


@respx.mock
def test_enviar_factura_ok_registra_salida_de_inventario(client, monkeypatch):
    factura = _factura(xml_generado="<ECF><Encabezado/></ECF>", items_inventario=_items_inventario())
    llamadas_sincronizacion = []

    async def fake_obtener(pool, fid):
        return factura

    async def fake_registrar(pool, fid, **kwargs):
        return {**factura, **kwargs}

    async def fake_sincronizacion(pool, fid, *, sincronizado, error):
        llamadas_sincronizacion.append((sincronizado, error))

    monkeypatch.setattr(repository, "obtener_factura", fake_obtener)
    monkeypatch.setattr(repository, "registrar_resultado_envio", fake_registrar)
    monkeypatch.setattr(repository, "registrar_sincronizacion_inventario", fake_sincronizacion)

    respx.post(f"{main.settings.inventario_api_url}/movimientos/validar-disponibilidad").mock(
        return_value=Response(200, json={"disponible": True, "faltantes": []})
    )
    ruta_salida = respx.post(f"{main.settings.inventario_api_url}/movimientos/salida-por-factura").mock(
        return_value=Response(200, json={"ok": True, "movimientos": []})
    )
    respx.post(f"{main.settings.recepcion_service_url}/ecf").mock(
        return_value=Response(200, json={"trackId": "track-abc"})
    )

    response = client.post(f"/facturas/{factura['id']}/enviar")

    assert response.status_code == 200
    assert ruta_salida.called
    assert llamadas_sincronizacion == [(True, None)]


@respx.mock
def test_enviar_factura_ok_no_falla_si_registrar_salida_falla(client, monkeypatch):
    """El e-CF ya se envió a la DGII (irreversible) -- un fallo al registrar
    la salida de inventario NO debe hacer fallar esta respuesta, solo se dejar
    constancia vía registrar_sincronizacion_inventario."""
    factura = _factura(xml_generado="<ECF><Encabezado/></ECF>", items_inventario=_items_inventario())
    llamadas_sincronizacion = []

    async def fake_obtener(pool, fid):
        return factura

    async def fake_registrar(pool, fid, **kwargs):
        return {**factura, **kwargs}

    async def fake_sincronizacion(pool, fid, *, sincronizado, error):
        llamadas_sincronizacion.append((sincronizado, error))

    monkeypatch.setattr(repository, "obtener_factura", fake_obtener)
    monkeypatch.setattr(repository, "registrar_resultado_envio", fake_registrar)
    monkeypatch.setattr(repository, "registrar_sincronizacion_inventario", fake_sincronizacion)

    respx.post(f"{main.settings.inventario_api_url}/movimientos/validar-disponibilidad").mock(
        return_value=Response(200, json={"disponible": True, "faltantes": []})
    )
    respx.post(f"{main.settings.inventario_api_url}/movimientos/salida-por-factura").mock(
        return_value=Response(500, text="boom")
    )
    respx.post(f"{main.settings.recepcion_service_url}/ecf").mock(
        return_value=Response(200, json={"trackId": "track-abc"})
    )

    response = client.post(f"/facturas/{factura['id']}/enviar")

    assert response.status_code == 200
    assert response.json()["track_id"] == "track-abc"
    assert len(llamadas_sincronizacion) == 1
    sincronizado, error = llamadas_sincronizacion[0]
    assert sincronizado is False
    assert error


@respx.mock
def test_enviar_factura_tipo_41_registra_compra_606_con_rnc_comprador(client, monkeypatch):
    """Tipo 41 (Compras Electrónico): en compras606 se reporta el RNC del
    proveedor informal, que en este sistema queda en el campo `Comprador`
    de la factura (ver docs/dgii/FORMATO_606.md)."""
    factura = _factura(tipo_ecf=41, xml_generado="<ECF><Encabezado/></ECF>")
    llamadas = []

    async def fake_obtener(pool, fid):
        return factura

    async def fake_registrar(pool, fid, **kwargs):
        return {**factura, **kwargs}

    async def fake_registrar_compra_606(pool, **kwargs):
        llamadas.append(kwargs)

    monkeypatch.setattr(repository, "obtener_factura", fake_obtener)
    monkeypatch.setattr(repository, "registrar_resultado_envio", fake_registrar)
    monkeypatch.setattr(envio, "registrar_compra_606", fake_registrar_compra_606)

    respx.post(f"{main.settings.recepcion_service_url}/ecf").mock(
        return_value=Response(200, json={"trackId": "track-abc"})
    )

    response = client.post(f"/facturas/{factura['id']}/enviar")

    assert response.status_code == 200
    assert len(llamadas) == 1
    assert llamadas[0]["origen"] == "auto_emitido"
    assert llamadas[0]["rnc_cedula"] == "101672919"  # RNCComprador de la factura
    assert llamadas[0]["referencia_tipo"] == "factura"
    assert llamadas[0]["referencia_id"] == factura["id"]
    assert llamadas[0]["monto_bienes"] == 1000  # item con IndicadorBienoServicio="1"
    assert llamadas[0]["fecha_comprobante"] == "20260902"  # de FechaEmision "02-09-2026"


@respx.mock
def test_enviar_factura_tipo_43_registra_compra_606_con_rnc_propio(client, monkeypatch):
    """Tipo 43 (Gastos Menores): sin proveedor identificado en el XSD, se
    reporta el RNC del propio encabezado (regla explícita del PDF oficial)."""
    datos = _factura()["datos"]
    del datos["Encabezado"]["Comprador"]  # eCF_43.xsd no tiene sección Comprador
    factura = _factura(tipo_ecf=43, xml_generado="<ECF><Encabezado/></ECF>", datos=datos)
    llamadas = []

    async def fake_obtener(pool, fid):
        return factura

    async def fake_registrar(pool, fid, **kwargs):
        return {**factura, **kwargs}

    async def fake_registrar_compra_606(pool, **kwargs):
        llamadas.append(kwargs)

    monkeypatch.setattr(repository, "obtener_factura", fake_obtener)
    monkeypatch.setattr(repository, "registrar_resultado_envio", fake_registrar)
    monkeypatch.setattr(envio, "registrar_compra_606", fake_registrar_compra_606)

    respx.post(f"{main.settings.recepcion_service_url}/ecf").mock(
        return_value=Response(200, json={"trackId": "track-abc"})
    )

    response = client.post(f"/facturas/{factura['id']}/enviar")

    assert response.status_code == 200
    assert len(llamadas) == 1
    assert llamadas[0]["rnc_cedula"] == main.settings.emisor_rnc


@respx.mock
def test_enviar_factura_tipo_31_no_registra_compra_606(client, monkeypatch):
    """Una venta normal (tipo 31) no forma parte del Formato 606 (que es de
    COMPRAS, no de ventas)."""
    factura = _factura(tipo_ecf=31, xml_generado="<ECF><Encabezado/></ECF>")
    llamadas = []

    async def fake_obtener(pool, fid):
        return factura

    async def fake_registrar(pool, fid, **kwargs):
        return {**factura, **kwargs}

    async def fake_registrar_compra_606(pool, **kwargs):
        llamadas.append(kwargs)

    monkeypatch.setattr(repository, "obtener_factura", fake_obtener)
    monkeypatch.setattr(repository, "registrar_resultado_envio", fake_registrar)
    monkeypatch.setattr(envio, "registrar_compra_606", fake_registrar_compra_606)

    respx.post(f"{main.settings.recepcion_service_url}/ecf").mock(
        return_value=Response(200, json={"trackId": "track-abc"})
    )

    response = client.post(f"/facturas/{factura['id']}/enviar")

    assert response.status_code == 200
    assert llamadas == []


@respx.mock
def test_enviar_factura_ok_no_falla_si_registrar_compra_606_falla(client, monkeypatch):
    factura = _factura(tipo_ecf=41, xml_generado="<ECF><Encabezado/></ECF>")

    async def fake_obtener(pool, fid):
        return factura

    async def fake_registrar(pool, fid, **kwargs):
        return {**factura, **kwargs}

    async def fake_registrar_compra_606(pool, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(repository, "obtener_factura", fake_obtener)
    monkeypatch.setattr(repository, "registrar_resultado_envio", fake_registrar)
    monkeypatch.setattr(envio, "registrar_compra_606", fake_registrar_compra_606)

    respx.post(f"{main.settings.recepcion_service_url}/ecf").mock(
        return_value=Response(200, json={"trackId": "track-abc"})
    )

    response = client.post(f"/facturas/{factura['id']}/enviar")

    assert response.status_code == 200


def test_crear_factura_serializa_datetime_reales(client, monkeypatch):
    """asyncpg devuelve datetime.datetime real (no str) para creado_en/actualizado_en."""
    ahora = datetime.now(timezone.utc)
    creada = _factura(creado_en=ahora, actualizado_en=ahora)

    async def fake_crear(pool, **kwargs):
        return creada

    monkeypatch.setattr(repository, "crear_factura", fake_crear)

    response = client.post(
        "/facturas",
        json={"tipo_ecf": 31, "encf": "E310000000001", "datos": creada["datos"]},
    )

    assert response.status_code == 201


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
