"""Rutas de la Fase 6 · Operación (tareas.py, sincronizacion.py y
reportes.py) con los repositorios mockeados. Las consultas se verifican
contra un Postgres real en test_operacion_postgres.py."""
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from services.erp_api.app import main, reportes_repository, sincronizacion_repository, tareas_repository
from services.erp_api.app.config import get_settings
from services.erp_api.app.repository import DatosInvalidosError
from services.erp_api.app.sincronizacion_repository import (
    ConflictoSincronizacionError,
    FacturaPapelNoEncontradaError,
    SincronizacionNoEncontradaError,
)
from services.erp_api.app.tareas_repository import TareaNoEncontradaError

AHORA = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)
D = Decimal


@pytest.fixture
def client(monkeypatch):
    async def fake_crear_pool(database_url):
        return object()

    async def fake_cerrar_pool(pool):
        return None

    async def fake_aplicar_migraciones(pool):
        return None

    monkeypatch.setattr(main, "crear_pool", fake_crear_pool)
    monkeypatch.setattr(main, "cerrar_pool", fake_cerrar_pool)
    monkeypatch.setattr(main, "aplicar_migraciones", fake_aplicar_migraciones)
    monkeypatch.setattr(get_settings(), "emisor_rnc", "1-31-89754-2")
    with TestClient(main.app) as c:
        yield c


def _capturar(monkeypatch, modulo, nombre, resultado=None, error=None):
    llamadas = []

    async def fake(*args, **kwargs):
        llamadas.append((args[1:], kwargs))
        if error is not None:
            raise error
        return resultado

    monkeypatch.setattr(modulo, nombre, fake)
    return llamadas


def _tarea(**cambios):
    datos = {
        "id": "declaracion:it1:202609", "origen": "automatica", "tipo": "declaracion", "categoria": "dgii",
        "titulo": "Declarar y pagar el IT-1 de septiembre 2026", "descripcion": None, "prioridad": "alta",
        "vence": date(2026, 10, 20), "vencida": True, "estado": "pendiente", "completada_en": None,
        "pospuesta_hasta": None, "enlace": "/impuestos/precalculo/it1?periodo=202609", "monto": None,
        "creado_en": None,
    }
    datos.update(cambios)
    return datos


# --- Mis tareas ---------------------------------------------------------

def test_listar_tareas_pasa_filtros_y_el_emisor(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "emisor_100_electronico", False)
    llamadas = _capturar(monkeypatch, tareas_repository, "listar_tareas", [_tarea(monto=D("10"))])
    r = client.get("/tareas", params={"estado": "pendiente", "categoria": "dgii"})
    assert r.status_code == 200
    assert r.json()[0]["vence"] == "2026-10-20" and r.json()[0]["monto"] == "10.00"
    assert llamadas[0][1] == {"estado": "pendiente", "categoria": "dgii", "emisor_100_electronico": False}
    assert client.get("/tareas", params={"estado": "otra"}).status_code == 422


def test_resumen_de_tareas(client, monkeypatch):
    _capturar(monkeypatch, tareas_repository, "resumen_tareas", {
        "pendientes": 3, "vencidas": 1, "vencen_hoy": 0, "alta": 2, "por_categoria": {"dgii": 2, "ecf": 1},
        "proximas": [_tarea()],
    })
    r = client.get("/tareas/resumen")
    assert r.status_code == 200 and r.json()["pendientes"] == 3 and r.json()["proximas"][0]["id"].startswith("declaracion")


def test_crear_editar_y_eliminar_tarea_manual(client, monkeypatch):
    tid = str(uuid.uuid4())
    manual = _tarea(id=tid, origen="manual", tipo="manual", categoria="gestion", enlace=None, creado_en=AHORA)
    crear = _capturar(monkeypatch, tareas_repository, "crear_tarea", manual)
    r = client.post("/tareas", json={"titulo": "  Llamar a La Cosecha ", "vence": "2026-10-30"})
    assert r.status_code == 201
    assert crear[0][1] == {"titulo": "Llamar a La Cosecha", "descripcion": None, "vence": date(2026, 10, 30), "prioridad": "media"}
    assert client.post("/tareas", json={"titulo": ""}).status_code == 422
    assert client.post("/tareas", json={"titulo": "x", "prioridad": "urgente"}).status_code == 422

    editar = _capturar(monkeypatch, tareas_repository, "actualizar_tarea", manual)
    assert client.put(f"/tareas/{tid}", json={"titulo": "Otra", "prioridad": "baja"}).status_code == 200
    assert editar[0][0] == (tid,)
    _capturar(monkeypatch, tareas_repository, "actualizar_tarea", error=TareaNoEncontradaError(tid))
    assert client.put(f"/tareas/{tid}", json={"titulo": "Otra"}).status_code == 404

    _capturar(monkeypatch, tareas_repository, "eliminar_tarea")
    assert client.delete(f"/tareas/{tid}").status_code == 204
    _capturar(monkeypatch, tareas_repository, "eliminar_tarea", error=DatosInvalidosError("automática"))
    assert client.delete("/tareas/stock_bajo:x").status_code == 422


