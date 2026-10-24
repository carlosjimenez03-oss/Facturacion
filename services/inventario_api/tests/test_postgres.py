"""Pruebas contra un Postgres real (migraciones, existencias por almacén,
lotes/FEFO, ajustes, transferencias, salida por factura, Kardex y
valuación). Se saltan si no se define INVENTARIO_TEST_DATABASE_URL, p. ej.:

    INVENTARIO_TEST_DATABASE_URL=postgresql://usuario@127.0.0.1:5432/postgres pytest services/inventario_api

Cada prueba crea una base de datos nueva (vacía: solo las migraciones del
servicio, sin db/init) y la borra al terminar; el usuario necesita permiso
CREATEDB.
"""
import asyncio
import os
import uuid
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import asyncpg
import pytest

from services.inventario_api.app import (
    ajustes_repository,
    almacenes_repository,
    kardex,
    lotes_repository,
    migraciones,
    repository,
    stock,
)
from services.inventario_api.app.errores import OperacionInvalidaError, StockInsuficienteError

URL_ADMIN = os.environ.get("INVENTARIO_TEST_DATABASE_URL")
REPO_ROOT = Path(__file__).resolve().parents[3]

pytestmark = pytest.mark.skipif(not URL_ADMIN, reason="INVENTARIO_TEST_DATABASE_URL no definido")

D = Decimal


def _url_base(nombre: str) -> str:
    base, _, _ = URL_ADMIN.rpartition("/")
    return f"{base}/{nombre}"


@pytest.fixture
async def base_vacia():
    """(pool, nombre) de una base recién creada, sin ningún esquema."""
    nombre = f"inv_test_{uuid.uuid4().hex[:10]}"
    admin = await asyncpg.connect(URL_ADMIN)
    await admin.execute(f'CREATE DATABASE "{nombre}"')
    pool = await asyncpg.create_pool(_url_base(nombre), min_size=1, max_size=6)
    try:
        yield pool
    finally:
        await pool.close()
        await admin.execute(f'DROP DATABASE "{nombre}" WITH (FORCE)')
        await admin.close()


@pytest.fixture
async def pool(base_vacia):
    await migraciones.aplicar_migraciones(base_vacia)
    return base_vacia


async def _producto(pool, sku="P-1", stock_inicial=D("0"), costo=D("0"), **extra):
    return await repository.crear_producto(
        pool,
        sku=sku,
        nombre=f"Producto {sku}",
        descripcion=None,
        unidad_medida="UND",
        precio_venta=D("100"),
        stock_inicial=stock_inicial,
        costo_unitario_inicial=costo,
        **extra,
    )


async def _existencias(pool, producto_id) -> dict[str, Decimal]:
    async with pool.acquire() as conn:
        filas = await conn.fetch(
            """
            SELECT a.nombre, e.cantidad FROM inventario.existencias e
            JOIN inventario.almacenes a ON a.id = e.almacen_id WHERE e.producto_id = $1
            """,
            uuid.UUID(producto_id),
        )
        total = await conn.fetchval(
            "SELECT stock_actual FROM inventario.productos WHERE id = $1", uuid.UUID(producto_id)
        )
    resultado = {fila["nombre"]: fila["cantidad"] for fila in filas}
    assert sum(resultado.values(), D("0")) == total, "stock_actual debe ser la suma de existencias"
    return resultado


async def _principal(pool):
    return next(a for a in await almacenes_repository.listar_almacenes(pool) if a["es_principal"])


# --- Migraciones ---------------------------------------------------------

async def test_migraciones_en_base_vacia_son_idempotentes(base_vacia):
    await migraciones.aplicar_migraciones(base_vacia)
    await migraciones.aplicar_migraciones(base_vacia)
    async with base_vacia.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM inventario.almacenes WHERE es_principal") == 1
        assert await conn.fetchval("SELECT to_regclass('inventario.movimientos_salida_factura_idx')") is None
        secuencias = await conn.fetchval(
            "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'inventario' AND c.relkind = 'S'"
        )
    assert secuencias == 3  # ajustes, transferencias y la identidad de movimientos.secuencia


async def test_migracion_sobre_004_con_datos_conserva_el_stock(base_vacia):
    ddl_004 = (REPO_ROOT / "db" / "init" / "004_inventario.sql").read_text(encoding="utf-8")
    async with base_vacia.acquire() as conn:
        await conn.execute(ddl_004)
        pid = await conn.fetchval(
            "INSERT INTO inventario.productos (sku, nombre, stock_actual, costo_promedio) "
            "VALUES ('LEG-1', 'Legado', 12, 7.5) RETURNING id"
        )
        await conn.execute(
            "INSERT INTO inventario.productos (sku, nombre, stock_actual) VALUES ('LEG-0', 'Sin stock', 0)"
        )
        await conn.execute(
            "INSERT INTO inventario.movimientos (producto_id, tipo, cantidad, costo_unitario, referencia_tipo) "
            "VALUES ($1, 'entrada', 12, 7.5, 'compra')",
            pid,
        )
    await migraciones.aplicar_migraciones(base_vacia)
    await migraciones.aplicar_migraciones(base_vacia)
    assert await _existencias(base_vacia, str(pid)) == {"Principal": D("12")}
    async with base_vacia.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM inventario.movimientos WHERE almacen_id IS NULL") == 0
        producto = await conn.fetchrow("SELECT tipo, tasa_itbis FROM inventario.productos WHERE id = $1", pid)
    assert (producto["tipo"], producto["tasa_itbis"]) == ("bien", "18")
    valuacion = await almacenes_repository.valuacion(base_vacia)
    assert valuacion["valor_total"] == D("90.00")


# --- Productos y compras ---------------------------------------------------------

