import uuid

import pytest
from fastapi.testclient import TestClient

from services.erp_api.app import main
from services.erp_api.app import repository


@pytest.fixture
def client(monkeypatch):
    async def fake_crear_pool(database_url):
        return object()  # pool "dummy": las funciones de repository se mockean por test

    async def fake_cerrar_pool(pool):
        return None

    async def fake_aplicar_migraciones(pool):
        return None

    monkeypatch.setattr(main, "crear_pool", fake_crear_pool)
    monkeypatch.setattr(main, "cerrar_pool", fake_cerrar_pool)
    monkeypatch.setattr(main, "aplicar_migraciones", fake_aplicar_migraciones)
    with TestClient(main.app) as c:
        yield c


def _cliente(**overrides) -> dict:
    base = {
        "id": str(uuid.uuid4()),
        "tipo": "cliente",
        "tipo_identificacion": "rnc",
        "rnc": "131234567",
        "nombre": "Ferretería Ozama SRL",
        "correo": None,
        "telefono": None,
        "direccion": None,
        "condicion_pago_dias": 30,
        "limite_credito": None,
        "notas": None,
        "activo": True,
        "saldo": 0,
        "creado_en": "2026-10-23T00:00:00Z",
        "actualizado_en": "2026-10-23T00:00:00Z",
    }
    base.update(overrides)
    return base


def _cotizacion(**overrides) -> dict:
    base = {
        "id": str(uuid.uuid4()),
        "numero": "COT-0001",
        "cliente_id": None,
        "cliente_nombre": "Cliente de paso",
        "cliente_rnc": None,
        "fecha": "2026-10-23",
        "vence": "2026-11-07",
        "items": [
            {
                "producto_id": None,
                "descripcion": "Cemento",
                "cantidad": "10",
                "precio_unitario": "450.00",
                "descuento_pct": "10",
                "itbis": "18",
                "importe": "4050.00",
            }
        ],
        "notas": None,
        "subtotal": "4500.00",
        "descuentos": "450.00",
        "itbis_total": "729.00",
        "total": "4779.00",
        "estado": "abierta",
        "factura_id": None,
        "creado_en": "2026-10-23T00:00:00Z",
        "actualizado_en": "2026-10-23T00:00:00Z",
    }
    base.update(overrides)
    return base


def _cobro(**overrides) -> dict:
    base = {
        "id": str(uuid.uuid4()),
        "factura_id": str(uuid.uuid4()),
        "encf": "E310000000001",
        "cliente_nombre": "Ferretería Ozama SRL",
        "fecha": "2026-10-23",
        "monto": "500",
        "metodo": "efectivo",
        "cuenta_destino": None,
        "referencia": None,
        "notas": None,
        "anulado": False,
        "creado_en": "2026-10-23T00:00:00Z",
    }
    base.update(overrides)
    return base


def test_health(client):
    assert client.get("/health").json()["status"] == "ok"


# --- Clientes ---------------------------------------------------------

def test_crear_cliente_montos_con_dos_decimales(client, monkeypatch):
    recibido = {}

    async def fake_crear(pool, **kwargs):
        recibido.update(kwargs)
        return _cliente(limite_credito="50000", saldo="0")

    monkeypatch.setattr(repository, "crear_cliente", fake_crear)

    response = client.post(
        "/clientes",
        json={"tipo": "cliente", "rnc": "131-23456-7", "nombre": "  Ferretería Ozama SRL  ", "limite_credito": "50000"},
    )
    assert response.status_code == 201
    cuerpo = response.json()
    assert cuerpo["saldo"] == "0.00"
    assert cuerpo["limite_credito"] == "50000.00"
    assert recibido["nombre"] == "Ferretería Ozama SRL"


