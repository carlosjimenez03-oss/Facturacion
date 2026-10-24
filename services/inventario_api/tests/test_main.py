import uuid
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from services.inventario_api.app import ajustes_repository, almacenes_repository, kardex, lotes_repository, main
from services.inventario_api.app import repository


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


def _producto(**overrides) -> dict:
    base = {
        "id": str(uuid.uuid4()),
        "sku": "SKU-1",
        "nombre": "Producto X",
        "descripcion": None,
        "unidad_medida": "UND",
        "precio_venta": "100.0000",
        "costo_promedio": "60.0000",
        "stock_actual": "10.0000",
        "activo": True,
        "creado_en": "2026-09-08T00:00:00Z",
        "actualizado_en": "2026-09-08T00:00:00Z",
    }
    base.update(overrides)
    return base


def _compra(**overrides) -> dict:
    base = {
        "id": str(uuid.uuid4()),
        "proveedor_id": None,
        "numero_documento": None,
        "estado": "borrador",
        "notas": None,
        "creado_en": "2026-09-08T00:00:00Z",
        "actualizado_en": "2026-09-08T00:00:00Z",
        "confirmada_en": None,
        "items": [],
    }
    base.update(overrides)
    return base


def test_health(client):
    assert client.get("/health").json()["status"] == "ok"


def test_crear_producto(client, monkeypatch):
    creado = _producto()

    async def fake_crear(pool, **kwargs):
        return creado

    monkeypatch.setattr(repository, "crear_producto", fake_crear)

    response = client.post(
        "/productos",
        json={"sku": "SKU-1", "nombre": "Producto X", "precio_venta": "100.00", "stock_inicial": "10"},
    )
    assert response.status_code == 201
    assert response.json()["sku"] == "SKU-1"


def test_obtener_producto_404(client, monkeypatch):
    async def fake_obtener(pool, pid):
        raise repository.ProductoNoEncontradoError(str(pid))

    monkeypatch.setattr(repository, "obtener_producto", fake_obtener)

    response = client.get(f"/productos/{uuid.uuid4()}")
    assert response.status_code == 404


def test_actualizar_producto(client, monkeypatch):
    actualizado = _producto(nombre="Producto Y")

    async def fake_actualizar(pool, pid, **kwargs):
        return actualizado

    monkeypatch.setattr(repository, "actualizar_producto", fake_actualizar)

    response = client.put(
        f"/productos/{uuid.uuid4()}",
        json={"sku": "SKU-1", "nombre": "Producto Y", "precio_venta": "120.00", "activo": True},
    )
    assert response.status_code == 200
    assert response.json()["nombre"] == "Producto Y"


def test_crear_compra(client, monkeypatch):
    producto_id = str(uuid.uuid4())
    creada = _compra(items=[{"id": str(uuid.uuid4()), "producto_id": producto_id, "cantidad": "5", "costo_unitario": "10"}])

    async def fake_crear(pool, **kwargs):
        return creada

    monkeypatch.setattr(repository, "crear_compra", fake_crear)

    response = client.post(
        "/compras",
        json={"items": [{"producto_id": producto_id, "cantidad": "5", "costo_unitario": "10"}]},
    )
    assert response.status_code == 201
    assert len(response.json()["items"]) == 1


def test_confirmar_compra_ok(client, monkeypatch):
    confirmada = _compra(estado="confirmada", confirmada_en="2026-09-08T00:00:00Z")

    async def fake_confirmar(pool, cid):
        return confirmada

    monkeypatch.setattr(repository, "confirmar_compra", fake_confirmar)

    response = client.post(f"/compras/{uuid.uuid4()}/confirmar")
    assert response.status_code == 200
    assert response.json()["estado"] == "confirmada"


def test_confirmar_compra_ya_confirmada_devuelve_409(client, monkeypatch):
    async def fake_confirmar(pool, cid):
        raise repository.CompraEstadoInvalidoError("La compra ya está en estado 'confirmada'")

    monkeypatch.setattr(repository, "confirmar_compra", fake_confirmar)

    response = client.post(f"/compras/{uuid.uuid4()}/confirmar")
    assert response.status_code == 409


def test_eliminar_compra_no_borrador_devuelve_404(client, monkeypatch):
    async def fake_eliminar(pool, cid):
        raise repository.CompraNoEncontradaError(str(cid))

    monkeypatch.setattr(repository, "eliminar_compra", fake_eliminar)

    response = client.delete(f"/compras/{uuid.uuid4()}")
    assert response.status_code == 404