async def test_producto_con_stock_inicial_y_servicio(pool):
    producto = await _producto(pool, stock_inicial=D("10"), costo=D("4"), categoria="Ferretería", stock_minimo=D("10"))
    assert producto["stock_actual"] == D("10")
    assert producto["costo_promedio"] == D("4")
    assert producto["estado_stock"] == "bajo_minimo"
    assert await _existencias(pool, producto["id"]) == {"Principal": D("10")}

    servicio = await _producto(pool, sku="SRV-1", tipo="servicio", tasa_itbis="exento")
    assert servicio["estado_stock"] == "no_aplica"
    with pytest.raises(OperacionInvalidaError):
        await _producto(pool, sku="SRV-2", tipo="servicio", stock_inicial=D("1"))

    # PUT de un cliente de antes (sin los campos nuevos) no los borra.
    actualizado = await repository.actualizar_producto(
        pool, uuid.UUID(producto["id"]), sku="P-1", nombre="Otro", descripcion=None,
        unidad_medida="UND", precio_venta=D("120"), activo=True,
    )
    assert actualizado["categoria"] == "Ferretería" and actualizado["stock_minimo"] == D("10")
    with pytest.raises(OperacionInvalidaError):
        await repository.actualizar_producto(
            pool, uuid.UUID(producto["id"]), sku="P-1", nombre="Otro", descripcion=None,
            unidad_medida="UND", precio_venta=D("120"), activo=True, tipo="servicio",
        )
    alertas = await repository.listar_productos(pool, alerta_stock=True)
    assert [p["sku"] for p in alertas] == ["P-1"]


async def test_confirmar_compra_en_almacen_con_lote(pool):
    sucursal = await almacenes_repository.crear_almacen(pool, nombre="Sucursal Santiago", direccion=None)
    producto = await _producto(pool, stock_inicial=D("10"), costo=D("4"))
    compra = await repository.crear_compra(
        pool, proveedor_id=None, numero_documento="B0100000001", notas=None,
        almacen_id=uuid.UUID(sucursal["id"]),
        items=[{
            "producto_id": uuid.UUID(producto["id"]), "cantidad": D("10"), "costo_unitario": D("6"),
            "lote_numero": "L-1", "lote_vencimiento": stock.hoy_rd() + timedelta(days=200),
        }],
    )
    await repository.confirmar_compra(pool, uuid.UUID(compra["id"]))
    actual = await repository.obtener_producto(pool, uuid.UUID(producto["id"]))
    assert actual["stock_actual"] == D("20") and actual["costo_promedio"] == D("5")
    assert await _existencias(pool, producto["id"]) == {"Principal": D("10"), "Sucursal Santiago": D("10")}
    lotes = await lotes_repository.listar_lotes(pool, dias_alerta=30, producto_id=uuid.UUID(producto["id"]))
    assert len(lotes) == 1 and lotes[0]["cantidad"] == D("10") and lotes[0]["estado"] == "vigente"
    assert lotes[0]["existencias"][0]["almacen_nombre"] == "Sucursal Santiago"
    detalle = await almacenes_repository.existencias_producto(pool, uuid.UUID(producto["id"]), dias_alerta=30)
    sucursal_det = next(a for a in detalle["almacenes"] if a["almacen_nombre"] == "Sucursal Santiago")
    assert sucursal_det["sin_lote"] == D("0") and len(sucursal_det["lotes"]) == 1


# --- Transferencias ---------------------------------------------------------

async def test_transferencia_atomica(pool):
    sucursal = await almacenes_repository.crear_almacen(pool, nombre="Sucursal", direccion=None)
    principal = await _principal(pool)
    a = await _producto(pool, sku="A", stock_inicial=D("10"), costo=D("2"))
    b = await _producto(pool, sku="B", stock_inicial=D("1"), costo=D("3"))

    with pytest.raises(StockInsuficienteError):
        await almacenes_repository.crear_transferencia(
            pool, almacen_origen_id=uuid.UUID(principal["id"]), almacen_destino_id=uuid.UUID(sucursal["id"]),
            notas=None,
            items=[
                {"producto_id": uuid.UUID(a["id"]), "cantidad": D("4")},
                {"producto_id": uuid.UUID(b["id"]), "cantidad": D("5")},  # no alcanza
            ],
        )
    # Nada se movió (ni la primera línea, ni el correlativo de transferencias visibles).
    assert await _existencias(pool, a["id"]) == {"Principal": D("10")}
    assert await almacenes_repository.listar_transferencias(pool) == []

    transferencia = await almacenes_repository.crear_transferencia(
        pool, almacen_origen_id=uuid.UUID(principal["id"]), almacen_destino_id=uuid.UUID(sucursal["id"]),
        notas="Reposición", items=[{"producto_id": uuid.UUID(a["id"]), "cantidad": D("4")}],
    )
    assert transferencia["numero"].startswith("TR-")
    assert await _existencias(pool, a["id"]) == {"Principal": D("6"), "Sucursal": D("4")}
    producto = await repository.obtener_producto(pool, uuid.UUID(a["id"]))
    assert producto["stock_actual"] == D("10") and producto["costo_promedio"] == D("2")

    # Inactivar un almacén con existencia no se permite; el principal tampoco.
    with pytest.raises(OperacionInvalidaError):
        await almacenes_repository.actualizar_almacen(
            pool, uuid.UUID(sucursal["id"]), nombre="Sucursal", direccion=None, permite_negativo=False, activo=False
        )
    # Cambiar de principal.
    await almacenes_repository.actualizar_almacen(
        pool, uuid.UUID(sucursal["id"]), nombre="Sucursal", direccion=None, permite_negativo=False,
        activo=True, es_principal=True,
    )
    assert (await _principal(pool))["nombre"] == "Sucursal"
    stats = {x["nombre"]: x for x in await almacenes_repository.listar_almacenes(pool)}
    assert stats["Sucursal"]["valor"] == D("8.00") and stats["Sucursal"]["productos"] == 1


async def test_salidas_concurrentes_no_sobrevenden(pool):
    producto = await _producto(pool, stock_inicial=D("10"), costo=D("1"))
    pid = uuid.UUID(producto["id"])

    async def salida():
        return await ajustes_repository.crear_ajuste(
            pool, almacen_id=None, motivo="merma", fecha=None, notas=None, aplicar=True,
            items=[{"producto_id": pid, "tipo": "salida", "cantidad": D("6")}],
        )

    resultados = await asyncio.gather(salida(), salida(), salida(), return_exceptions=True)
    ok = [r for r in resultados if isinstance(r, dict)]
    errores = [r for r in resultados if isinstance(r, StockInsuficienteError)]
    assert len(ok) == 1 and len(errores) == 2
    assert await _existencias(pool, producto["id"]) == {"Principal": D("4")}
    # Los que fallaron no dejaron ajuste creado (crear+aplicar es una sola transacción).
    assert len(await ajustes_repository.listar_ajustes(pool)) == 1


