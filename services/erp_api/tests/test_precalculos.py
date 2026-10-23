"""Matemática de los pre-cálculos (services/erp_api/app/precalculos.py):
vencimientos del calendario DGII, IT-1 (proporcionalidad, retenciones,
saldo a favor), IR-17 (tasas antes y después de la Ley 30-26) e IR-2."""
from datetime import date
from decimal import Decimal

from services.erp_api.app import precalculos
from services.erp_api.app.precalculos import Compras606, VentasItbis

HOY = date(2026, 10, 23)


def _casillas(precalculo):
    return {c["codigo"]: c for s in precalculo["secciones"] for c in s["casillas"]}


def test_vence_dias_fijos_y_fin_de_semana():
    assert precalculos.vence("ir17", "202610") == date(2026, 11, 10)  # martes
    assert precalculos.vence("606", "202610") == date(2026, 11, 16)  # 15 = domingo -> lunes
    assert precalculos.vence("it1", "202610") == date(2026, 11, 20)  # viernes
    assert precalculos.vence("it1", "202612") == date(2027, 1, 20)
    assert precalculos.vence("ir17", "202609") == date(2026, 10, 12)  # 10 = sábado -> lunes
    assert precalculos.vence("ir2", "2026") == date(2027, 4, 30)
    # 120 días desde el 31-dic: 29 de abril si el año siguiente es bisiesto;
    # el 29-abr-2028 es sábado -> lunes 1 de mayo.
    assert precalculos.vence("ir2", "2027") == date(2028, 5, 1)
    assert precalculos.vence("ir2", "2031") == date(2032, 4, 29)
    assert precalculos.fecha_larga(date(2026, 11, 20)) == "viernes 20 de noviembre de 2026"


def test_estado_declaracion():
    assert precalculos.estado_declaracion(date(2026, 10, 20), False, HOY) == "vencida"
    assert precalculos.estado_declaracion(date(2026, 11, 20), False, HOY) == "pendiente"
    assert precalculos.estado_declaracion(date(2026, 10, 20), True, HOY) == "presentada"
    # 607/608 del emisor 100 % electrónico: nunca "vencida"; si se marcó, presentada.
    assert precalculos.estado_declaracion(date(2026, 10, 20), False, HOY, aplica=False) == "no_aplica"
    assert precalculos.estado_declaracion(date(2026, 10, 20), True, HOY, aplica=False) == "presentada"


def _it1(ajustes=None, saldo_sugerido=Decimal("200"), **ventas):
    datos = {"g18": Decimal("100000"), "exentas": Decimal("25000"), "i18": Decimal("18000")} | ventas
    return precalculos.calcular_it1(
        periodo="202610",
        ventas=VentasItbis(**datos),
        compras=Compras606(itbis_facturado=Decimal("11000"), itbis_costo=Decimal("1000")),
        itbis_retenido_proveedores=Decimal("1200"),
        ajustes=ajustes or {"itbis_retenido_terceros": Decimal("500")},
        saldo_favor_sugerido=saldo_sugerido,
        hoy=HOY,
    )