def test_validar_disponibilidad_insuficiente(client, monkeypatch):
    producto_id = uuid.uuid4()

    async def fake_validar(pool, items):
        return (False, [{"producto_id": str(producto_id), "nombre": "Producto X", "solicitado": "5", "disponible": "1"}])

    monkeypatch.setattr(repository, "validar_disponibilidad", fake_validar)

    response = client.post(
        "/movimientos/validar-disponibilidad",
        json={"items": [{"producto_id": str(producto_id), "cantidad": "5"}]},
    )
    assert response.status_code == 200
    cuerpo = response.json()
    assert cuerpo["disponible"] is False
    assert len(cuerpo["faltantes"]) == 1


def test_salida_por_factura(client, monkeypatch):
    producto_id = uuid.uuid4()
    movimiento = {
        "id": str(uuid.uuid4()),
        "producto_id": str(producto_id),
        "tipo": "salida",
        "cantidad": "2",
        "costo_unitario": None,
        "motivo": "Venta (factura enviada a la DGII)",
        "referencia_tipo": "factura",
        "referencia_id": "factura-1",
        "creado_en": "2026-09-08T00:00:00Z",
    }

    async def fake_salida(pool, factura_id, items, *, documento=None):
        assert documento is None
        return [movimiento]

    monkeypatch.setattr(repository, "registrar_salida_por_factura", fake_salida)

    response = client.post(
        "/movimientos/salida-por-factura",
        json={"factura_id": "factura-1", "items": [{"producto_id": str(producto_id), "cantidad": "2"}]},
    )
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert len(response.json()["movimientos"]) == 1


# --- Fase 4 ---------------------------------------------------------

def test_actualizar_producto_solo_pasa_los_campos_nuevos_que_vienen(client, monkeypatch):
    """Un cliente de antes (sin tipo/categoría/...) no los borra."""
    recibidos = []

    async def fake_actualizar(pool, pid, **kwargs):
        recibidos.append(kwargs)
        return _producto()

    monkeypatch.setattr(repository, "actualizar_producto", fake_actualizar)
    base = {"sku": "SKU-1", "nombre": "X", "precio_venta": "1", "activo": True}

    client.put(f"/productos/{uuid.uuid4()}", json=base)
    client.put(f"/productos/{uuid.uuid4()}", json=base | {"categoria": None, "tasa_itbis": "16"})

    assert "categoria" not in recibidos[0] and "tasa_itbis" not in recibidos[0]
    assert recibidos[1]["categoria"] is None and recibidos[1]["tasa_itbis"] == "16"
    assert client.put(f"/productos/{uuid.uuid4()}", json=base | {"tipo": None}).status_code == 422
    assert client.put(f"/productos/{uuid.uuid4()}", json=base | {"tasa_itbis": "12"}).status_code == 422


def test_validar_disponibilidad_pasa_almacen_y_lote(client, monkeypatch):
    recibidos = []

    async def fake_validar(pool, items):
        recibidos.extend(items)
        return (True, [])

    monkeypatch.setattr(repository, "validar_disponibilidad", fake_validar)
    almacen_id, lote_id = uuid.uuid4(), uuid.uuid4()

    response = client.post(
        "/movimientos/validar-disponibilidad",
        json={"items": [{"producto_id": str(uuid.uuid4()), "cantidad": "1", "numero_linea": "1",
                         "almacen_id": str(almacen_id), "lote_id": str(lote_id)}]},
    )

    assert response.status_code == 200
    assert recibidos[0]["almacen_id"] == almacen_id and recibidos[0]["lote_id"] == lote_id
    assert recibidos[0]["numero_linea"] == "1"


def test_stock_insuficiente_responde_409_con_el_faltante(client, monkeypatch):
    async def fake_aplicar(pool, ajuste_id):
        raise repository.StockInsuficienteError(
            producto_id="p", nombre="Cemento", almacen_id="a", solicitado=Decimal("5"), disponible=Decimal("2")
        )

    monkeypatch.setattr(ajustes_repository, "aplicar_ajuste", fake_aplicar)

    response = client.post(f"/ajustes/{uuid.uuid4()}/aplicar")

    assert response.status_code == 409
    assert response.json()["faltante"] == {
        "producto_id": "p", "nombre": "Cemento", "almacen_id": "a", "lote_id": None,
        "solicitado": "5", "disponible": "2",
    }