# --- Ajustes ---------------------------------------------------------

async def test_ajuste_aplicar_y_anular(pool):
    producto = await _producto(pool, stock_inicial=D("10"), costo=D("4"))
    pid = uuid.UUID(producto["id"])
    ajuste = await ajustes_repository.crear_ajuste(
        pool, almacen_id=None, motivo="conteo_fisico", fecha=None, notas=None,
        items=[
            {"producto_id": pid, "tipo": "entrada", "cantidad": D("10"), "costo_unitario": D("6")},
            {"producto_id": pid, "tipo": "salida", "cantidad": D("5")},
        ],
    )
    assert ajuste["estado"] == "borrador" and ajuste["numero"] == "AJ-0001"
    assert (await repository.obtener_producto(pool, pid))["stock_actual"] == D("10")  # borrador: sin efecto

    aplicado = await ajustes_repository.aplicar_ajuste(pool, uuid.UUID(ajuste["id"]))
    assert aplicado["estado"] == "aplicado"
    producto = await repository.obtener_producto(pool, pid)
    assert producto["stock_actual"] == D("15") and producto["costo_promedio"] == D("5")
    movs = await repository.listar_movimientos(pool, referencia_tipo="ajuste", referencia_id=ajuste["id"])
    salida = next(m for m in movs if m["tipo"] == "salida")
    assert salida["costo_unitario"] == D("5") and salida["documento"] == "AJ-0001"
    with pytest.raises(OperacionInvalidaError):
        await ajustes_repository.aplicar_ajuste(pool, uuid.UUID(ajuste["id"]))
    with pytest.raises(OperacionInvalidaError):
        await ajustes_repository.eliminar_ajuste(pool, uuid.UUID(ajuste["id"]))

    anulado = await ajustes_repository.anular_ajuste(pool, uuid.UUID(ajuste["id"]))
    assert anulado["estado"] == "anulado"
    assert await _existencias(pool, producto["id"]) == {"Principal": D("10")}
    inversos = await repository.listar_movimientos(pool, referencia_tipo="anulacion_ajuste")
    assert sorted(m["tipo"] for m in inversos) == ["entrada", "salida"]

    # Anular una entrada cuya mercancía ya salió falla y deja el ajuste aplicado.
    entrada = await ajustes_repository.crear_ajuste(
        pool, almacen_id=None, motivo="otro", fecha=None, notas=None, aplicar=True,
        items=[{"producto_id": pid, "tipo": "entrada", "cantidad": D("5")}],
    )
    await ajustes_repository.crear_ajuste(
        pool, almacen_id=None, motivo="dano", fecha=None, notas=None, aplicar=True,
        items=[{"producto_id": pid, "tipo": "salida", "cantidad": D("14")}],
    )
    with pytest.raises(StockInsuficienteError):
        await ajustes_repository.anular_ajuste(pool, uuid.UUID(entrada["id"]))
    assert (await ajustes_repository.obtener_ajuste(pool, uuid.UUID(entrada["id"])))["estado"] == "aplicado"


# --- Lotes / FEFO y salida por factura ---------------------------------------------------------

async def _producto_con_lotes(pool):
    hoy = stock.hoy_rd()
    producto = await _producto(pool, sku="CAF-001", stock_inicial=D("5"), costo=D("10"))  # 5 sin lote
    pid = uuid.UUID(producto["id"])
    lotes = {}
    for numero, dias, cantidad in (("L-B", 200, D("10")), ("L-A", 10, D("4")), ("L-VENCIDO", -3, D("7"))):
        lote = await lotes_repository.crear_lote(
            pool, producto_id=pid, numero=numero, fecha_vencimiento=hoy + timedelta(days=dias), notas=None,
            dias_alerta=30, cantidad_inicial=cantidad, costo_unitario=D("10"),
        )
        lotes[numero] = lote
    return pid, lotes


async def test_lotes_fefo_y_alertas(pool):
    pid, lotes = await _producto_con_lotes(pool)
    assert lotes["L-A"]["estado"] == "por_vencer" and lotes["L-VENCIDO"]["estado"] == "vencido"
    assert lotes["L-B"]["estado"] == "vigente"
    assert (await repository.obtener_producto(pool, pid))["maneja_lotes"] is True

    sugerencia = await lotes_repository.sugerir_fefo(pool, producto_id=pid, almacen_id=None, cantidad=D("16"))
    assert [(t["numero"], t["cantidad"]) for t in sugerencia["tomas"]] == [("L-A", D("4")), ("L-B", D("10"))]
    assert sugerencia["sin_lote"] == D("2") and sugerencia["faltante"] == D("0")
    grande = await lotes_repository.sugerir_fefo(pool, producto_id=pid, almacen_id=None, cantidad=D("30"))
    assert grande["faltante"] == D("11")  # 4 + 10 + 5 sin lote = 19 vendibles (el vencido no cuenta)

    alertas = await lotes_repository.alertas_lotes(pool, dias=30)
    assert [lote["numero"] for lote in alertas["por_vencer"]] == ["L-A"]
    assert [lote["numero"] for lote in alertas["vencidos"]] == ["L-VENCIDO"]
    assert (await lotes_repository.alertas_lotes(pool, dias=300))["por_vencer"][-1]["numero"] == "L-B"

    bloqueado = await lotes_repository.actualizar_lote(
        pool, uuid.UUID(lotes["L-A"]["id"]), numero="L-A", fecha_vencimiento=lotes["L-A"]["fecha_vencimiento"],
        bloqueado=True, notas="Retenido", dias_alerta=30,
    )
    assert bloqueado["estado"] == "bloqueado"
    sugerencia = await lotes_repository.sugerir_fefo(pool, producto_id=pid, almacen_id=None, cantidad=D("4"))
    assert [t["numero"] for t in sugerencia["tomas"]] == ["L-B"]


