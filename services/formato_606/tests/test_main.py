import uuid
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from services.formato_606.app import main
from services.formato_606.app import repository


@pytest.fixture
def client(monkeypatch):
    async def fake_crear_pool(database_url):
        return object()

    async def fake_cerrar_pool(pool):
        return None

    monkeypatch.setattr(main, "crear_pool", fake_crear_pool)
    monkeypatch.setattr(main, "cerrar_pool", fake_cerrar_pool)
    with TestClient(main.app) as c:
        yield c


def _registro(**overrides) -> dict:
    base = {
        "id": str(uuid.uuid4()),
        "periodo": "202601",
        "origen": "manual",
        "referencia_tipo": None,
        "referencia_id": None,
        "rnc_cedula": "131234567",
        "tipo_id": 1,
        "tipo_bienes_servicios": 2,
        "ncf": "E310000000001",
        "ncf_modificado": None,
        "fecha_comprobante": "20260115",
        "fecha_pago": None,
        "monto_servicios": Decimal("400.00"),
        "monto_bienes": Decimal("600.00"),
        "monto_facturado": Decimal("1000.00"),
        "itbis_facturado": Decimal("180.00"),
        "itbis_retenido": Decimal("0"),
        "itbis_proporcionalidad": Decimal("0"),
        "itbis_costo": Decimal("0"),
        "itbis_adelantar": Decimal("180.00"),
        "itbis_percibido": Decimal("0"),
        "tipo_retencion_isr": None,
        "monto_retencion_renta": Decimal("0"),
        "isr_percibido": Decimal("0"),
        "impuesto_selectivo_consumo": Decimal("0"),
        "otros_impuestos": Decimal("0"),
        "monto_propina_legal": Decimal("0"),
        "forma_pago": 2,
        "creado_en": "2026-01-15T00:00:00Z",
        "actualizado_en": "2026-01-15T00:00:00Z",
    }
    base.update(overrides)
    return base


def test_health(client):
    assert client.get("/health").json()["status"] == "ok"


def test_listar_registros(client, monkeypatch):
    async def fake_listar(pool, periodo):
        return [_registro()]

    monkeypatch.setattr(repository, "listar_por_periodo", fake_listar)

    response = client.get("/registros", params={"periodo": "202601"})

    assert response.status_code == 200
    assert len(response.json()) == 1


def test_obtener_registro_404(client, monkeypatch):
    async def fake_obtener(pool, rid):
        raise repository.RegistroNoEncontradoError(str(rid))

    monkeypatch.setattr(repository, "obtener", fake_obtener)

    response = client.get(f"/registros/{uuid.uuid4()}")

    assert response.status_code == 404


def test_crear_registro_manual(client, monkeypatch):
    creado = _registro()

    async def fake_crear(pool, datos):
        return creado

    monkeypatch.setattr(repository, "crear_manual", fake_crear)

    response = client.post(
        "/registros",
        json={
            "rnc_cedula": "131234567",
            "ncf": "E310000000001",
            "fecha_comprobante": "20260115",
        },
    )

    assert response.status_code == 201


def test_actualizar_registro(client, monkeypatch):
    actualizado = _registro(forma_pago=1)

    async def fake_actualizar(pool, rid, campos):
        return actualizado

    monkeypatch.setattr(repository, "actualizar_clasificacion", fake_actualizar)

    response = client.put(f"/registros/{uuid.uuid4()}", json={"forma_pago": 1})

    assert response.status_code == 200
    assert response.json()["forma_pago"] == 1


def test_eliminar_registro_no_editable_409(client, monkeypatch):
    async def fake_eliminar(pool, rid):
        raise repository.RegistroNoEditableError(str(rid))

    monkeypatch.setattr(repository, "eliminar_manual", fake_eliminar)

    response = client.delete(f"/registros/{uuid.uuid4()}")

    assert response.status_code == 409


def test_validar_periodo_invalido_422(client):
    response = client.post("/periodos/2026/validar")
    assert response.status_code == 422


def test_validar_periodo_sin_errores(client, monkeypatch):
    async def fake_listar(pool, periodo):
        return [_registro()]

    monkeypatch.setattr(repository, "listar_por_periodo", fake_listar)

    response = client.post("/periodos/202601/validar")

    assert response.status_code == 200
    cuerpo = response.json()
    assert cuerpo["valido"] is True
    assert cuerpo["total_registros"] == 1
    assert cuerpo["errores"] == []


def test_validar_periodo_con_errores(client, monkeypatch):
    async def fake_listar(pool, periodo):
        return [_registro(forma_pago=None)]

    monkeypatch.setattr(repository, "listar_por_periodo", fake_listar)

    response = client.post("/periodos/202601/validar")

    assert response.status_code == 200
    cuerpo = response.json()
    assert cuerpo["valido"] is False
    assert len(cuerpo["errores"]) == 1


def test_generar_archivo_bloquea_si_hay_errores(client, monkeypatch):
    async def fake_listar(pool, periodo):
        return [_registro(forma_pago=None)]

    monkeypatch.setattr(repository, "listar_por_periodo", fake_listar)

    response = client.get("/periodos/202601/generar")

    assert response.status_code == 409


def test_generar_archivo_sin_registros_404(client, monkeypatch):
    async def fake_listar(pool, periodo):
        return []

    monkeypatch.setattr(repository, "listar_por_periodo", fake_listar)

    response = client.get("/periodos/202601/generar")

    assert response.status_code == 404


def test_generar_archivo_ok(client, monkeypatch):
    async def fake_listar(pool, periodo):
        return [
            {
                **_registro(),
                "monto_servicios": Decimal("400.00"),
                "monto_bienes": Decimal("600.00"),
                "itbis_facturado": Decimal("180.00"),
                "itbis_retenido": Decimal("0"),
                "itbis_proporcionalidad": Decimal("0"),
                "itbis_costo": Decimal("0"),
                "itbis_percibido": Decimal("0"),
                "monto_retencion_renta": Decimal("0"),
                "isr_percibido": Decimal("0"),
                "impuesto_selectivo_consumo": Decimal("0"),
                "otros_impuestos": Decimal("0"),
                "monto_propina_legal": Decimal("0"),
            }
        ]

    monkeypatch.setattr(repository, "listar_por_periodo", fake_listar)
    monkeypatch.setattr(main.settings, "emisor_rnc", "131234567")

    response = client.get("/periodos/202601/generar")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "DGII_F_606_131234567_202601.TXT" in response.headers["content-disposition"]
    contenido = response.content.decode("utf-8")
    assert contenido.startswith("606|131234567|202601|1")
