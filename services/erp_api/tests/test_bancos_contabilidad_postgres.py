"""Bancos y Contabilidad (fase 5) contra un Postgres REAL: ventas e-CF
aceptadas (crédito/contado, notas de crédito y débito), cobros, compras del
606 y pagos con retenciones, cuentas bancarias, asientos automáticos
(idempotentes, regenerados al cambiar una cuenta por defecto, anulados al
anular el documento), balanza cuadrada y coherente con el IT-1 / IR-17 y
las cuentas por cobrar/pagar, extracto CSV importado y conciliado
automáticamente, cierre de la conciliación, transferencias atómicas y
depreciación de activos fijos con la sugerencia del IR-2.

Se salta si no hay `CONTABILIDAD_TEST_DATABASE_URL` (DSN de una base con
permiso para crear bases, p. ej. `postgresql://postgres@localhost:5590/postgres`):
cada prueba crea una base nueva, carga `db/init/*.sql` y aplica las
migraciones de erp-api dos veces.
"""
import asyncio
import json
import os
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path

import asyncpg
import pytest

from services.erp_api.app import (
    bancos_repository as br,
    compras_repository as cr,
    contabilidad_repository as ctr,
    impuestos_repository as ir,
    precalculos,
    repository as vr,
)
from services.erp_api.app.bancos_repository import ConflictoBancosError
from services.erp_api.app.contabilidad_repository import ConflictoContabilidadError
from services.erp_api.app.migraciones import aplicar_migraciones
from services.erp_api.app.repository import DatosInvalidosError
from services.erp_api.app.schemas import FacturaCompraPayload