async def test_salida_por_factura_fefo_lote_almacen_e_idempotencia(pool):
    pid, lotes = await _producto_con_lotes(pool)
    sucursal = await almacenes_repository.crear_almacen(pool, nombre="Sucursal", direccion=None)
    otro = await _producto(pool, sku="OTRO", stock_inicial=D("3"), costo=D("1"))
    servicio = await _producto(pool, sku="SRV", tipo="servicio")
    await almacenes_repository.crear_transferencia(
        pool, almacen_origen_id=uuid.UUID((await _principal(pool))["id"]),
        almacen_destino_id=uuid.UUID(sucursal["id"]), notas=None,
        items=[{"producto_id": uuid.UUID(otro["id"]), "cantidad": D("3")}],
    )
    items = [
        {"numero_linea": "1", "producto_id": pid, "cantidad": D("6")},  # FEFO: 4 de L-A + 2 de L-B
        {"numero_linea": "2", "producto_id": pid, "cantidad": D("1"), "lote_id": uuid.UUID(lotes["L-B"]["id"])},
        {"numero_linea": "3", "producto_id": uuid.UUID(otro["id"]), "cantidad": D("5"),
         "almacen_id": uuid.UUID(sucursal["id"])},  # deja -2: la venta ya se envió
        {"numero_linea": "4", "producto_id": uuid.UUID(servicio["id"]), "cantidad": D("1")},
    ]
    disponible, faltantes = await repository.validar_disponibilidad(pool, items)
    assert disponible is False
    assert [(f["nombre"], f["solicitado"], f["disponible"]) for f in faltantes] == [("Producto OTRO", D("5"), D("3"))]
    vencido = await repository.validar_disponibilidad(
        pool, [{"producto_id": pid, "cantidad": D("1"), "lote_id": uuid.UUID(lotes["L-VENCIDO"]["id"])}]
    )
    assert vencido[0] is False and vencido[1][0]["motivo"] == "lote_vencido"

    factura_id = str(uuid.uuid4())
    resultados = await asyncio.gather(
        *[repository.registrar_salida_por_factura(pool, factura_id, items, documento="E310000000001") for _ in range(3)]
    )
    assert all(len(r) == 4 for r in resultados)  # 2 (FEFO) + 1 (lote) + 1 (otro); el servicio no mueve
    assert len({tuple(sorted(m["id"] for m in r)) for r in resultados}) == 1  # los 3 devuelven los mismos

    lotes_ahora = {
        lote["numero"]: lote["cantidad"]
        for lote in await lotes_repository.listar_lotes(pool, dias_alerta=30, producto_id=pid)
    }
    assert lotes_ahora == {"L-A": D("0"), "L-B": D("7"), "L-VENCIDO": D("7")}
    assert await _existencias(pool, str(pid)) == {"Principal": D("19")}
    assert await _existencias(pool, otro["id"]) == {"Principal": D("0"), "Sucursal": D("-2")}
    movs = await repository.listar_movimientos(pool, referencia_tipo="factura", referencia_id=factura_id)
    assert {m["documento"] for m in movs} == {"E310000000001"}
    assert {m["referencia_linea"] for m in movs} == {"1", "2", "3"}

    # Reintento posterior (otra llamada, ya sin concurrencia) tampoco descuenta.
    await repository.registrar_salida_por_factura(pool, factura_id, items)
    assert await _existencias(pool, str(pid)) == {"Principal": D("19")}


# --- Kardex y valuación ---------------------------------------------------------

async def test_kardex_saldo_acumulado_y_valuacion(pool):
    producto = await _producto(pool, sku="CEM-425", stock_inicial=D("200"), costo=D("410"), categoria="Construcción")
    pid = uuid.UUID(producto["id"])
    for cantidad, costo in ((D("500"), D("425")),):
        compra = await repository.crear_compra(
            pool, proveedor_id=None, numero_documento="E310000045102", notas=None,
            items=[{"producto_id": pid, "cantidad": cantidad, "costo_unitario": costo}],
        )
        await repository.confirmar_compra(pool, uuid.UUID(compra["id"]))
    await repository.registrar_salida_por_factura(
        pool, str(uuid.uuid4()), [{"producto_id": pid, "cantidad": D("350")}], documento="E310000000118"
    )
    await ajustes_repository.crear_ajuste(
        pool, almacen_id=None, motivo="merma", fecha=None, notas=None, aplicar=True,
        items=[{"producto_id": pid, "tipo": "salida", "cantidad": D("2")}],
    )

    resultado = await kardex.obtener_kardex(pool, producto_id=pid)
    filas = resultado["filas"]
    assert [f["concepto"] for f in filas] == ["Stock inicial", "Compra", "Venta", "Ajuste de salida"]
    assert [f["saldo_cantidad"] for f in filas] == [D("200"), D("700"), D("350"), D("348")]
    promedio = (D("200") * 410 + D("500") * 425) / 700
    assert filas[1]["costo_promedio"] == promedio.quantize(D("0.0001"))
    assert filas[2]["costo_unitario"] == promedio.quantize(D("0.0001"))
    producto = await repository.obtener_producto(pool, pid)
    final = resultado["saldo_final"]
    assert final["cantidad"] == producto["stock_actual"] == D("348")
    assert abs(final["valor"] - producto["stock_actual"] * producto["costo_promedio"]) < D("0.10")

    peps = await kardex.obtener_kardex(pool, producto_id=pid, metodo="peps")
    # PEPS: la venta de 350 sale de la capa inicial (200 a 410) y 150 de la compra (a 425).
    assert peps["filas"][2]["costo_total"] == -(D("200") * 410 + D("150") * 425)
    assert peps["saldo_final"]["valor"] == D("348") * 425

    hoy = stock.hoy_rd()
    periodo = await kardex.obtener_kardex(pool, producto_id=pid, desde=hoy + timedelta(days=1))
    assert periodo["filas"] == [] and periodo["saldo_inicial"]["cantidad"] == D("348")
    solo_salidas = await kardex.obtener_kardex(pool, producto_id=pid, tipo="salida")
    assert [f["saldo_cantidad"] for f in solo_salidas["filas"]] == [D("350"), D("348")]
    csv = kardex.kardex_csv(resultado)
    assert csv.startswith("﻿Fecha,Documento") and "E310000045102" in csv and "Saldo inicial" in csv

    valuacion = await almacenes_repository.valuacion(pool)
    async with pool.acquire() as conn:
        ir2 = await conn.fetchval(
            "SELECT COALESCE(sum(GREATEST(stock_actual, 0) * costo_promedio), 0) FROM inventario.productos"
        )
    assert valuacion["valor_total"] == Decimal(ir2).quantize(D("0.01"))
    assert valuacion["por_categoria"][0]["categoria"] == "Construcción"
    assert valuacion["por_almacen"][0]["valor"] == valuacion["valor_total"]


