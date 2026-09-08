import uuid

import pytest
from fastapi.testclient import TestClient

from services.inventario_api.app import main
from services.inventario_api.app import repository


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

    async def fake_salida(pool, factura_id, items):
        return [movimiento]

    monkeypatch.setattr(repository, "registrar_salida_por_factura", fake_salida)

    response = client.post(
        "/movimientos/salida-por-factura",
        json={"factura_id": "factura-1", "items": [{"producto_id": str(producto_id), "cantidad": "2"}]},
    )
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert len(response.json()["movimientos"]) == 1
