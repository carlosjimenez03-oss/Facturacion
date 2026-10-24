"""Resultado de la DGII por trackId: POST /facturas/{id}/actualizar-estado,
la consulta inmediata tras enviar y la tarea de fondo (app.estado_dgii)."""
import asyncio
import json
import uuid

import pytest
import respx
from fastapi.testclient import TestClient
from httpx import ConnectError, Response

from services.facturas_api.app import estado_dgii, main, repository
from services.facturas_api.tests.test_main import _factura

_URL_RESULTADO = f"{main.settings.consulta_resultado_url}/resultado/track-abc"


async def _sin_espera(segundos=None):
    return None


@pytest.fixture(autouse=True)
def _sin_sincronizacion_de_fondo(monkeypatch):
    monkeypatch.setattr(main.settings, "sincronizar_estado_cada_segundos", 0)
    monkeypatch.setattr(main, "_ESPERA_CONSULTA_TRAS_ENVIO_SEGUNDOS", 0)


@pytest.fixture
def client(monkeypatch):
    async def fake_crear_pool(database_url):
        return object()

    monkeypatch.setattr(main, "crear_pool", fake_crear_pool)
    monkeypatch.setattr(main, "cerrar_pool", lambda pool: _sin_espera())
    with TestClient(main.app) as c:
        yield c


def _consulta(codigo: int, estado: str, mensajes=None, track_id: str = "track-abc") -> dict:
    return {
        "trackId": track_id,
        "codigo": codigo,
        "estado": estado,
        "rnc": "131234567",
        "eNCF": "E310000000001",
        "secuenciaUtilizada": codigo in (1, 2, 4),
        "fechaRecepcion": "02-09-2026 10:15:00",
        "mensajes": mensajes or [],
    }


@pytest.fixture
def resultado_dgii(monkeypatch):
    """Factura enviada + fakes de repository/auditoría; devuelve lo registrado."""
    registro = {
        "factura": _factura(
            estado="enviado", track_id="track-abc", dgii_respuesta={"trackId": "track-abc"}
        ),
        "actualizaciones": [],
        "auditoria": [],
    }

    async def fake_obtener(pool, fid):
        return registro["factura"]

    async def fake_registrar_consulta(pool, fid, **kwargs):
        registro["actualizaciones"].append(kwargs)
        return {
            **registro["factura"],
            "estado": kwargs["estado"],
            "dgii_estado": kwargs["dgii_estado"],
            "dgii_codigo": kwargs["dgii_codigo"],
            "dgii_respuesta": {**kwargs["consulta"], "envio": registro["factura"]["dgii_respuesta"]},
            "error": kwargs["error"],
        }

    async def fake_registrar_envio(pool, **kwargs):
        registro["auditoria"].append(kwargs)

    monkeypatch.setattr(repository, "obtener_factura", fake_obtener)
    monkeypatch.setattr(repository, "registrar_resultado_consulta", fake_registrar_consulta)
    monkeypatch.setattr(estado_dgii, "registrar_envio", fake_registrar_envio)
    monkeypatch.setattr(main, "registrar_envio", fake_registrar_envio)
    return registro


def _actualizar(client, registro):
    return client.post(f"/facturas/{registro['factura']['id']}/actualizar-estado")


@respx.mock
def test_actualizar_estado_aceptado(client, resultado_dgii):
    respx.get(_URL_RESULTADO).mock(return_value=Response(200, json=_consulta(1, "Aceptado")))

    response = _actualizar(client, resultado_dgii)

    assert response.status_code == 200
    cuerpo = response.json()
    assert cuerpo["estado"] == "aceptado"
    assert cuerpo["dgii_estado"] == "Aceptado"
    assert cuerpo["dgii_codigo"] == "1"
    assert cuerpo["error"] is None
    assert cuerpo["dgii_respuesta"]["envio"] == {"trackId": "track-abc"}
    [actualizacion] = resultado_dgii["actualizaciones"]
    assert actualizacion["track_id"] == "track-abc"
    [auditoria] = resultado_dgii["auditoria"]
    assert auditoria["modulo"] == "consulta_resultado"
    assert auditoria["codigo"] == "1"
    assert auditoria["track_id"] == "track-abc"
    assert auditoria["encf"] == "E310000000001"


