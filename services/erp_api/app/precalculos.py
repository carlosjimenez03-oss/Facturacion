"""Pre-cálculos de las declaraciones IT-1 (ITBIS mensual), IR-17 (otras
retenciones de ISR, mensual) e IR-2 (ISR de sociedades, anual) y el
calendario de vencimientos de la DGII. Funciones puras (sin base de datos):
reciben los datos ya leídos y los valores manuales (`ajustes`) y devuelven
el pre-cálculo con la estructura de `PreCalculo` de
frontend/src/lib/impuestosApi.ts.

Las secciones, números de casilla, etiquetas y ayudas siguen el prototipo
docs/diseno/prototipo-kontara/pantallas/PreCalculo.dc.html (objetos `it1`,
`ir2` e `ir17`); los valores salen de los e-CF y del 606. Es una ayuda para
llenar el formulario oficial en la Oficina Virtual, no lo sustituye -- ver
docs/ERP_API.md, sección "Impuestos", para las fuentes y las
interpretaciones.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from .formatos import a_decimal, desglose_por_tasa

_CENTAVO = Decimal("0.01")
_CERO = Decimal("0")

MESES = (
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
)
_DIAS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")

# Día del mes siguiente en que vence cada obligación mensual (mismo
# calendario que frontend/src/lib/calendarioFiscal.ts): IR-17 el 10 (Código
# Tributario / instructivo IR-17), formatos 606/607/608 el 15 (Norma General
# 07-2018) e IT-1 el 20.
DIA_VENCE_MENSUAL = {"ir17": 10, "606": 15, "607": 15, "608": 15, "it1": 20}
# IR-2: 120 días después del cierre fiscal (31 de diciembre).
DIAS_VENCE_IR2 = 120

FORMULARIOS_MENSUALES = ("606", "607", "608", "it1", "ir17")

# Categorías del campo 3 del 606, "Tipo de Bienes y Servicios Comprados"
# (docs/dgii/FORMATO_606.md).
TIPOS_BIENES_SERVICIOS = {
    1: "Gastos de personal",
    2: "Gastos por trabajos, suministros y servicios",
    3: "Arrendamientos",
    4: "Gastos de activos fijos",
    5: "Gastos de representación",
    6: "Otras deducciones admitidas",
    7: "Gastos financieros",
    8: "Gastos extraordinarios",
    9: "Compras y gastos que formarán parte del costo de venta",
    10: "Adquisiciones de activos",
    11: "Gastos de seguros",
}
TIPO_COSTO_VENTA = 9
TIPO_ADQUISICION_ACTIVOS = 10

# Campo 17 del 606 ("Tipo de Retención en ISR") -> casilla del IR-17 y su
# tasa. Las tasas cambian con la Ley 30-26: el formulario oficial
# "IR-17-2026 (Julio 2026 en adelante)" sube alquileres, honorarios y otras
# rentas del Art. 309 lit. f de 10 % a 15 % y otras rentas del Decreto
# 139-98 de 2 % a 3 %; el "IR-17-2025" tiene las anteriores.
PERIODO_LEY_30_26 = "202607"


@dataclass(frozen=True)
class RetencionIsr:
    etiqueta: str
    casilla_ir17: str
    tasa: Decimal
    tasa_desde_ley_30_26: Decimal


RETENCIONES_ISR = {
    1: RetencionIsr("Alquileres", "1", Decimal("0.10"), Decimal("0.15")),
    2: RetencionIsr("Honorarios por servicios independientes", "2", Decimal("0.10"), Decimal("0.15")),
    3: RetencionIsr("Otras rentas (Art. 309 lit. f)", "16", Decimal("0.10"), Decimal("0.15")),
    4: RetencionIsr("Otras rentas presuntas (Decreto 139-98, Art. 70)", "17", Decimal("0.02"), Decimal("0.03")),
    5: RetencionIsr(
        "Intereses pagados a personas jurídicas residentes", "19", Decimal("0.01"), Decimal("0.01")
    ),
    6: RetencionIsr(
        "Intereses pagados a personas físicas residentes", "11", Decimal("0.10"), Decimal("0.10")
    ),
    7: RetencionIsr("Pagos a proveedores del Estado", "12", Decimal("0.05"), Decimal("0.05")),
    8: RetencionIsr("Juegos telefónicos", "13", Decimal("0.05"), Decimal("0.05")),
    9: RetencionIsr(
        "Compras a proveedores informales de ganadería de carne bovina", "21", Decimal("0.01"), Decimal("0.01")
    ),
}

# IR-2: tasa general (Art. 297 del Código Tributario) y la transitoria de
# la Ley 30-26 para los ejercicios 2026-2028 de quienes ingresan
# RD$1,000 millones o más.
TASA_ISR = Decimal("0.27")
TASA_ISR_TRANSITORIA = Decimal("0.30")
TOPE_TASA_TRANSITORIA = Decimal("1000000000")
ANIOS_TASA_TRANSITORIA = range(2026, 2029)

# Valores que el sistema no puede saber: clave -> (etiqueta, ayuda).
AJUSTES: dict[str, dict[str, tuple[str, str]]] = {
    "it1": {
        "itbis_retenido_terceros": (
            "ITBIS retenido por terceros",
            "ITBIS que le retuvieron sus clientes (Estado, tarjetas de crédito, sociedades) según los "
            "comprobantes de retención recibidos.",
        ),
        "saldo_favor_anterior": (
            "Saldo a favor del período anterior",
            "Nuevo saldo a favor de la declaración IT-1 del mes anterior. Por defecto se toma el que "
            "resulta del pre-cálculo del mes anterior.",
        ),
    },
    "ir17": {
        "recargos": (
            "Recargos e intereses",
            "Solo si presenta o paga después del vencimiento: recargo por mora e interés indemnizatorio "
            "(calculadora de la DGII).",
        ),
    },
    "ir2": {
        "inventario_inicial": (
            "Inventario inicial",
            "Valor del inventario al 1 de enero (inventario final del ejercicio anterior).",
        ),
        "inventario_final": (
            "Inventario final",
            "Por defecto, la valuación actual del Kardex (existencia × costo promedio). Ingrese el valor "
            "real al 31 de diciembre.",
        ),
        "depreciacion": (
            "Depreciación del ejercicio",
            "Depreciación fiscal de los activos (las adquisiciones de activos del 606 no son gasto del año).",
        ),
        "ajustes_fiscales": (
            "Ajustes fiscales",
            "Gastos no deducibles (sin comprobante válido, multas, recargos) menos ingresos exentos u otros "
            "ajustes admitidos por el Código Tributario. Puede ser negativo.",
        ),
        "anticipos": ("Anticipos pagados", "Anticipos del ISR pagados durante el ejercicio."),
        "retenciones_isr": (
            "Retenciones de ISR que nos hicieron",
            "ISR que le retuvieron sus clientes (p. ej. el Estado) durante el ejercicio.",
        ),
    },
}


def redondear(valor: Decimal) -> Decimal:
    return valor.quantize(_CENTAVO, rounding=ROUND_HALF_UP)


# --- Calendario ---------------------------------------------------------

def _habil(fecha: date) -> date:
    """Sábado o domingo -> lunes siguiente (como calendarioFiscal.ts). Los
    feriados no se trasladan aquí."""
    if fecha.weekday() == 5:
        return fecha + timedelta(days=2)
    if fecha.weekday() == 6:
        return fecha + timedelta(days=1)
    return fecha


def vence(formulario: str, periodo: str) -> date:
    """Fecha límite de presentación/pago: el día fijo del mes siguiente al
    período (AAAAMM) o, para el IR-2 (AAAA), 120 días después del 31 de
    diciembre (30 de abril; 29 si el año siguiente es bisiesto)."""
    if formulario == "ir2":
        return _habil(date(int(periodo), 12, 31) + timedelta(days=DIAS_VENCE_IR2))
    anio, mes = int(periodo[:4]), int(periodo[4:6])
    anio, mes = (anio + 1, 1) if mes == 12 else (anio, mes + 1)
    return _habil(date(anio, mes, DIA_VENCE_MENSUAL[formulario]))


def fecha_larga(fecha: date) -> str:
    return f"{_DIAS[fecha.weekday()]} {fecha.day} de {MESES[fecha.month - 1]} de {fecha.year}"


def nombre_periodo(periodo: str) -> str:
    return periodo if len(periodo) == 4 else f"{MESES[int(periodo[4:6]) - 1]} {periodo[:4]}"


def periodo_anterior(periodo: str) -> str:
    anio, mes = int(periodo[:4]), int(periodo[4:6])
    return f"{anio - 1}12" if mes == 1 else f"{anio}{mes - 1:02d}"


def estado_declaracion(vence_en: date, presentada: bool, hoy: date) -> str:
    if presentada:
        return "presentada"
    return "vencida" if vence_en < hoy else "pendiente"


def _resumen_vence(fecha: date, hoy: date) -> str:
    dias = (fecha - hoy).days
    cuando = f"en {dias} días" if dias > 1 else ("mañana" if dias == 1 else ("hoy" if dias == 0 else f"hace {-dias} días"))
    return f"Vence {fecha:%d/%m/%Y} · {cuando}"


# --- Estructura de la respuesta ---------------------------------------------------------

def casilla(codigo: str, etiqueta: str, valor: Decimal, tipo: str = "normal", ayuda: str | None = None) -> dict:
    return {"codigo": codigo, "etiqueta": etiqueta, "valor": redondear(valor), "tipo": tipo, "ayuda": ayuda}


def lista_ajustes(formulario: str, valores: dict[str, Decimal]) -> list[dict]:
    return [
        {"clave": clave, "etiqueta": etiqueta, "valor": redondear(valores.get(clave, _CERO)), "ayuda": ayuda}
        for clave, (etiqueta, ayuda) in AJUSTES[formulario].items()
    ]


def _valor(ajustes: dict[str, Decimal], clave: str, por_defecto: Decimal = _CERO) -> Decimal:
    valor = ajustes.get(clave)
    return por_defecto if valor is None else valor


def _fmt(valor: Decimal) -> str:
    """Monto para un texto (etiquetas, advertencias): "1,234.56"."""
    return f"{redondear(valor):,.2f}"


def _monto(valor: Decimal) -> str:
    """Monto del `resumen`: decimal plano ("1234.56"); el frontend lo
    formatea como monto. Cualquier otro texto ("35.2%") se muestra tal cual."""
    return f"{redondear(valor):.2f}"


# --- IT-1 ---------------------------------------------------------

@dataclass
class VentasItbis:
    """Ventas del período por tasa (montos sin ITBIS; notas de crédito ya restadas)."""

    g18: Decimal = _CERO
    g16: Decimal = _CERO
    exportaciones: Decimal = _CERO
    exentas: Decimal = _CERO
    # Parte de `exentas` que es exenta por destino (ventas a regímenes
    # especiales, E44): cuenta como gravada para la proporcionalidad.
    exentas_por_destino: Decimal = _CERO
    i18: Decimal = _CERO
    i16: Decimal = _CERO
    retenciones_en_ecf: Decimal = _CERO


def acumular_ventas(facturas: list[dict[str, Any]]) -> VentasItbis:
    """Suma el desglose por tasa de los e-CF de venta (E34 resta, E33 suma)."""
    ventas = VentasItbis()
    for factura in facturas:
        signo = Decimal(-1) if factura["tipo_ecf"] == 34 else Decimal(1)
        d = desglose_por_tasa(factura["datos"])
        ventas.g18 += signo * d["g18"]
        ventas.g16 += signo * d["g16"]
        ventas.exportaciones += signo * d["g0"]
        ventas.exentas += signo * d["exento"]
        if factura["tipo_ecf"] == 44:
            ventas.exentas_por_destino += signo * d["exento"]
        ventas.i18 += signo * d["i18"]
        ventas.i16 += signo * d["i16"]
        totales = (factura["datos"].get("Encabezado") or {}).get("Totales") or {}
        ventas.retenciones_en_ecf += signo * a_decimal(totales.get("TotalITBISRetenido"))
    return ventas


@dataclass
class Compras606:
    itbis_facturado: Decimal = _CERO
    itbis_costo: Decimal = _CERO
    itbis_proporcionalidad: Decimal = _CERO


def calcular_it1(
    *,
    periodo: str,
    ventas: VentasItbis,
    compras: Compras606,
    itbis_retenido_proveedores: Decimal,
    ajustes: dict[str, Decimal],
    saldo_favor_sugerido: Decimal,
    hoy: date,
) -> tuple[dict[str, Any], Decimal]:
    """Devuelve (pre-cálculo, nuevo saldo a favor) -- el saldo a favor es el
    valor sugerido de "saldo a favor anterior" del mes siguiente."""
    total_ingresos = ventas.g18 + ventas.g16 + ventas.exportaciones + ventas.exentas
    itbis_facturado = ventas.i18 + ventas.i16
    # ITBIS por adelantar del 606 (campo 15 = facturado - llevado al costo).
    adelantado = compras.itbis_facturado - compras.itbis_costo
    # Coeficiente de proporcionalidad (casilla 54 del Anexo A del IT-1):
    # (exportaciones + exentas por destino + gravadas) / total de operaciones.
    hay_exentas = ventas.exentas - ventas.exentas_por_destino > 0
    if total_ingresos > 0 and hay_exentas:
        admitidas = ventas.g18 + ventas.g16 + ventas.exportaciones + ventas.exentas_por_destino
        proporcion = min(max(admitidas / total_ingresos, _CERO), Decimal(1))
    else:
        proporcion = Decimal(1)
    advertencias: list[str] = []
    if proporcion < 1:
        if compras.itbis_proporcionalidad > 0:
            sujeto = min(compras.itbis_proporcionalidad, adelantado)
        else:
            # Sin ITBIS separado como "sujeto a proporcionalidad" en el 606,
            # se aplica el coeficiente a todo el adelantado: es lo
            # conservador (nunca deduce de más).
            sujeto = adelantado
            advertencias.append(
                "Hubo ventas exentas y en el 606 no se separó el ITBIS sujeto a proporcionalidad: se aplicó el "
                "coeficiente a todo el ITBIS adelantado. Si parte del ITBIS es directamente imputable a ventas "
                "gravadas, clasifíquelo en el 606."
            )
        admitido = redondear(adelantado - sujeto + sujeto * proporcion)
    else:
        admitido = redondear(adelantado)
    no_admitido = adelantado - admitido
    retenido_terceros = _valor(ajustes, "itbis_retenido_terceros")
    saldo_anterior = _valor(ajustes, "saldo_favor_anterior", saldo_favor_sugerido)
    propio = itbis_facturado - admitido - retenido_terceros - saldo_anterior
    total_pagar = max(propio, _CERO) + itbis_retenido_proveedores
    nuevo_saldo_favor = max(-propio, _CERO)
    if ventas.retenciones_en_ecf and not ajustes.get("itbis_retenido_terceros"):
        advertencias.append(
            f"Sus e-CF del período informan RD${_fmt(ventas.retenciones_en_ecf)} de ITBIS retenido por los "
            "clientes: regístrelo en \"ITBIS retenido por terceros\" cuando tenga los comprobantes de retención."
        )
    if nuevo_saldo_favor and itbis_retenido_proveedores:
        advertencias.append(
            f"Queda un saldo a favor de RD${_fmt(nuevo_saldo_favor)} para el mes siguiente; las retenciones "
            "efectuadas a proveedores se pagan igual."
        )
    fecha_vence = vence("it1", periodo)
    pct = redondear(proporcion * 100)
    secciones = [
        {"numero": "I", "titulo": "Ingresos por operaciones", "casillas": [
            casilla("1", "Operaciones gravadas al 18%", ventas.g18),
            casilla("2", "Operaciones gravadas al 16%", ventas.g16),
            casilla("3", "Exportaciones de bienes", ventas.exportaciones, ayuda=(
                "Tasa 0%: no generan ITBIS pero conservan el derecho a deducir el ITBIS adelantado.")),
            casilla("4", "Operaciones exentas", ventas.exentas, ayuda=(
                "Ventas de bienes y servicios exentos (alimentos básicos, transporte de carga, etc.). Afectan la "
                "proporcionalidad, salvo las exentas por destino (ventas a regímenes especiales, E44"
                + (f": RD${_fmt(ventas.exentas_por_destino)}" if ventas.exentas_por_destino else "") + ").")),
            casilla("5", "Total de ingresos", total_ingresos, "subtotal"),
        ]},
        {"numero": "II", "titulo": "ITBIS facturado", "casillas": [
            casilla("6", "ITBIS 18%", ventas.i18),
            casilla("7", "ITBIS 16%", ventas.i16),
            casilla("8", "Total ITBIS facturado", itbis_facturado, "subtotal"),
        ]},
        {"numero": "III", "titulo": "ITBIS adelantado", "casillas": [
            casilla("9", "ITBIS pagado en compras y servicios", adelantado, ayuda=(
                "ITBIS por adelantar del Formato 606 del período (ITBIS facturado menos el llevado al costo).")),
            casilla("10", f"Proporción admitida: {pct}%", -no_admitido, ayuda=(
                "Proporcionalidad: solo se admite el ITBIS adelantado en la proporción de ventas gravadas y "
                "exportaciones sobre el total, porque hubo ventas exentas. El valor es el ITBIS no admitido.")),
            casilla("11", "ITBIS adelantado admitido", -admitido, "subtotal"),
        ]},
        {"numero": "IV", "titulo": "Retenciones", "casillas": [
            casilla("12", "ITBIS retenido por terceros (nos retuvieron)", -retenido_terceros, ayuda=(
                "Retenciones que nos hicieron clientes como el Estado o tarjetas de crédito. Se restan del "
                "impuesto a pagar.")),
            casilla("13", "ITBIS retenido a proveedores (efectuadas)", itbis_retenido_proveedores, ayuda=(
                "ITBIS que usted retuvo a sus proveedores y debe pagar en esta declaración (606 con fecha de "
                "pago en el período).")),
            casilla("14", "Saldo a favor del período anterior", -saldo_anterior),
        ]},
        {"numero": "V", "titulo": "Liquidación", "casillas": [
            casilla("15", "Impuesto por operaciones propias", propio, "subtotal"),
            casilla("16", "Total a pagar", total_pagar, "total"),
        ]},
    ]
    if propio < 0 and not itbis_retenido_proveedores:
        resultado = {"etiqueta": "Saldo a favor", "valor": redondear(nuevo_saldo_favor)}
    else:
        resultado = {"etiqueta": "ITBIS a pagar", "valor": redondear(total_pagar)}
    precalculo = {
        "formulario": "it1",
        "periodo": periodo,
        "titulo": f"Pre-cálculo IT-1 · {nombre_periodo(periodo)}",
        "subtitulo": f"Declaración jurada mensual del ITBIS · vence el {fecha_larga(fecha_vence)}",
        "nota": (
            "Calculado con los e-CF de venta aceptados por la DGII (607) y las compras registradas en el 606 del "
            "período. Se actualiza en tiempo real."
        ),
        "vence": fecha_vence,
        "secciones": secciones,
        "resultado": resultado,
        "resumen": [
            {"etiqueta": "ITBIS facturado", "valor": _monto(itbis_facturado)},
            {"etiqueta": "(−) Adelantado admitido", "valor": _monto(admitido)},
            {"etiqueta": "(−) Retenido por terceros", "valor": _monto(retenido_terceros)},
            {"etiqueta": "(+) Retenciones efectuadas", "valor": _monto(itbis_retenido_proveedores)},
            {"etiqueta": "Nuevo saldo a favor", "valor": _monto(nuevo_saldo_favor)},
            {"etiqueta": "Vencimiento", "valor": _resumen_vence(fecha_vence, hoy)},
        ],
        "ajustes": lista_ajustes("it1", {**ajustes, "saldo_favor_anterior": saldo_anterior}),
        "advertencias": advertencias,
    }
    return precalculo, nuevo_saldo_favor


# --- IR-17 ---------------------------------------------------------

def calcular_ir17(
    *, periodo: str, retenciones: list[dict[str, Any]], ajustes: dict[str, Decimal], hoy: date
) -> dict[str, Any]:
    """`retenciones`: filas del 606 pagadas en el período agrupadas por
    `tipo_retencion_isr` (None si falta) con `monto_retencion_renta` y
    `monto_pagado` (servicios + bienes)."""
    nueva_ley = periodo >= PERIODO_LEY_30_26
    filas: list[dict] = []
    resumen: list[dict] = []
    advertencias: list[str] = []
    total = _CERO
    for grupo in sorted(retenciones, key=lambda g: (g["tipo_retencion_isr"] is None, g["tipo_retencion_isr"] or 0)):
        tipo = grupo["tipo_retencion_isr"]
        monto = grupo["monto_retencion_renta"]
        total += monto
        codigo = str(len(filas) + 1)
        definicion = RETENCIONES_ISR.get(tipo) if tipo is not None else None
        if definicion is None:
            filas.append(casilla(codigo, "Retenciones sin tipo de retención en el 606", monto, ayuda=(
                "Renglones del 606 con monto retenido pero sin 'Tipo de Retención en ISR': clasifíquelos.")))
            advertencias.append("Hay retenciones de ISR en el 606 sin tipo de retención: clasifíquelas en el 606.")
            resumen.append({"etiqueta": "Sin tipo", "valor": _monto(monto)})
            continue
        tasa = definicion.tasa_desde_ley_30_26 if nueva_ley else definicion.tasa
        pct = f"{redondear(tasa * 100).normalize():f}"
        filas.append(casilla(
            codigo,
            f"{definicion.etiqueta} · {pct}% de RD$ {_fmt(grupo['monto_pagado'])}",
            monto,
            ayuda=(
                f"Casilla {definicion.casilla_ir17} del IR-17 (tasa {pct}%). Suma del 'Monto Retención Renta' "
                "del 606 de las facturas pagadas en el período; la base mostrada es el monto facturado de esas "
                "facturas."
            ),
        ))
        resumen.append({"etiqueta": definicion.etiqueta, "valor": _monto(monto)})
    n = len(filas)
    recargos = _valor(ajustes, "recargos")
    total_pagar = total + recargos
    fecha_vence = vence("ir17", periodo)
    secciones = [
        {"numero": "I", "titulo": "Retenciones de ISR efectuadas", "casillas": filas + [
            casilla(str(n + 1), "Total retenido", total, "subtotal")]},
        {"numero": "II", "titulo": "Liquidación", "casillas": [
            casilla(str(n + 2), "Recargos e intereses", recargos),
            casilla(str(n + 3), "Total a pagar", total_pagar, "total"),
        ]},
    ]
    if not retenciones:
        advertencias.append(
            "No hay facturas del 606 pagadas en el período con retención de ISR. Si no retuvo, no presenta "
            "IR-17 (solo aplica a quienes realizaron retenciones)."
        )
    return {
        "formulario": "ir17",
        "periodo": periodo,
        "titulo": f"Pre-cálculo IR-17 · {nombre_periodo(periodo)}",
        "subtitulo": f"Declaración y pago de retenciones de ISR · vence el {fecha_larga(fecha_vence)}",
        "nota": (
            "Incluye las retenciones de las facturas de compra pagadas en el período (fecha de pago del 606), "
            "sin importar su fecha de emisión."
            + (" Tasas del formulario IR-17-2026 (Ley 30-26)." if nueva_ley else " Tasas del formulario IR-17-2025.")
        ),
        "vence": fecha_vence,
        "secciones": secciones,
        "resultado": {"etiqueta": "ISR retenido a pagar", "valor": redondear(total_pagar)},
        "resumen": resumen + [{"etiqueta": "Vencimiento", "valor": _resumen_vence(fecha_vence, hoy)}],
        "ajustes": lista_ajustes("ir17", ajustes),
        "advertencias": advertencias,
    }


# --- IR-2 ---------------------------------------------------------

def calcular_ir2(
    *,
    anio: str,
    ingresos: Decimal,
    compras_por_tipo: dict[int | None, Decimal],
    valuacion_inventario: Decimal,
    ajustes: dict[str, Decimal],
    hoy: date,
) -> dict[str, Any]:
    """`compras_por_tipo`: monto del 606 del año (servicios + bienes + ITBIS
    llevado al costo) por tipo de bienes y servicios (None = sin clasificar)."""
    advertencias: list[str] = []
    inventario_inicial = _valor(ajustes, "inventario_inicial")
    inventario_final = _valor(ajustes, "inventario_final", valuacion_inventario)
    if ajustes.get("inventario_final") is None:
        advertencias.append(
            "El inventario final es la valuación actual del Kardex (existencia × costo promedio): solo es exacta "
            "al cierre del 31 de diciembre. Ingrese el valor real como ajuste."
        )
    compras = compras_por_tipo.get(TIPO_COSTO_VENTA, _CERO)
    costo = inventario_inicial + compras - inventario_final
    utilidad_bruta = ingresos - costo
    gastos = [
        (TIPOS_BIENES_SERVICIOS[tipo], monto)
        for tipo, monto in sorted(compras_por_tipo.items(), key=lambda kv: (kv[0] is None, kv[0] or 0))
        if tipo not in (TIPO_COSTO_VENTA, TIPO_ADQUISICION_ACTIVOS, None) and monto
    ]
    depreciacion = _valor(ajustes, "depreciacion")
    if depreciacion:
        gastos.append(("Depreciación", depreciacion))
    if compras_por_tipo.get(TIPO_ADQUISICION_ACTIVOS):
        advertencias.append(
            f"Las adquisiciones de activos del 606 (RD${_fmt(compras_por_tipo[TIPO_ADQUISICION_ACTIVOS])}) no se "
            "restan como gasto: se deprecian. Ingrese la depreciación del ejercicio como ajuste."
        )
    if compras_por_tipo.get(None):
        advertencias.append(
            f"Hay compras del 606 sin 'Tipo de Bienes y Servicios' (RD${_fmt(compras_por_tipo[None])}) que no se "
            "incluyeron: clasifíquelas en el 606."
        )
    total_gastos = sum((monto for _, monto in gastos), _CERO)
    renta_contable = utilidad_bruta - total_gastos
    ajustes_fiscales = _valor(ajustes, "ajustes_fiscales")
    renta_imponible = renta_contable + ajustes_fiscales
    tasa = TASA_ISR
    if int(anio) in ANIOS_TASA_TRANSITORIA and ingresos >= TOPE_TASA_TRANSITORIA:
        tasa = TASA_ISR_TRANSITORIA
        advertencias.append(
            "Ingresos de RD$1,000 millones o más: la Ley 30-26 aplica la tasa transitoria de 30% en los "
            "ejercicios 2026 a 2028."
        )
    impuesto = redondear(max(renta_imponible, _CERO) * tasa)
    anticipos = _valor(ajustes, "anticipos")
    retenciones = _valor(ajustes, "retenciones_isr")
    saldo = impuesto - anticipos - retenciones
    pct = f"{(tasa * 100).normalize():f}"
    codigo = 6
    filas_gastos = []
    for etiqueta, monto in gastos:
        codigo += 1
        filas_gastos.append(casilla(str(codigo), etiqueta, -monto))
    c = codigo + 1  # siguiente casilla libre
    fecha_vence = vence("ir2", anio)
    secciones = [
        {"numero": "I", "titulo": "Ingresos", "casillas": [
            casilla("1", "Ingresos brutos por ventas", ingresos, "subtotal", ayuda=(
                "Ventas del año sin ITBIS según los e-CF aceptados (las notas de crédito restan)."))]},
        {"numero": "II", "titulo": "Costo de ventas", "casillas": [
            casilla("2", f"Inventario inicial (01/01/{anio})", inventario_inicial),
            casilla("3", "(+) Compras del período", compras, ayuda=(
                "Compras del 606 clasificadas como 'Compras y gastos que formarán parte del costo de venta' (09).")),
            casilla("4", "(−) Inventario final", -inventario_final, ayuda=(
                "Valor del inventario según el Kardex valorizado (costo promedio ponderado).")),
            casilla("5", "Costo de ventas", costo, "subtotal"),
            casilla("6", "Utilidad bruta", utilidad_bruta, "subtotal"),
        ]},
        {"numero": "III", "titulo": "Gastos deducibles", "casillas": filas_gastos + [
            casilla(str(c), "Total gastos", -total_gastos, "subtotal")]},
        {"numero": "IV", "titulo": "Renta imponible y liquidación", "casillas": [
            casilla(str(c + 1), "Renta neta contable", renta_contable),
            casilla(str(c + 2), "(+) Ajustes fiscales: gastos no deducibles", ajustes_fiscales, ayuda=(
                "Gastos sin comprobante válido, multas y recargos, y otros no admitidos por el Código Tributario.")),
            casilla(str(c + 3), "Renta neta imponible", renta_imponible, "subtotal"),
            casilla(str(c + 4), f"Impuesto liquidado ({pct}%)", impuesto),
            casilla(str(c + 5), "(−) Anticipos pagados", -anticipos),
            casilla(str(c + 6), "(−) Retenciones de ISR que nos hicieron", -retenciones),
            casilla(str(c + 7), "Saldo a pagar estimado", saldo, "total"),
        ]},
    ]
    margen = (utilidad_bruta / ingresos * 100) if ingresos else _CERO
    tasa_efectiva = (impuesto / ingresos * 100) if ingresos else _CERO
    return {
        "formulario": "ir2",
        "periodo": anio,
        "titulo": f"Pre-cálculo IR-2 · ejercicio {anio}",
        "subtitulo": (
            f"Declaración jurada anual de sociedades · vence el {fecha_larga(fecha_vence)}"
        ),
        "nota": (
            "Proyección con las ventas (e-CF aceptados) y las compras del 606 del año. El inventario final se "
            "toma del Kardex valorizado."
        ),
        "vence": fecha_vence,
        "secciones": secciones,
        "resultado": (
            {"etiqueta": "Saldo a favor estimado", "valor": redondear(-saldo)}
            if saldo < 0
            else {"etiqueta": "ISR estimado a pagar", "valor": redondear(saldo)}
        ),
        "resumen": [
            {"etiqueta": "Utilidad bruta", "valor": _monto(utilidad_bruta)},
            {"etiqueta": "Margen bruto", "valor": f"{margen:.1f}%"},
            {"etiqueta": "Renta neta imponible", "valor": _monto(renta_imponible)},
            {"etiqueta": "Tasa efectiva s/ ingresos", "valor": f"{tasa_efectiva:.2f}%"},
        ],
        "ajustes": lista_ajustes("ir2", {**ajustes, "inventario_final": inventario_final}),
        "advertencias": advertencias,
    }
