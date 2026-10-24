"""Cálculos puros de Kontara Nómina, sin base de datos: aportes a la
Seguridad Social (TSS, Ley 87-01) con sus topes, retención del ISR a
asalariados (escala anual de la DGII), horas extra, regalía pascual,
vacaciones y prestaciones laborales (Código de Trabajo).

Todas las tasas, topes y la escala del ISR llegan en `Parametros` (tabla
`nomina.parametros`, por fecha de vigencia): aquí no hay ningún valor fijo
de un año. Las fuentes y los valores de cada año están en docs/NOMINA.md.

Convenciones:

- La TSS cotiza por MES: los topes (20, 10 y 4 salarios mínimos
  cotizables) son mensuales. Un período quincenal se lleva a su equivalente
  mensual (ingresos / fracción), se aplica el tope y la tasa, y el aporte se
  vuelve a multiplicar por la fracción (0.5). Igual con el ISR: la retención
  mensual es el impuesto anual de (base mensual × 12) / 12, y la de una
  quincena es la mitad de la que correspondería a la quincena llevada a mes.
- Cada componente se redondea a centavos (ROUND_HALF_UP) antes de sumarse,
  como aparece en el recibo.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

_CERO = Decimal("0")
_CENTAVO = Decimal("0.01")
_DOCE = Decimal("12")


def redondear(valor: Decimal) -> Decimal:
    return Decimal(valor).quantize(_CENTAVO, rounding=ROUND_HALF_UP)


# --- Parámetros ---------------------------------------------------------

@dataclass(frozen=True)
class TramoIsr:
    """Un tramo de la escala anual: sobre el excedente de `desde`, `fijo` +
    `tasa` × (renta − desde). La DGII publica los montos fijos redondeados
    (RD$31,216 y RD$79,776), por eso no se deducen de los tramos anteriores."""

    desde: Decimal
    tasa: Decimal
    fijo: Decimal = _CERO


@dataclass(frozen=True)
class Parametros:
    vigente_desde: date
    salario_minimo_cotizable: Decimal
    afp_empleado: Decimal
    afp_empleador: Decimal
    sfs_empleado: Decimal
    sfs_empleador: Decimal
    srl_empleador: Decimal
    infotep_empleador: Decimal
    infotep_bonificacion_empleado: Decimal
    tope_afp_salarios: Decimal
    tope_sfs_salarios: Decimal
    tope_srl_salarios: Decimal
    escala_isr: tuple[TramoIsr, ...]
    # Monto anual exento de la regalía pascual; None = exenta sin tope.
    regalia_tope_exento: Decimal | None = None
    divisor_salario_diario: Decimal = Decimal("23.83")
    horas_jornada: Decimal = Decimal("8")
    recargo_horas_extra: Decimal = Decimal("0.35")
    recargo_horas_extra_exceso: Decimal = Decimal("1.00")

    @property
    def tope_afp(self) -> Decimal:
        return redondear(self.salario_minimo_cotizable * self.tope_afp_salarios)

    @property
    def tope_sfs(self) -> Decimal:
        return redondear(self.salario_minimo_cotizable * self.tope_sfs_salarios)

    @property
    def tope_srl(self) -> Decimal:
        return redondear(self.salario_minimo_cotizable * self.tope_srl_salarios)

    @property
    def exento_isr_anual(self) -> Decimal:
        """Renta anual exenta: el inicio del primer tramo con tasa > 0."""
        gravados = [t.desde for t in self.escala_isr if t.tasa > 0]
        return min(gravados) if gravados else _CERO


def escala_desde_json(tramos: list[dict[str, Any]]) -> tuple[TramoIsr, ...]:
    escala = tuple(
        TramoIsr(desde=Decimal(str(t["desde"])), tasa=Decimal(str(t["tasa"])), fijo=Decimal(str(t.get("fijo", 0))))
        for t in tramos
    )
    validar_escala(escala)
    return tuple(sorted(escala, key=lambda t: t.desde))


def escala_a_json(escala: tuple[TramoIsr, ...] | list[TramoIsr]) -> list[dict[str, str]]:
    return [{"desde": f"{t.desde:.2f}", "tasa": f"{t.tasa}", "fijo": f"{t.fijo:.2f}"} for t in escala]


def validar_escala(escala: tuple[TramoIsr, ...] | list[TramoIsr]) -> None:
    if not escala:
        raise ValueError("La escala del ISR necesita al menos un tramo")
    desdes = [t.desde for t in escala]
    if len(set(desdes)) != len(desdes):
        raise ValueError("Dos tramos de la escala del ISR empiezan en el mismo monto")
    for t in escala:
        if t.desde < 0 or t.fijo < 0 or not (0 <= t.tasa < 1):
            raise ValueError("Tramo del ISR inválido: montos ≥ 0 y tasa entre 0 y 1 (0.15 = 15 %)")


# --- ISR ---------------------------------------------------------

def isr_anual(renta_anual: Decimal, escala: tuple[TramoIsr, ...]) -> Decimal:
    """Impuesto anual sobre la renta neta de un asalariado (sin redondear)."""
    renta = Decimal(renta_anual)
    aplicable = None
    for tramo in sorted(escala, key=lambda t: t.desde):
        if renta > tramo.desde:
            aplicable = tramo
    if aplicable is None or aplicable.tasa == 0 and aplicable.fijo == 0:
        return _CERO
    return aplicable.fijo + (renta - aplicable.desde) * aplicable.tasa


def isr_mensual(base_mensual: Decimal, escala: tuple[TramoIsr, ...]) -> Decimal:
    """Retención del mes: ISR anual de (salario − AFP − SFS) × 12, entre 12."""
    if base_mensual <= 0:
        return _CERO
    return redondear(isr_anual(Decimal(base_mensual) * _DOCE, escala) / _DOCE)


# --- Horas extra ---------------------------------------------------------

def salario_diario(salario_mensual: Decimal, p: Parametros) -> Decimal:
    """Salario diario = mensual / 23.83 (promedio de días laborables)."""
    return Decimal(salario_mensual) / p.divisor_salario_diario


def valor_hora(salario_mensual: Decimal, p: Parametros) -> Decimal:
    return salario_diario(salario_mensual, p) / p.horas_jornada


def monto_horas_extra(salario_mensual: Decimal, horas_35: Decimal, horas_100: Decimal, p: Parametros) -> Decimal:
    """Horas extra: con recargo del 35 % (hasta 68 h semanales) y del 100 %
    (en exceso de 68 h), Código de Trabajo arts. 203 y 204."""
    hora = valor_hora(salario_mensual, p)
    return redondear(hora * (1 + p.recargo_horas_extra) * Decimal(horas_35)) + redondear(
        hora * (1 + p.recargo_horas_extra_exceso) * Decimal(horas_100)
    )


# --- Línea de nómina ---------------------------------------------------------

@dataclass(frozen=True)
class EntradaLinea:
    """Lo que el usuario captura para un empleado en un período.

    `fraccion` = parte del mes que paga el período (1 mensual, 0.5 quincenal).
    `otros_ingresos`: comisiones, incentivos (cotizan TSS y pagan ISR).
    `bonificacion`: participación en los beneficios (paga ISR y el 0.5 % de
    INFOTEP, no cotiza TSS). `ingresos_exentos`: viáticos y reembolsos.
    `regalia`: salario de Navidad (no cotiza TSS; exento de ISR hasta el tope).
    `proporcion`: parte del período trabajada (ingreso o salida a mitad del
    período); reduce el salario, no el valor de la hora extra.
    """

    salario_mensual: Decimal
    tasa_srl: Decimal
    fraccion: Decimal = Decimal("1")
    proporcion: Decimal = Decimal("1")
    horas_extra_35: Decimal = _CERO
    horas_extra_100: Decimal = _CERO
    otros_ingresos: Decimal = _CERO
    bonificacion: Decimal = _CERO
    ingresos_exentos: Decimal = _CERO
    regalia: Decimal = _CERO
    otros_descuentos: Decimal = _CERO


@dataclass(frozen=True)
class ResultadoLinea:
    salario: Decimal
    horas_extra: Decimal
    otros_ingresos: Decimal
    bonificacion: Decimal
    ingresos_exentos: Decimal
    regalia: Decimal
    total_ingresos: Decimal
    salario_cotizable: Decimal
    base_isr: Decimal
    afp: Decimal
    sfs: Decimal
    isr: Decimal
    infotep_empleado: Decimal
    otros_descuentos: Decimal
    total_descuentos: Decimal
    neto: Decimal
    afp_empleador: Decimal
    sfs_empleador: Decimal
    srl: Decimal
    infotep: Decimal
    total_aportes_empleador: Decimal
    costo_total: Decimal
    detalle: dict[str, str] = field(default_factory=dict)


def _tope_fraccion(cotizable_mensual: Decimal, tope: Decimal) -> Decimal:
    return min(cotizable_mensual, tope)


def calcular_linea(e: EntradaLinea, p: Parametros) -> ResultadoLinea:
    """Nómina de un empleado en un período. Ver el docstring del módulo."""
    if e.fraccion <= 0 or e.fraccion > 1:
        raise ValueError("La fracción del período debe estar entre 0 y 1")
    if e.proporcion < 0 or e.proporcion > 1:
        raise ValueError("La proporción trabajada debe estar entre 0 y 1")
    for nombre in ("salario_mensual", "horas_extra_35", "horas_extra_100", "otros_ingresos", "bonificacion",
                   "ingresos_exentos", "regalia", "otros_descuentos", "tasa_srl"):
        if getattr(e, nombre) < 0:
            raise ValueError(f"{nombre} no puede ser negativo")
    f = e.fraccion
    salario = redondear(e.salario_mensual * f * e.proporcion)
    horas_extra = monto_horas_extra(e.salario_mensual, e.horas_extra_35, e.horas_extra_100, p)
    otros = redondear(e.otros_ingresos)
    bonificacion = redondear(e.bonificacion)
    exentos = redondear(e.ingresos_exentos)
    regalia = redondear(e.regalia)

    cotizable = salario + horas_extra + otros
    cotizable_mensual = cotizable / f

    afp = redondear(_tope_fraccion(cotizable_mensual, p.tope_afp) * p.afp_empleado * f)
    sfs = redondear(_tope_fraccion(cotizable_mensual, p.tope_sfs) * p.sfs_empleado * f)

    regalia_exenta = regalia if p.regalia_tope_exento is None else min(regalia, p.regalia_tope_exento)
    regalia_gravada = regalia - regalia_exenta
    base_isr = cotizable + bonificacion + regalia_gravada - afp - sfs
    isr = redondear(isr_mensual(base_isr / f, p.escala_isr) * f) if base_isr > 0 else _CERO
    infotep_empleado = redondear(bonificacion * p.infotep_bonificacion_empleado)
    descuentos_otros = redondear(e.otros_descuentos)

    total_ingresos = cotizable + bonificacion + exentos + regalia
    total_descuentos = afp + sfs + isr + infotep_empleado + descuentos_otros
    neto = total_ingresos - total_descuentos

    afp_pat = redondear(_tope_fraccion(cotizable_mensual, p.tope_afp) * p.afp_empleador * f)
    sfs_pat = redondear(_tope_fraccion(cotizable_mensual, p.tope_sfs) * p.sfs_empleador * f)
    srl = redondear(_tope_fraccion(cotizable_mensual, p.tope_srl) * e.tasa_srl * f)
    infotep = redondear(cotizable * p.infotep_empleador)
    aportes = afp_pat + sfs_pat + srl + infotep

    return ResultadoLinea(
        salario=salario,
        horas_extra=horas_extra,
        otros_ingresos=otros,
        bonificacion=bonificacion,
        ingresos_exentos=exentos,
        regalia=regalia,
        total_ingresos=total_ingresos,
        salario_cotizable=cotizable,
        base_isr=max(base_isr, _CERO),
        afp=afp,
        sfs=sfs,
        isr=isr,
        infotep_empleado=infotep_empleado,
        otros_descuentos=descuentos_otros,
        total_descuentos=total_descuentos,
        neto=neto,
        afp_empleador=afp_pat,
        sfs_empleador=sfs_pat,
        srl=srl,
        infotep=infotep,
        total_aportes_empleador=aportes,
        costo_total=total_ingresos + aportes,
        detalle={
            "tope_afp": f"{p.tope_afp:.2f}",
            "tope_sfs": f"{p.tope_sfs:.2f}",
            "tope_srl": f"{p.tope_srl:.2f}",
            "regalia_exenta": f"{regalia_exenta:.2f}",
            "valor_hora": f"{redondear(valor_hora(e.salario_mensual, p)):.2f}",
        },
    )


# --- Períodos ---------------------------------------------------------

def fechas_periodo(tipo: str, anio: int, mes: int, quincena: int | None) -> tuple[date, date]:
    """Inicio y fin de un período: mensual = mes completo; quincena 1 = del 1
    al 15, quincena 2 = del 16 al último día del mes."""
    ultimo = calendar.monthrange(anio, mes)[1]
    if tipo == "mensual":
        if quincena is not None:
            raise ValueError("Un período mensual no lleva quincena")
        return date(anio, mes, 1), date(anio, mes, ultimo)
    if tipo != "quincenal" or quincena not in (1, 2):
        raise ValueError("Un período quincenal lleva quincena 1 o 2")
    return (date(anio, mes, 1), date(anio, mes, 15)) if quincena == 1 else (date(anio, mes, 16), date(anio, mes, ultimo))


def fraccion_periodo(tipo: str) -> Decimal:
    return Decimal("1") if tipo == "mensual" else Decimal("0.5")


# --- Regalía, vacaciones y prestaciones ---------------------------------------------------------

def regalia_pascual(salarios_ordinarios: list[Decimal]) -> Decimal:
    """Salario de Navidad (Código de Trabajo, art. 219): la duodécima parte
    del salario ordinario devengado en el año calendario (sin horas extra,
    comisiones ni bonificaciones)."""
    return redondear(sum((Decimal(s) for s in salarios_ordinarios), _CERO) / _DOCE)


def regalia_estimada(salario_mensual: Decimal, fecha_ingreso: date, anio: int, corte: date | None = None) -> Decimal:
    """Regalía proporcional con el salario actual: salario mensual × meses
    trabajados en el año hasta `corte` (31/12 por defecto) / 12. El mes de
    ingreso cuenta en proporción a sus días."""
    inicio = max(fecha_ingreso, date(anio, 1, 1))
    fin = corte or date(anio, 12, 31)
    if fin < inicio:
        return _CERO
    meses = Decimal("0")
    a, m = inicio.year, inicio.month
    while (a, m) <= (fin.year, fin.month):
        dias_mes = calendar.monthrange(a, m)[1]
        desde = inicio.day if (a, m) == (inicio.year, inicio.month) else 1
        hasta = fin.day if (a, m) == (fin.year, fin.month) else dias_mes
        meses += Decimal(hasta - desde + 1) / Decimal(dias_mes)
        a, m = (a + 1, 1) if m == 12 else (a, m + 1)
    return redondear(Decimal(salario_mensual) * meses / _DOCE)


def meses_servicio(fecha_ingreso: date, fecha: date) -> int:
    """Meses completos entre el ingreso y `fecha`."""
    meses = (fecha.year - fecha_ingreso.year) * 12 + fecha.month - fecha_ingreso.month
    if fecha.day < fecha_ingreso.day:
        meses -= 1
    return max(meses, 0)


# Código de Trabajo, art. 180: vacaciones proporcionales de quien no cumplió un año.
_VACACIONES_PROPORCIONALES = {5: 6, 6: 7, 7: 8, 8: 9, 9: 10, 10: 11, 11: 12}


def dias_vacaciones(fecha_ingreso: date, fecha: date) -> int:
    """Días laborables de vacaciones (Código de Trabajo, arts. 177 y 180):
    14 tras un año de servicio, 18 tras cinco; con menos de un año, la
    escala proporcional desde 5 meses."""
    meses = meses_servicio(fecha_ingreso, fecha)
    if meses >= 60:
        return 18
    if meses >= 12:
        return 14
    return _VACACIONES_PROPORCIONALES.get(meses, 0)


@dataclass(frozen=True)
class Prestaciones:
    meses_servicio: int
    salario_diario: Decimal
    dias_preaviso: int
    preaviso: Decimal
    dias_cesantia: Decimal
    cesantia: Decimal
    dias_vacaciones: int
    vacaciones: Decimal
    regalia: Decimal
    total: Decimal


def _dias_preaviso(meses: int) -> int:
    """Código de Trabajo, art. 76."""
    if meses >= 12:
        return 28
    if meses >= 6:
        return 14
    if meses >= 3:
        return 7
    return 0


def _dias_cesantia(meses: int) -> Decimal:
    """Código de Trabajo, art. 80: 6 días (3 a 6 meses), 13 días (6 meses a
    un año), 21 días por año (1 a 5 años) y 23 días por año (más de 5). La
    fracción de año se toma en proporción a los meses (estimación)."""
    if meses < 3:
        return _CERO
    if meses < 6:
        return Decimal("6")
    if meses < 12:
        return Decimal("13")
    por_anio = Decimal("23") if meses >= 60 else Decimal("21")
    return (por_anio * Decimal(meses) / _DOCE).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def prestaciones(
    salario_mensual: Decimal, fecha_ingreso: date, fecha_salida: date, p: Parametros, *, desahucio: bool = True
) -> Prestaciones:
    """Estimado de prestaciones laborales al terminar el contrato. Con
    `desahucio=False` (renuncia) no hay preaviso ni cesantía: solo
    vacaciones no disfrutadas y regalía proporcional."""
    if fecha_salida < fecha_ingreso:
        raise ValueError("La fecha de salida es anterior a la de ingreso")
    meses = meses_servicio(fecha_ingreso, fecha_salida)
    diario = salario_diario(salario_mensual, p)
    d_preaviso = _dias_preaviso(meses) if desahucio else 0
    d_cesantia = _dias_cesantia(meses) if desahucio else _CERO
    d_vac = dias_vacaciones(fecha_ingreso, fecha_salida)
    preaviso = redondear(diario * d_preaviso)
    cesantia = redondear(diario * d_cesantia)
    vacaciones = redondear(diario * d_vac)
    regalia = regalia_estimada(salario_mensual, fecha_ingreso, fecha_salida.year, fecha_salida)
    return Prestaciones(
        meses_servicio=meses,
        salario_diario=redondear(diario),
        dias_preaviso=d_preaviso,
        preaviso=preaviso,
        dias_cesantia=d_cesantia,
        cesantia=cesantia,
        dias_vacaciones=d_vac,
        vacaciones=vacaciones,
        regalia=regalia,
        total=preaviso + cesantia + vacaciones + regalia,
    )


# --- Archivos ---------------------------------------------------------

def cedula_valida(cedula: str) -> bool:
    """Cédula dominicana: 11 dígitos con dígito verificador (Luhn de la JCE)."""
    if len(cedula) != 11 or not cedula.isdigit():
        return False
    suma = 0
    for i, c in enumerate(cedula[:10]):
        n = int(c) * (1 if i % 2 == 0 else 2)
        suma += n // 10 + n % 10
    return (10 - suma % 10) % 10 == int(cedula[10])