DSN = os.environ.get("CONTABILIDAD_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="sin CONTABILIDAD_TEST_DATABASE_URL (Postgres real)")

REPO = Path(__file__).resolve().parents[3]
D = Decimal
HOY = date(2026, 10, 23)
OCT = (date(2026, 10, 1), date(2026, 10, 31))


@pytest.fixture
async def pool(monkeypatch):
    ctr._CONTEOS_PENDIENTES.clear()
    for modulo in (vr, cr, br, ctr, ir):
        monkeypatch.setattr(modulo, "hoy", lambda: HOY)
    nombre = f"contab_{uuid.uuid4().hex[:10]}"
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
    await aplicar_migraciones(p)  # idempotente: no duplica el catálogo
    yield p
    await p.close()
    admin = await asyncpg.connect(DSN)
    await admin.execute(f"DROP DATABASE IF EXISTS {nombre} WITH (FORCE)")
    await admin.close()


# --- Datos de prueba ---------------------------------------------------------

def ecf_venta(tipo, encf, fecha, *, items, tipo_pago="2", rnc="131000001", nombre="Constructora Hispaniola, SRL",
              ncf_modificado=None):
    """e-CF como lo arma el frontend: líneas con IndicadorFacturacion (1 = 18 %, 4 = exento) y totales globales."""
    gravado = sum((D(m) for ind, m in items if ind == "1"), D(0))
    exento = sum((D(m) for ind, m in items if ind == "4"), D(0))
    itbis = (gravado * D("0.18")).quantize(D("0.01"))
    datos = {
        "Encabezado": {
            "IdDoc": {"TipoeCF": str(tipo), "eNCF": encf, "TipoPago": tipo_pago},
            "Emisor": {"RNCEmisor": "131897542", "FechaEmision": fecha},
            "Comprador": {"RNCComprador": rnc, "RazonSocialComprador": nombre},
            "Totales": {"MontoGravadoTotal": f"{gravado:.2f}", "MontoExento": f"{exento:.2f}",
                        "TotalITBIS": f"{itbis:.2f}", "MontoTotal": f"{gravado + exento + itbis:.2f}"},
        },
        "DetallesItems": {"Item": [
            {"NumeroLinea": str(i), "IndicadorFacturacion": ind, "NombreItem": "Producto", "MontoItem": m}
            for i, (ind, m) in enumerate(items, start=1)
        ]},
    }
    if ncf_modificado:
        datos["InformacionReferencia"] = {"NCFModificado": ncf_modificado, "CodigoModificacion": "3"}
    return datos


async def insertar_factura(pool, tipo, encf, fecha, *, estado="aceptado", **kwargs):
    return await pool.fetchval(
        """
        INSERT INTO facturacion.facturas (tipo_ecf, ambiente, rnc_emisor, encf, estado, datos)
        VALUES ($1, 'test', '131897542', $2, $3, $4::json) RETURNING id
        """,
        tipo, encf, estado, json.dumps(ecf_venta(tipo, encf, fecha, **kwargs)),
    )


async def cuenta_bancaria(pool, banco, tipo="corriente", numero="001-234471-1", saldo="500000", **extra):
    return await br.crear_cuenta(
        pool, nombre=extra.pop("nombre", None), banco=banco, tipo=tipo, numero=numero, moneda=extra.pop("moneda", "DOP"),
        saldo_inicial=D(saldo), fecha_saldo_inicial=date(2026, 10, 1), cuenta_contable_id=None, activa=True, notas=None,
        **extra,
    )


async def compra(pool, ncf, tipo_bs, servicios="0", bienes="0", itbis="0", rnc="101672919", nombre="Proveedor, SRL"):
    return await cr.crear_factura_compra(pool, **FacturaCompraPayload(
        proveedor_rnc=rnc, proveedor_nombre=nombre, ncf=ncf, fecha_comprobante=date(2026, 10, 5),
        tipo_bienes_servicios=tipo_bs, monto_servicios=D(servicios), monto_bienes=D(bienes), itbis_facturado=D(itbis),
    ).model_dump())


async def pagar(pool, registro_id, monto, *, cuenta=None, referencia=None, **ret):
    return await cr.registrar_pago(
        pool, registro606_id=uuid.UUID(str(registro_id)), fecha=date(2026, 10, 20), monto=D(monto),
        metodo="transferencia", cuenta_origen=None, referencia=referencia, notas=None,
        tipo_retencion_isr=ret.get("tipo"), retencion_isr=D(ret.get("isr", "0")), retencion_itbis=D(ret.get("itbis", "0")),
        cuenta_bancaria_id=cuenta,
    )


async def cobrar(pool, factura_id, monto, fecha, *, cuenta=None, referencia=None, metodo="transferencia"):
    return await vr.registrar_cobro(
        pool, factura_id=factura_id, fecha=fecha, monto=D(monto), metodo=metodo, cuenta_destino=None,
        referencia=referencia, notas=None, cuenta_bancaria_id=cuenta,
    )


async def saldo_cuenta(pool, codigo, hasta=OCT[1]):
    cuentas = await ctr.listar_cuentas(pool, hasta=hasta)
    return next(c["saldo"] for c in cuentas if c["codigo"] == codigo)


async def escenario(pool):
    """Octubre 2026 de una distribuidora: ventas, cobros, compras, pagos y banco."""
    popular = await cuenta_bancaria(pool, "Banco Popular Dominicano", nombre="Banco Popular")
    banreservas = await cuenta_bancaria(pool, "Banreservas", numero="960-000932-0", saldo="0", nombre="Banreservas")
    f1 = await insertar_factura(pool, 31, "E310000000101", "05-10-2026", items=[("1", "100000.00")])
    await insertar_factura(pool, 32, "E320000000102", "08-10-2026", items=[("1", "10000.00"), ("4", "5000.00")],
                           tipo_pago="1", rnc="", nombre="")
    f3 = await insertar_factura(pool, 31, "E310000000103", "12-10-2026", items=[("1", "50000.00")])
    await insertar_factura(pool, 34, "E340000000104", "15-10-2026", items=[("1", "10000.00")],
                           ncf_modificado="E310000000101")
    await insertar_factura(pool, 33, "E330000000105", "16-10-2026", items=[("1", "2000.00")],
                           ncf_modificado="E310000000103")
    # Enviada pero sin aceptar: no se contabiliza (igual que el IT-1).
    await insertar_factura(pool, 31, "E310000000106", "17-10-2026", estado="enviado", items=[("1", "7000.00")])
    await cobrar(pool, f1, "50000", date(2026, 10, 10), cuenta=popular["id"], referencia="TRF-1001")
    await cobrar(pool, f3, "59000", date(2026, 10, 20), cuenta=popular["id"], referencia="TRF-1002")
    p1 = await compra(pool, "B0100000010", 2, servicios="20000", itbis="3600")
    p2 = await compra(pool, "B0100000011", 9, bienes="40000", itbis="7200")
    p3 = await compra(pool, "B0100000012", 3, servicios="30000", itbis="5400", rnc="00112345678", nombre="Juan Pérez")
    # Renglón del 606 sin clasificar: no se puede contabilizar.
    await pool.execute(
        """
        INSERT INTO compras606.registros (periodo, origen, rnc_cedula, tipo_id, ncf, fecha_comprobante,
                                          monto_servicios, itbis_facturado)
        VALUES ('202610', 'manual', '130000002', 1, 'B0100000099', '20261007', 1000, 180)
        """
    )
    await pagar(pool, p1["registro606_id"], "22520", cuenta=popular["id"], referencia="TRF-88190", itbis="1080")
    await pagar(pool, p3["registro606_id"], "27000", cuenta=popular["id"], referencia="CHK-1043", tipo=1, isr="3000",
                itbis="5400")
    await br.registrar_movimiento(pool, cuenta_id=popular["id"], fecha=date(2026, 10, 18), tipo="comision",
                                  monto=D("1250"), descripcion="Comisión manejo de cuenta", referencia="COM-10")
    await br.registrar_transferencia(pool, cuenta_origen_id=popular["id"], cuenta_destino_id=banreservas["id"],
                                     fecha=date(2026, 10, 21), monto=D("250000"), concepto="Fondos para nómina",
                                     referencia="TRF-2001")
    return {"popular": popular, "banreservas": banreservas, "f1": f1, "f3": f3, "p1": p1, "p2": p2, "p3": p3}


EXTRACTO_POPULAR = (
    "Banco Popular Dominicano;;;;;\n"
    "Estado de cuenta corriente;****4471;;;;\n"
    "Fecha Posteo;Descripción;No. Referencia;Débito;Crédito;Balance\n"
    "10/10/2026;TRANSF RECIBIDA CONSTRUCTORA HISPANIOLA;TRF-1001;;50,000.00;550,000.00\n"
    "18/10/2026;COMISION MANEJO CUENTA;COM-10;1,250.00;;548,750.00\n"
    "19/10/2026;CARGO SERVICIO ALERTAS SMS;SMS-77;150.00;;548,600.00\n"
    "20/10/2026;CHEQUE PAGADO 1043;CHK-1043;27,000.00;;521,600.00\n"
    "21/10/2026;TRANSF A BANRESERVAS;TRF-2001;250,000.00;;271,600.00\n"
    "21/10/2026;PAGO PROVEEDOR;TRF-88190;22,520.00;;249,080.00\n"
    ";;;Total débitos;;\n"
).encode("cp1252")


# --- Pruebas ---------------------------------------------------------

async def test_migraciones_sobre_una_base_de_la_fase_3():
    """Producción (Supabase) ya tiene ventas/compras con datos: las migraciones
    del arranque agregan bancos/contabilidad y la columna cuenta_bancaria_id."""
    nombre = f"fase3_{uuid.uuid4().hex[:10]}"
    admin = await asyncpg.connect(DSN)
    await admin.execute(f"CREATE DATABASE {nombre} TEMPLATE template0 ENCODING 'UTF8'")
    await admin.close()
    dsn = DSN.rsplit("/", 1)[0] + f"/{nombre}"
    try:
        conn = await asyncpg.connect(dsn)
        for archivo in sorted((REPO / "db" / "init").glob("*.sql")):
            if archivo.name < "011":
                await conn.execute(archivo.read_text(encoding="utf-8"))
        factura = await conn.fetchval(
            "INSERT INTO facturacion.facturas (tipo_ecf, ambiente, rnc_emisor, estado, datos)"
            " VALUES (31, 'test', '131897542', 'aceptado', '{}'::json) RETURNING id"
        )
        await conn.execute(
            "INSERT INTO ventas.cobros (factura_id, fecha, monto, metodo) VALUES ($1, '2026-10-01', 10, 'efectivo')",
            factura,
        )
        await conn.close()
        p = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
        await aplicar_migraciones(p)
        await aplicar_migraciones(p)
        assert await p.fetchval("SELECT count(*) FROM ventas.cobros WHERE cuenta_bancaria_id IS NULL") == 1
        assert await p.fetchval("SELECT count(*) FROM contabilidad.cuentas") == 79
        assert await p.fetchval("SELECT to_regclass('bancos.extracto_lineas') IS NOT NULL")
        await p.close()
    finally:
        admin = await asyncpg.connect(DSN)
        await admin.execute(f"DROP DATABASE IF EXISTS {nombre} WITH (FORCE)")
        await admin.close()


async def test_catalogo_sembrado_una_sola_vez(pool):
    assert await pool.fetchval("SELECT count(*) FROM contabilidad.cuentas") == 79
    assert await pool.fetchval("SELECT count(*) FROM contabilidad.cuentas_por_defecto") == 37
    huerfanas = await pool.fetchval(
        "SELECT count(*) FROM contabilidad.cuentas WHERE codigo LIKE '%.%' AND padre_id IS NULL"
    )
    assert huerfanas == 0


async def test_asientos_balanza_y_coherencia_con_impuestos(pool):
    datos = await escenario(pool)
    previo = await ctr.pendientes_de_contabilizar(pool, *OCT)
    resultado = await ctr.generar_asientos(pool, *OCT)
    # 5 e-CF aceptados + 2 cobros + 3 compras + 2 pagos + 1 comisión + 1 transferencia
    # + el ajuste por proporcionalidad del ITBIS (la venta exenta del E32).
    assert resultado["creados"] == 15 == previo["creados"]
    motivos = [n["motivo"] for n in resultado["no_contabilizados"]]
    assert len(motivos) == 1 and "clasifíquelo" in motivos[0]

    balanza = await ctr.balanza(pool, *OCT)
    assert balanza["cuadrada"]
    assert balanza["totales"]["debitos"] == balanza["totales"]["creditos"]

    # Coherencia con el IT-1 de octubre: ventas netas, ITBIS facturado, adelantado y retenido.
    async with pool.acquire() as conn:
        ventas, compras606, retenido = await ir._datos_it1(conn, "202610")
    resultados = await ctr.estado_resultados(pool, *OCT)
    assert resultados["ingresos"]["total"] == ventas.g18 + ventas.g16 + ventas.exportaciones + ventas.exentas == D("157000")
    assert await saldo_cuenta(pool, "2.1.02.01") == ventas.i18 + ventas.i16 == D("27360")
    # ITBIS adelantado = el admitido del IT-1 (mismo coeficiente de proporcionalidad); el renglón sin
    # clasificar sí está en el IT-1 (180) pero no se contabilizó.
    proporcionalidad = precalculos.itbis_adelantado_admitido(ventas, compras606)
    no_admitido = proporcionalidad.no_admitido
    assert no_admitido > 0 and proporcionalidad.proporcion < 1
    assert await saldo_cuenta(pool, "1.1.04.01") == proporcionalidad.admitido - D("180")
    it1 = await ir.precalculo(pool, "it1", "202610")
    casillas = {c["codigo"]: c["valor"] for sec in it1["secciones"] for c in sec["casillas"]}
    assert -casillas["11"] == proporcionalidad.admitido
    assert await saldo_cuenta(pool, "2.1.03.01") == retenido == D("6480")
    ir17 = await ir.precalculo(pool, "ir17", "202610")
    assert await saldo_cuenta(pool, "2.1.03.02") == ir17["resultado"]["valor"] == D("3000")
    assert resultados["costos"]["total"] == D("40000")
    assert resultados["gastos"]["total"] == D("50000") + D("1250") + no_admitido
    assert resultados["utilidad_neta"] == D("157000") - D("40000") - D("51250") - no_admitido

    # CxC y CxP del mayor = lo pendiente de Ventas y Compras.
    # (sin la factura solo enviada: la CxC la cuenta, pero sin aceptar no se contabiliza)
    cxc = sum((c["pendiente"] for c in await vr.listar_cuentas_por_cobrar(pool) if c["encf"] != "E310000000106"), D(0))
    assert await saldo_cuenta(pool, "1.1.02.01") == cxc == D("58560")
    cxp = sum((c["pendiente"] for c in await cr.listar_cuentas_por_pagar(pool) if c["ncf"] != "B0100000099"), D(0))
    assert await saldo_cuenta(pool, "2.1.01.01") == cxp == D("47200")
    # Banco (cuenta por defecto 'banco') = saldo de las cuentas bancarias − saldos iniciales (no asentados).
    cuentas = await br.listar_cuentas(pool)
    assert await saldo_cuenta(pool, "1.1.01.03") == sum((c["saldo_actual"] - c["saldo_inicial"] for c in cuentas), D(0))
    # Caja: venta al contado.
    assert await saldo_cuenta(pool, "1.1.01.01") == D("16800")

    balance = await ctr.balance_general(pool, OCT[1])
    assert balance["cuadrado"]
    assert balance["resultado_ejercicio"] == resultados["utilidad_neta"]

    # Idempotente.
    otra = await ctr.generar_asientos(pool, *OCT)
    assert (otra["creados"], otra["actualizados"], otra["anulados"], otra["sin_cambios"]) == (0, 0, 0, 15)

    # Cambiar una cuenta por defecto regenera los asientos que la usan.
    otras = await ctr.listar_cuentas(pool)
    deducciones = next(c for c in otras if c["codigo"] == "6.1.06")
    await ctr.actualizar_cuentas_por_defecto(pool, {"gasto_606_02": deducciones["id"]})
    cambio = await ctr.generar_asientos(pool, *OCT)
    assert (cambio["creados"], cambio["actualizados"]) == (0, 1)
    assert await saldo_cuenta(pool, "6.1.02") == 0 and await saldo_cuenta(pool, "6.1.06") == D("20000") + no_admitido

    # Anular un cobro anula su asiento.
    cobro = await cobrar(pool, datos["f1"], "1000", date(2026, 10, 22), metodo="efectivo")
    assert (await ctr.generar_asientos(pool, *OCT))["creados"] == 1
    await vr.anular_cobro(pool, uuid.UUID(str(cobro["id"])))
    anulacion = await ctr.generar_asientos(pool, *OCT)
    assert anulacion["anulados"] == 1
    assert (await ctr.balanza(pool, *OCT))["cuadrada"]

    # Los automáticos no se anulan a mano.
    automatico = (await ctr.listar_asientos(pool, origen="compra"))[0]
    with pytest.raises(ConflictoContabilidadError):
        await ctr.anular_asiento(pool, automatico["id"])


async def test_falta_cuenta_por_defecto_va_al_reporte(pool):
    await insertar_factura(pool, 31, "E310000000201", "05-10-2026", items=[("1", "1000.00")])
    await ctr.actualizar_cuentas_por_defecto(pool, {"itbis_por_pagar": None})
    resultado = await ctr.generar_asientos(pool, *OCT)
    assert resultado["creados"] == 0
    assert "ITBIS por pagar" in resultado["no_contabilizados"][0]["motivo"]


async def test_asiento_manual(pool):
    cuentas = {c["codigo"]: c["id"] for c in await ctr.listar_cuentas(pool)}
    borrador = await ctr.crear_asiento(pool, fecha=date(2026, 10, 1), concepto="Aporte de capital", referencia=None,
                                       contabilizar=False, lineas=[
                                           {"cuenta_id": cuentas["1.1.01.03"], "debito": D("1000000")},
                                           {"cuenta_id": cuentas["3.1.01"], "credito": D("900000")},
                                       ])
    assert borrador["estado"] == "borrador" and not borrador["cuadrado"]
    with pytest.raises(DatosInvalidosError, match="no cuadra"):
        await ctr.contabilizar_asiento(pool, borrador["id"])
    with pytest.raises(DatosInvalidosError, match="grupo"):
        await ctr.crear_asiento(pool, fecha=None, concepto="x", referencia=None, contabilizar=True, lineas=[
            {"cuenta_id": cuentas["1.1.01"], "debito": D("1")}, {"cuenta_id": cuentas["3.1.01"], "credito": D("1")}])
    asiento = await ctr.actualizar_asiento(pool, borrador["id"], fecha=date(2026, 10, 1), concepto="Aporte de capital",
                                           referencia="ACTA-1", contabilizar=True, lineas=[
                                               {"cuenta_id": cuentas["1.1.01.03"], "debito": D("1000000")},
                                               {"cuenta_id": cuentas["3.1.01"], "credito": D("1000000")},
                                           ])
    assert asiento["estado"] == "contabilizado" and asiento["numero"].startswith("AS-")
    assert await saldo_cuenta(pool, "3") == D("1000000")
    anulado = await ctr.anular_asiento(pool, asiento["id"], "Error de digitación")
    assert anulado["estado"] == "anulado" and await saldo_cuenta(pool, "3") == 0
    # Catálogo: una subcuenta convierte a su padre en cuenta de grupo; no se borra con movimientos.
    nueva = await ctr.crear_cuenta(pool, codigo="1.1.02.02.01", nombre="Préstamos a empleados")
    assert nueva["tipo"] == "activo" and nueva["padre_codigo"] == "1.1.02.02"
    padre = await ctr.obtener_cuenta(pool, cuentas["1.1.02.02"])
    assert not padre["acepta_movimientos"] and padre["hijos"] == 1
    with pytest.raises(ConflictoContabilidadError, match="tiene movimientos"):
        await ctr.crear_cuenta(pool, codigo="1.1.01.03.01", nombre="Banco Popular ****4471")
    with pytest.raises(ConflictoContabilidadError, match="cuenta por defecto"):
        await ctr.crear_cuenta(pool, codigo="1.1.01.02.01", nombre="Caja chica sucursal")
    with pytest.raises(ConflictoContabilidadError):
        await ctr.eliminar_cuenta(pool, cuentas["1.1.01.03"])
    await ctr.eliminar_cuenta(pool, nueva["id"])
    assert (await ctr.obtener_cuenta(pool, cuentas["1.1.02.02"]))["acepta_movimientos"]


async def test_extracto_conciliacion_automatica_y_cierre(pool):
    datos = await escenario(pool)
    popular = datos["popular"]
    vista = br.previsualizar_extracto(EXTRACTO_POPULAR, None)
    assert vista["mapeo"]["fecha"] == "Fecha Posteo" and vista["mapeo"]["debito"] == "Débito"
    assert vista["total_lineas"] == 6

    importado = await br.importar_extracto(pool, popular["id"], EXTRACTO_POPULAR, nombre_archivo="popular.csv",
                                           mapeo=None, saldo_final=None)
    assert (importado["importadas"], importado["duplicadas"]) == (6, 0)
    otra_vez = await br.importar_extracto(pool, popular["id"], EXTRACTO_POPULAR, nombre_archivo="popular.csv",
                                          mapeo=None, saldo_final=None)
    assert (otra_vez["importadas"], otra_vez["duplicadas"], otra_vez["extracto"]) == (0, 6, None)

    sugerencias = await br.sugerencias(pool, popular["id"], dias=3)
    assert len(sugerencias) == 5
    pago = next(s for s in sugerencias if s["linea"]["referencia"] == "TRF-88190")
    assert pago["movimiento"]["origen"] == "pago" and "1 día" in pago["motivo"]
    auto = await br.conciliar_automatico(pool, popular["id"], dias=3)
    assert auto["conciliadas"] == 5

    estado = await br.conciliacion(pool, popular["id"], hasta=date(2026, 10, 21))
    assert estado["resumen"]["lineas_pendientes"] == 1 and not estado["resumen"]["puede_cerrar"]
    with pytest.raises(ConflictoBancosError, match="sin conciliar"):
        await br.cerrar_conciliacion(pool, popular["id"], hasta=date(2026, 10, 21), saldo_banco=None, notas=None)

    sms = next(x for x in estado["lineas"] if x["referencia"] == "SMS-77")
    linea = await br.crear_movimiento_desde_linea(pool, sms["id"])
    assert linea["conciliada"] and linea["movimiento"]["tipo"] == "cargo_bancario"

    estado = await br.conciliacion(pool, popular["id"], hasta=date(2026, 10, 21))
    resumen = estado["resumen"]
    # El cobro de 59,000 del 20/10 no está en el extracto: partida en tránsito.
    assert resumen["transito_entradas"] == D("59000") and resumen["saldo_banco"] == D("249080")
    assert resumen["saldo_libros"] == D("308080") and resumen["diferencia"] == 0 and resumen["puede_cerrar"]
    with pytest.raises(ConflictoBancosError, match="no cuadra"):
        await br.cerrar_conciliacion(pool, popular["id"], hasta=date(2026, 10, 21), saldo_banco=D("250000"), notas=None)
    cerrada = await br.cerrar_conciliacion(pool, popular["id"], hasta=date(2026, 10, 21), saldo_banco=None, notas=None)
    assert cerrada["saldo_banco"] == D("249080") and cerrada["movimientos_en_transito"] == 1

    cuenta = await br.obtener_cuenta(pool, popular["id"])
    assert cuenta["conciliada_hasta"] == date(2026, 10, 21) and cuenta["numero_enmascarado"] == "****4711"
    with pytest.raises(ConflictoBancosError, match="conciliada hasta"):
        await br.registrar_movimiento(pool, cuenta_id=popular["id"], fecha=date(2026, 10, 15), tipo="deposito",
                                      monto=D("10"))
    with pytest.raises(ConflictoBancosError):
        await br.desconciliar_linea(pool, sms["id"])
    # Reabrir y volver a cerrar.
    await br.reabrir_conciliacion(pool, cerrada["id"])
    await br.cerrar_conciliacion(pool, popular["id"], hasta=date(2026, 10, 21), saldo_banco=D("249080"), notas="ok")

    # El cargo bancario creado desde la línea también se contabiliza.
    generado = await ctr.generar_asientos(pool, *OCT)
    assert generado["creados"] == 16 and (await ctr.balanza(pool, *OCT))["cuadrada"]


async def test_transferencia_atomica(pool, monkeypatch):
    origen = await cuenta_bancaria(pool, "Banco Popular Dominicano")
    destino = await cuenta_bancaria(pool, "Banreservas", numero="960-000932-0", saldo="0")
    llamadas = {"n": 0}
    original = br._nombre_cuenta

    def falla_en_la_segunda_pata(fila):
        llamadas["n"] += 1
        if llamadas["n"] == 2:
            raise RuntimeError("se cayó la conexión")
        return original(fila)

    monkeypatch.setattr(br, "_nombre_cuenta", falla_en_la_segunda_pata)
    with pytest.raises(RuntimeError):
        await br.registrar_transferencia(pool, cuenta_origen_id=origen["id"], cuenta_destino_id=destino["id"],
                                         fecha=date(2026, 10, 21), monto=D("1000"))
    monkeypatch.setattr(br, "_nombre_cuenta", original)
    assert await pool.fetchval("SELECT count(*) FROM bancos.transferencias") == 0
    assert await pool.fetchval("SELECT count(*) FROM bancos.movimientos") == 0

    t = await br.registrar_transferencia(pool, cuenta_origen_id=origen["id"], cuenta_destino_id=destino["id"],
                                         fecha=date(2026, 10, 21), monto=D("1000"), concepto="Fondo")
    saldos = {c["id"]: c["saldo_actual"] for c in await br.listar_cuentas(pool)}
    assert (saldos[origen["id"]], saldos[destino["id"]]) == (D("499000"), D("1000"))
    await br.anular_transferencia(pool, t["id"])
    saldos = {c["id"]: c["saldo_actual"] for c in await br.listar_cuentas(pool)}
    assert (saldos[origen["id"]], saldos[destino["id"]]) == (D("500000"), D("0"))
    assert await pool.fetchval("SELECT count(*) FROM bancos.movimientos WHERE NOT anulado") == 0
    with pytest.raises(ConflictoBancosError):
        await br.anular_transferencia(pool, t["id"])
    # Cuenta en dólares: exige el monto que llega.
    usd = await cuenta_bancaria(pool, "Banco BHD", numero="2210", saldo="0", moneda="USD")
    with pytest.raises(DatosInvalidosError, match="monto que llega"):
        await br.registrar_transferencia(pool, cuenta_origen_id=origen["id"], cuenta_destino_id=usd["id"],
                                         fecha=date(2026, 10, 21), monto=D("60000"))
    cambio = await br.registrar_transferencia(pool, cuenta_origen_id=origen["id"], cuenta_destino_id=usd["id"],
                                              fecha=date(2026, 10, 21), monto=D("60000"), monto_destino=D("1000"))
    assert cambio["tasa_cambio"] == D("60.0000")
    # Un cobro en RD$ no entra a una cuenta en dólares.
    factura = await insertar_factura(pool, 31, "E310000000301", "05-10-2026", items=[("1", "1000.00")])
    with pytest.raises(DatosInvalidosError, match="USD"):
        await cobrar(pool, factura, "100", date(2026, 10, 10), cuenta=usd["id"])


async def test_depreciacion_y_sugerencia_del_ir2(pool):
    camion = await ctr.crear_activo(pool, descripcion="Camión Isuzu NPR 2024", categoria=2,
                                    fecha_adquisicion=date(2026, 1, 15), costo=D("3850000"))
    assert camion["depreciar_desde"] == "202602" and camion["tasa_dgii"] == D("25")
    await ctr.crear_activo(pool, descripcion="Nave de almacén", categoria=1, fecha_adquisicion=date(2026, 3, 10),
                           costo=D("1000000"))
    await ctr.crear_activo(pool, descripcion="Equipos de cómputo", categoria=2, fecha_adquisicion=date(2026, 6, 2),
                           costo=D("640000"), valor_residual=D("40000"), metodo="linea_recta", vida_util_meses=36)
    vista = await ctr.depreciacion_del_periodo(pool, "202610")
    # 3,850,000 × 25 % / 12 + 1,000,000 × 5 % / 12 + (640,000 − 40,000) / 36
    assert vista["total"] == D("80208.33") + D("4166.67") + D("16666.67")
    assert vista["advertencias"]  # meses anteriores sin registrar
    resultado = await ctr.registrar_depreciacion(pool, "202610")
    assert resultado["asiento"]["total_debito"] == vista["total"]
    with pytest.raises(ConflictoContabilidadError, match="ya está registrada"):
        await ctr.registrar_depreciacion(pool, "202610")
    assert await saldo_cuenta(pool, "6.1.08") == vista["total"]
    assert await saldo_cuenta(pool, "1.2.02.02") == D("80208.33") + D("16666.67")
    with pytest.raises(ConflictoContabilidadError):
        await ctr.actualizar_activo(pool, camion["id"], {"costo": D("4000000")})

    # Depreciación fiscal 2026 (Art. 287 para todos): sugerencia del IR-2. Categoría 2 en una cuenta
    # de categoría (25 % de la base del año, sin prorrateo); categoría 1 por activo, por los meses en uso.
    fiscal = await ctr.obtener_depreciacion_fiscal(pool, "2026")
    esperado = (D("3850000") + D("640000")) * D("0.25") + D("4166.67") * 9
    assert fiscal["total"] == esperado
    assert [(c["categoria"], c["base"], c["valor_final"]) for c in fiscal["categorias"]] == [
        (2, D("4490000"), D("3367500"))]
    ir2 = await ir.precalculo(pool, "ir2", "2026")
    ajuste = next(a for a in ir2["ajustes"] if a["clave"] == "depreciacion")
    assert (ajuste["sugerido"], ajuste["valor"], ajuste["manual"]) == (esperado, esperado, False)
    gastos = next(s for s in ir2["secciones"] if s["numero"] == "III")
    assert any(c["etiqueta"] == "Depreciación" and c["valor"] == -esperado for c in gastos["casillas"])
    # El valor manual guardado sigue mandando.
    manual = await ir.guardar_ajustes(pool, "ir2", "2026", {"depreciacion": "12000"})
    ajuste = next(a for a in manual["ajustes"] if a["clave"] == "depreciacion")
    assert (ajuste["valor"], ajuste["manual"], ajuste["sugerido"]) == (D("12000"), True, esperado)

    # Anular la depreciación del mes permite recalcularla.
    await ctr.anular_asiento(pool, resultado["asiento"]["id"])
    assert (await ctr.depreciacion_del_periodo(pool, "202610"))["registrada"] is False
    await ctr.registrar_depreciacion(pool, "202610")


# --- Revisión de la fase 5 ---------------------------------------------------------

SEP = (date(2026, 9, 1), date(2026, 9, 30))


async def test_retenciones_del_606_clasificadas_antes_de_pagar(pool):
    """Retenciones en el renglón del 606 (p. ej. un E41) sin pago que las lleve: sin pagos van en la
    compra; pagada (aunque sea otro mes), las lleva el último pago. CxP y 2.1.03.x = Compras / IT-1 / IR-17."""
    p = await cr.crear_factura_compra(pool, **FacturaCompraPayload(
        proveedor_rnc="00112345678", proveedor_nombre="Informal", ncf="B0100000050", fecha_comprobante=date(2026, 9, 5),
        tipo_bienes_servicios=2, monto_servicios=D("10000"), itbis_facturado=D("1800"),
    ).model_dump())
    rid = uuid.UUID(str(p["registro606_id"]))
    await pool.execute(
        "UPDATE compras606.registros SET itbis_retenido = 1800, tipo_retencion_isr = 2, monto_retencion_renta = 1000"
        " WHERE id = $1", rid,
    )
    await ctr.generar_asientos(pool, *SEP)

    async def cxp_compras():
        return sum((c["pendiente"] for c in await cr.listar_cuentas_por_pagar(pool) if c["ncf"] == "B0100000050"), D(0))

    assert await saldo_cuenta(pool, "2.1.01.01") == await cxp_compras() == D("9000")
    assert (await saldo_cuenta(pool, "2.1.03.01"), await saldo_cuenta(pool, "2.1.03.02")) == (D("1800"), D("1000"))

    # Pagado en octubre: generar octubre regenera la compra de septiembre y el pago lleva las retenciones.
    pago = await pagar(pool, rid, "9000")
    cambio = await ctr.generar_asientos(pool, *OCT)
    assert (cambio["creados"], cambio["actualizados"]) == (1, 1)
    assert await saldo_cuenta(pool, "2.1.01.01") == await cxp_compras() == 0
    assert await saldo_cuenta(pool, "2.1.03.01", hasta=SEP[1]) == 0
    async with pool.acquire() as conn:
        _, _, retenido = await ir._datos_it1(conn, "202610")
    assert await saldo_cuenta(pool, "2.1.03.01") == retenido == D("1800")
    ir17 = await ir.precalculo(pool, "ir17", "202610")
    assert await saldo_cuenta(pool, "2.1.03.02") == ir17["resultado"]["valor"] == D("1000")
    assert (await ctr.generar_asientos(pool, *OCT))["sin_cambios"] == 2  # idempotente

    # Anulado el pago, las vuelve a llevar la compra.
    await cr.anular_pago(pool, uuid.UUID(str(pago["id"])))
    anulacion = await ctr.generar_asientos(pool, *OCT)
    assert (anulacion["anulados"], anulacion["actualizados"]) == (1, 1)
    assert await saldo_cuenta(pool, "2.1.01.01") == await cxp_compras() == D("9000")
    assert await saldo_cuenta(pool, "2.1.03.01") == D("1800")
    assert (await ctr.balanza(pool, SEP[0], OCT[1]))["cuadrada"]


async def test_anular_cobro_o_pago_conciliado(pool):
    popular = await cuenta_bancaria(pool, "Banco Popular Dominicano", nombre="Banco Popular", saldo="0")
    factura = await insertar_factura(pool, 31, "E310000000201", "05-10-2026", items=[("1", "100000.00")])
    cobro = await cobrar(pool, factura, "50000", date(2026, 10, 10), cuenta=popular["id"], referencia="TRF-1001")
    p = await compra(pool, "B0100000060", 2, servicios="1000", itbis="180")
    pago = await pagar(pool, p["registro606_id"], "1180", cuenta=popular["id"], referencia="CHK-1")
    csv = (
        "Fecha;Descripcion;Referencia;Debito;Credito;Balance\n"
        "10/10/2026;TRANSF;TRF-1001;;50,000.00;50,000.00\n"
        "20/10/2026;CHEQUE;CHK-1;1,180.00;;48,820.00\n"
    ).encode()
    await br.importar_extracto(pool, popular["id"], csv, nombre_archivo="a.csv", mapeo=None, saldo_final=None)
    assert (await br.conciliar_automatico(pool, popular["id"]))["conciliadas"] == 2
    cerrada = await br.cerrar_conciliacion(pool, popular["id"], hasta=date(2026, 10, 15), saldo_banco=None, notas=None)

    # Conciliado en la conciliación cerrada: 409 (no se descuadra el período cerrado).
    with pytest.raises(vr.CobroEstadoInvalidoError, match="conciliación cerrada al 15/10/2026"):
        await vr.anular_cobro(pool, uuid.UUID(str(cobro["id"])))
    # Conciliado en el período abierto: se anula y su línea queda sin conciliar.
    await cr.anular_pago(pool, uuid.UUID(str(pago["id"])))
    assert (await br.obtener_cuenta(pool, popular["id"]))["lineas_sin_conciliar"] == 1
    assert await pool.fetchval("SELECT count(*) FROM bancos.extracto_lineas WHERE mov_id IS NOT NULL") == 1
    # Reabierta, el cobro se anula igual.
    await br.reabrir_conciliacion(pool, cerrada["id"])
    await vr.anular_cobro(pool, uuid.UUID(str(cobro["id"])))
    assert (await br.obtener_cuenta(pool, popular["id"]))["lineas_sin_conciliar"] == 2
    assert await pool.fetchval("SELECT count(*) FROM bancos.extracto_lineas WHERE mov_id IS NOT NULL") == 0


async def test_estado_de_resultados_con_dos_cuentas_raiz_del_mismo_tipo(pool):
    await ctr.crear_cuenta(pool, codigo="7", nombre="Otros gastos no deducibles", tipo="gasto")
    multas = await ctr.crear_cuenta(pool, codigo="7.1", nombre="Multas")
    cuentas = {c["codigo"]: c["id"] for c in await ctr.listar_cuentas(pool)}
    await ctr.crear_asiento(pool, fecha=date(2026, 10, 5), concepto="Gastos", referencia=None, contabilizar=True, lineas=[
        {"cuenta_id": cuentas["6.1.02"], "debito": D("100")}, {"cuenta_id": multas["id"], "debito": D("50")},
        {"cuenta_id": cuentas["1.1.01.01"], "credito": D("150")}])
    resultados = await ctr.estado_resultados(pool, *OCT)
    assert resultados["gastos"]["total"] == D("150") and resultados["utilidad_neta"] == D("-150")
    balance = await ctr.balance_general(pool, OCT[1])
    assert balance["cuadrado"] and balance["activos"]["total"] == D("-150")


async def test_migraciones_no_bloquean_cobros_y_pagos_en_cada_arranque(pool):
    """Con una lectura en curso sobre ventas.cobros y compras.pagos (AccessShareLock), las migraciones
    terminan: no hay ALTER TABLE (AccessExclusiveLock) cuando la columna ya existe."""
    otra = await pool.acquire()
    try:
        transaccion = otra.transaction()
        await transaccion.start()
        await otra.fetch("SELECT 1 FROM ventas.cobros LIMIT 1")
        await otra.fetch("SELECT 1 FROM compras.pagos LIMIT 1")
        await asyncio.wait_for(aplicar_migraciones(pool, reintentos=1), timeout=20)
        await transaccion.rollback()
    finally:
        await pool.release(otra)
    # Una base con el DDL anterior de la fase 5 (sin valor_venta ni el origen 'proporcionalidad').
    await pool.execute("ALTER TABLE contabilidad.activos_fijos DROP COLUMN valor_venta")
    await pool.execute("ALTER TABLE contabilidad.asientos DROP CONSTRAINT asientos_origen_check")
    await pool.execute("ALTER TABLE contabilidad.asientos ADD CONSTRAINT asientos_origen_check CHECK (origen <> 'x')")
    await aplicar_migraciones(pool)
    definicion = await pool.fetchval(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'asientos_origen_check'"
    )
    assert "proporcionalidad" in definicion
    assert await pool.fetchval(
        "SELECT count(*) FROM information_schema.columns WHERE table_name = 'activos_fijos' AND column_name = 'valor_venta'"
    ) == 1


async def test_cobro_de_factura_sin_aceptar_no_se_contabiliza(pool):
    factura = await insertar_factura(pool, 31, "E310000000301", "05-10-2026", estado="enviado", items=[("1", "1000.00")])
    await cobrar(pool, factura, "500", date(2026, 10, 10), metodo="efectivo")
    resultado = await ctr.generar_asientos(pool, *OCT)
    assert resultado["creados"] == 0
    assert [n["origen"] for n in resultado["no_contabilizados"]] == ["cobro"]
    assert "no está aceptada" in resultado["no_contabilizados"][0]["motivo"]
    await pool.execute("UPDATE facturacion.facturas SET estado = 'aceptado' WHERE id = $1", factura)
    assert (await ctr.generar_asientos(pool, *OCT))["creados"] == 2  # la venta y el cobro
    assert await saldo_cuenta(pool, "1.1.02.01") == D("680")
    # Si la factura deja de estar aceptada, el asiento del cobro se anula junto con el de la venta.
    await pool.execute("UPDATE facturacion.facturas SET estado = 'rechazado' WHERE id = $1", factura)
    assert (await ctr.generar_asientos(pool, *OCT))["anulados"] == 2
    assert await saldo_cuenta(pool, "1.1.02.01") == 0 and await saldo_cuenta(pool, "1.1.01.01") == 0


async def test_saldo_del_extracto_con_las_lineas_mas_recientes_primero(pool):
    popular = await cuenta_bancaria(pool, "Banco Popular Dominicano", nombre="Banco Popular", saldo="500000")
    await br.registrar_movimiento(pool, cuenta_id=popular["id"], fecha=date(2026, 10, 10), tipo="deposito",
                                  monto=D("1000"), referencia="D-1")
    await br.registrar_movimiento(pool, cuenta_id=popular["id"], fecha=date(2026, 10, 10), tipo="retiro",
                                  monto=D("300"), referencia="R-1")
    # El banco lista primero lo último: el saldo del día es el de la PRIMERA fila del archivo.
    csv = (
        "Fecha;Descripcion;Referencia;Debito;Credito;Balance\n"
        "10/10/2026;RETIRO;R-1;300.00;;500,700.00\n"
        "10/10/2026;DEPOSITO;D-1;;1,000.00;501,000.00\n"
    ).encode()
    await br.importar_extracto(pool, popular["id"], csv, nombre_archivo="b.csv", mapeo=None, saldo_final=None)
    await br.conciliar_automatico(pool, popular["id"])
    resumen = (await br.conciliacion(pool, popular["id"], hasta=date(2026, 10, 10)))["resumen"]
    assert (resumen["saldo_banco"], resumen["origen_saldo_banco"]) == (D("500700"), "ultima_linea")
    assert resumen["diferencia"] == 0 and resumen["puede_cerrar"] and resumen["advertencias"] == []


async def test_activos_no_cambian_un_ir2_presentado(pool):
    camion = await ctr.crear_activo(pool, descripcion="Camión", categoria=2, fecha_adquisicion=date(2026, 1, 15),
                                    costo=D("1000000"))
    await ir.marcar_presentada(pool, "ir2", "2026", monto=None, referencia=None, presentada_en=None)
    with pytest.raises(ConflictoContabilidadError, match="IR-2"):
        await ctr.crear_activo(pool, descripcion="Laptop", categoria=2, fecha_adquisicion=date(2026, 3, 1),
                               costo=D("80000"))
    with pytest.raises(ConflictoContabilidadError, match="IR-2"):
        await ctr.dar_baja_activo(pool, camion["id"], date(2026, 9, 30), D("700000"))
    with pytest.raises(ConflictoContabilidadError, match="IR-2"):
        await ctr.actualizar_activo(pool, camion["id"], {"costo": D("1100000")})
    with pytest.raises(ConflictoContabilidadError, match="IR-2"):
        await ctr.eliminar_activo(pool, camion["id"])
    # Lo que no es fiscal sí se cambia.
    assert (await ctr.actualizar_activo(pool, camion["id"], {"ubicacion": "Almacén"}))["ubicacion"] == "Almacén"
    await ir.desmarcar_presentada(pool, "ir2", "2026")
    vendido = await ctr.dar_baja_activo(pool, camion["id"], date(2026, 9, 30), D("700000"))
    assert vendido["valor_venta"] == D("700000")
    # Vendido por menos que la cuenta: base 300,000 × 25 %.
    fiscal = await ctr.obtener_depreciacion_fiscal(pool, "2026")
    assert fiscal["total"] == D("75000") and fiscal["ingreso_gravable"] == 0
    await ctr.dar_baja_activo(pool, camion["id"], date(2026, 9, 30), D("1200000"))
    ir2 = await ir.precalculo(pool, "ir2", "2026")
    assert any("renta gravable" in a for a in ir2["advertencias"])


async def test_ajuste_por_proporcionalidad_mensual(pool):
    await insertar_factura(pool, 31, "E310000000401", "05-10-2026", items=[("1", "60000.00"), ("4", "40000.00")])
    await compra(pool, "B0100000070", 2, servicios="10000", itbis="1800")
    resultado = await ctr.generar_asientos(pool, *OCT)
    ajuste = (await ctr.listar_asientos(pool, origen="proporcionalidad"))
    assert resultado["creados"] == 3 and len(ajuste) == 1 and ajuste[0]["fecha"] == date(2026, 10, 31)
    # 60 % admitido: 1,800 × 40 % = 720 no admitido.
    assert await saldo_cuenta(pool, "1.1.04.01") == D("1080") and await saldo_cuenta(pool, "6.1.06") == D("720")
    assert (await ctr.generar_asientos(pool, *OCT))["sin_cambios"] == 3
    # Opcional: sin cuenta por defecto `itbis_no_admitido`, no se genera (y se anula el que había).
    await ctr.actualizar_cuentas_por_defecto(pool, {"itbis_no_admitido": None})
    assert (await ctr.generar_asientos(pool, *OCT))["anulados"] == 1
    assert await saldo_cuenta(pool, "1.1.04.01") == D("1800")


async def test_resumen_reutiliza_el_conteo_de_pendientes(pool, monkeypatch):
    await insertar_factura(pool, 31, "E310000000501", "05-10-2026", items=[("1", "1000.00")])
    llamadas = {"n": 0}
    original = ctr.pendientes_de_contabilizar

    async def contar(*args, **kwargs):
        llamadas["n"] += 1
        return await original(*args, **kwargs)

    monkeypatch.setattr(ctr, "pendientes_de_contabilizar", contar)
    assert (await ctr.resumen(pool, *OCT))["documentos_por_contabilizar"] == 1
    assert (await ctr.resumen(pool, *OCT))["documentos_por_contabilizar"] == 1
    assert llamadas["n"] == 1
    await ctr.generar_asientos(pool, *OCT)  # vacía el conteo guardado
    assert (await ctr.resumen(pool, *OCT))["documentos_por_contabilizar"] == 0
    assert llamadas["n"] == 2