def test_crear_cliente_rnc_invalido_devuelve_422_con_mensaje(client, monkeypatch):
    async def fake_crear(pool, **kwargs):
        raise repository.DatosInvalidosError("El RNC debe tener 9 dígitos (tiene 4)")

    monkeypatch.setattr(repository, "crear_cliente", fake_crear)

    response = client.post("/clientes", json={"tipo": "cliente", "tipo_identificacion": "rnc", "rnc": "1312", "nombre": "X"})
    assert response.status_code == 422
    assert response.json()["detail"] == "El RNC debe tener 9 dígitos (tiene 4)"


def test_crear_cliente_duplicado_devuelve_409(client, monkeypatch):
    async def fake_crear(pool, **kwargs):
        raise repository.ClienteDuplicadoError("Ya existe un contacto con el RNC/cédula 131234567")

    monkeypatch.setattr(repository, "crear_cliente", fake_crear)

    response = client.post("/clientes", json={"tipo": "cliente", "rnc": "131234567", "nombre": "X"})
    assert response.status_code == 409
    assert "131234567" in response.json()["detail"]


def test_listar_clientes_pasa_filtros(client, monkeypatch):
    recibido = {}

    async def fake_listar(pool, **kwargs):
        recibido.update(kwargs)
        return [_cliente()]

    monkeypatch.setattr(repository, "listar_clientes", fake_listar)

    response = client.get("/clientes", params={"q": "ozama", "tipo": "proveedor", "activo": "true"})
    assert response.status_code == 200
    assert len(response.json()) == 1
    assert recibido["q"] == "ozama" and recibido["tipo"] == "proveedor" and recibido["activo"] is True


def test_cliente_por_rnc_404(client, monkeypatch):
    async def fake_por_rnc(pool, rnc):
        raise repository.ClienteNoEncontradoError(rnc)

    monkeypatch.setattr(repository, "obtener_cliente_por_rnc", fake_por_rnc)

    assert client.get("/clientes/por-rnc/131234567").status_code == 404


def test_obtener_cliente_id_invalido_devuelve_422(client):
    assert client.get("/clientes/no-es-uuid").status_code == 422


def test_eliminar_cliente_con_cotizaciones_devuelve_409(client, monkeypatch):
    async def fake_eliminar(pool, cid):
        raise repository.ClienteConCotizacionesError(str(cid))

    monkeypatch.setattr(repository, "eliminar_cliente", fake_eliminar)

    response = client.delete(f"/clientes/{uuid.uuid4()}")
    assert response.status_code == 409
    assert "desactívelo" in response.json()["detail"]


def test_eliminar_cliente_ok(client, monkeypatch):
    async def fake_eliminar(pool, cid):
        return None

    monkeypatch.setattr(repository, "eliminar_cliente", fake_eliminar)

    assert client.delete(f"/clientes/{uuid.uuid4()}").status_code == 204


# --- Cotizaciones ---------------------------------------------------------

def test_crear_cotizacion(client, monkeypatch):
    recibido = {}

    async def fake_crear(pool, **kwargs):
        recibido.update(kwargs)
        return _cotizacion()

    monkeypatch.setattr(repository, "crear_cotizacion", fake_crear)

    response = client.post(
        "/cotizaciones",
        json={
            "cliente_nombre": "Cliente de paso",
            "items": [{"descripcion": "Cemento", "cantidad": "10", "precio_unitario": "450.00",
                       "descuento_pct": "10", "itbis": "18"}],
        },
    )
    assert response.status_code == 201
    cuerpo = response.json()
    assert cuerpo["numero"] == "COT-0001"
    assert cuerpo["items"][0]["importe"] == "4050.00"
    assert cuerpo["total"] == "4779.00"
    assert recibido["fecha"] is None and recibido["vence"] is None
    assert len(recibido["items"]) == 1


def test_crear_cotizacion_sin_items_devuelve_422(client):
    response = client.post("/cotizaciones", json={"cliente_nombre": "X", "items": []})
    assert response.status_code == 422


def test_crear_cotizacion_itbis_invalido_devuelve_422(client):
    response = client.post(
        "/cotizaciones",
        json={"cliente_nombre": "X",
              "items": [{"descripcion": "a", "cantidad": "1", "precio_unitario": "1", "itbis": "12"}]},
    )
    assert response.status_code == 422