@respx.mock
def test_actualizar_estado_aceptado_condicional(client, resultado_dgii):
    respx.get(_URL_RESULTADO).mock(
        return_value=Response(
            200, json=_consulta(4, "Aceptado Condicional", [{"codigo": 613, "valor": "Advertencia"}])
        )
    )

    response = _actualizar(client, resultado_dgii)

    assert response.status_code == 200
    cuerpo = response.json()
    assert cuerpo["estado"] == "aceptado"
    assert cuerpo["dgii_estado"] == "Aceptado Condicional"
    assert cuerpo["dgii_codigo"] == "4"
    assert cuerpo["error"] is None


@respx.mock
def test_actualizar_estado_rechazado_guarda_mensajes(client, resultado_dgii):
    mensajes = [
        {"codigo": 1, "valor": "El RNC del comprador no es válido"},
        {"codigo": 2, "valor": "Monto total no cuadra"},
    ]
    respx.get(_URL_RESULTADO).mock(return_value=Response(200, json=_consulta(2, "Rechazado", mensajes)))

    response = _actualizar(client, resultado_dgii)

    assert response.status_code == 200
    cuerpo = response.json()
    assert cuerpo["estado"] == "rechazado"
    assert cuerpo["dgii_estado"] == "Rechazado"
    assert cuerpo["dgii_codigo"] == "2"
    assert cuerpo["error"] == "El RNC del comprador no es válido (código 1); Monto total no cuadra (código 2)"
    # el panel de rechazo del frontend lee `mensajes` / `secuenciaUtilizada` en la raíz
    assert cuerpo["dgii_respuesta"]["mensajes"] == mensajes
    assert cuerpo["dgii_respuesta"]["secuenciaUtilizada"] is True


@pytest.mark.parametrize("codigo,estado", [(3, "En Proceso"), (0, "No encontrado")])
@respx.mock
def test_actualizar_estado_sin_resultado_definitivo_no_cambia(client, resultado_dgii, codigo, estado):
    respx.get(_URL_RESULTADO).mock(return_value=Response(200, json=_consulta(codigo, estado)))

    response = _actualizar(client, resultado_dgii)

    assert response.status_code == 200
    assert response.json()["estado"] == "enviado"
    assert resultado_dgii["actualizaciones"] == []
    # la consulta manual sí queda auditada aunque no haya resultado todavía
    assert resultado_dgii["auditoria"][0]["codigo"] == str(codigo)


@respx.mock
def test_actualizar_estado_factura_definitiva_no_consulta(client, resultado_dgii):
    resultado_dgii["factura"].update(estado="aceptado", dgii_estado="Aceptado", dgii_codigo="1")
    ruta = respx.get(_URL_RESULTADO).mock(return_value=Response(200, json=_consulta(2, "Rechazado")))

    response = _actualizar(client, resultado_dgii)

    assert response.status_code == 200
    assert response.json()["estado"] == "aceptado"
    assert not ruta.called
    assert resultado_dgii["actualizaciones"] == []


@respx.mock
def test_actualizar_estado_reconsulta_aceptado_condicional(client, resultado_dgii):
    resultado_dgii["factura"].update(estado="aceptado", dgii_estado="Aceptado Condicional", dgii_codigo="4")
    respx.get(_URL_RESULTADO).mock(return_value=Response(200, json=_consulta(1, "Aceptado")))

    response = _actualizar(client, resultado_dgii)

    assert response.status_code == 200
    assert response.json()["dgii_estado"] == "Aceptado"


@respx.mock
def test_actualizar_estado_error_con_track_id(client, resultado_dgii):
    resultado_dgii["factura"].update(estado="error", error="timeout")
    respx.get(_URL_RESULTADO).mock(return_value=Response(200, json=_consulta(1, "Aceptado")))

    response = _actualizar(client, resultado_dgii)

    assert response.json()["estado"] == "aceptado"