async def test_operaciones_cruzadas_concurrentes_sin_deadlock(pool):
    """Transferencias en sentidos opuestos con los productos en distinto
    orden, ajustes y ventas a la vez: ningún deadlock y el total cuadra."""
    sucursal = await almacenes_repository.crear_almacen(pool, nombre="Sucursal", direccion=None)
    principal = await _principal(pool)
    a = await _producto(pool, sku="A", stock_inicial=D("100"), costo=D("1"))
    b = await _producto(pool, sku="B", stock_inicial=D("100"), costo=D("1"))
    ida = (uuid.UUID(principal["id"]), uuid.UUID(sucursal["id"]))

    def transferir(origen, destino, primero, segundo):
        return almacenes_repository.crear_transferencia(
            pool, almacen_origen_id=origen, almacen_destino_id=destino, notas=None,
            items=[{"producto_id": uuid.UUID(primero["id"]), "cantidad": D("1")},
                   {"producto_id": uuid.UUID(segundo["id"]), "cantidad": D("1")}],
        )

    tareas = []
    for i in range(10):
        tareas.append(transferir(ida[0], ida[1], a, b))
        tareas.append(transferir(ida[0], ida[1], b, a))
        tareas.append(ajustes_repository.crear_ajuste(
            pool, almacen_id=None, motivo="merma", fecha=None, notas=None, aplicar=True,
            items=[{"producto_id": uuid.UUID(b["id"]), "tipo": "salida", "cantidad": D("1")},
                   {"producto_id": uuid.UUID(a["id"]), "tipo": "salida", "cantidad": D("1")}],
        ))
        tareas.append(repository.registrar_salida_por_factura(
            pool, "factura-%d" % i, [{"producto_id": uuid.UUID(a["id"]), "cantidad": D("1")},
                                     {"producto_id": uuid.UUID(b["id"]), "cantidad": D("1")}],
        ))
    resultados = await asyncio.gather(*tareas, return_exceptions=True)
    assert [r for r in resultados if isinstance(r, BaseException)] == []
    for producto in (a, b):
        assert await _existencias(pool, producto["id"]) == {"Principal": D("60"), "Sucursal": D("20")}


async def test_lote_explicito_y_fefo_del_mismo_producto_en_una_factura(pool):
    """El FEFO de una línea no puede tomar las unidades que otra línea pidió
    con lote explícito: la validación las descuenta y la salida procesa
    primero las líneas con lote."""
    pid, lotes = await _producto_con_lotes(pool)  # L-A 4, L-B 10, vencido 7, 5 sin lote
    lote_a = uuid.UUID(lotes["L-A"]["id"])
    for fefo, esperado in ((D("15"), True), (D("16"), False)):
        disponible, _ = await repository.validar_disponibilidad(
            pool,
            [{"producto_id": pid, "cantidad": fefo}, {"producto_id": pid, "cantidad": D("4"), "lote_id": lote_a}],
        )
        assert disponible is esperado

    await repository.registrar_salida_por_factura(
        pool,
        str(uuid.uuid4()),
        [{"numero_linea": "1", "producto_id": pid, "cantidad": D("15")},
         {"numero_linea": "2", "producto_id": pid, "cantidad": D("4"), "lote_id": lote_a}],
    )
    restantes = {
        lote["numero"]: lote["cantidad"]
        for lote in await lotes_repository.listar_lotes(pool, dias_alerta=30, producto_id=pid)
    }
    assert restantes == {"L-A": D("0"), "L-B": D("0"), "L-VENCIDO": D("7")}
    assert await _existencias(pool, str(pid)) == {"Principal": D("7")}


# --- Revisión de la Fase 4 ---------------------------------------------------------

async def _locks_access_exclusive(conn) -> list[str]:
    filas = await conn.fetch(
        "SELECT relation::regclass::text AS rel FROM pg_locks "
        "WHERE pid = pg_backend_pid() AND locktype = 'relation' AND mode = 'AccessExclusiveLock' ORDER BY 1"
    )
    return [fila["rel"] for fila in filas]


async def test_migracion_aplicada_no_vuelve_a_correr_ddl(pool):
    """Un arranque con la base al día no toma ningún AccessExclusiveLock
    (antes cada ALTER TABLE ... IF NOT EXISTS lo tomaba sobre productos,
    movimientos, compras y compra_items)."""
    async with pool.acquire() as conn:
        assert await migraciones.version_aplicada(conn) == migraciones.VERSION_ACTUAL == 21
        async with conn.transaction():
            # Mismo camino que aplicar_migraciones, en una transacción para
            # poder mirar los locks que deja.
            assert await migraciones._migrar(conn) == []
            assert await _locks_access_exclusive(conn) == []
    assert await migraciones.aplicar_migraciones(pool) == []


async def test_db_init_local_queda_anotado_y_la_app_no_lo_repite(base_vacia):
    """Postgres local: db/init corre 004, 011 y 021; el arranque ya no migra."""
    async with base_vacia.acquire() as conn:
        for archivo in ("004_inventario.sql", "011_inventario_almacenes_lotes.sql", "021_multiempresa.sql"):
            await conn.execute((REPO_ROOT / "db" / "init" / archivo).read_text(encoding="utf-8"))
    assert await migraciones.aplicar_migraciones(base_vacia) == []