def test_crear_cotizacion_sin_cliente_devuelve_422_con_mensaje(client, monkeypatch):
    async def fake_crear(pool, **kwargs):
        raise repository.DatosInvalidosError("Indique el cliente (cliente_id o cliente_nombre)")

    monkeypatch.setattr(repository, "crear_cotizacion", fake_crear)

    response = client.post(
        "/cotizaciones",
        json={"items": [{"descripcion": "a", "cantidad": "1", "precio_unitario": "1", "itbis": "exento"}]},
    )
    assert response.status_code == 422
    assert response.json()["detail"].startswith("Indique el cliente")


def test_actualizar_cotizacion_no_abierta_devuelve_409(client, monkeypatch):
    async def fake_actualizar(pool, cid, **kwargs):
        raise repository.CotizacionEstadoInvalidoError("Solo se pueden editar cotizaciones abiertas")

    monkeypatch.setattr(repository, "actualizar_cotizacion", fake_actualizar)

    response = client.put(
        f"/cotizaciones/{uuid.uuid4()}",
        json={"cliente_nombre": "X",
              "items": [{"descripcion": "a", "cantidad": "1", "precio_unitario": "1", "itbis": "18"}]},
    )
    assert response.status_code == 409


def test_listar_cotizaciones_estado_vencida(client, monkeypatch):
    recibido = {}

    async def fake_listar(pool, **kwargs):
        recibido.update(kwargs)
        return [_cotizacion(estado="vencida")]

    monkeypatch.setattr(repository, "listar_cotizaciones", fake_listar)

    response = client.get("/cotizaciones", params={"estado": "vencida"})
    assert response.status_code == 200
    assert response.json()[0]["estado"] == "vencida"
    assert "items" not in response.json()[0]  # el listado es CotizacionResumen
    assert recibido["estado"] == "vencida"


def test_cambiar_estado_no_admite_facturada(client):
    response = client.post(f"/cotizaciones/{uuid.uuid4()}/estado", json={"estado": "facturada"})
    assert response.status_code == 422


def test_cambiar_estado_desde_facturada_devuelve_409(client, monkeypatch):
    async def fake_cambiar(pool, cid, estado):
        raise repository.CotizacionEstadoInvalidoError("La cotización ya fue facturada y no cambia de estado")

    monkeypatch.setattr(repository, "cambiar_estado_cotizacion", fake_cambiar)

    response = client.post(f"/cotizaciones/{uuid.uuid4()}/estado", json={"estado": "abierta"})
    assert response.status_code == 409


def test_marcar_facturada(client, monkeypatch):
    factura_id = uuid.uuid4()

    async def fake_marcar(pool, cid, fid):
        assert fid == factura_id
        return _cotizacion(estado="facturada", factura_id=str(fid))

    monkeypatch.setattr(repository, "marcar_cotizacion_facturada", fake_marcar)

    response = client.post(f"/cotizaciones/{uuid.uuid4()}/facturada", json={"factura_id": str(factura_id)})
    assert response.status_code == 200
    assert response.json()["estado"] == "facturada"
    assert response.json()["factura_id"] == str(factura_id)


def test_eliminar_cotizacion_404(client, monkeypatch):
    async def fake_eliminar(pool, cid):
        raise repository.CotizacionNoEncontradaError(str(cid))

    monkeypatch.setattr(repository, "eliminar_cotizacion", fake_eliminar)

    assert client.delete(f"/cotizaciones/{uuid.uuid4()}").status_code == 404


# --- Cuentas por cobrar y cobros ---------------------------------------------------------

