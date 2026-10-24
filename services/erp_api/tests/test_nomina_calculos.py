"""Cálculos puros de Nómina (services/erp_api/app/nomina_calculos.py) con
casos calculados a mano con los parámetros de 2026 (docs/NOMINA.md):
salario mínimo cotizable RD$23,223.00 -> topes AFP 464,460.00 (20),
SFS 232,230.00 (10) y SRL 92,892.00 (4); escala ISR exento hasta
416,220.00, 15 % hasta 624,329.00, 31,216.00 + 20 % hasta 867,123.00 y
79,776.00 + 25 % después."""
from datetime import date
from decimal import Decimal

import pytest

from services.erp_api.app import nomina_calculos as calc
from services.erp_api.app.nomina_calculos import EntradaLinea, Parametros, TramoIsr

D = Decimal

ESCALA_2026 = (
    TramoIsr(D("0"), D("0")),
    TramoIsr(D("416220"), D("0.15")),
    TramoIsr(D("624329"), D("0.20"), D("31216")),
    TramoIsr(D("867123"), D("0.25"), D("79776")),
)

P2026 = Parametros(
    vigente_desde=date(2026, 2, 1),
    salario_minimo_cotizable=D("23223.00"),
    afp_empleado=D("0.0287"),
    afp_empleador=D("0.0710"),
    sfs_empleado=D("0.0304"),
    sfs_empleador=D("0.0709"),
    srl_empleador=D("0.0110"),
    infotep_empleador=D("0.01"),
    infotep_bonificacion_empleado=D("0.005"),
    tope_afp_salarios=D("20"),
    tope_sfs_salarios=D("10"),
    tope_srl_salarios=D("4"),
    escala_isr=ESCALA_2026,
)


def _linea(salario, **cambios):
    return calc.calcular_linea(EntradaLinea(salario_mensual=D(salario), tasa_srl=D("0.011"), **cambios), P2026)


# --- Topes y escala ---------------------------------------------------------

def test_topes_2026_salen_del_salario_minimo_cotizable():
    assert P2026.tope_afp == D("464460.00")
    assert P2026.tope_sfs == D("232230.00")
    assert P2026.tope_srl == D("92892.00")
    assert P2026.exento_isr_anual == D("416220")


def test_topes_del_tramo_anterior():
    p = Parametros(**{**P2026.__dict__, "salario_minimo_cotizable": D("21674.80")})
    assert (p.tope_afp, p.tope_sfs, p.tope_srl) == (D("433496.00"), D("216748.00"), D("86699.20"))


@pytest.mark.parametrize(
    ("renta", "impuesto"),
    [
        (D("0"), D("0")),
        (D("416220"), D("0")),  # el límite exacto está exento
        (D("416220.01"), D("0.0015")),
        (D("564540"), D("22248")),  # (564,540 − 416,220) × 15 %
        (D("624329"), D("31216.35")),  # fin del tramo 15 % (la DGII redondea el fijo siguiente a 31,216)
        (D("677448"), D("41839.80")),  # 31,216 + (677,448 − 624,329) × 20 %
        (D("867123"), D("79774.80")),
        (D("1000000"), D("112995.25")),  # 79,776 + (1,000,000 − 867,123) × 25 %
    ],
)
def test_isr_anual_por_tramos(renta, impuesto):
    assert calc.isr_anual(renta, ESCALA_2026) == impuesto


def test_isr_mensual_anualiza_la_base():
    assert calc.isr_mensual(D("47045"), ESCALA_2026) == D("1854.00")
    assert calc.isr_mensual(D("34685"), ESCALA_2026) == D("0.00")  # 416,220 / 12
    assert calc.isr_mensual(D("-5"), ESCALA_2026) == D("0")


