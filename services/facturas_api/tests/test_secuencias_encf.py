"""Partes puras de la asignación del e-NCF (app/secuencias_encf.py) y la
cabecera de llamada interna hacia inventario-api. La asignación con la
secuencia bloqueada, la concurrencia y el índice único se prueban contra un
Postgres real en services/erp_api/tests/test_seguridad_postgres.py."""
from datetime import date

import httpx
import pytest
import respx

from services.facturas_api.app import inventario_client, secuencias_encf
from services.facturas_api.app.config import get_settings


def test_encf_de_y_normalizar():
    assert secuencias_encf.encf_de(31, 131) == "E310000000131"
    assert secuencias_encf.normalizar_encf(" e310000000001 ") == "E310000000001"
    assert secuencias_encf.normalizar_encf("") is None
    assert secuencias_encf.normalizar_encf(None) is None


def test_con_encf_respeta_el_orden_del_xsd():
    datos = {"Encabezado": {"Version": "1.0", "IdDoc": {"TipoeCF": "31", "TipoPago": "1"}}}
    nuevo = secuencias_encf._con_encf(datos, "E310000000005")
    assert list(nuevo["Encabezado"]["IdDoc"]) == ["TipoeCF", "eNCF", "TipoPago"]
    assert "eNCF" not in datos["Encabezado"]["IdDoc"]  # no modifica el original
    con = {"Encabezado": {"IdDoc": {"TipoeCF": "31", "eNCF": "E310000000001", "FechaVencimientoSecuencia": "31-12-2027"}}}
    assert secuencias_encf._con_encf(con, "E310000000009")["Encabezado"]["IdDoc"] == {
        "TipoeCF": "31", "eNCF": "E310000000009", "FechaVencimientoSecuencia": "31-12-2027",
    }


SECUENCIA = {"activa": True, "fecha_vencimiento": date(2027, 12, 31), "hasta": 10}


@pytest.mark.parametrize(
    ("cambios", "siguiente", "mensaje"),
    [
        ({"activa": False}, 1, "desactivada"),
        ({"fecha_vencimiento": date(2026, 10, 23)}, 1, "venció el 23/10/2026"),
        ({}, 11, "agotada \\(autorizada hasta E310000000010\\)"),
    ],
)
def test_validar_secuencia(cambios, siguiente, mensaje):
    with pytest.raises(secuencias_encf.EncfNoDisponibleError, match=mensaje):
        secuencias_encf.validar_secuencia(31, {**SECUENCIA, **cambios}, siguiente, date(2026, 10, 24))


def test_validar_secuencia_vigente():
    secuencias_encf.validar_secuencia(31, SECUENCIA, 10, date(2026, 10, 24))
    # Vence hoy: todavía vale (la DGII la acepta hasta ese día).
    secuencias_encf.validar_secuencia(31, {**SECUENCIA, "fecha_vencimiento": date(2026, 10, 24)}, 1, date(2026, 10, 24))
    secuencias_encf.validar_secuencia(32, {**SECUENCIA, "fecha_vencimiento": None}, 1, date(2026, 10, 24))


async def test_inventario_client_manda_la_cabecera_interna(monkeypatch):
    monkeypatch.setattr(get_settings(), "kontara_token_interno", "token-interno-de-prueba-con-32-caracteres!")
    items = [{"producto_id": "p1", "cantidad": "1"}]
    with respx.mock:
        validar = respx.post("http://inv:8000/movimientos/validar-disponibilidad").mock(
            return_value=httpx.Response(200, json={"disponible": True})
        )
        salida = respx.post("http://inv:8000/movimientos/salida-por-factura").mock(return_value=httpx.Response(201))
        await inventario_client.validar_disponibilidad("http://inv:8000", items)
        await inventario_client.registrar_salida_por_factura("http://inv:8000", "f1", items)
    for ruta in (validar, salida):
        assert ruta.calls[0].request.headers["x-kontara-interno"] == "token-interno-de-prueba-con-32-caracteres!"


async def test_inventario_client_sin_token_no_manda_cabecera(monkeypatch):
    monkeypatch.setattr(get_settings(), "kontara_token_interno", "")
    with respx.mock:
        ruta = respx.post("http://inv:8000/movimientos/validar-disponibilidad").mock(
            return_value=httpx.Response(200, json={"disponible": True})
        )
        await inventario_client.validar_disponibilidad("http://inv:8000", [{"producto_id": "p1", "cantidad": "1"}])
    assert "x-kontara-interno" not in ruta.calls[0].request.headers