def test_completar_reabrir_y_posponer(client, monkeypatch):
    completar = _capturar(monkeypatch, tareas_repository, "completar_tarea", _tarea(estado="completada", completada_en=AHORA))
    r = client.post("/tareas/declaracion:it1:202609/completar")
    assert r.status_code == 200 and r.json()["estado"] == "completada"
    assert completar[0][0] == ("declaracion:it1:202609",)

    _capturar(monkeypatch, tareas_repository, "reabrir_tarea", _tarea())
    assert client.post("/tareas/declaracion:it1:202609/reabrir").json()["estado"] == "pendiente"

    posponer = _capturar(monkeypatch, tareas_repository, "posponer_tarea",
                         _tarea(estado="pospuesta", pospuesta_hasta=date(2026, 10, 30)))
    r = client.post("/tareas/declaracion:it1:202609/posponer", json={"hasta": "2026-10-30"})
    assert r.status_code == 200 and r.json()["pospuesta_hasta"] == "2026-10-30"
    assert posponer[0][0] == ("declaracion:it1:202609", date(2026, 10, 30))
    assert client.post("/tareas/x/posponer", json={}).status_code == 422

    _capturar(monkeypatch, tareas_repository, "completar_tarea", error=TareaNoEncontradaError("x"))
    r = client.post("/tareas/stock_bajo:x/completar")
    assert r.status_code == 404 and "ya se resolvió" in r.json()["detail"]
    _capturar(monkeypatch, tareas_repository, "posponer_tarea", error=DatosInvalidosError("anterior a hoy"))
    assert client.post("/tareas/x/posponer", json={"hasta": "2020-01-01"}).status_code == 422


# --- Centro de sincronización ---------------------------------------------------------

def _contingencia(**cambios):
    datos = {
        "activa": False, "desde": None, "errores_consecutivos": 0, "ultimo_intento": AHORA, "ultimo_error": None,
        "proximo_intento": None, "pendientes": 1, "monto_pendiente": D("2950"), "facturas_papel": 0, "lotes": [],
    }
    datos.update(cambios)
    return datos


def test_resumen_de_sincronizacion(client, monkeypatch):
    _capturar(monkeypatch, sincronizacion_repository, "resumen", {
        "estado": "error", "contingencia": _contingencia(activa=True, desde=AHORA, errores_consecutivos=3),
        "canales": [{
            "canal": "dgii_envio", "titulo": "Envío", "descripcion": "x", "pendientes": 0, "errores": 1,
            "estado": "error", "ultima_actividad": AHORA, "detalle": None,
        }],
        "pendientes_total": 0, "errores_total": 1, "en_proceso_dgii": 0, "monto_en_proceso": D("0"),
        "en_proceso_desde": None, "ultimo_envio": AHORA, "ultima_consulta_resultado": None,
        "eventos_7d": 3, "errores_7d": 3, "rechazos_7d": 0,
    })
    r = client.get("/sincronizacion/resumen")
    assert r.status_code == 200
    assert r.json()["contingencia"]["activa"] and r.json()["contingencia"]["monto_pendiente"] == "2950.00"
    assert r.json()["contingencia"]["lotes"] == []