def test_it1_proporcionalidad_retenciones_y_saldo_anterior():
    precalculo, nuevo_saldo = _it1()
    c = _casillas(precalculo)

    assert c["5"]["valor"] == Decimal("125000.00")
    assert c["8"]["valor"] == Decimal("18000.00")
    assert c["9"]["valor"] == Decimal("10000.00")  # 11,000 facturado - 1,000 llevado al costo
    assert c["10"]["etiqueta"] == "Proporción admitida: 80.00%"
    assert c["10"]["valor"] == Decimal("-2000.00")
    assert c["11"]["valor"] == Decimal("-8000.00")
    assert c["12"]["valor"] == Decimal("-500.00")
    assert c["13"]["valor"] == Decimal("1200.00")
    assert c["14"]["valor"] == Decimal("-200.00")  # saldo a favor sugerido del mes anterior
    assert c["15"]["valor"] == Decimal("9300.00")  # 18,000 - 8,000 - 500 - 200
    assert c["16"]["valor"] == Decimal("10500.00")
    assert precalculo["resultado"] == {"etiqueta": "ITBIS a pagar", "valor": Decimal("10500.00")}
    assert nuevo_saldo == 0
    assert precalculo["vence"] == date(2026, 11, 20)
    assert {"etiqueta": "ITBIS facturado", "valor": "18000.00"} in precalculo["resumen"]
    assert any("sujeto a proporcionalidad" in a for a in precalculo["advertencias"])
    assert {a["clave"]: a["valor"] for a in precalculo["ajustes"]} == {
        "itbis_retenido_terceros": Decimal("500.00"), "saldo_favor_anterior": Decimal("200.00")}
    # El saldo anterior es el sugerido (no un valor guardado); el retenido
    # por terceros sí es manual y no tiene sugerencia.
    ajustes = {a["clave"]: a for a in precalculo["ajustes"]}
    assert (ajustes["saldo_favor_anterior"]["manual"], ajustes["saldo_favor_anterior"]["sugerido"]) == (
        False, Decimal("200.00"))
    assert (ajustes["itbis_retenido_terceros"]["manual"], ajustes["itbis_retenido_terceros"]["sugerido"]) == (
        True, None)


def test_it1_sin_exentas_admite_todo_y_saldo_a_favor():
    precalculo, nuevo_saldo = precalculos.calcular_it1(
        periodo="202610",
        ventas=VentasItbis(g18=Decimal("1000"), exportaciones=Decimal("50000"), i18=Decimal("180")),
        compras=Compras606(itbis_facturado=Decimal("5000")),
        itbis_retenido_proveedores=Decimal("0"),
        ajustes={"saldo_favor_anterior": Decimal("0")},
        saldo_favor_sugerido=Decimal("999"),
        hoy=HOY,
    )
    c = _casillas(precalculo)

    assert c["10"]["etiqueta"] == "Proporción admitida: 100.00%"
    assert c["11"]["valor"] == Decimal("-5000.00")
    assert c["14"]["valor"] == 0  # el valor guardado (0) gana al sugerido
    saldo = next(a for a in precalculo["ajustes"] if a["clave"] == "saldo_favor_anterior")
    assert (saldo["valor"], saldo["manual"], saldo["sugerido"]) == (0, True, Decimal("999.00"))
    assert precalculo["resultado"] == {"etiqueta": "Saldo a favor", "valor": Decimal("4820.00")}
    assert nuevo_saldo == Decimal("4820")


def test_it1_proporcionalidad_solo_sobre_lo_sujeto_y_exentas_por_destino():
    precalculo, _ = precalculos.calcular_it1(
        periodo="202610",
        ventas=VentasItbis(g18=Decimal("60000"), exentas=Decimal("40000"), exentas_por_destino=Decimal("20000"),
                           i18=Decimal("10800")),
        compras=Compras606(itbis_facturado=Decimal("6000"), itbis_proporcionalidad=Decimal("1000")),
        itbis_retenido_proveedores=Decimal("0"),
        ajustes={},
        saldo_favor_sugerido=Decimal("0"),
        hoy=HOY,
    )
    c = _casillas(precalculo)

    # (60,000 gravadas + 20,000 exentas por destino) / 100,000 = 80 %, solo
    # sobre los 1,000 sujetos a proporcionalidad: 5,000 + 800.
    assert c["10"]["etiqueta"] == "Proporción admitida: 80.00%"
    assert c["11"]["valor"] == Decimal("-5800.00")


def test_acumular_ventas_resta_notas_de_credito():
    def ecf(tipo, gravado, itbis, exento="0"):
        return {"tipo_ecf": tipo, "datos": {"Encabezado": {"Totales": {
            "MontoGravadoI1": gravado, "MontoExento": exento, "TotalITBIS1": itbis}}}}

    ventas = precalculos.acumular_ventas([
        ecf(31, "1000", "180", "100"), ecf(33, "200", "36"), ecf(34, "300", "54"), ecf(44, "0", "0", "700")])

    assert (ventas.g18, ventas.i18, ventas.exentas, ventas.exentas_por_destino) == (900, 162, 800, 700)