async def test_migracion_no_deja_en_deadlock_a_una_salida_en_curso(base_vacia):
    """Escenario del revisor: una salida por factura ya leyó `movimientos`
    (AccessShare) y luego bloquea `productos` mientras otra réplica arranca
    y migra. La migración espera cada tabla menos que deadlock_timeout: se
    rinde ella primero, suelta todo y reintenta; la salida termina sin
    error."""
    ddl_004 = (REPO_ROOT / "db" / "init" / "004_inventario.sql").read_text(encoding="utf-8")
    async with base_vacia.acquire() as conn:
        await conn.execute(ddl_004)
        pid = await conn.fetchval(
            "INSERT INTO inventario.productos (sku, nombre, stock_actual, costo_promedio) "
            "VALUES ('A', 'A', 10, 100) RETURNING id"
        )
    venta = await base_vacia.acquire()
    tx = venta.transaction()
    await tx.start()
    await venta.fetch("SELECT * FROM inventario.movimientos WHERE referencia_tipo = 'factura'")
    esperas: list[float] = []

    async def dormir(segundos):
        esperas.append(segundos)
        await asyncio.sleep(0.2)

    migracion = asyncio.create_task(migraciones.aplicar_migraciones(base_vacia, dormir=dormir))
    await asyncio.sleep(0.3)
    await venta.fetch("SELECT id FROM inventario.productos WHERE id = $1 FOR NO KEY UPDATE", pid)
    await tx.commit()
    await base_vacia.release(venta)
    assert await asyncio.wait_for(migracion, 30) == [11, 21]
    assert esperas  # tuvo que reintentar
    assert await _existencias(base_vacia, str(pid)) == {"Principal": D("10")}


async def test_descuadre_posterior_a_la_migracion_se_reporta_sin_corregir(pool, caplog):
    producto = await _producto(pool, sku="DESC", stock_inicial=D("5"), costo=D("1"))
    async with pool.acquire() as conn:  # lo que escribiría una réplica de antes de la Fase 4
        await conn.execute(
            "UPDATE inventario.productos SET stock_actual = stock_actual - 2 WHERE id = $1", uuid.UUID(producto["id"])
        )
    with caplog.at_level("WARNING", logger=migraciones.__name__):
        await migraciones.aplicar_migraciones(pool)
    assert "DESC (stock_actual 3.0000, almacenes 5.0000)" in caplog.text
    async with pool.acquire() as conn:
        assert await conn.fetchval(
            "SELECT sum(cantidad) FROM inventario.existencias WHERE producto_id = $1", uuid.UUID(producto["id"])
        ) == D("5")


async def test_anular_entrada_sale_al_costo_original_y_restaura_el_promedio(pool):
    producto = await _producto(pool, sku="ANU", stock_inicial=D("10"), costo=D("100"))
    pid = uuid.UUID(producto["id"])
    ajuste = await ajustes_repository.crear_ajuste(
        pool, almacen_id=None, motivo="otro", fecha=None, notas=None, aplicar=True,
        items=[{"producto_id": pid, "tipo": "entrada", "cantidad": D("10"), "costo_unitario": D("200")}],
    )
    assert (await repository.obtener_producto(pool, pid))["costo_promedio"] == D("150")
    await ajustes_repository.anular_ajuste(pool, uuid.UUID(ajuste["id"]))
    producto = await repository.obtener_producto(pool, pid)
    assert (producto["stock_actual"], producto["costo_promedio"]) == (D("10"), D("100"))
    inverso = (await repository.listar_movimientos(pool, referencia_tipo="anulacion_ajuste"))[0]
    assert inverso["costo_unitario"] == D("200")
    final = (await kardex.obtener_kardex(pool, producto_id=pid))["saldo_final"]
    assert final == {"cantidad": D("10"), "costo_promedio": D("100.0000"), "valor": D("1000.00")}


async def test_salida_sin_lote_no_toma_unidades_de_los_lotes(pool):
    """Anular una entrada sin lote cuando lo "sin lote" ya se vendió: antes
    pasaba (el total del almacén alcanzaba con el lote vencido) y lo "sin
    lote" quedaba en -5 con el lote en +10."""
    producto = await _producto(pool, sku="LOT")
    pid = uuid.UUID(producto["id"])
    await lotes_repository.crear_lote(
        pool, producto_id=pid, numero="L1", fecha_vencimiento=stock.hoy_rd() - timedelta(days=1), notas=None,
        dias_alerta=30, cantidad_inicial=D("10"), costo_unitario=D("10"),
    )
    ajuste = await ajustes_repository.crear_ajuste(
        pool, almacen_id=None, motivo="otro", fecha=None, notas=None, aplicar=True,
        items=[{"producto_id": pid, "tipo": "entrada", "cantidad": D("5"), "costo_unitario": D("10")}],
    )
    await repository.registrar_salida_por_factura(pool, "F-2", [{"producto_id": pid, "cantidad": D("5")}])
    with pytest.raises(StockInsuficienteError) as error:
        await ajustes_repository.anular_ajuste(pool, uuid.UUID(ajuste["id"]))
    assert error.value.disponible == D("0")
    existencias = await almacenes_repository.existencias_producto(pool, pid, dias_alerta=30)
    assert existencias["almacenes"][0]["sin_lote"] == D("0")


async def test_maneja_lotes_no_se_apaga_con_lotes_con_existencia(pool):
    producto = await _producto(pool, sku="ML", stock_inicial=D("3"), costo=D("1"))
    pid = uuid.UUID(producto["id"])
    datos = {"sku": "ML", "nombre": "ML", "descripcion": None, "unidad_medida": None,
             "precio_venta": D("1"), "activo": True}
    # Una compra con lote de un producto que no maneja lotes se lo activa.
    compra = await repository.crear_compra(
        pool, proveedor_id=None, numero_documento=None, notas=None,
        items=[{"producto_id": pid, "cantidad": D("2"), "costo_unitario": D("1"), "lote_numero": "C-1"}],
    )
    await repository.confirmar_compra(pool, uuid.UUID(compra["id"]))
    assert (await repository.obtener_producto(pool, pid))["maneja_lotes"] is True
    with pytest.raises(OperacionInvalidaError, match="1 lote"):
        await repository.actualizar_producto(pool, pid, **datos, maneja_lotes=False)
    # Sin existencia en lotes sí se puede.
    lote = (await lotes_repository.listar_lotes(pool, dias_alerta=30, producto_id=pid))[0]
    await ajustes_repository.crear_ajuste(
        pool, almacen_id=None, motivo="otro", fecha=None, notas=None, aplicar=True,
        items=[{"producto_id": pid, "tipo": "salida", "cantidad": D("2"), "lote_id": uuid.UUID(str(lote["id"]))}],
    )
    actualizado = await repository.actualizar_producto(pool, pid, **datos, maneja_lotes=False)
    assert actualizado["maneja_lotes"] is False