def test_escala_desde_json_ordena_y_valida():
    escala = calc.escala_desde_json([
        {"desde": "624329", "tasa": "0.20", "fijo": "31216"}, {"desde": "0", "tasa": "0"},
        {"desde": "416220", "tasa": "0.15"},
    ])
    assert [t.desde for t in escala] == [D("0"), D("416220"), D("624329")]
    assert calc.escala_a_json(escala)[2] == {"desde": "624329.00", "tasa": "0.20", "fijo": "31216.00"}
    with pytest.raises(ValueError):
        calc.escala_desde_json([{"desde": "0", "tasa": "15"}])  # tasa como porcentaje, no fracción
    with pytest.raises(ValueError):
        calc.escala_desde_json([{"desde": "0", "tasa": "0"}, {"desde": "0", "tasa": "0.1"}])
    with pytest.raises(ValueError):
        calc.escala_desde_json([])


# --- Línea de nómina ---------------------------------------------------------

def test_salario_exento_de_isr():
    r = _linea("30000")
    assert (r.afp, r.sfs, r.isr) == (D("861.00"), D("912.00"), D("0"))
    assert r.neto == D("28227.00")
    # Patronal: 7.10 % 2,130 · 7.09 % 2,127 · SRL 1.10 % 330 · INFOTEP 1 % 300
    assert (r.afp_empleador, r.sfs_empleador, r.srl, r.infotep) == (D("2130.00"), D("2127.00"), D("330.00"), D("300.00"))
    assert r.costo_total == D("34887.00")


def test_salario_en_tramo_15():
    r = _linea("50000")
    assert r.afp == D("1435.00")  # 2.87 %
    assert r.sfs == D("1520.00")  # 3.04 %
    assert r.base_isr == D("47045.00")
    assert r.isr == D("1854.00")  # (47,045 × 12 − 416,220) × 15 % / 12
    assert r.neto == D("45191.00")
    assert r.total_aportes_empleador == D("8145.00")  # 3,550 + 3,545 + 550 + 500
    assert r.costo_total == D("58145.00")


def test_salario_en_tramo_20():
    r = _linea("60000")
    assert (r.afp, r.sfs) == (D("1722.00"), D("1824.00"))
    assert r.isr == D("3486.65")  # (31,216 + (56,454 × 12 − 624,329) × 20 %) / 12
    assert r.neto == D("52967.35")


def test_salario_sobre_todos_los_topes_y_tramo_25():
    r = _linea("500000")
    assert r.afp == D("13330.00")  # 464,460 × 2.87 % = 13,330.002
    assert r.sfs == D("7059.79")  # 232,230 × 3.04 % = 7,059.792
    assert r.base_isr == D("479610.21")
    # (79,776 + (479,610.21 × 12 − 867,123) × 25 %) / 12 = 108,485.49
    assert r.isr == D("108485.49")
    assert r.neto == D("371124.72")
    assert r.afp_empleador == D("32976.66")  # 464,460 × 7.10 %
    assert r.sfs_empleador == D("16465.11")  # 232,230 × 7.09 % = 16,465.107
    assert r.srl == D("1021.81")  # 92,892 × 1.10 % = 1,021.812
    assert r.infotep == D("5000.00")  # el 1 % de INFOTEP no tiene tope
    assert r.costo_total == D("555463.58")


def test_srl_tope_antes_que_sfs():
    r = _linea("100000")
    assert r.srl == D("1021.81")  # tope 92,892
    assert r.sfs == D("3040.00")  # por debajo del tope SFS


def test_tasa_srl_de_la_empresa():
    r = calc.calcular_linea(EntradaLinea(salario_mensual=D("50000"), tasa_srl=D("0.013")), P2026)
    assert r.srl == D("650.00")


def test_horas_extra_35_y_100():
    # 47,660 / 23.83 = 2,000 diarios -> 250 la hora
    assert calc.valor_hora(D("47660"), P2026) == D("250")
    assert calc.monto_horas_extra(D("47660"), D("10"), D("2"), P2026) == D("4375.00")  # 3,375 + 1,000
    r = _linea("47660", horas_extra_35=D("10"), horas_extra_100=D("2"))
    assert r.horas_extra == D("4375.00")
    assert r.salario_cotizable == D("52035.00")
    assert r.afp == D("1493.40")  # las horas extra cotizan
    assert r.infotep == D("520.35")