def test_ir17_tasas_segun_periodo_y_recargos():
    grupos = [
        {"tipo_retencion_isr": 2, "monto_retencion_renta": Decimal("1500"), "monto_pagado": Decimal("10000")},
        {"tipo_retencion_isr": 1, "monto_retencion_renta": Decimal("2160"), "monto_pagado": Decimal("14400")},
        {"tipo_retencion_isr": None, "monto_retencion_renta": Decimal("50"), "monto_pagado": Decimal("500")},
    ]

    nuevo = precalculos.calcular_ir17(periodo="202610", retenciones=grupos,
                                      ajustes={"recargos": Decimal("25")}, hoy=HOY)
    anterior = precalculos.calcular_ir17(periodo="202606", retenciones=grupos[:1], ajustes={}, hoy=HOY)
    c = _casillas(nuevo)

    assert c["1"]["etiqueta"] == "Alquileres · 15% de RD$ 14,400.00"
    assert c["2"]["etiqueta"] == "Honorarios por servicios independientes · 15% de RD$ 10,000.00"
    assert c["3"]["etiqueta"] == "Retenciones sin tipo de retención en el 606"
    assert (c["4"]["valor"], c["5"]["valor"], c["6"]["valor"]) == (Decimal("3710.00"), 25, Decimal("3735.00"))
    assert nuevo["resultado"]["valor"] == Decimal("3735.00")
    assert nuevo["vence"] == date(2026, 11, 10)
    assert any("sin tipo" in a for a in nuevo["advertencias"])
    assert not any("tasa vigente" in a for a in nuevo["advertencias"])
    assert "Tasa vigente para los pagos de octubre 2026: 15%" in c["1"]["ayuda"]
    assert "Casilla" not in c["1"]["ayuda"]
    assert "desde julio 2026" in nuevo["nota"]
    # En junio 2026 (antes de la Ley 30-26) la tasa vigente es 10 %: la
    # etiqueta muestra la tasa efectiva del 606 (15 %) y se avisa.
    c_ant = _casillas(anterior)
    assert c_ant["1"]["etiqueta"] == "Honorarios por servicios independientes · 15% de RD$ 10,000.00"
    assert "Tasa vigente para los pagos de junio 2026: 10%" in c_ant["1"]["ayuda"]
    assert any("tasa vigente para los pagos de junio 2026 es 10%" in a for a in anterior["advertencias"])
    assert "anteriores a julio 2026" in anterior["nota"]


def test_ir17_etiqueta_coherente_con_lo_retenido_y_mes_de_pago():
    # Caso del e2e: 606 de alquiler con base 20,000 y 2,000 retenidos (10 %)
    # pagado en octubre 2026, cuando la tasa vigente ya es 15 %.
    grupos = [{"tipo_retencion_isr": 1, "monto_retencion_renta": Decimal("2000"), "monto_pagado": Decimal("20000")}]

    octubre = precalculos.calcular_ir17(periodo="202610", retenciones=grupos, ajustes={}, hoy=HOY)
    junio = precalculos.calcular_ir17(periodo="202606", retenciones=grupos, ajustes={}, hoy=HOY)
    julio = precalculos.calcular_ir17(periodo=precalculos.PERIODO_LEY_30_26, retenciones=grupos, ajustes={}, hoy=HOY)
    sin_base = precalculos.calcular_ir17(
        periodo="202610", retenciones=[{**grupos[0], "monto_pagado": Decimal("0")}], ajustes={}, hoy=HOY)

    linea = _casillas(octubre)["1"]
    assert (linea["etiqueta"], linea["valor"]) == ("Alquileres · 10% de RD$ 20,000.00", Decimal("2000.00"))
    assert any("es el 10% de la base" in a and "es 15%" in a for a in octubre["advertencias"])
    assert _casillas(junio)["1"]["etiqueta"] == "Alquileres · 10% de RD$ 20,000.00"
    assert not any("tasa vigente" in a for a in junio["advertencias"])
    assert any("es 15%" in a for a in julio["advertencias"])
    assert _casillas(sin_base)["1"]["etiqueta"] == "Alquileres"