async def test_kardex_con_existencia_negativa_coincide_con_el_servicio(pool):
    producto = await _producto(pool, sku="NEG", stock_inicial=D("5"), costo=D("100"))
    pid = uuid.UUID(producto["id"])
    await repository.registrar_salida_por_factura(pool, "F-1", [{"producto_id": pid, "cantidad": D("10")}])
    await ajustes_repository.crear_ajuste(
        pool, almacen_id=None, motivo="otro", fecha=None, notas=None, aplicar=True,
        items=[{"producto_id": pid, "tipo": "entrada", "cantidad": D("10"), "costo_unitario": D("120")}],
    )
    producto = await repository.obtener_producto(pool, pid)
    final = (await kardex.obtener_kardex(pool, producto_id=pid))["saldo_final"]
    assert final["cantidad"] == producto["stock_actual"] == D("5")
    assert final["costo_promedio"] == producto["costo_promedio"] == D("120")
    assert final["valor"] == D("600.00")


async def test_kardex_sigue_el_orden_real_de_aplicacion(pool):
    """T1 empieza primero pero bloquea el producto después de que T2 (una
    entrada a otro costo) confirma: el Kardex debe mostrar T2 antes."""
    producto = await _producto(pool, sku="ORD", stock_inicial=D("10"), costo=D("100"))
    pid = uuid.UUID(producto["id"])
    c1 = await pool.acquire()
    c2 = await pool.acquire()
    try:
        t1 = c1.transaction()
        await t1.start()
        await c1.execute("SELECT 1")
        await asyncio.sleep(0.05)
        async with c2.transaction():
            await stock.registrar_entrada(
                c2, producto_id=pid, almacen_id=await stock.almacen_principal_id(c2), cantidad=D("10"),
                costo_unitario=D("200"), motivo="x", referencia_tipo="ajuste",
            )
        almacen = await stock.obtener_almacen(c1, None)
        await stock.registrar_salida(
            c1, producto_id=pid, almacen=almacen, cantidad=D("15"), motivo="y", referencia_tipo="ajuste"
        )
        await t1.commit()
    finally:
        await pool.release(c1)
        await pool.release(c2)
    resultado = await kardex.obtener_kardex(pool, producto_id=pid)
    assert [f["saldo_cantidad"] for f in resultado["filas"]] == [D("10"), D("20"), D("5")]
    assert [f["saldo_valor"] for f in resultado["filas"]] == [D("1000.00"), D("3000.00"), D("750.00")]
    fechas = [f["fecha"] for f in resultado["filas"]]
    assert fechas == sorted(fechas)  # creado_en = clock_timestamp()


async def test_codigo_barras_vacio_es_null(pool):
    a = await _producto(pool, sku="CB-1", codigo_barras="")
    b = await _producto(pool, sku="CB-2", codigo_barras="  ")
    assert a["codigo_barras"] is None and b["codigo_barras"] is None
    datos = {"sku": "CB-1", "nombre": "x", "descripcion": None, "unidad_medida": None,
             "precio_venta": D("1"), "activo": True}
    actualizado = await repository.actualizar_producto(pool, uuid.UUID(a["id"]), **datos, codigo_barras=" 746 ")
    assert actualizado["codigo_barras"] == "746"
    actualizado = await repository.actualizar_producto(pool, uuid.UUID(a["id"]), **datos, codigo_barras="")
    assert actualizado["codigo_barras"] is None


async def test_validar_disponibilidad_almacen_inactivo_y_lote_ajeno(pool):
    sucursal = await almacenes_repository.crear_almacen(pool, nombre="Cerrada", direccion=None)
    await almacenes_repository.actualizar_almacen(
        pool, uuid.UUID(sucursal["id"]), nombre="Cerrada", direccion=None, permite_negativo=True, activo=False
    )
    producto = await _producto(pool, sku="VD", stock_inicial=D("5"), costo=D("1"))
    pid = uuid.UUID(producto["id"])
    disponible, faltantes = await repository.validar_disponibilidad(
        pool, [{"producto_id": pid, "cantidad": D("1"), "almacen_id": uuid.UUID(sucursal["id"])}]
    )
    assert disponible is False and faltantes[0]["motivo"] == "almacen_inactivo"

    otro = await _producto(pool, sku="VD-2")
    lote = await lotes_repository.crear_lote(
        pool, producto_id=uuid.UUID(otro["id"]), numero="X", fecha_vencimiento=None, notas=None, dias_alerta=30
    )
    with pytest.raises(OperacionInvalidaError):
        await repository.validar_disponibilidad(
            pool, [{"producto_id": pid, "cantidad": D("1"), "lote_id": uuid.UUID(str(lote["id"]))}]
        )
    # Sin lotes, lo pedido sin lote se compara con lo "sin lote" (= todo).
    disponible, _ = await repository.validar_disponibilidad(pool, [{"producto_id": pid, "cantidad": D("5")}])
    assert disponible is True