def test_pendientes_historial_y_envios_por_dia(client, monkeypatch):
    item = {
        "id": "dgii_envio:1", "canal": "dgii_envio", "referencia_id": str(uuid.uuid4()), "documento": "E310000000005",
        "tipo_ecf": 31, "contraparte": "X", "fecha": AHORA, "monto": D("2950"), "estado": "error",
        "detalle": "timeout", "accion": "reenviar", "decision": None, "motivo": None, "automatico": False,
        "enlace": "/facturas/1",
    }
    pendientes = _capturar(monkeypatch, sincronizacion_repository, "listar_pendientes", [item])
    assert client.get("/sincronizacion/pendientes", params={"canal": "dgii_envio"}).json()[0]["accion"] == "reenviar"
    assert pendientes[0][1] == {"canal": "dgii_envio"}
    assert client.get("/sincronizacion/pendientes", params={"canal": "otro"}).status_code == 422

    historial = _capturar(monkeypatch, sincronizacion_repository, "historial", [{
        "id": 7, "creado_en": AHORA, "modulo": "recepcion", "modulo_label": "Envío de e-CF", "ambiente": "test",
        "encf": "E310000000005", "track_id": None, "estado": None, "codigo": None, "error": "timeout",
        "resultado": "error",
    }])
    r = client.get("/sincronizacion/historial", params={
        "modulo": "recepcion", "resultado": "error", "q": "E31", "desde": "2026-10-01", "hasta": "2026-10-24",
        "limit": 50, "offset": 10,
    })
    assert r.status_code == 200 and r.json()[0]["resultado"] == "error"
    assert historial[0][1] == {
        "modulo": "recepcion", "resultado": "error", "q": "E31", "desde": date(2026, 10, 1),
        "hasta": date(2026, 10, 24), "limite": 50, "offset": 10,
    }
    assert client.get("/sincronizacion/historial", params={"limit": 0}).status_code == 422
    _capturar(monkeypatch, sincronizacion_repository, "historial", error=DatosInvalidosError("fechas"))
    assert client.get("/sincronizacion/historial").status_code == 422

    dias = _capturar(monkeypatch, sincronizacion_repository, "envios_por_dia", [{
        "fecha": date(2026, 10, 23), "documentos": 2, "aceptados": 1, "rechazados": 0, "errores": 1,
        "origen": "con_fallas", "resultado": "con_errores",
    }])
    assert client.get("/sincronizacion/envios-por-dia", params={"dias": 3}).json()[0]["fecha"] == "2026-10-23"
    assert dias[0][1] == {"dias": 3}


def test_reintentar_entrega(client, monkeypatch):
    eid = uuid.uuid4()
    llamadas = _capturar(monkeypatch, sincronizacion_repository, "reintentar_entrega", {
        "id": str(eid), "encf": "E310000009004", "enviado_al_emisor": True, "error_envio_emisor": None,
    })
    r = client.post(f"/sincronizacion/entregas/{eid}/reintentar")
    assert r.status_code == 200 and r.json()["enviado_al_emisor"]
    assert llamadas[0][0] == (eid,) and callable(llamadas[0][1]["entregar"])
    assert client.post("/sincronizacion/entregas/no-uuid/reintentar").status_code == 422
    _capturar(monkeypatch, sincronizacion_repository, "reintentar_entrega", error=SincronizacionNoEncontradaError())
    assert client.post(f"/sincronizacion/entregas/{eid}/reintentar").status_code == 404
    _capturar(monkeypatch, sincronizacion_repository, "reintentar_entrega", error=ConflictoSincronizacionError("ya"))
    assert client.post(f"/sincronizacion/entregas/{eid}/reintentar").status_code == 409


def _papel(**cambios):
    datos = {
        "id": str(uuid.uuid4()), "ncf": "B0100004521", "tipo_comprobante": "01", "fecha": date(2026, 10, 23),
        "cliente_rnc": "131234567", "cliente_nombre": "La Cosecha", "gravado_18": D("23500"), "gravado_16": D("0"),
        "gravado_0": D("0"), "exento": D("0"), "itbis_18": D("4230"), "itbis_16": D("0"), "itbis": D("4230"),
        "total": D("27730"), "forma_pago": 1, "notas": None, "encf_reemplazo": None, "creado_en": AHORA,
    }
    datos.update(cambios)
    return datos