def test_bonificacion_paga_isr_e_infotep_pero_no_tss():
    r = _linea("30000", bonificacion=D("20000"))
    assert (r.afp, r.sfs) == (D("861.00"), D("912.00"))
    assert r.base_isr == D("48227.00")
    assert r.isr == D("2031.30")  # (48,227 × 12 − 416,220) × 15 % / 12
    assert r.infotep_empleado == D("100.00")  # 0.5 % de la bonificación
    assert r.infotep == D("300.00")
    assert r.neto == D("46095.70")


def test_regalia_exenta_sin_tope_y_con_tope():
    r = _linea("30000", regalia=D("30000"))
    assert (r.afp, r.isr) == (D("861.00"), D("0"))  # no cotiza ni paga ISR
    assert r.total_ingresos == D("60000.00")
    assert r.neto == D("58227.00")
    con_tope = calc.calcular_linea(
        EntradaLinea(salario_mensual=D("30000"), tasa_srl=D("0.011"), regalia=D("30000")),
        Parametros(**{**P2026.__dict__, "regalia_tope_exento": D("10000")}),
    )
    assert con_tope.base_isr == D("48227.00")  # 20,000 gravados
    assert con_tope.detalle["regalia_exenta"] == "10000.00"


def test_otros_ingresos_exentos_y_descuentos():
    r = _linea("30000", otros_ingresos=D("5000"), ingresos_exentos=D("2500"), otros_descuentos=D("1000"))
    assert r.salario_cotizable == D("35000.00")
    assert r.afp == D("1004.50")
    assert r.sfs == D("1064.00")
    assert r.isr == D("0")  # 32,931.50 × 12 < 416,220
    assert r.total_ingresos == D("37500.00")
    assert r.neto == D("37500.00") - D("1004.50") - D("1064.00") - D("1000.00")


def test_quincena_es_la_mitad_del_mes():
    mes = _linea("60000")
    q = _linea("60000", fraccion=D("0.5"))
    assert q.salario == D("30000.00")
    assert (q.afp, q.sfs) == (D("861.00"), D("912.00"))
    assert q.isr == D("1743.33")  # 3,486.65 / 2 redondeado
    assert q.neto * 2 == pytest.approx(mes.neto, abs=D("0.02"))


def test_quincena_aplica_el_tope_mensual():
    q = _linea("500000", fraccion=D("0.5"))
    assert q.afp == D("6665.00")  # 464,460 × 2.87 % / 2
    assert q.srl == D("510.91")  # 92,892 × 1.1 % / 2 = 510.906


def test_proporcion_por_ingreso_a_mitad_de_mes():
    r = _linea("31000", proporcion=D("16") / D("31"))
    assert r.salario == D("16000.00")
    assert r.afp == D("459.20")


def test_entradas_invalidas():
    with pytest.raises(ValueError):
        _linea("30000", fraccion=D("0"))
    with pytest.raises(ValueError):
        _linea("30000", otros_descuentos=D("-1"))
    with pytest.raises(ValueError):
        _linea("30000", proporcion=D("1.5"))


# --- Períodos ---------------------------------------------------------

def test_fechas_periodo():
    assert calc.fechas_periodo("mensual", 2026, 2, None) == (date(2026, 2, 1), date(2026, 2, 28))
    assert calc.fechas_periodo("quincenal", 2026, 10, 1) == (date(2026, 10, 1), date(2026, 10, 15))
    assert calc.fechas_periodo("quincenal", 2024, 2, 2) == (date(2024, 2, 16), date(2024, 2, 29))
    with pytest.raises(ValueError):
        calc.fechas_periodo("quincenal", 2026, 10, None)
    with pytest.raises(ValueError):
        calc.fechas_periodo("mensual", 2026, 10, 1)