def test_ir2_inventario_final_sugerido_y_manual():
    def ir2(ajustes):
        return precalculos.calcular_ir2(anio="2026", ingresos=Decimal("0"), compras_por_tipo={},
                                        valuacion_inventario=Decimal("80000"), ajustes=ajustes, hoy=HOY)

    sugerido = {a["clave"]: a for a in ir2({})["ajustes"]}["inventario_final"]
    manual = {a["clave"]: a for a in ir2({"inventario_final": Decimal("75000")})["ajustes"]}["inventario_final"]

    assert (sugerido["valor"], sugerido["manual"], sugerido["sugerido"]) == (Decimal("80000.00"), False,
                                                                              Decimal("80000.00"))
    assert (manual["valor"], manual["manual"], manual["sugerido"]) == (Decimal("75000.00"), True, Decimal("80000.00"))
    anticipos = {a["clave"]: a for a in ir2({})["ajustes"]}["anticipos"]
    assert (anticipos["valor"], anticipos["manual"], anticipos["sugerido"]) == (0, False, None)


def test_ir2_costo_gastos_y_tasa():
    precalculo = precalculos.calcular_ir2(
        anio="2026",
        ingresos=Decimal("1000000"),
        compras_por_tipo={9: Decimal("400000"), 1: Decimal("150000"), 3: Decimal("60000"),
                          10: Decimal("90000"), None: Decimal("5000")},
        valuacion_inventario=Decimal("80000"),
        ajustes={"inventario_inicial": Decimal("50000"), "ajustes_fiscales": Decimal("10000"),
                 "anticipos": Decimal("20000"), "retenciones_isr": Decimal("1000"),
                 "depreciacion": Decimal("12000")},
        hoy=HOY,
    )
    c = _casillas(precalculo)

    assert c["5"]["valor"] == Decimal("370000.00")  # 50,000 + 400,000 - 80,000
    assert c["6"]["valor"] == Decimal("630000.00")
    assert [c[k]["etiqueta"] for k in ("7", "8", "9")] == [
        "Gastos de personal", "Arrendamientos", "Depreciación"]
    assert c["10"]["valor"] == Decimal("-222000.00")
    assert c["11"]["valor"] == Decimal("408000.00")
    assert c["13"]["valor"] == Decimal("418000.00")
    assert c["14"]["etiqueta"] == "Impuesto liquidado (27%)"
    assert c["14"]["valor"] == Decimal("112860.00")
    assert c["17"]["valor"] == Decimal("91860.00")
    assert precalculo["vence"] == date(2027, 4, 30)
    assert {"etiqueta": "Margen bruto", "valor": "63.0%"} in precalculo["resumen"]
    advertencias = " ".join(precalculo["advertencias"])
    assert "Kardex" in advertencias and "adquisiciones de activos" in advertencias and "sin 'Tipo" in advertencias


def test_ir2_tasa_transitoria_ley_30_26_y_saldo_a_favor():
    grande = precalculos.calcular_ir2(anio="2026", ingresos=Decimal("1000000000"), compras_por_tipo={},
                                      valuacion_inventario=Decimal("0"), ajustes={"inventario_final": Decimal("0")},
                                      hoy=HOY)
    pequena = precalculos.calcular_ir2(anio="2029", ingresos=Decimal("0"), compras_por_tipo={},
                                       valuacion_inventario=Decimal("0"), ajustes={"anticipos": Decimal("500")},
                                       hoy=HOY)

    # Sin gastos: 7 = total gastos, 8-10 renta, 11 = impuesto.
    assert _casillas(grande)["11"]["etiqueta"] == "Impuesto liquidado (30%)"
    assert pequena["resultado"] == {"etiqueta": "Saldo a favor estimado", "valor": Decimal("500.00")}