def test_facturas_en_papel(client, monkeypatch):
    listar = _capturar(monkeypatch, sincronizacion_repository, "listar_facturas_papel", [_papel()])
    r = client.get("/sincronizacion/facturas-papel", params={"desde": "2026-10-01", "q": "B01"})
    assert r.status_code == 200 and r.json()[0]["total"] == "27730.00"
    assert listar[0][1] == {"desde": date(2026, 10, 1), "hasta": None, "q": "B01"}

    cuerpo = {"ncf": "B0100004521", "fecha": "2026-10-23", "cliente_rnc": "131234567", "gravado_18": "23500"}
    crear = _capturar(monkeypatch, sincronizacion_repository, "crear_factura_papel", _papel())
    assert client.post("/sincronizacion/facturas-papel", json=cuerpo).status_code == 201
    assert crear[0][1]["gravado_18"] == D("23500") and crear[0][1]["itbis_18"] is None
    assert client.post("/sincronizacion/facturas-papel", json={**cuerpo, "forma_pago": 9}).status_code == 422
    assert client.post("/sincronizacion/facturas-papel", json={**cuerpo, "gravado_18": "-1"}).status_code == 422
    _capturar(monkeypatch, sincronizacion_repository, "crear_factura_papel", error=ConflictoSincronizacionError("ya"))
    assert client.post("/sincronizacion/facturas-papel", json=cuerpo).status_code == 409
    _capturar(monkeypatch, sincronizacion_repository, "crear_factura_papel", error=DatosInvalidosError("RNC"))
    assert client.post("/sincronizacion/facturas-papel", json=cuerpo).status_code == 422

    fid = uuid.uuid4()
    editar = _capturar(monkeypatch, sincronizacion_repository, "actualizar_factura_papel", _papel(encf_reemplazo="E310000000050"))
    assert client.put(f"/sincronizacion/facturas-papel/{fid}", json=cuerpo).json()["encf_reemplazo"] == "E310000000050"
    assert editar[0][0] == (fid,)
    _capturar(monkeypatch, sincronizacion_repository, "actualizar_factura_papel", error=FacturaPapelNoEncontradaError())
    assert client.put(f"/sincronizacion/facturas-papel/{fid}", json=cuerpo).status_code == 404
    _capturar(monkeypatch, sincronizacion_repository, "eliminar_factura_papel")
    assert client.delete(f"/sincronizacion/facturas-papel/{fid}").status_code == 204
    assert client.delete("/sincronizacion/facturas-papel/x").status_code == 422


# --- Reportes ---------------------------------------------------------

def _montos_venta(**cambios):
    datos = dict.fromkeys(
        ("gravado_18", "gravado_16", "gravado_0", "exento", "itbis_18", "itbis_16", "subtotal", "itbis", "total"), D("0")
    )
    datos.update(cambios)
    return datos


def test_reporte_ventas(client, monkeypatch):
    llamadas = _capturar(monkeypatch, reportes_repository, "reporte_ventas", {
        "desde": date(2026, 10, 1), "hasta": date(2026, 10, 31), "agrupar": "producto", "advertencias": [],
        "filas": [{"clave": "cemento", "etiqueta": "Cemento", "detalle": None, "documentos": 2, "cantidad": D("13"),
                   **_montos_venta(subtotal=D("6500"), total=D("7670"))}],
        "totales": {"documentos": 2, **_montos_venta(total=D("7670"))},
    })
    r = client.get("/reportes/ventas", params={
        "desde": "2026-10-01", "hasta": "2026-10-31", "agrupar": "producto", "cliente_rnc": "131234567", "tipo_ecf": 31,
    })
    assert r.status_code == 200 and r.json()["filas"][0]["subtotal"] == "6500.00" and r.json()["filas"][0]["cantidad"] == "13"
    assert llamadas[0][1] == {
        "desde": date(2026, 10, 1), "hasta": date(2026, 10, 31), "agrupar": "producto", "cliente_rnc": "131234567",
        "tipo_ecf": 31,
    }
    assert client.get("/reportes/ventas", params={"desde": "2026-10-01"}).status_code == 422
    assert client.get("/reportes/ventas", params={"desde": "2026-10-01", "hasta": "2026-10-31", "agrupar": "x"}).status_code == 422
    _capturar(monkeypatch, reportes_repository, "reporte_ventas", error=DatosInvalidosError("fechas"))
    assert client.get("/reportes/ventas", params={"desde": "2026-10-31", "hasta": "2026-10-01"}).status_code == 422


def _fila_itbis(periodo):
    return {"periodo": periodo, **dict.fromkeys(
        ("ventas_gravadas", "itbis_ventas", "compras", "itbis_compras", "itbis_costo", "itbis_adelantado",
         "itbis_retenido", "diferencia"), D("1"))}