def test_operacion_invalida_responde_409(client, monkeypatch):
    async def fake_transferir(pool, **kwargs):
        raise repository.OperacionInvalidaError("El almacén 'X' está inactivo")

    monkeypatch.setattr(almacenes_repository, "crear_transferencia", fake_transferir)

    response = client.post(
        "/transferencias",
        json={"almacen_origen_id": str(uuid.uuid4()), "almacen_destino_id": str(uuid.uuid4()),
              "items": [{"producto_id": str(uuid.uuid4()), "cantidad": "1"}]},
    )

    assert response.status_code == 409
    assert "inactivo" in response.json()["detail"]


def test_almacen_no_encontrado_responde_404(client, monkeypatch):
    async def fake_obtener(pool, almacen_id):
        raise repository.AlmacenNoEncontradoError(str(almacen_id))

    monkeypatch.setattr(almacenes_repository, "obtener_almacen", fake_obtener)

    assert client.get(f"/almacenes/{uuid.uuid4()}").status_code == 404


def test_crear_ajuste_con_aplicar(client, monkeypatch):
    recibidos = []
    ajuste = {
        "id": str(uuid.uuid4()), "numero": "AJ-0001", "fecha": "2026-10-23", "almacen_id": str(uuid.uuid4()),
        "almacen_nombre": "Principal", "motivo": "merma", "notas": None, "estado": "aplicado",
        "creado_en": "2026-10-23T00:00:00Z", "actualizado_en": "2026-10-23T00:00:00Z",
        "aplicado_en": "2026-10-23T00:00:00Z", "anulado_en": None, "items": [],
    }

    async def fake_crear(pool, **kwargs):
        recibidos.append(kwargs)
        return ajuste

    monkeypatch.setattr(ajustes_repository, "crear_ajuste", fake_crear)

    response = client.post(
        "/ajustes?aplicar=true",
        json={"motivo": "merma", "items": [{"producto_id": str(uuid.uuid4()), "tipo": "salida", "cantidad": "2"}]},
    )

    assert response.status_code == 201
    assert recibidos[0]["aplicar"] is True and recibidos[0]["almacen_id"] is None
    assert client.post("/ajustes", json={"motivo": "robo", "items": []}).status_code == 422


def test_kardex_csv(client, monkeypatch):
    async def fake_kardex(pool, **kwargs):
        resultado = kardex.calcular_kardex([])
        resultado.update(
            producto_id=uuid.uuid4(), producto_sku="CEM-425", producto_nombre="Cemento", unidad_medida="saco",
            stock_minimo=Decimal("0"), almacen_id=None, almacen_nombre=None, desde=None, hasta=None,
        )
        return resultado

    monkeypatch.setattr(kardex, "obtener_kardex", fake_kardex)
    pid = uuid.uuid4()

    json_resp = client.get(f"/kardex?producto_id={pid}")
    csv_resp = client.get(f"/kardex?producto_id={pid}&formato=csv&metodo=peps")

    assert json_resp.status_code == 200 and json_resp.json()["saldo_final"]["cantidad"] == "0"
    assert csv_resp.status_code == 200
    assert csv_resp.headers["content-type"].startswith("text/csv")
    assert 'filename="kardex_CEM-425_peps.csv"' in csv_resp.headers["content-disposition"]
    assert csv_resp.text.lstrip("﻿").startswith("Fecha,Documento")


def test_alertas_lotes_usa_los_dias_configurados(client, monkeypatch):
    recibidos = []

    async def fake_alertas(pool, *, dias):
        recibidos.append(dias)
        return {"dias": dias, "por_vencer": [], "vencidos": []}

    monkeypatch.setattr(lotes_repository, "alertas_lotes", fake_alertas)

    client.get("/lotes/alertas")
    client.get("/lotes/alertas?dias=90")

    assert recibidos == [main.settings.dias_alerta_vencimiento, 90]


def test_reverso_por_factura(client, monkeypatch):
    recibidos = []

    async def fake_reverso(pool, factura_id, *, documento=None):
        recibidos.append((factura_id, documento))
        return []

    monkeypatch.setattr(repository, "registrar_reverso_por_factura", fake_reverso)
    response = client.post(
        "/movimientos/reverso-por-factura", json={"factura_id": "factura-1", "documento": "E310000000001"}
    )
    assert response.status_code == 200 and response.json() == {"ok": True, "movimientos": []}
    assert recibidos == [("factura-1", "E310000000001")]
    assert client.post("/movimientos/reverso-por-factura", json={}).status_code == 422
