"""Asientos propuestos desde los documentos y depreciación (funciones puras)."""
from datetime import date
from decimal import Decimal

import pytest

from services.erp_api.app import contabilidad_calculos as cc
from services.erp_api.app.contabilidad_calculos import NoContabilizableError

D = Decimal


def _lados(asiento):
    debe = {x.clave: x.debito for x in asiento.lineas if x.debito}
    haber = {x.clave: x.credito for x in asiento.lineas if x.credito}
    return debe, haber


def _cuadra(asiento):
    return sum(x.debito for x in asiento.lineas) == sum(x.credito for x in asiento.lineas) > 0


def _ecf(items, tipo_pago="2", totales=None):
    return {
        "Encabezado": {"IdDoc": {"TipoPago": tipo_pago}, "Comprador": {"RazonSocialComprador": "Cliente"},
                       "Totales": totales or {}},
        "DetallesItems": {"Item": [{"IndicadorFacturacion": i, "MontoItem": m} for i, m in items]},
    }


def test_catalogo_sugerido_consistente():
    codigos = {c for c, *_ in cc.CATALOGO_SUGERIDO}
    for codigo, _, _, acepta in cc.CATALOGO_SUGERIDO:
        padre = cc.codigo_padre(codigo)
        assert padre is None or padre in codigos, codigo
        hijos = [c for c in codigos if cc.codigo_padre(c) == codigo]
        assert acepta == (not hijos), codigo  # solo las hojas aceptan movimientos
    for concepto, (_, _, codigo) in cc.CONCEPTOS.items():
        assert codigo in codigos, concepto
        assert next(a for c, _, _, a in cc.CATALOGO_SUGERIDO if c == codigo), concepto
    semilla = cc.ddl_semilla_catalogo()
    assert semilla.count("('1.1.01.01', 'Caja general', 'activo', 'deudora', true)") == 1
    assert "('1.2.02.01', 'Depreciación acumulada edificaciones', 'activo', 'acreedora', true)" in semilla


def test_venta_a_credito_con_gravado_y_exento():
    datos = _ecf([("1", "10000.00"), ("4", "5000.00")], totales={"MontoTotal": "16800.00"})
    asiento = cc.asiento_venta(factura_id="f", tipo_ecf=31, encf="E310000000001", fecha=date(2026, 10, 5), datos=datos,
                               a_credito=True)
    debe, haber = _lados(asiento)
    assert debe == {"cxc_clientes": D("16800.00")}
    assert haber == {"ventas_gravadas": D("10000.00"), "ventas_exentas": D("5000.00"), "itbis_por_pagar": D("1800.00")}
    assert asiento.origen == "venta" and _cuadra(asiento)


def test_venta_contado_con_propina_y_centavos():
    datos = _ecf([("1", "1000.00")], tipo_pago="1", totales={"MontoTotal": "1280.01"})
    debe, haber = _lados(cc.asiento_venta(factura_id="f", tipo_ecf=32, encf=None, fecha=date(2026, 10, 5), datos=datos,
                                          a_credito=False))
    assert debe == {"caja": D("1280.01")}
    assert haber["otros_impuestos_por_pagar"] == D("100.01")
    centavos = _ecf([("1", "1000.00")], totales={"MontoTotal": "1180.01"})
    _, haber = _lados(cc.asiento_venta(factura_id="f", tipo_ecf=31, encf=None, fecha=date(2026, 10, 5), datos=centavos,
                                       a_credito=True))
    assert haber["redondeo"] == D("0.01")


def test_nota_de_credito_rebaja_la_cxc_con_devoluciones():
    datos = _ecf([("1", "1000.00")], totales={"MontoTotal": "1180.00"})
    asiento = cc.asiento_venta(factura_id="n", tipo_ecf=34, encf="E340000000001", fecha=date(2026, 10, 5), datos=datos,
                               a_credito=True)
    debe, haber = _lados(asiento)
    assert debe == {"devoluciones_ventas": D("1000.00"), "itbis_por_pagar": D("180.00")}
    assert haber == {"cxc_clientes": D("1180.00")}
    assert asiento.origen == "nota_credito"


def test_venta_gratuita_no_se_contabiliza():
    with pytest.raises(NoContabilizableError, match="gratuita"):
        cc.asiento_venta(factura_id="f", tipo_ecf=31, encf=None, fecha=date(2026, 10, 5),
                         datos=_ecf([("1", "1")], tipo_pago="3", totales={"MontoTotal": "1.18"}), a_credito=False)


def test_compra_con_itbis_al_costo_y_nota_de_credito():
    asiento = cc.asiento_compra(registro_id="r", fecha=date(2026, 10, 5), ncf="B0100000010", proveedor="P",
                                tipo_bienes_servicios=2, total=D("1180"), itbis_facturado=D("180"),
                                itbis_costo=D("80"), es_nota_credito=False)
    debe, haber = _lados(asiento)
    assert debe == {"gasto_606_02": D("1080.00"), "itbis_adelantado": D("100.00")}
    assert haber == {"cxp_proveedores": D("1180.00")}
    nota = cc.asiento_compra(registro_id="n", fecha=date(2026, 10, 5), ncf="B0400000001", proveedor="P",
                             tipo_bienes_servicios=9, total=D("118"), itbis_facturado=D("18"), itbis_costo=D("0"),
                             es_nota_credito=True)
    debe, haber = _lados(nota)
    assert debe == {"cxp_proveedores": D("118.00")} and haber == {"gasto_606_09": D("100.00"), "itbis_adelantado": D("18.00")}
    gasto_menor = cc.asiento_compra(registro_id="e", fecha=date(2026, 10, 5), ncf="E430000000001", proveedor="P",
                                    tipo_bienes_servicios=2, total=D("500"), itbis_facturado=D("0"), itbis_costo=D("0"),
                                    es_nota_credito=False, pagada_al_contado=True)
    assert _lados(gasto_menor)[1] == {"caja_chica": D("500.00")}
    with pytest.raises(NoContabilizableError, match="clasifíquelo"):
        cc.asiento_compra(registro_id="r", fecha=date(2026, 10, 5), ncf="B01", proveedor="P", tipo_bienes_servicios=None,
                          total=D("1"), itbis_facturado=D("0"), itbis_costo=D("0"), es_nota_credito=False)