def test_reporte_itbis_y_compras(client, monkeypatch):
    itbis = _capturar(monkeypatch, reportes_repository, "reporte_itbis", {
        "desde": date(2026, 9, 1), "hasta": date(2026, 10, 31), "advertencias": [],
        "filas": [_fila_itbis("202609"), _fila_itbis("202610")], "totales": _fila_itbis("total"),
    })
    r = client.get("/reportes/itbis", params={"desde": "2026-09-01", "hasta": "2026-10-31"})
    assert r.status_code == 200 and len(r.json()["filas"]) == 2 and itbis[0][1]["desde"] == date(2026, 9, 1)

    fila = {"clave": "131111111", "etiqueta": "Cementos", "detalle": "131111111", "documentos": 2, **dict.fromkeys(
        ("monto_servicios", "monto_bienes", "monto_facturado", "itbis_facturado", "itbis_retenido", "isr_retenido",
         "total"), D("5"))}
    compras = _capturar(monkeypatch, reportes_repository, "reporte_compras", {
        "desde": date(2026, 10, 1), "hasta": date(2026, 10, 31), "agrupar": "categoria", "advertencias": [],
        "filas": [fila], "totales": fila,
    })
    r = client.get("/reportes/compras", params={
        "desde": "2026-10-01", "hasta": "2026-10-31", "agrupar": "categoria", "proveedor_rnc": "131111111",
        "tipo_bienes_servicios": 9,
    })
    assert r.status_code == 200 and r.json()["filas"][0]["total"] == "5.00"
    assert compras[0][1]["tipo_bienes_servicios"] == 9 and compras[0][1]["proveedor_rnc"] == "131111111"
    assert client.get("/reportes/compras", params={
        "desde": "2026-10-01", "hasta": "2026-10-31", "tipo_bienes_servicios": 12}).status_code == 422


def test_reportes_de_antiguedad(client, monkeypatch):
    fila = {"rnc": "131234567", "nombre": "La Cosecha", "documentos": 1, **dict.fromkeys(
        ("por_vencer", "d1_30", "d31_60", "d61_90", "mas_90", "total"), D("10"))}
    llamadas = _capturar(monkeypatch, reportes_repository, "reporte_antiguedad", {
        "tipo": "cxc", "fecha_corte": date(2026, 10, 24), "filas": [fila], "totales": fila,
        "documentos": [{"id": "x", "documento": "E31", "rnc": None, "nombre": "X", "fecha_emision": None,
                        "vence": date(2026, 9, 30), "dias_vencida": 24, "tramo": "d1_30", "pendiente": D("10")}],
    })
    assert client.get("/reportes/antiguedad-cxc", params={"cliente_rnc": "131234567"}).json()["filas"][0]["d1_30"] == "10.00"
    assert llamadas[0][0] == ("cxc",) and llamadas[0][1] == {"rnc": "131234567"}
    client.get("/reportes/antiguedad-cxp", params={"proveedor_rnc": "131111111"})
    assert llamadas[1][0] == ("cxp",) and llamadas[1][1] == {"rnc": "131111111"}


def test_reporte_cobros_pagos(client, monkeypatch):
    fila = {"periodo": "202610", "cobros": D("10500"), "cantidad_cobros": 2, "pagos": D("9000"), "cantidad_pagos": 1,
            "neto": D("1500")}
    _capturar(monkeypatch, reportes_repository, "reporte_cobros_pagos", {
        "desde": date(2026, 10, 1), "hasta": date(2026, 10, 31), "advertencias": [], "filas": [fila],
        "totales": fila | {"periodo": "total"},
        "por_metodo": [{"metodo": "transferencia", "cobros": D("9500"), "pagos": D("9000")}],
        "movimientos": [{"id": "1", "tipo": "cobro", "fecha": date(2026, 10, 10), "documento": "E31",
                         "contraparte": "X", "metodo": "transferencia", "referencia": None, "monto": D("9500")}],
    })
    r = client.get("/reportes/cobros-pagos", params={"desde": "2026-10-01", "hasta": "2026-10-31"})
    assert r.status_code == 200 and r.json()["totales"]["neto"] == "1500.00"