@respx.mock
def test_actualizar_estado_idempotente_si_otra_peticion_ya_lo_registro(client, resultado_dgii, monkeypatch):
    """Si la fila ya no está en un estado actualizable, el repository no pisa
    nada (devuelve None) y se responde la factura tal como quedó."""
    ya_aceptada = {**resultado_dgii["factura"], "estado": "aceptado", "dgii_estado": "Aceptado"}
    lecturas = iter([resultado_dgii["factura"], ya_aceptada])

    async def fake_registrar_consulta(pool, fid, **kwargs):
        return None

    async def fake_obtener(pool, fid):
        return next(lecturas)

    monkeypatch.setattr(repository, "registrar_resultado_consulta", fake_registrar_consulta)
    monkeypatch.setattr(repository, "obtener_factura", fake_obtener)
    respx.get(_URL_RESULTADO).mock(return_value=Response(200, json=_consulta(2, "Rechazado")))

    response = _actualizar(client, resultado_dgii)

    assert response.status_code == 200
    assert response.json()["estado"] == "aceptado"


def test_actualizar_estado_409_sin_track_id(client, resultado_dgii):
    resultado_dgii["factura"].update(estado="firmado", track_id=None)

    response = _actualizar(client, resultado_dgii)

    assert response.status_code == 409


@respx.mock
def test_actualizar_estado_502_si_consulta_resultado_no_responde(client, resultado_dgii):
    respx.get(_URL_RESULTADO).mock(side_effect=ConnectError("conexión rechazada"))

    response = _actualizar(client, resultado_dgii)

    assert response.status_code == 502
    assert "No se pudo consultar el estado" in response.json()["detail"]
    assert resultado_dgii["actualizaciones"] == []
    assert "conexión rechazada" in resultado_dgii["auditoria"][0]["error"]


@respx.mock
def test_actualizar_estado_502_si_consulta_resultado_responde_error(client, resultado_dgii):
    respx.get(_URL_RESULTADO).mock(return_value=Response(502, text="DGII caída"))

    response = _actualizar(client, resultado_dgii)

    assert response.status_code == 502
    assert "DGII caída" in response.json()["detail"]


# --- Consulta inmediata tras /enviar ---


def _preparar_envio(monkeypatch):
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
    return factura


@respx.mock
def test_enviar_factura_consulta_resultado_inmediato(client, monkeypatch, resultado_dgii):
    factura = _preparar_envio(monkeypatch)
    respx.get(_URL_RESULTADO).mock(return_value=Response(200, json=_consulta(1, "Aceptado")))

    response = client.post(f"/facturas/{factura['id']}/enviar")

    assert response.status_code == 200
    cuerpo = response.json()
    assert {k: cuerpo[k] for k in ("id", "estado", "track_id", "error", "contingencia")} == {
        "id": factura["id"], "estado": "aceptado", "track_id": "track-abc", "error": None, "contingencia": False,
    }


@respx.mock
def test_enviar_factura_en_proceso_queda_enviado(client, monkeypatch, resultado_dgii):
    factura = _preparar_envio(monkeypatch)
    respx.get(_URL_RESULTADO).mock(return_value=Response(200, json=_consulta(3, "En Proceso")))

    response = client.post(f"/facturas/{factura['id']}/enviar")

    assert response.status_code == 200
    assert response.json()["estado"] == "enviado"


@pytest.mark.parametrize(
    "fallo",
    [ConnectError("conexión rechazada"), Response(500, text="error interno"), Response(200, text="no-json")],
)
@respx.mock
def test_enviar_factura_no_falla_si_la_consulta_inmediata_falla(client, monkeypatch, resultado_dgii, fallo):
    factura = _preparar_envio(monkeypatch)
    ruta = respx.get(_URL_RESULTADO)
    if isinstance(fallo, Exception):
        ruta.mock(side_effect=fallo)
    else:
        ruta.mock(return_value=fallo)

    response = client.post(f"/facturas/{factura['id']}/enviar")

    assert response.status_code == 200
    assert response.json()["estado"] == "enviado"
    assert response.json()["track_id"] == "track-abc"
    assert resultado_dgii["actualizaciones"] == []


