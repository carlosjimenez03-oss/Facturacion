"""Fase 6 · Operación (Mis tareas, Centro de sincronización y Reportes)
contra un Postgres REAL, con la semilla de operacion_semilla.py y totales
calculados a mano.

Se salta si no hay `OPERACION_TEST_DATABASE_URL` (DSN de una base con permiso
para crear bases, p. ej. `postgresql://postgres@localhost:5640/postgres`):
cada prueba crea una base nueva, carga `db/init/*.sql`, aplica las
migraciones de erp-api y siembra los datos.
"""
import os
import uuid
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import asyncpg
import pytest

from services.erp_api.app import impuestos_repository, reportes_repository as rr
from services.erp_api.app import sincronizacion_repository as sr
from services.erp_api.app import tareas_repository as tr
from services.erp_api.app.migraciones import aplicar_migraciones
from services.erp_api.app.repository import DatosInvalidosError
from services.erp_api.tests import operacion_semilla as s

DSN = os.environ.get("OPERACION_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="sin OPERACION_TEST_DATABASE_URL (Postgres real)")

REPO = Path(__file__).resolve().parents[3]
D = Decimal
OCTUBRE = {"desde": date(2026, 10, 1), "hasta": date(2026, 10, 31)}


@pytest.fixture
async def pool(monkeypatch):
    for modulo in (tr, rr, sr, impuestos_repository):
        monkeypatch.setattr(modulo, "hoy", lambda: s.HOY)
    nombre = f"operacion_{uuid.uuid4().hex[:10]}"
    admin = await asyncpg.connect(DSN)
    await admin.execute(f"CREATE DATABASE {nombre} TEMPLATE template0 ENCODING 'UTF8'")
    await admin.close()
    dsn = DSN.rsplit("/", 1)[0] + f"/{nombre}"
    conn = await asyncpg.connect(dsn)
    for archivo in sorted((REPO / "db" / "init").glob("*.sql")):
        await conn.execute(archivo.read_text(encoding="utf-8"))
    await conn.close()
    p = await asyncpg.create_pool(dsn, min_size=1, max_size=5)
    await aplicar_migraciones(p)
    async with p.acquire() as c:
        await s.sembrar(c)
    yield p
    await p.close()
    admin = await asyncpg.connect(DSN)
    await admin.execute(f"DROP DATABASE IF EXISTS {nombre} WITH (FORCE)")
    await admin.close()


def _claves(tareas):
    return {t["id"] for t in tareas}


# --- Mis tareas ---------------------------------------------------------

async def test_tareas_automaticas_de_todos_los_modulos(pool):
    tareas = await tr.listar_tareas(pool)
    por_tipo = {}
    for t in tareas:
        por_tipo.setdefault(t["tipo"], []).append(t)
    assert {k: len(v) for k, v in por_tipo.items()} == {
        "declaracion": 6,  # 606/IT-1/IR-17 de septiembre (vencidas) y de octubre (por vencer); 607/608 no aplican
        "factura_rechazada": 1,
        "factura_error": 1,
        "dgii_en_proceso": 1,
        "ecf_por_aprobar": 3,
        "cxc_vencida": 2,
        "cxp_vencida": 1,
        "cxp_por_vencer": 1,
        "cotizacion_por_vencer": 1,
        "lote_por_vencer": 1,
        "stock_bajo": 1,
        "inventario_sin_sincronizar": 1,
        "papel_sin_reemplazo": 1,
    }
    declaraciones = {t["id"]: t for t in por_tipo["declaracion"]}
    it1 = declaraciones["declaracion:it1:202609"]
    assert it1["vence"] == date(2026, 10, 20) and it1["vencida"] and it1["prioridad"] == "alta"
    assert it1["enlace"] == "/impuestos/precalculo/it1?periodo=202609"
    assert declaraciones["declaracion:606:202610"]["vence"] == date(2026, 11, 16)  # 15/11 es domingo
    assert not declaraciones["declaracion:606:202610"]["vencida"]
    cxc = {t["id"]: t for t in por_tipo["cxc_vencida"]}
    assert cxc[f"cxc_vencida:{s.F10}"]["prioridad"] == "alta"  # 101 días
    assert cxc[f"cxc_vencida:{s.F3}"]["monto"] == D("20000.00")  # 29,500 − 9,500 cobrado
    assert por_tipo["cxp_vencida"][0]["monto"] == D("5900.00")
    assert por_tipo["cxp_por_vencer"][0]["monto"] == D("22420.00")  # 23,600 − 1,180 de la nota de crédito
    assert por_tipo["lote_por_vencer"][0]["vence"] == date(2026, 11, 10)
    assert por_tipo["stock_bajo"][0]["descripcion"] == "Existencia 50 · mínimo 60"
    rechazada = por_tipo["factura_rechazada"][0]
    assert "RNC del comprador no válido" in rechazada["descripcion"] and rechazada["enlace"] == f"/facturas/{s.F6}"
    # Las vencidas y de prioridad alta primero.
    assert tareas[0]["vencida"] and tareas[0]["prioridad"] == "alta"

    resumen = await tr.resumen_tareas(pool)
    assert resumen["pendientes"] == 21
    assert resumen["vencidas"] == 6  # 3 declaraciones de septiembre, 2 CxC y 1 CxP
    assert resumen["por_categoria"]["dgii"] == 6
    assert len(resumen["proximas"]) == 5


async def test_tareas_aparecen_y_desaparecen_con_su_condicion(pool):
    claves = _claves(await tr.listar_tareas(pool))
    assert f"cxc_vencida:{s.F3}" in claves and f"stock_bajo:{s.P1}" in claves

    # Se cobra el resto de F3, se baja el mínimo del cemento y se presenta el IT-1 de septiembre.
    await pool.execute(
        "INSERT INTO ventas.cobros (factura_id, fecha, monto, metodo) VALUES ($1, '2026-10-24', 20000, 'cheque')", s.F3
    )
    await pool.execute("UPDATE inventario.productos SET stock_minimo = 0 WHERE id = $1", s.P1)
    await pool.execute(
        "INSERT INTO impuestos.declaraciones_presentadas (formulario, periodo, presentada_en) VALUES ('it1', '202609', '2026-10-20')"
    )
    await pool.execute("UPDATE facturacion.facturas SET estado = 'aceptado' WHERE id = $1", s.F7)
    claves = _claves(await tr.listar_tareas(pool))
    assert f"cxc_vencida:{s.F3}" not in claves
    assert f"stock_bajo:{s.P1}" not in claves
    assert "declaracion:it1:202609" not in claves
    assert not any(c.startswith("dgii_en_proceso:") for c in claves)

    # Una salida de inventario más: la tarea agrupada cambia de clave.
    antes = next(c for c in claves if c.startswith("inventario_sin_sincronizar:"))
    await pool.execute(
        "UPDATE facturacion.facturas SET items_inventario = '[{\"cantidad\": \"1\"}]', inventario_sincronizado = false,"
        " inventario_error = 'caído' WHERE id = $1",
        s.F2,
    )
    despues = next(c for c in _claves(await tr.listar_tareas(pool)) if c.startswith("inventario_sin_sincronizar:"))
    assert despues != antes


async def test_completar_posponer_y_olvidar_tareas_automaticas(pool):
    clave = f"stock_bajo:{s.P1}"
    completada = await tr.completar_tarea(pool, clave)
    assert completada["estado"] == "completada" and completada["completada_en"] is not None
    assert clave in _claves(await tr.listar_tareas(pool, estado="completada"))
    assert clave not in _claves(await tr.listar_tareas(pool, estado="pendiente"))

    # Se resuelve la condición: el estado guardado se borra al listar...
    await pool.execute("UPDATE inventario.productos SET stock_actual = 100 WHERE id = $1", s.P1)
    assert clave not in _claves(await tr.listar_tareas(pool))
    assert await pool.fetchval("SELECT count(*) FROM operacion.tareas_automaticas WHERE clave = $1", clave) == 0
    # ...y si vuelve a darse, la tarea reaparece pendiente.
    await pool.execute("UPDATE inventario.productos SET stock_actual = 5 WHERE id = $1", s.P1)
    assert clave in _claves(await tr.listar_tareas(pool, estado="pendiente"))

    pospuesta = await tr.posponer_tarea(pool, "declaracion:606:202609", s.HOY + timedelta(days=3))
    assert pospuesta["estado"] == "pospuesta" and pospuesta["pospuesta_hasta"] == s.HOY + timedelta(days=3)
    assert "declaracion:606:202609" in _claves(await tr.listar_tareas(pool, estado="pospuesta"))
    reabierta = await tr.reabrir_tarea(pool, "declaracion:606:202609")
    assert reabierta["estado"] == "pendiente"
    with pytest.raises(DatosInvalidosError):
        await tr.posponer_tarea(pool, "declaracion:606:202609", s.HOY - timedelta(days=1))
    # Una clave que no corresponde a ninguna condición vigente.
    with pytest.raises(tr.TareaNoEncontradaError):
        await tr.completar_tarea(pool, f"stock_bajo:{s.P2}")

    # Si una fuente no responde (esquema ausente), su estado guardado no se toca.
    await tr.completar_tarea(pool, f"lote_por_vencer:{s.L1}:20261110")
    await pool.execute("ALTER TABLE inventario.lotes RENAME TO lotes_x")
    tareas = await tr.listar_tareas(pool)
    assert not any(t["tipo"] == "lote_por_vencer" for t in tareas)
    assert await pool.fetchval("SELECT count(*) FROM operacion.tareas_automaticas WHERE clave LIKE 'lote_por_vencer:%'") == 1


async def test_tareas_manuales(pool):
    tarea = await tr.crear_tarea(
        pool, titulo="Llamar a La Cosecha", descripcion=None, vence=date(2026, 10, 20), prioridad="alta"
    )
    assert tarea["origen"] == "manual" and tarea["vencida"] and tarea["categoria"] == "gestion"
    editada = await tr.actualizar_tarea(
        pool, tarea["id"], titulo="Llamar a La Cosecha por E310000000002", descripcion="Saldo 20,000",
        vence=date(2026, 10, 30), prioridad="media",
    )
    assert editada["titulo"].endswith("E310000000002") and not editada["vencida"]
    assert (await tr.completar_tarea(pool, tarea["id"]))["estado"] == "completada"
    assert (await tr.reabrir_tarea(pool, tarea["id"]))["estado"] == "pendiente"
    assert (await tr.posponer_tarea(pool, tarea["id"], date(2026, 10, 27)))["estado"] == "pospuesta"
    await tr.eliminar_tarea(pool, tarea["id"])
    with pytest.raises(tr.TareaNoEncontradaError):
        await tr.eliminar_tarea(pool, tarea["id"])
    with pytest.raises(DatosInvalidosError):
        await tr.eliminar_tarea(pool, f"stock_bajo:{s.P1}")


# --- Centro de sincronización ---------------------------------------------------------

async def test_resumen_y_pendientes_de_sincronizacion(pool):
    resumen = await sr.resumen(pool)
    canales = {c["canal"]: c for c in resumen["canales"]}
    assert (canales["dgii_envio"]["errores"], canales["dgii_resultado"]["pendientes"]) == (1, 1)
    assert canales["inventario"]["errores"] == 1
    assert (canales["aprobacion_comercial"]["pendientes"], canales["aprobacion_comercial"]["errores"]) == (0, 2)
    assert canales["entrega_emisor"]["errores"] == 1
    assert resumen["en_proceso_dgii"] == 1 and resumen["monto_en_proceso"] == D("1180.00")
    assert resumen["estado"] == "error" and resumen["errores_total"] == 5
    assert (resumen["eventos_7d"], resumen["errores_7d"], resumen["rechazos_7d"]) == (7, 1, 1)
    c = resumen["contingencia"]
    assert not c["activa"] and c["errores_consecutivos"] == 1
    assert (c["pendientes"], c["monto_pendiente"], c["facturas_papel"]) == (1, D("2950.00"), 1)

    pendientes = await sr.listar_pendientes(pool)
    por_id = {i["id"]: i for i in pendientes}
    assert por_id[f"dgii_envio:{s.F8}"]["accion"] == "reenviar"
    assert por_id[f"dgii_resultado:{s.F7}"]["accion"] == "actualizar_estado"
    assert por_id[f"dgii_resultado:{s.F7}"]["automatico"]
    assert por_id[f"inventario:{s.F7}"]["estado"] == "error"
    atascada = por_id[f"aprobacion_comercial:{s.ECF_ATASCADA}"]
    assert (atascada["accion"], atascada["decision"]) == ("reintentar_aprobacion", "aprobado")
    assert por_id[f"aprobacion_comercial:{s.ECF_FALLIDA}"]["accion"] == "decidir_aprobacion"
    assert por_id[f"entrega_emisor:{s.ECF_ENTREGA}"]["accion"] == "reintentar_entrega"
    assert [i["estado"] for i in pendientes][-1] == "en_curso"  # errores primero
    assert {i["canal"] for i in await sr.listar_pendientes(pool, canal="inventario")} == {"inventario"}


async def test_contingencia_por_fallos_seguidos(pool):
    for minutos in (20, 5):
        await pool.execute(
            "INSERT INTO facturacion.envios_dgii (modulo, ambiente, rnc_emisor, encf, error, creado_en)"
            " VALUES ('recepcion', 'test', '131897542', 'E310000000005', 'Timeout', now() - make_interval(mins => $1))",
            minutos,
        )
    c = (await sr.resumen(pool))["contingencia"]
    assert c["activa"] and c["errores_consecutivos"] == 3 and c["ultimo_error"] == "Timeout"
    desde = await pool.fetchval(
        "SELECT creado_en FROM facturacion.envios_dgii WHERE error = 'Timeout al contactar la DGII'"
    )
    assert c["desde"] == desde
    # Un envío correcto termina la contingencia.
    await pool.execute(
        "INSERT INTO facturacion.envios_dgii (modulo, ambiente, rnc_emisor, encf, track_id)"
        " VALUES ('recepcion', 'test', '131897542', 'E310000000005', 't')"
    )
    assert not (await sr.resumen(pool))["contingencia"]["activa"]


async def test_historial_y_envios_por_dia(pool):
    errores = await sr.historial(pool, resultado="error")
    assert [e["encf"] for e in errores] == ["E310000000005"]
    assert {e["modulo"] for e in await sr.historial(pool, modulo="consulta_resultado")} == {"consulta_resultado"}
    assert len(await sr.historial(pool, q="E3100000000")) == 5
    assert len(await sr.historial(pool, limite=2)) == 2
    dias = await sr.envios_por_dia(pool, dias=7)
    assert sum(d["documentos"] for d in dias) == 4 and sum(d["aceptados"] for d in dias) == 1
    assert sum(d["rechazados"] for d in dias) == 1 and sum(d["errores"] for d in dias) == 1


async def test_reintentar_entrega_al_proveedor(pool):
    async def falla(rnc, encf, xml):
        raise RuntimeError("sin urlAceptacion")

    r = await sr.reintentar_entrega(pool, s.ECF_ENTREGA, entregar=falla)
    assert not r["enviado_al_emisor"] and r["error_envio_emisor"] == "sin urlAceptacion"

    recibido = []

    async def entrega(rnc, encf, xml):
        recibido.append((rnc, encf, xml))

    r = await sr.reintentar_entrega(pool, s.ECF_ENTREGA, entregar=entrega)
    assert r["enviado_al_emisor"] and recibido == [("133333333", "E310000009004", b"<ACECF>firmado</ACECF>")]
    with pytest.raises(sr.ConflictoSincronizacionError, match="ya se entregó"):
        await sr.reintentar_entrega(pool, s.ECF_ENTREGA, entregar=entrega)
    with pytest.raises(sr.ConflictoSincronizacionError):
        await sr.reintentar_entrega(pool, s.ECF_PEND, entregar=entrega)
    assert not any(i["canal"] == "entrega_emisor" for i in await sr.listar_pendientes(pool))


async def test_facturas_en_papel(pool):
    base = dict(fecha=date(2026, 10, 23), cliente_nombre="Ferretería Ochoa", gravado_0=0, exento=0, itbis_16=None,
                forma_pago=1, notas=None, encf_reemplazo=None)
    f = await sr.crear_factura_papel(pool, ncf="b0200000077", cliente_rnc=None, gravado_18=1000, gravado_16=500,
                                     itbis_18=None, **base)
    assert (f["ncf"], f["tipo_comprobante"], f["itbis_18"], f["itbis_16"], f["total"]) == (
        "B0200000077", "02", D("180.00"), D("80.00"), D("1760.00"))
    with pytest.raises(sr.ConflictoSincronizacionError):
        await sr.crear_factura_papel(pool, ncf="B0200000077", cliente_rnc=None, gravado_18=1, gravado_16=0,
                                     itbis_18=None, **base)
    with pytest.raises(DatosInvalidosError, match="crédito fiscal"):
        await sr.crear_factura_papel(pool, ncf="B0100000078", cliente_rnc=None, gravado_18=1, gravado_16=0,
                                     itbis_18=None, **base)
    with pytest.raises(DatosInvalidosError, match="e-NCF"):
        await sr.crear_factura_papel(pool, ncf="E310000000099", cliente_rnc=None, gravado_18=1, gravado_16=0,
                                     itbis_18=None, **base)
    editada = await sr.actualizar_factura_papel(
        pool, uuid.UUID(f["id"]), ncf="B0200000077", cliente_rnc=None, gravado_18=1000, gravado_16=500,
        itbis_18=D("180"), **(base | {"encf_reemplazo": "E320000000050"}),
    )
    assert editada["encf_reemplazo"] == "E320000000050"
    assert [x["ncf"] for x in await sr.listar_facturas_papel(pool, q="B02")] == ["B0200000077"]
    # Con su e-CF de reemplazo ya no genera tarea; la de la semilla sí.
    tipos = [t for t in await tr.listar_tareas(pool) if t["tipo"] == "papel_sin_reemplazo"]
    assert len(tipos) == 1 and "B0100004521" in tipos[0]["titulo"]
    await sr.eliminar_factura_papel(pool, uuid.UUID(f["id"]))
    with pytest.raises(sr.FacturaPapelNoEncontradaError):
        await sr.eliminar_factura_papel(pool, uuid.UUID(f["id"]))


# --- Reportes ---------------------------------------------------------

async def test_ventas_por_mes_cliente_producto_y_tipo(pool):
    mes = await rr.reporte_ventas(pool, agrupar="mes", **OCTUBRE)
    # F1 7,900 + F2 2,360 − F4 590 + F5 11,600 (16 %); F6 rechazada, F7 enviada y F8 con error no cuentan.
    assert len(mes["filas"]) == 1
    t = mes["totales"]
    assert (t["documentos"], t["gravado_18"], t["gravado_16"], t["exento"]) == (4, D("6500"), D("10000"), D("2000"))
    assert (t["itbis_18"], t["itbis_16"], t["subtotal"], t["itbis"], t["total"]) == (
        D("1170"), D("1600"), D("18500"), D("2770"), D("21270"))
    assert "1 e-CF de venta del período siguen 'enviado'" in mes["advertencias"][0]

    # Mismo desglose por tasa que el pre-cálculo del IT-1.
    it1 = await impuestos_repository.precalculo(pool, "it1", "202610")
    casillas = {c["etiqueta"]: c["valor"] for seccion in it1["secciones"] for c in seccion["casillas"]}
    assert any(v == D("1170.00") for v in casillas.values()) and any(v == D("1600.00") for v in casillas.values())

    clientes = {f["etiqueta"]: f["total"] for f in (await rr.reporte_ventas(pool, agrupar="cliente", **OCTUBRE))["filas"]}
    assert clientes == {"Hotel Brisas de Bávaro, SA": D("11600"), "Supermercado La Cosecha, SRL": D("7310"),
                        "Consumidor final": D("2360")}
    productos = {f["etiqueta"]: (f["cantidad"], f["subtotal"], f["itbis"])
                 for f in (await rr.reporte_ventas(pool, agrupar="producto", **OCTUBRE))["filas"]}
    assert productos == {
        "Cemento gris 42.5 kg": (D("13"), D("6500"), D("1170")),
        "Servicio de transporte": (D("1"), D("10000"), D("1600")),
        "Arroz selecto": (D("2"), D("2000"), D("0")),
    }
    tipos = {f["clave"]: f["total"] for f in (await rr.reporte_ventas(pool, agrupar="tipo_ecf", **OCTUBRE))["filas"]}
    assert tipos == {"31": D("19500"), "32": D("2360"), "34": D("-590")}
    filtrado = await rr.reporte_ventas(pool, agrupar="mes", cliente_rnc="1-31-23456-7", **OCTUBRE)
    assert filtrado["totales"]["total"] == D("7310")
    dias = await rr.reporte_ventas(pool, agrupar="dia", desde=date(2026, 9, 1), hasta=date(2026, 10, 31))
    assert [f["clave"] for f in dias["filas"]] == ["2026-09-15", "2026-10-05", "2026-10-10", "2026-10-12", "2026-10-20"]
    with pytest.raises(DatosInvalidosError):
        await rr.reporte_ventas(pool, desde=date(2026, 10, 2), hasta=date(2026, 10, 1))


async def test_itbis_ventas_vs_compras(pool):
    r = await rr.reporte_itbis(pool, desde=date(2026, 9, 10), hasta=date(2026, 10, 5))
    assert (r["desde"], r["hasta"]) == (date(2026, 9, 1), date(2026, 10, 31))
    sep, oct_ = r["filas"]
    assert (sep["itbis_ventas"], sep["itbis_compras"], sep["itbis_costo"], sep["itbis_adelantado"], sep["diferencia"]) == (
        D("4500"), D("900"), D("900"), D("0"), D("4500"))
    # Octubre: compras 20,000 + 10,000 − 1,000 (NC); ITBIS 3,600 + 1,800 − 180; retenido por fecha de pago 1,800.
    assert (oct_["compras"], oct_["itbis_compras"], oct_["itbis_adelantado"], oct_["itbis_retenido"]) == (
        D("29000"), D("5220"), D("5220"), D("1800"))
    assert (oct_["itbis_ventas"], oct_["diferencia"]) == (D("2770"), D("-2450"))
    assert r["totales"]["itbis_ventas"] == D("7270")


async def test_compras_por_proveedor_categoria_y_mes(pool):
    proveedores = {f["etiqueta"]: f for f in (await rr.reporte_compras(pool, agrupar="proveedor", **OCTUBRE))["filas"]}
    cementos = proveedores["Cementos del Atlántico, SA"]
    assert (cementos["documentos"], cementos["monto_bienes"], cementos["itbis_facturado"], cementos["total"]) == (
        2, D("19000"), D("3420"), D("22420"))
    juan = proveedores["Juan Pérez"]
    assert (juan["monto_servicios"], juan["itbis_retenido"], juan["isr_retenido"], juan["total"]) == (
        D("10000"), D("1800"), D("1000"), D("11800"))
    categorias = {f["clave"]: f["total"] for f in (await rr.reporte_compras(pool, agrupar="categoria", **OCTUBRE))["filas"]}
    assert categorias == {"9": D("22420"), "2": D("11800")}
    meses = await rr.reporte_compras(pool, agrupar="mes", desde=date(2026, 9, 1), hasta=date(2026, 10, 31))
    assert [(f["clave"], f["total"]) for f in meses["filas"]] == [("202609", D("5900")), ("202610", D("34220"))]
    assert meses["totales"]["total"] == D("40120")


async def test_antiguedad_de_saldos(pool):
    cxc = await rr.reporte_antiguedad(pool, "cxc")
    t = cxc["totales"]
    # F1 7,900 − 590 (NC) − 1,000 = 6,310 por vencer; F3 20,000 a 24 días; F10 3,540 a 101 días.
    assert (t["por_vencer"], t["d1_30"], t["d31_60"], t["d61_90"], t["mas_90"], t["total"]) == (
        D("6310"), D("20000"), 0, 0, D("3540"), D("29850"))
    assert cxc["filas"][0]["nombre"] == "Supermercado La Cosecha, SRL"
    assert {d["documento"]: d["tramo"] for d in cxc["documentos"]} == {
        "E310000000001": "por_vencer", "E310000000002": "d1_30", "E310000000006": "mas_90"}
    assert (await rr.reporte_antiguedad(pool, "cxc", rnc="130000001"))["totales"]["total"] == D("3540")
    cxp = await rr.reporte_antiguedad(pool, "cxp")
    assert (cxp["totales"]["por_vencer"], cxp["totales"]["d1_30"], cxp["totales"]["total"]) == (
        D("22420"), D("5900"), D("28320"))


async def test_cobros_y_pagos(pool):
    r = await rr.reporte_cobros_pagos(pool, **OCTUBRE)
    t = r["totales"]
    assert (t["cobros"], t["cantidad_cobros"], t["pagos"], t["cantidad_pagos"], t["neto"]) == (
        D("10500"), 2, D("9000"), 1, D("1500"))  # el cobro anulado no cuenta
    assert {m["metodo"]: (m["cobros"], m["pagos"]) for m in r["por_metodo"]} == {
        "transferencia": (D("9500"), D("9000")), "efectivo": (D("1000"), 0)}
    assert [m["tipo"] for m in r["movimientos"]] == ["cobro", "pago", "cobro"]
    assert r["movimientos"][1]["contraparte"] == "Juan Pérez"


async def test_inventario_valorizado_rotacion_y_mas_vendidos(pool):
    v = await rr.reporte_inventario_valorizado(pool)
    assert [(f["sku"], f["valor"]) for f in v["filas"]] == [
        ("VAR-038", D("18000")), ("CEM-425", D("17500")), ("PIN-001", D("8000"))]
    assert (v["totales"]["valor"], v["totales"]["valor_venta"]) == (D("43500"), D("62000"))
    assert v["filas"][1]["bajo_minimo"]
    principal = await pool.fetchval("SELECT id FROM inventario.almacenes WHERE es_principal")
    va = await rr.reporte_inventario_valorizado(pool, almacen_id=principal)
    assert va["totales"]["valor"] == D("39900")  # varilla 80 × 180 en el principal
    assert (await rr.reporte_inventario_valorizado(pool, categoria="Pinturas"))["totales"]["valor"] == D("8000")

    rot = {f["sku"]: f for f in (await rr.reporte_rotacion(pool, **OCTUBRE))["filas"]}
    cemento = rot["CEM-425"]
    assert (cemento["existencia_inicial"], cemento["entradas"], cemento["salidas"], cemento["existencia_final"]) == (
        D("40"), D("30"), D("20"), D("50"))
    assert (cemento["costo_salidas"], cemento["rotacion"], cemento["dias_inventario"]) == (D("7000"), D("0.44"), D("69.8"))
    assert (rot["VAR-038"]["entradas"], rot["VAR-038"]["salidas"]) == (0, 0)  # la transferencia no cuenta
    sep = {f["sku"]: f for f in (await rr.reporte_rotacion(pool, desde=date(2026, 9, 1), hasta=date(2026, 9, 30)))["filas"]}
    assert (sep["VAR-038"]["existencia_inicial"], sep["VAR-038"]["rotacion"], sep["VAR-038"]["dias_inventario"]) == (
        D("200"), D("0.67"), D("45.0"))

    top = await rr.reporte_mas_vendidos(pool, **OCTUBRE)
    assert [(f["sku"], f["cantidad"], f["facturas"], f["costo"], f["participacion"]) for f in top["filas"]] == [
        ("CEM-425", D("14"), 2, D("4900"), D("100"))]  # la merma (ajuste) no es venta
    assert (await rr.reporte_mas_vendidos(pool, desde=date(2026, 9, 1), hasta=date(2026, 9, 30)))["total_cantidad"] == D("100")


async def test_retenciones(pool):
    r = await rr.reporte_retenciones(pool, **OCTUBRE)
    assert [(f["etiqueta"], f["isr_retenido"], f["itbis_retenido"]) for f in r["efectuadas"]] == [
        ("Juan Pérez", D("1000"), D("1800"))]
    assert {f["clave"]: f["total"] for f in r["por_tipo"]} == {"itbis": D("1800"), "isr2": D("1000")}
    assert r["totales_efectuadas"]["total"] == D("2800")
    assert [(f["etiqueta"], f["itbis_retenido"]) for f in r["sufridas"]] == [("Hotel Brisas de Bávaro, SA", D("480"))]
    # Mismo total que la pantalla de Retenciones de Compras para el mes.
    from services.erp_api.app import compras_repository

    mes = await compras_repository.retenciones_del_mes(pool, "202610")
    assert mes["totales"]["isr_retenido"] + mes["totales"]["itbis_retenido"] == r["totales_efectuadas"]["total"]