async def test_inactivar_almacen_espera_a_la_transferencia_en_curso(pool):
    principal = await _principal(pool)
    sucursal = await almacenes_repository.crear_almacen(pool, nombre="Destino", direccion=None)
    sid = uuid.UUID(sucursal["id"])
    producto = await _producto(pool, sku="TR", stock_inicial=D("5"), costo=D("1"))
    conn = await pool.acquire()
    try:
        tx = conn.transaction()
        await tx.start()
        # FOR SHARE de los dos almacenes en orden de id, como crear_transferencia.
        bloqueados = {
            almacen_id: await stock.obtener_almacen(conn, almacen_id)
            for almacen_id in sorted((uuid.UUID(principal["id"]), sid))
        }
        destino = bloqueados[sid]
        inactivar = asyncio.create_task(
            almacenes_repository.actualizar_almacen(
                pool, sid, nombre="Destino", direccion=None, permite_negativo=False, activo=False
            )
        )
        await asyncio.sleep(0.3)
        assert not inactivar.done()  # espera el FOR SHARE
        await stock.registrar_salida(
            conn, producto_id=uuid.UUID(producto["id"]), almacen=bloqueados[uuid.UUID(principal["id"])],
            cantidad=D("2"), motivo="t", referencia_tipo="transferencia", es_transferencia=True,
        )
        await stock.registrar_entrada(
            conn, producto_id=uuid.UUID(producto["id"]), almacen_id=destino["id"], cantidad=D("2"),
            motivo="t", referencia_tipo="transferencia", es_transferencia=True,
        )
        await tx.commit()
    finally:
        await pool.release(conn)
    with pytest.raises(OperacionInvalidaError, match="todavía tiene existencia"):
        await inactivar
    assert await _existencias(pool, producto["id"]) == {principal["nombre"]: D("3"), "Destino": D("2")}

    # Y al revés: inactivo primero, la transferencia hacia él se rechaza.
    vacio = await almacenes_repository.crear_almacen(pool, nombre="Vacío", direccion=None)
    await almacenes_repository.actualizar_almacen(
        pool, uuid.UUID(vacio["id"]), nombre="Vacío", direccion=None, permite_negativo=False, activo=False
    )
    with pytest.raises(OperacionInvalidaError, match="inactivo"):
        await almacenes_repository.crear_transferencia(
            pool, almacen_origen_id=uuid.UUID(principal["id"]), almacen_destino_id=uuid.UUID(vacio["id"]),
            notas=None, items=[{"producto_id": uuid.UUID(producto["id"]), "cantidad": D("1")}],
        )


async def test_valuacion_por_almacen_con_existencia_negativa(pool):
    """Documentado: cada almacén se valúa con GREATEST(cantidad, 0) y el
    total por producto (como el IR-2), así que con un almacén en negativo
    la suma por almacén es mayor que `valor_total`."""
    sucursal = await almacenes_repository.crear_almacen(pool, nombre="Neg", direccion=None, permite_negativo=True)
    producto = await _producto(pool, sku="VN", stock_inicial=D("10"), costo=D("2"))
    await repository.registrar_salida_por_factura(
        pool, "F-N", [{"producto_id": uuid.UUID(producto["id"]), "cantidad": D("4"),
                       "almacen_id": uuid.UUID(sucursal["id"])}],
    )
    valuacion = await almacenes_repository.valuacion(pool)
    por_almacen = {a["nombre"]: a["valor"] for a in valuacion["por_almacen"]}
    assert por_almacen == {"Principal": D("20.00"), "Neg": D("0.00")}
    assert valuacion["valor_total"] == D("12.00")  # (10 − 4) × 2


async def test_reverso_por_factura_rechazada_idempotente(pool):
    """Fase 7: la DGII rechazó la factura; su mercancía vuelve a su almacén y
    lote al costo con que salió, una sola vez aunque se reintente a la vez, y
    la salida por factura no vuelve a descontar después."""
    pid, lotes = await _producto_con_lotes(pool)
    simple = await _producto(pool, sku="SIMPLE", stock_inicial=D("10"), costo=D("50"))
    items = [
        {"numero_linea": "1", "producto_id": pid, "cantidad": D("6")},  # FEFO: 4 de L-A + 2 de L-B
        {"numero_linea": "2", "producto_id": uuid.UUID(simple["id"]), "cantidad": D("3")},
    ]
    factura_id = str(uuid.uuid4())
    antes_lotes = {
        lote["numero"]: lote["cantidad"]
        for lote in await lotes_repository.listar_lotes(pool, dias_alerta=30, producto_id=pid)
    }
    antes = await _existencias(pool, str(pid))
    salidas = await repository.registrar_salida_por_factura(pool, factura_id, items, documento="E310000000099")
    assert len(salidas) == 3

    # Sin salidas registradas (otra factura): no mueve nada.
    assert await repository.registrar_reverso_por_factura(pool, str(uuid.uuid4())) == []

    resultados = await asyncio.gather(
        *[repository.registrar_reverso_por_factura(pool, factura_id) for _ in range(3)]
    )
    assert all(len(r) == 3 for r in resultados)
    assert len({tuple(sorted(m["id"] for m in r)) for r in resultados}) == 1
    assert await _existencias(pool, str(pid)) == antes
    assert await _existencias(pool, simple["id"]) == {"Principal": D("10")}
    despues_lotes = {
        lote["numero"]: lote["cantidad"]
        for lote in await lotes_repository.listar_lotes(pool, dias_alerta=30, producto_id=pid)
    }
    assert despues_lotes == antes_lotes
    reversos = await repository.listar_movimientos(pool, referencia_tipo="reverso_factura", referencia_id=factura_id)
    por_linea = {(m["referencia_linea"], m["lote_id"]): m for m in reversos}
    for salida in salidas:
        entrada = por_linea[(salida["referencia_linea"], salida["lote_id"])]
        assert entrada["tipo"] == "entrada" and entrada["cantidad"] == salida["cantidad"]
        assert entrada["costo_unitario"] == salida["costo_unitario"]  # al costo con que salió
        assert entrada["documento"] == "E310000000099"
    # El costo promedio no cambia (entra al mismo costo).
    producto = await repository.obtener_producto(pool, uuid.UUID(simple["id"]))
    assert producto["costo_promedio"] == D("50")

    # Un reintento tardío de la salida no vuelve a descontar.
    await repository.registrar_salida_por_factura(pool, factura_id, items)
    assert await _existencias(pool, simple["id"]) == {"Principal": D("10")}

    # Kardex: "Reverso de venta".
    filas = (await kardex.obtener_kardex(pool, producto_id=uuid.UUID(simple["id"])))["filas"]
    assert [f["concepto"] for f in filas][-2:] == ["Venta", "Reverso de venta (e-CF rechazado)"]