@respx.mock
def test_enviar_factura_no_falla_si_el_repository_falla_en_la_consulta(client, monkeypatch, resultado_dgii):
    factura = _preparar_envio(monkeypatch)
    respx.get(_URL_RESULTADO).mock(return_value=Response(200, json=_consulta(1, "Aceptado")))

    async def fake_registrar_consulta(pool, fid, **kwargs):
        raise RuntimeError("se cayó la conexión a Postgres")

    monkeypatch.setattr(repository, "registrar_resultado_consulta", fake_registrar_consulta)

    response = client.post(f"/facturas/{factura['id']}/enviar")

    assert response.status_code == 200
    assert response.json()["estado"] == "enviado"


# --- Tarea de fondo ---


def _pendiente(track_id: str) -> dict:
    return {
        "id": _factura()["id"],
        "rnc_emisor": "131234567",
        "encf": "E310000000001",
        "estado": "enviado",
        "track_id": track_id,
        "dgii_estado": None,
    }


@respx.mock
async def test_sincronizar_pendientes_actualiza_y_sigue_ante_errores(monkeypatch, resultado_dgii):
    pendientes = [_pendiente("t-acep"), _pendiente("t-proc"), _pendiente("t-falla"), _pendiente("t-rech")]
    pedidos = {}

    async def fake_listar(pool, *, limite, dias):
        pedidos.update(limite=limite, dias=dias)
        return pendientes

    async def fake_registrar_consulta(pool, fid, **kwargs):
        if kwargs["track_id"] == "t-falla":
            raise RuntimeError("fallo inesperado")
        resultado_dgii["actualizaciones"].append(kwargs)
        return {"id": str(fid), "estado": kwargs["estado"]}

    monkeypatch.setattr(repository, "listar_pendientes_de_resultado", fake_listar)
    monkeypatch.setattr(repository, "registrar_resultado_consulta", fake_registrar_consulta)
    base = main.settings.consulta_resultado_url
    respx.get(f"{base}/resultado/t-acep").mock(return_value=Response(200, json=_consulta(1, "Aceptado")))
    respx.get(f"{base}/resultado/t-proc").mock(return_value=Response(200, json=_consulta(3, "En Proceso")))
    respx.get(f"{base}/resultado/t-falla").mock(return_value=Response(200, json=_consulta(1, "Aceptado")))
    respx.get(f"{base}/resultado/t-rech").mock(return_value=Response(200, json=_consulta(2, "Rechazado")))

    cambiadas = await estado_dgii.sincronizar_pendientes(object(), main.settings)

    assert cambiadas == 2
    assert pedidos == {"limite": 50, "dias": 30}
    assert [a["estado"] for a in resultado_dgii["actualizaciones"]] == ["aceptado", "rechazado"]
    # la tarea de fondo no audita cada "En Proceso" (solo resultados definitivos)
    assert [a["track_id"] for a in resultado_dgii["auditoria"]] == ["t-acep", "t-falla", "t-rech"]


@respx.mock
async def test_sincronizar_pendientes_corta_si_consulta_resultado_no_responde(monkeypatch, resultado_dgii):
    async def fake_listar(pool, *, limite, dias):
        return [_pendiente("t-1"), _pendiente("t-2")]

    monkeypatch.setattr(repository, "listar_pendientes_de_resultado", fake_listar)
    ruta = respx.get(url__regex=r".*/resultado/.*").mock(side_effect=ConnectError("caído"))

    cambiadas = await estado_dgii.sincronizar_pendientes(object(), main.settings)

    assert cambiadas == 0
    assert ruta.call_count == 1


async def test_bucle_sincronizacion_sobrevive_a_excepciones_y_se_cancela(monkeypatch):
    llamadas = []
    sincronizado = asyncio.Event()

    async def fake_sincronizar(pool, settings):
        llamadas.append(1)
        if len(llamadas) == 1:
            raise RuntimeError("Postgres no responde")
        sincronizado.set()
        return 1

    monkeypatch.setattr(estado_dgii, "sincronizar_pendientes", fake_sincronizar)
    settings = main.settings.model_copy(update={"sincronizar_estado_cada_segundos": 0.001})

    tarea = asyncio.create_task(estado_dgii.bucle_sincronizacion(object(), settings))
    await asyncio.wait_for(sincronizado.wait(), timeout=2)
    tarea.cancel()
    with pytest.raises(asyncio.CancelledError):
        await tarea

    assert len(llamadas) >= 2