def test_pago_con_retenciones():
    asiento = cc.asiento_pago(pago_id="p", fecha=date(2026, 10, 20), ncf="B01", proveedor="P", monto=D("27000"),
                              retencion_isr=D("3000"), retencion_itbis=D("5400"), cuenta_banco="banco")
    debe, haber = _lados(asiento)
    assert debe == {"cxp_proveedores": D("35400.00")}
    assert haber == {"banco": D("27000.00"), "itbis_retenido_por_pagar": D("5400.00"),
                     "isr_retenido_por_pagar": D("3000.00")}


def test_cobro_movimientos_y_transferencia():
    assert _lados(cc.asiento_cobro(cobro_id="c", fecha=date(2026, 10, 1), monto=D("50"), encf="E31", cliente="X",
                                   cuenta_banco="banco")) == ({"banco": D("50.00")}, {"cxc_clientes": D("50.00")})
    comision = cc.asiento_movimiento(movimiento_id="m", fecha=date(2026, 10, 1), tipo="comision", monto_dop=D("1250"),
                                     descripcion=None, referencia=None, cuenta_banco="banco", contrapartida=None)
    assert _lados(comision) == ({"gastos_bancarios": D("1250.00")}, {"banco": D("1250.00")})
    interes = cc.asiento_movimiento(movimiento_id="m", fecha=date(2026, 10, 1), tipo="interes", monto_dop=D("10"),
                                    descripcion=None, referencia=None, cuenta_banco="banco", contrapartida=None)
    assert _lados(interes) == ({"banco": D("10.00")}, {"ingresos_financieros": D("10.00")})
    t = cc.asiento_transferencia(transferencia_id="t", fecha=date(2026, 10, 1), monto_dop=D("100"), concepto=None,
                                 referencia=None, cuenta_origen="a", cuenta_destino="b")
    assert _lados(t) == ({"b": D("100.00")}, {"a": D("100.00")})


def test_huella_cambia_con_las_cuentas():
    lineas = [{"cuenta_id": "a", "debito": D("1"), "credito": D("0")}, {"cuenta_id": "b", "debito": D("0"), "credito": D("1")}]
    otra = [{"cuenta_id": "c", "debito": D("1"), "credito": D("0")}, lineas[1]]
    assert cc.huella(lineas, date(2026, 1, 1), "x", None) == cc.huella(list(reversed(lineas)), date(2026, 1, 1), "x", None)
    assert cc.huella(lineas, date(2026, 1, 1), "x", None) != cc.huella(otra, date(2026, 1, 1), "x", None)


# --- Depreciación ---------------------------------------------------------

def _activo(**cambios):
    datos = dict(categoria=2, metodo="dgii", costo=D("3850000"), valor_residual=D("0"), vida_util_meses=None,
                 depreciar_desde="202602")
    datos.update(cambios)
    return cc.Activo(**datos)


def test_tasas_del_articulo_287():
    assert cc.TASAS_CATEGORIA == {1: D("0.05"), 2: D("0.25"), 3: D("0.15")}


def test_dgii_saldo_decreciente_categoria_2():
    cuotas = cc.cuotas_depreciacion(_activo(), "202712")
    assert cuotas["202602"] == D("80208.33") and "202601" not in cuotas
    assert cc.depreciacion_del_anio(_activo(), 2026) == D("80208.33") * 11
    # 2027: 25 % del valor pendiente al 1 de enero.
    pendiente = D("3850000") - D("80208.33") * 11
    assert cuotas["202701"] == cc.redondear(cc.redondear(pendiente * D("0.25")) / 12)


def test_dgii_categoria_1_sobre_el_costo():
    activo = _activo(categoria=1, costo=D("1200000"), depreciar_desde="202601")
    cuotas = cc.cuotas_depreciacion(activo, "202812")
    assert set(cuotas.values()) == {D("5000.00")}


def test_linea_recta_termina_en_el_residual():
    activo = _activo(metodo="linea_recta", costo=D("1000"), valor_residual=D("100"), vida_util_meses=7,
                     depreciar_desde="202601")
    cuotas = cc.cuotas_depreciacion(activo, "203012")
    assert len(cuotas) == 7 and sum(cuotas.values()) == D("900")
    assert list(cuotas.values())[:6] == [D("128.57")] * 6 and cuotas["202607"] == D("128.58")


def test_baja_y_acumulada_inicial():
    activo = _activo(metodo="linea_recta", costo=D("1200"), vida_util_meses=12, depreciar_desde="202601",
                     baja_periodo="202604", depreciacion_acumulada_inicial=D("600"))
    assert cc.cuotas_depreciacion(activo, "202612") == {"202601": D("50.00"), "202602": D("50.00"), "202603": D("50.00")}


def test_asiento_de_depreciacion_por_categoria():
    asiento = cc.asiento_depreciacion("202610", {2: D("100"), 1: D("50")}, "octubre 2026")
    assert asiento.fecha == date(2026, 10, 31) and asiento.origen_id == "202610"
    debe, haber = _lados(asiento)
    assert sum(x.debito for x in asiento.lineas) == D("150")
    assert haber == {"depreciacion_acumulada_1": D("50.00"), "depreciacion_acumulada_2": D("100.00")}