# --- Regalía, vacaciones y prestaciones ---------------------------------------------------------

def test_regalia_pascual_doceava_parte():
    assert calc.regalia_pascual([D("30000")] * 12) == D("30000.00")
    assert calc.regalia_pascual([D("25000")] * 6 + [D("31000")] * 6) == D("28000.00")


def test_regalia_estimada_proporcional():
    assert calc.regalia_estimada(D("36000"), date(2020, 5, 1), 2026) == D("36000.00")
    assert calc.regalia_estimada(D("36000"), date(2026, 7, 1), 2026) == D("18000.00")  # 6 meses
    # Ingreso el 16/09 (15 de 30 días) -> 3.5 meses
    assert calc.regalia_estimada(D("36000"), date(2026, 9, 16), 2026) == D("10500.00")
    assert calc.regalia_estimada(D("36000"), date(2020, 1, 1), 2026, date(2026, 3, 31)) == D("9000.00")
    assert calc.regalia_estimada(D("36000"), date(2027, 1, 1), 2026) == D("0")


def test_meses_y_dias_de_vacaciones():
    assert calc.meses_servicio(date(2026, 1, 15), date(2026, 7, 14)) == 5
    assert calc.meses_servicio(date(2026, 1, 15), date(2026, 7, 15)) == 6
    assert calc.dias_vacaciones(date(2026, 1, 1), date(2026, 4, 30)) == 0
    assert calc.dias_vacaciones(date(2026, 1, 1), date(2026, 6, 1)) == 6  # 5 meses
    assert calc.dias_vacaciones(date(2026, 1, 1), date(2026, 12, 1)) == 12  # 11 meses
    assert calc.dias_vacaciones(date(2025, 1, 1), date(2026, 1, 1)) == 14
    assert calc.dias_vacaciones(date(2020, 1, 1), date(2025, 1, 1)) == 18


def test_prestaciones_por_desahucio():
    # 47,660 / 23.83 = 2,000 diarios; 2 años exactos
    r = calc.prestaciones(D("47660"), date(2024, 10, 1), date(2026, 10, 1), P2026)
    assert r.meses_servicio == 24
    assert r.salario_diario == D("2000.00")
    assert (r.dias_preaviso, r.preaviso) == (28, D("56000.00"))
    assert (r.dias_cesantia, r.cesantia) == (D("42.00"), D("84000.00"))  # 21 días × 2 años
    assert (r.dias_vacaciones, r.vacaciones) == (14, D("28000.00"))
    assert r.regalia == D("35873.12")  # 47,660 × (9 meses + 1/31) / 12, hasta el día de salida
    assert r.total == D("203873.12")


def test_prestaciones_mas_de_cinco_anios_y_renuncia():
    r = calc.prestaciones(D("47660"), date(2020, 10, 1), date(2026, 10, 1), P2026)
    assert r.dias_cesantia == D("138.00")  # 23 días × 6 años
    renuncia = calc.prestaciones(D("47660"), date(2020, 10, 1), date(2026, 10, 1), P2026, desahucio=False)
    assert (renuncia.preaviso, renuncia.cesantia) == (D("0.00"), D("0.00"))
    assert renuncia.dias_vacaciones == 18
    corto = calc.prestaciones(D("47660"), date(2026, 6, 1), date(2026, 10, 1), P2026)
    assert (corto.dias_preaviso, corto.dias_cesantia) == (7, D("6"))
    with pytest.raises(ValueError):
        calc.prestaciones(D("47660"), date(2026, 6, 1), date(2026, 5, 1), P2026)


def test_cedula_valida():
    assert calc.cedula_valida("00113918205")
    assert not calc.cedula_valida("00113918204")
    assert not calc.cedula_valida("0011391820")