def test_lifespan_inicia_y_cancela_la_sincronizacion(monkeypatch):
    estado = {"iniciada": False, "cancelada": False}

    async def fake_bucle(pool, settings):
        estado["iniciada"] = True
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            estado["cancelada"] = True
            raise

    async def fake_crear_pool(database_url):
        return object()

    monkeypatch.setattr(main, "crear_pool", fake_crear_pool)
    monkeypatch.setattr(main, "cerrar_pool", lambda pool: _sin_espera())
    monkeypatch.setattr(estado_dgii, "bucle_sincronizacion", fake_bucle)
    monkeypatch.setattr(main.settings, "sincronizar_estado_cada_segundos", 60)

    with TestClient(main.app) as c:
        assert c.get("/health").status_code == 200
        assert estado["iniciada"]

    assert estado["cancelada"]


def test_interpretar_resultado():
    assert estado_dgii.interpretar_resultado({"codigo": 1, "estado": "Aceptado"}) == ("aceptado", "Aceptado", None)
    assert estado_dgii.interpretar_resultado({"codigo": "4"}) == ("aceptado", "Aceptado Condicional", None)
    assert estado_dgii.interpretar_resultado({"codigo": 2, "mensajes": []}) == (
        "rechazado",
        "Rechazado",
        "Rechazado por la DGII (sin mensajes de validación)",
    )
    assert estado_dgii.interpretar_resultado({"codigo": 3}) is None
    assert estado_dgii.interpretar_resultado({"codigo": 0}) is None
    assert estado_dgii.interpretar_resultado({"codigo": None}) is None


# --- Reintento de salidas de inventario ---


def _sin_inventario(fid: str) -> dict:
    return {
        "id": fid,
        "encf": "E310000000001",
        "estado": "aceptado",
        "items_inventario": [{"numero_linea": "1", "producto_id": str(uuid.uuid4()), "cantidad": "2.00"}],
    }


@respx.mock
async def test_sincronizar_inventario_pendiente_reintenta_y_marca(monkeypatch):
    ok, falla = str(uuid.uuid4()), str(uuid.uuid4())
    marcas = []

    async def fake_listar(pool, *, limite):
        return [_sin_inventario(ok), _sin_inventario(falla)]

    async def fake_marcar(pool, fid, *, sincronizado, error):
        marcas.append((str(fid), sincronizado, error is None))

    monkeypatch.setattr(repository, "listar_inventario_pendiente", fake_listar)
    monkeypatch.setattr(repository, "registrar_sincronizacion_inventario", fake_marcar)

    def responder(request):
        cuerpo = json.loads(request.content)
        assert cuerpo["documento"] == "E310000000001"
        return Response(200, json=[]) if cuerpo["factura_id"] == ok else Response(409, json={"detail": "x"})

    respx.post(f"{main.settings.inventario_api_url}/movimientos/salida-por-factura").mock(side_effect=responder)

    sincronizadas = await estado_dgii.sincronizar_inventario_pendiente(object(), main.settings)

    assert sincronizadas == 1
    assert marcas == [(ok, True, True), (falla, False, False)]


@respx.mock
async def test_sincronizar_inventario_pendiente_corta_si_inventario_no_responde(monkeypatch):
    marcas = []

    async def fake_listar(pool, *, limite):
        return [_sin_inventario(str(uuid.uuid4())), _sin_inventario(str(uuid.uuid4()))]

    async def fake_marcar(pool, fid, *, sincronizado, error):
        marcas.append(sincronizado)

    monkeypatch.setattr(repository, "listar_inventario_pendiente", fake_listar)
    monkeypatch.setattr(repository, "registrar_sincronizacion_inventario", fake_marcar)
    respx.post(f"{main.settings.inventario_api_url}/movimientos/salida-por-factura").mock(
        side_effect=ConnectError("caído")
    )

    assert await estado_dgii.sincronizar_inventario_pendiente(object(), main.settings) == 0
    assert marcas == [False]