def test_listar_cuentas_por_cobrar(client, monkeypatch):
    recibido = {}

    async def fake_listar(pool, **kwargs):
        recibido.update(kwargs)
        return [
            {
                "factura_id": str(uuid.uuid4()),
                "encf": "E310000000001",
                "tipo_ecf": 31,
                "cliente_rnc": "131234567",
                "cliente_nombre": "Ferretería Ozama SRL",
                "fecha_emision": "2026-09-01",
                "vence": "2026-10-01",
                "total": "11800",
                "cobrado": "0",
                "pendiente": "11800",
                "estado": "vencida",
                "dias_vencida": 22,
            }
        ]

    monkeypatch.setattr(repository, "listar_cuentas_por_cobrar", fake_listar)

    response = client.get("/cuentas-por-cobrar", params={"cliente_rnc": "131234567"})
    assert response.status_code == 200
    cuenta = response.json()[0]
    assert cuenta["cobrado"] == "0.00" and cuenta["pendiente"] == "11800.00"
    assert cuenta["vence"] == "2026-10-01"
    assert recibido == {"estado": None, "cliente_rnc": "131234567"}


def test_listar_cuentas_por_cobrar_estado_invalido(client):
    assert client.get("/cuentas-por-cobrar", params={"estado": "otra"}).status_code == 422


def test_registrar_cobro(client, monkeypatch):
    async def fake_registrar(pool, **kwargs):
        assert kwargs["fecha"] is None
        return _cobro(monto="500")

    monkeypatch.setattr(repository, "registrar_cobro", fake_registrar)

    response = client.post(
        "/cobros", json={"factura_id": str(uuid.uuid4()), "monto": "500", "metodo": "efectivo"}
    )
    assert response.status_code == 201
    assert response.json()["monto"] == "500.00"


def test_registrar_cobro_excede_pendiente_devuelve_422(client, monkeypatch):
    async def fake_registrar(pool, **kwargs):
        raise repository.DatosInvalidosError("El monto del cobro (900.00) supera lo pendiente de la factura (500.00)")

    monkeypatch.setattr(repository, "registrar_cobro", fake_registrar)

    response = client.post(
        "/cobros", json={"factura_id": str(uuid.uuid4()), "monto": "900", "metodo": "cheque"}
    )
    assert response.status_code == 422
    assert "supera lo pendiente" in response.json()["detail"]


def test_registrar_cobro_metodo_invalido_devuelve_422(client):
    response = client.post(
        "/cobros", json={"factura_id": str(uuid.uuid4()), "monto": "10", "metodo": "bitcoin"}
    )
    assert response.status_code == 422


def test_anular_cobro_ya_anulado_devuelve_409(client, monkeypatch):
    async def fake_anular(pool, cid):
        raise repository.CobroEstadoInvalidoError("El cobro ya está anulado")

    monkeypatch.setattr(repository, "anular_cobro", fake_anular)

    assert client.post(f"/cobros/{uuid.uuid4()}/anular").status_code == 409


def test_listar_cobros_factura_id_invalido_devuelve_422(client):
    assert client.get("/cobros", params={"factura_id": "x"}).status_code == 422


# --- Documentos de venta ---------------------------------------------------------

def test_listar_documentos_venta(client, monkeypatch):
    recibido = {}

    async def fake_listar(pool, **kwargs):
        recibido.update(kwargs)
        return [
            {
                "id": str(uuid.uuid4()),
                "tipo_ecf": 34,
                "encf": "E340000000001",
                "estado": "aceptado",
                "cliente_rnc": "131234567",
                "cliente_nombre": "Ferretería Ozama SRL",
                "fecha_emision": "2026-10-22",
                "total": "1180",
                "encf_afectado": "E310000000001",
                "codigo_modificacion": "3",
                "dgii_codigo": None,
                "error": None,
                "creado_en": "2026-10-22T00:00:00Z",
            }
        ]

    monkeypatch.setattr(repository, "listar_documentos_venta", fake_listar)

    response = client.get("/documentos-venta", params={"tipo_ecf": 34, "q": "ozama"})
    assert response.status_code == 200
    documento = response.json()[0]
    assert documento["total"] == "1180.00"
    assert documento["encf_afectado"] == "E310000000001"
    assert recibido["tipo_ecf"] == 34 and recibido["q"] == "ozama"