def test_reportes_de_inventario(client, monkeypatch):
    pid = str(uuid.uuid4())
    valorizado = _capturar(monkeypatch, reportes_repository, "reporte_inventario_valorizado", {
        "fecha_corte": date(2026, 10, 24), "almacen_id": None, "advertencias": [],
        "filas": [{"producto_id": pid, "sku": "CEM", "nombre": "Cemento", "categoria": None, "unidad_medida": "UND",
                   "existencia": D("50.0000"), "costo_promedio": D("350.0000"), "valor": D("17500"),
                   "precio_venta": D("500"), "valor_venta": D("25000"), "stock_minimo": D("60"), "bajo_minimo": True}],
        "totales": {"productos": 1, "existencia": D("50"), "valor": D("17500"), "valor_venta": D("25000")},
    })
    almacen = uuid.uuid4()
    r = client.get("/reportes/inventario-valorizado", params={
        "almacen_id": str(almacen), "categoria": "Construcción", "incluir_sin_existencia": "true"})
    assert r.status_code == 200 and r.json()["filas"][0]["valor"] == "17500.00"
    assert valorizado[0][1] == {"almacen_id": almacen, "categoria": "Construcción", "incluir_sin_existencia": True}
    assert client.get("/reportes/inventario-valorizado", params={"almacen_id": "x"}).status_code == 422

    fila = {"producto_id": pid, "sku": "CEM", "nombre": "Cemento", "categoria": None, "existencia_inicial": D("40"),
            "entradas": D("30"), "salidas": D("20"), "existencia_final": D("50"), "costo_salidas": D("7000"),
            "inventario_promedio": D("45"), "rotacion": D("0.44"), "dias_inventario": D("69.8")}
    rotacion = _capturar(monkeypatch, reportes_repository, "reporte_rotacion", {
        "desde": date(2026, 10, 1), "hasta": date(2026, 10, 31), "advertencias": [], "filas": [fila], "totales": fila,
    })
    r = client.get("/reportes/rotacion-inventario", params={"desde": "2026-10-01", "hasta": "2026-10-31", "categoria": "C"})
    assert r.json()["filas"][0]["rotacion"] == "0.44" and rotacion[0][1]["categoria"] == "C"

    vendidos = _capturar(monkeypatch, reportes_repository, "reporte_mas_vendidos", {
        "desde": date(2026, 10, 1), "hasta": date(2026, 10, 31), "advertencias": [],
        "filas": [{"posicion": 1, "producto_id": pid, "sku": "CEM", "nombre": "Cemento", "categoria": None,
                   "cantidad": D("14"), "facturas": 2, "costo": D("4900"), "participacion": D("100")}],
        "total_cantidad": D("14"), "total_costo": D("4900"),
    })
    r = client.get("/reportes/mas-vendidos", params={"desde": "2026-10-01", "hasta": "2026-10-31", "limite": 5})
    assert r.json()["filas"][0]["costo"] == "4900.00" and vendidos[0][1]["limite"] == 5
    assert client.get("/reportes/mas-vendidos", params={"desde": "2026-10-01", "hasta": "2026-10-31", "limite": 0}).status_code == 422


def test_reporte_retenciones(client, monkeypatch):
    fila = {"clave": "40212345678", "etiqueta": "Juan Pérez", "detalle": None, "documentos": 1,
            "monto_base": D("10000"), "isr_retenido": D("1000"), "itbis_retenido": D("1800"), "total": D("2800")}
    _capturar(monkeypatch, reportes_repository, "reporte_retenciones", {
        "desde": date(2026, 10, 1), "hasta": date(2026, 10, 31), "advertencias": [], "efectuadas": [fila],
        "por_tipo": [fila], "totales_efectuadas": fila, "sufridas": [], "totales_sufridas": fila,
    })
    r = client.get("/reportes/retenciones", params={"desde": "2026-10-01", "hasta": "2026-10-31"})
    assert r.status_code == 200 and r.json()["totales_efectuadas"]["total"] == "2800.00"


# --- Funciones puras ---------------------------------------------------------

def test_tramos_de_antiguedad():
    assert [reportes_repository._tramo(d) for d in (0, 1, 30, 31, 60, 61, 90, 91)] == [
        "por_vencer", "d1_30", "d1_30", "d31_60", "d31_60", "d61_90", "d61_90", "mas_90"]


def test_periodos_y_motivo_dgii():
    assert reportes_repository._periodos(date(2025, 11, 30), date(2026, 2, 1)) == ["202511", "202512", "202601", "202602"]
    assert tareas_repository._motivo_dgii('{"mensajes": [{"codigo": "2", "valor": "RNC inválido"}]}', None) == "RNC inválido"
    assert tareas_repository._motivo_dgii('{"Mensajes": ["Texto"]}', "error") == "Texto"
    assert tareas_repository._motivo_dgii("no-json", " timeout ") == "timeout"
    assert tareas_repository._motivo_dgii(None, None) is None


def test_estado_de_una_tarea():
    hoy = date(2026, 10, 24)
    assert tareas_repository._estado(AHORA, None, hoy) == "completada"
    assert tareas_repository._estado(None, hoy, hoy) == "pospuesta"
    assert tareas_repository._estado(None, date(2026, 10, 23), hoy) == "pendiente"
    assert tareas_repository._estado(None, None, hoy) == "pendiente"
