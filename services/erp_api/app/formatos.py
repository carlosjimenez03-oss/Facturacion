"""Formatos de envío de datos 607 (ventas) y 608 (comprobantes anulados)
de la DGII (Norma General 07-2018 y 05-2019): mapeo de los e-CF de venta a
los 23 campos del 607, catálogo de tipos de anulación, validación y armado
de los archivos de texto. Funciones puras (sin base de datos), para poder
probarlas sin Postgres. Ver docs/ERP_API.md, sección "Impuestos".

El formato exacto de los TXT (delimitador, orden de campos, línea de
encabezado, CRLF sin salto final, tipo de ingreso de un dígito) se tomó de
las macros `GenerarArchivo`/`cmdGenerarArchivo_Click` de las plantillas
oficiales "Herramienta de Envío Formato 607" y "Herramienta de envío
Formato 608" (versión NG 07-2018 y 05-2019), igual que el 606 de
services/formato_606/app/generador.py.

IMPORTANTE (DGII, Comunidad de Ayuda, respuestas oficiales con base legal en
la Norma General 01-20 y la Ley 32-23): los emisores electrónicos NO
reportan sus e-CF en el 607 ni sus e-NCF anulados en el 608 -- la DGII ya
recibe los e-CF en XML y las anulaciones por el servicio ANECF / notas de
crédito. Las plantillas oficiales, de hecho, rechazan NCF de 13 posiciones
(solo aceptan 11 o 19). Por eso el 607/608 de aquí muestra los e-CF como
referencia (cuadre con el IT-1) pero el TXT solo lleva comprobantes que la
DGII sí espera en el formato (NCF serie B/A/P/Q de 11 o 19 posiciones).
"""
from __future__ import annotations

import re
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

_CENTAVO = Decimal("0.01")
_CERO = Decimal("0")

# Tipos de e-CF de venta que van al 607 (las compras/pagos 41, 43 y 47 van
# al 606).
TIPOS_VENTA_607 = (31, 32, 33, 34, 44, 45, 46)

# Norma General 10-18: desde el período julio 2018, las facturas de consumo
# solo se reportan una a una (con la identificación del comprador) cuando
# valen RD$250,000.00 o más; las menores van en el "Resumen General de
# Facturas de Consumo" de la Oficina Virtual (cantidad y monto).
TOPE_CONSUMO = Decimal("250000")

# Tabla "Tipo de Anulación" del instructivo oficial del 608 (Impuestos
# Internos, "Llenado y Remisión del Formato de Envío de Comprobantes
# Fiscales Anulados (608)").
TIPOS_ANULACION: dict[str, str] = {
    "01": "Deterioro de factura preimpresa",
    "02": "Errores de impresión (factura preimpresa)",
    "03": "Impresión defectuosa",
    "04": "Corrección de la información",
    "05": "Cambio de productos",
    "06": "Devolución de productos",
    "07": "Omisión de productos",
    "08": "Errores en secuencia de NCF",
    "09": "Por cese de operaciones",
    "10": "Pérdida o hurto de talonarios",
}

# Las anulaciones de rangos de e-NCF (ANECF) son de secuencias no usadas o
# de e-CF firmados que nunca se enviaron: el motivo que mejor las describe
# en la tabla del 608 es "Errores en secuencia de NCF". Solo es informativo
# (los e-NCF no van en el TXT del 608, ver el docstring del módulo).
TIPO_ANULACION_ANECF = "08"

# Tope de e-NCF que se despliegan por período a partir de los rangos ANECF
# (la plantilla oficial del 608 admite hasta 4,999 registros).
MAXIMO_ENCF_ANULADOS = 4999

_TIPO_INGRESO_POR_DEFECTO = "01"

# Columnas de forma de pago del 607 (campos 17 a 23) según el código
# FormaPago de la TablaFormasPago del e-CF. El 607 no tiene columna de "nota
# de crédito" (código 7): va en "otras formas".
_COLUMNA_FORMA_PAGO = {
    "1": "efectivo",
    "2": "cheque_transferencia",
    "3": "tarjeta",
    "4": "venta_credito",
    "5": "bonos_certificados",
    "6": "permuta",
    "7": "otras_formas",
    "8": "otras_formas",
}
COLUMNAS_FORMA_PAGO = (
    "efectivo",
    "cheque_transferencia",
    "tarjeta",
    "venta_credito",
    "bonos_certificados",
    "permuta",
    "otras_formas",
)
COLUMNAS_MONTO_607 = (
    "monto_facturado",
    "itbis_facturado",
    "itbis_retenido_terceros",
    "itbis_percibido",
    "retencion_renta_terceros",
    "isr_percibido",
    "impuesto_selectivo_consumo",
    "otros_impuestos",
    "monto_propina_legal",
) + COLUMNAS_FORMA_PAGO

# Código de la tabla de impuestos adicionales del e-CF que corresponde a la
# propina legal (10 %, Ley 54-32).
_CODIGO_PROPINA_LEGAL = "001"

# Patrones de NCF que aceptan las plantillas oficiales (constante
# `strRegexNcf` del 607 y validación de serie/tipo del 608). Ninguna acepta
# e-NCF de 13 posiciones.
_REGEX_NCF_607 = re.compile(
    r"^(([AQP][0-9]{8}((0[1-4])|(1[1-5]))[0-9]{8})|(B((0[1-4])|(1[1-6]))[0-9]{8}))$", re.IGNORECASE
)
_TIPOS_NCF_608 = frozenset({"01", "02", "03", "04", "11", "12", "13", "14", "15", "16", "17"})
_REGEX_ENCF = re.compile(r"^E[0-9]{12}$", re.IGNORECASE)


def _redondear(valor: Decimal) -> Decimal:
    return valor.quantize(_CENTAVO, rounding=ROUND_HALF_UP)


def a_decimal(valor: Any) -> Decimal:
    """Monto del e-CF (string/número) -> Decimal; 0 si falta o no es numérico."""
    if valor is None or valor == "":
        return _CERO
    try:
        return Decimal(str(valor).strip())
    except ArithmeticError:
        return _CERO


def fecha_dgii(valor: Any) -> date | None:
    """DD-MM-AAAA del e-CF -> date (None si falta o es inválida)."""
    if not isinstance(valor, str) or not re.fullmatch(r"\d{2}-\d{2}-\d{4}", valor.strip()):
        return None
    dia, mes, anio = (int(p) for p in valor.strip().split("-"))
    try:
        return date(anio, mes, dia)
    except ValueError:
        return None


def aaaammdd(fecha: date | None) -> str:
    return fecha.strftime("%Y%m%d") if fecha is not None else ""


def es_encf(ncf: str | None) -> bool:
    return bool(ncf) and bool(_REGEX_ENCF.match(ncf))


def _lista(valor: Any) -> list[Any]:
    if valor is None:
        return []
    return valor if isinstance(valor, list) else [valor]


def _identificacion_comprador(comprador: dict[str, Any]) -> tuple[str, int]:
    """RNC/cédula (solo dígitos) o el identificador extranjero, y su tipo
    según el 607: 9 dígitos -> 1 (RNC), 11 -> 2 (cédula), otro -> 3
    (pasaporte / identificación tributaria). Sin identificación -> ("", 3):
    el contrato exige un número y en el TXT el campo queda vacío (ver
    `linea_607`)."""
    rnc = re.sub(r"[^0-9A-Za-z]", "", str(comprador.get("RNCComprador") or "")).upper()
    if not rnc:
        rnc = re.sub(r"\s", "", str(comprador.get("IdentificadorExtranjero") or "")).upper()
    if not rnc:
        return "", 3
    if rnc.isdigit() and len(rnc) == 9:
        return rnc, 1
    if rnc.isdigit() and len(rnc) == 11:
        return rnc, 2
    return rnc, 3


def _impuestos_adicionales(totales: dict[str, Any]) -> tuple[Decimal, Decimal, Decimal]:
    """(selectivo al consumo, otros impuestos/tasas, propina legal) a partir
    de Totales.ImpuestosAdicionales del e-CF."""
    selectivo = otros = propina = _CERO
    tabla = totales.get("ImpuestosAdicionales") or {}
    for impuesto in _lista(tabla.get("ImpuestoAdicional") if isinstance(tabla, dict) else tabla):
        if not isinstance(impuesto, dict):
            continue
        selectivo += a_decimal(impuesto.get("MontoImpuestoSelectivoConsumoEspecifico"))
        selectivo += a_decimal(impuesto.get("MontoImpuestoSelectivoConsumoAdvalorem"))
        otro = a_decimal(impuesto.get("OtrosImpuestosAdicionales"))
        if str(impuesto.get("TipoImpuesto") or "") == _CODIGO_PROPINA_LEGAL:
            propina += otro
        else:
            otros += otro
    return selectivo, otros, propina


def desglose_por_tasa(datos: dict[str, Any]) -> dict[str, Decimal]:
    """Montos sin impuestos por tasa de ITBIS y el ITBIS de cada tasa de un
    e-CF: {g18, g16, g0, exento, i18, i16}. Usa Totales.MontoGravadoI1/I2/I3,
    MontoExento y TotalITBIS1/2 cuando el e-CF los trae; si no, los deriva de
    las líneas (IndicadorFacturacion 1 = 18 %, 2 = 16 %, 3 = 0 %, 4 =
    exento; 0 = no facturable, no cuenta), descontando el ITBIS incluido si
    IndicadorMontoGravado = 1."""
    encabezado = datos.get("Encabezado") or {}
    totales = encabezado.get("Totales") or {}
    id_doc = encabezado.get("IdDoc") or {}
    tasas = {"1": Decimal("0.18"), "2": Decimal("0.16"), "3": _CERO, "4": _CERO}
    claves = {"1": "g18", "2": "g16", "3": "g0", "4": "exento"}
    desde_lineas = {"g18": _CERO, "g16": _CERO, "g0": _CERO, "exento": _CERO}
    itbis_incluido = str(id_doc.get("IndicadorMontoGravado") or "0") == "1"
    for item in _lista((datos.get("DetallesItems") or {}).get("Item")):
        if not isinstance(item, dict):
            continue
        indicador = str(item.get("IndicadorFacturacion") or "")
        if indicador not in claves:
            continue
        monto = a_decimal(item.get("MontoItem"))
        if itbis_incluido and tasas[indicador]:
            monto = monto / (1 + tasas[indicador])
        desde_lineas[claves[indicador]] += monto

    def total_o_lineas(clave_total: str, clave: str) -> Decimal:
        if totales.get(clave_total) not in (None, ""):
            return a_decimal(totales.get(clave_total))
        return _redondear(desde_lineas[clave])

    resultado = {
        "g18": total_o_lineas("MontoGravadoI1", "g18"),
        "g16": total_o_lineas("MontoGravadoI2", "g16"),
        "g0": total_o_lineas("MontoGravadoI3", "g0"),
        "exento": total_o_lineas("MontoExento", "exento"),
    }
    i18 = totales.get("TotalITBIS1")
    i16 = totales.get("TotalITBIS2")
    total_itbis = totales.get("TotalITBIS")
    if i18 not in (None, "") or i16 not in (None, ""):
        resultado["i18"], resultado["i16"] = a_decimal(i18), a_decimal(i16)
    elif total_itbis not in (None, "") and not (resultado["g18"] and resultado["g16"]):
        # Una sola tasa gravada: el TotalITBIS del e-CF es el de esa tasa.
        clave = "i16" if resultado["g16"] else "i18"
        resultado["i18"], resultado["i16"] = _CERO, _CERO
        resultado[clave] = a_decimal(total_itbis)
    else:
        resultado["i18"] = _redondear(resultado["g18"] * Decimal("0.18"))
        resultado["i16"] = _redondear(resultado["g16"] * Decimal("0.16"))
    return resultado


def _formas_pago(datos: dict[str, Any], monto_total: Decimal) -> dict[str, Decimal]:
    """Campos 17-23 del 607 desde IdDoc.TablaFormasPago; sin tabla, todo el
    MontoTotal va a "venta a crédito" si TipoPago = 2, a "otras formas" si
    es gratuito (3) y a "efectivo" en cualquier otro caso."""
    columnas = {columna: _CERO for columna in COLUMNAS_FORMA_PAGO}
    id_doc = (datos.get("Encabezado") or {}).get("IdDoc") or {}
    tabla = id_doc.get("TablaFormasPago") or {}
    formas = _lista(tabla.get("FormaDePago") if isinstance(tabla, dict) else tabla)
    hubo = False
    for forma in formas:
        if not isinstance(forma, dict):
            continue
        columna = _COLUMNA_FORMA_PAGO.get(str(forma.get("FormaPago") or "").strip())
        if columna is None:
            continue
        columnas[columna] += a_decimal(forma.get("MontoPago"))
        hubo = True
    if not hubo:
        tipo_pago = str(id_doc.get("TipoPago") or "1")
        columna = {"2": "venta_credito", "3": "otras_formas"}.get(tipo_pago, "efectivo")
        columnas[columna] = monto_total
    return columnas


def fila_607(factura: dict[str, Any]) -> dict[str, Any]:
    """Un e-CF de venta (`id`, `tipo_ecf`, `encf`, `datos`) -> renglón del
    607 con los 23 campos (montos en Decimal, fechas como date) más los datos
    de presentación (`factura_id`, `cliente_nombre`, `tipo_ecf`)."""
    datos = factura["datos"]
    encabezado = datos.get("Encabezado") or {}
    id_doc = encabezado.get("IdDoc") or {}
    comprador = encabezado.get("Comprador") or {}
    totales = encabezado.get("Totales") or {}
    referencia = datos.get("InformacionReferencia") or {}
    rnc, tipo_id = _identificacion_comprador(comprador)
    desglose = desglose_por_tasa(datos)
    monto_total = a_decimal(totales.get("MontoTotal"))
    itbis = a_decimal(totales.get("TotalITBIS")) if totales.get("TotalITBIS") not in (None, "") else (
        desglose["i18"] + desglose["i16"]
    )
    selectivo, otros, propina = _impuestos_adicionales(totales)
    # Campo 8, "valor de la venta sin incluir impuestos": la suma de lo
    # gravado y lo exento; si el e-CF no permite desglosarlo, el MontoTotal
    # menos los impuestos.
    monto_facturado = desglose["g18"] + desglose["g16"] + desglose["g0"] + desglose["exento"]
    if not monto_facturado:
        monto_facturado = monto_total - itbis - selectivo - otros - propina
    itbis_retenido = a_decimal(totales.get("TotalITBISRetenido"))
    renta_retenida = a_decimal(totales.get("TotalISRRetencion"))
    fecha = fecha_dgii((encabezado.get("Emisor") or {}).get("FechaEmision"))
    tipo_ingreso = str(id_doc.get("TipoIngresos") or _TIPO_INGRESO_POR_DEFECTO).zfill(2)
    fila: dict[str, Any] = {
        "factura_id": str(factura["id"]) if factura.get("id") is not None else None,
        "rnc_cedula": rnc,
        "tipo_id": tipo_id,
        "ncf": factura.get("encf") or str(id_doc.get("eNCF") or ""),
        "ncf_modificado": (str(referencia.get("NCFModificado") or "").strip() or None),
        "tipo_ingreso": tipo_ingreso,
        "fecha_comprobante": fecha,
        # Si el e-CF ya informa retenciones, se asume la fecha de emisión
        # como fecha de retención (el 607 la exige siempre que haya
        # retención); el usuario debe confirmarla.
        "fecha_retencion": fecha if (itbis_retenido or renta_retenida) else None,
        "monto_facturado": _redondear(monto_facturado),
        "itbis_facturado": _redondear(itbis),
        "itbis_retenido_terceros": _redondear(itbis_retenido),
        "itbis_percibido": _redondear(a_decimal(totales.get("TotalITBISPercepcion"))),
        "retencion_renta_terceros": _redondear(renta_retenida),
        "isr_percibido": _redondear(a_decimal(totales.get("TotalISRPercepcion"))),
        "impuesto_selectivo_consumo": _redondear(selectivo),
        "otros_impuestos": _redondear(otros),
        "monto_propina_legal": _redondear(propina),
        "cliente_nombre": (str(comprador.get("RazonSocialComprador") or "").strip() or None),
        "tipo_ecf": factura.get("tipo_ecf"),
    }
    # Las notas de crédito no llevan forma de pago (la plantilla oficial las
    # exime de la regla "al menos una de las casillas 17 a 23").
    formas = (
        {columna: _CERO for columna in COLUMNAS_FORMA_PAGO}
        if factura.get("tipo_ecf") == 34
        else _formas_pago(datos, monto_total)
    )
    fila.update({columna: _redondear(valor) for columna, valor in formas.items()})
    return fila


def es_consumo_resumido(factura: dict[str, Any]) -> bool:
    """Factura de consumo (E32) menor de RD$250,000.00 (MontoTotal): no se
    reporta una a una sino en el resumen de facturas de consumo. Se compara
    el MontoTotal (con impuestos) -- la interpretación conservadora: ante la
    duda, el comprobante se reporta individualmente."""
    if factura.get("tipo_ecf") != 32:
        return False
    totales = ((factura.get("datos") or {}).get("Encabezado") or {}).get("Totales") or {}
    return a_decimal(totales.get("MontoTotal")) < TOPE_CONSUMO


def armar_607(facturas: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any], list[str]]:
    """Facturas de venta aceptadas del período -> (filas del 607, totales,
    advertencias). Las notas de crédito (E34) restan en los totales, como en
    la plantilla oficial."""
    filas: list[dict[str, Any]] = []
    advertencias: list[str] = []
    consumo_cantidad = 0
    consumo_monto = _CERO
    for factura in facturas:
        if es_consumo_resumido(factura):
            consumo_cantidad += 1
            totales = (factura["datos"].get("Encabezado") or {}).get("Totales") or {}
            consumo_monto += a_decimal(totales.get("MontoTotal"))
            continue
        fila = fila_607(factura)
        filas.append(fila)
        if fila["tipo_ecf"] == 32 and not fila["rnc_cedula"]:
            advertencias.append(
                f"{fila['ncf']}: factura de consumo de RD$250,000.00 o más sin RNC/cédula del comprador "
                "(la DGII exige identificar al comprador desde ese monto)"
            )
        elif fila["tipo_ecf"] not in (32, 33, 34) and not fila["rnc_cedula"]:
            advertencias.append(f"{fila['ncf']}: comprobante sin RNC/cédula del comprador")
        if fila["fecha_retencion"] is not None:
            advertencias.append(
                f"{fila['ncf']}: tiene retenciones de terceros; se usó la fecha de emisión como fecha de "
                "retención -- confírmela"
            )
    if consumo_cantidad:
        advertencias.append(
            f"{consumo_cantidad} factura(s) de consumo menores de RD$250,000.00 por RD$"
            f"{_redondear(consumo_monto):,.2f} no van una a una: corresponden al Resumen General de Facturas "
            "de Consumo (Norma General 10-18)"
        )
    totales = {"cantidad": len(filas)}
    for campo in ("monto_facturado", "itbis_facturado", "itbis_retenido_terceros", "retencion_renta_terceros"):
        totales[campo] = sum(
            ((-f[campo] if f["tipo_ecf"] == 34 else f[campo]) for f in filas), _CERO
        )
    return filas, totales, advertencias


ADVERTENCIA_ECF_607 = (
    "La DGII indica que los emisores electrónicos no reportan sus e-CF en el Formato 607: los recibe "
    "directamente en XML (Norma General 01-20, Ley 32-23). Estos renglones son de referencia (cuadre con "
    "el IT-1) y no van en el archivo TXT; si no tuvo ventas con NCF serie B, presente el 607 en cero "
    "(Oficina Virtual > Declaraciones Juradas > Declaraciones en Cero)."
)
ADVERTENCIA_ECF_608 = (
    "La DGII indica que los emisores 100 % electrónicos no remiten el 608: los e-NCF se anulan con el "
    "servicio de Anulación de e-NCF (ANECF) o con notas de crédito. Las anulaciones de e-NCF se muestran "
    "como referencia y no van en el archivo TXT; en él solo van los NCF serie B (11 o 19 posiciones) "
    "registrados a mano."
)


# --- Validación (reglas de las plantillas oficiales) ---------------------------------------------------------

def validar_ncf_607(ncf: str) -> bool:
    return len(ncf) in (11, 19) and bool(_REGEX_NCF_607.match(ncf))


def validar_ncf_608(ncf: str) -> bool:
    """Reglas de `cmdValidar_Click` de la plantilla del 608: 11 o 19
    posiciones, serie A/B/P/Q y tipo de comprobante de la lista."""
    ncf = ncf.upper()
    if len(ncf) not in (11, 19) or ncf[0] not in "ABPQ" or not ncf[1:].isdigit():
        return False
    tipo = ncf[9:11] if len(ncf) == 19 else ncf[1:3]
    return tipo in _TIPOS_NCF_608


def errores_fila_607(fila: dict[str, Any]) -> list[str]:
    """Validaciones de `clsFormato607Validator` aplicables al renglón."""
    errores: list[str] = []
    ncf = fila["ncf"] or ""
    if not validar_ncf_607(ncf):
        errores.append("NCF inválido para el 607 (debe tener 11 o 19 posiciones)")
    modificado = fila.get("ncf_modificado")
    if modificado and not validar_ncf_607(modificado):
        errores.append("NCF modificado inválido para el 607")
    tipo_ncf = ncf[1:3] if len(ncf) == 11 else ncf[9:11]
    tipo_mod = (modificado or "")[1:3] if len(modificado or "") == 11 else (modificado or "")[9:11]
    exime_id = tipo_ncf in ("03", "04") and tipo_mod == "02" and fila["monto_facturado"] < TOPE_CONSUMO
    if not exime_id:
        rnc = fila["rnc_cedula"] or ""
        if not rnc:
            errores.append("Falta el RNC/cédula del comprador")
        elif fila["tipo_id"] == 1 and not (rnc.isdigit() and len(rnc) == 9):
            errores.append("El RNC debe tener 9 dígitos")
        elif fila["tipo_id"] == 2 and not (rnc.isdigit() and len(rnc) == 11 and rnc != "00000000000"):
            errores.append("La cédula debe tener 11 dígitos")
        elif fila["tipo_id"] not in (1, 2, 3):
            errores.append("Tipo de identificación inválido")
    if fila["tipo_ingreso"] not in ("01", "02", "03", "04", "05", "06"):
        errores.append("Tipo de ingreso inválido (01-06)")
    if fila["fecha_comprobante"] is None:
        errores.append("Falta la fecha del comprobante")
    if fila["monto_facturado"] < fila["itbis_facturado"]:
        errores.append("El monto facturado debe ser mayor o igual al ITBIS facturado")
    if (fila["itbis_retenido_terceros"] or fila["retencion_renta_terceros"]) and fila["fecha_retencion"] is None:
        errores.append("Con retenciones de terceros debe indicar la fecha de retención")
    for campo in COLUMNAS_MONTO_607:
        if fila[campo] < 0:
            errores.append(f"{campo} no puede ser negativo")
    if tipo_ncf != "04" and not any(fila[c] for c in COLUMNAS_FORMA_PAGO):
        errores.append("Debe tener al menos una forma de pago (casillas 17 a 23)")
    return errores


# --- Archivos de texto ---------------------------------------------------------

def _fmt_monto(valor: Decimal) -> str:
    return f"{valor:.2f}"


def linea_607(fila: dict[str, Any]) -> str:
    """Los 23 campos del detalle, en el orden de la macro oficial. El tipo de
    ingreso va con un dígito (la macro toma `Mid(celda, 2, 1)` de "01".."06")."""
    campos = [
        fila["rnc_cedula"] or "",
        str(fila["tipo_id"]) if fila["rnc_cedula"] else "",
        fila["ncf"],
        fila.get("ncf_modificado") or "",
        fila["tipo_ingreso"][1:2],
        aaaammdd(fila["fecha_comprobante"]),
        aaaammdd(fila.get("fecha_retencion")),
    ] + [_fmt_monto(fila[campo]) for campo in COLUMNAS_MONTO_607]
    return "|".join(campos)


def _archivo(encabezado: str, lineas: list[str]) -> bytes:
    # CRLF y sin salto de línea final: réplica de las macros oficiales (la
    # última línea se imprime con `Print #1, strDetalle;`). Mismo criterio
    # que services/formato_606/app/generador.py.
    return "\r\n".join([encabezado] + lineas).encode("utf-8")


def construir_archivo_607(*, rnc: str, periodo: str, filas: list[dict[str, Any]]) -> bytes:
    """`DGII_F_607_<RNC>_<AAAAMM>.TXT`: `607|RNC|AAAAMM|cantidad` + un renglón por comprobante."""
    return _archivo(f"607|{rnc}|{periodo}|{len(filas)}", [linea_607(fila) for fila in filas])


def construir_archivo_608(*, rnc: str, periodo: str, filas: list[dict[str, Any]]) -> bytes:
    """`DGII_F_608_<RNC>_<AAAAMM>.TXT`: `608|RNC|AAAAMM|cantidad` + `NCF|AAAAMMDD|TT`."""
    lineas = [
        f"{fila['ncf']}|{aaaammdd(fila['fecha_comprobante'])}|{fila['tipo_anulacion']}" for fila in filas
    ]
    return _archivo(f"608|{rnc}|{periodo}|{len(filas)}", lineas)


def nombre_archivo(formato: str, rnc: str, periodo: str) -> str:
    return f"DGII_F_{formato}_{rnc}_{periodo}.TXT"


# --- 608: rangos de anulación e-NCF ---------------------------------------------------------

def _limites_rango(desde: str, hasta: str) -> tuple[str, int, int] | None:
    desde, hasta = desde.strip().upper(), hasta.strip().upper()
    if not (es_encf(desde) and es_encf(hasta)) or desde[:3] != hasta[:3]:
        return None
    inicio, fin = int(desde[3:]), int(hasta[3:])
    return (desde[:3], inicio, fin) if inicio <= fin else None


def cantidad_rango(desde: str, hasta: str) -> int:
    limites = _limites_rango(desde, hasta)
    return limites[2] - limites[1] + 1 if limites else 0


def expandir_rango(desde: str, hasta: str, limite: int) -> list[str]:
    """E310000000001..E310000000005 -> los 5 e-NCF (como mucho `limite`).
    Rango mal formado (series o tipos distintos, desde > hasta) -> []."""
    limites = _limites_rango(desde, hasta)
    if limites is None:
        return []
    prefijo, inicio, fin = limites
    fin = min(fin, inicio + limite - 1)
    return [f"{prefijo}{n:010d}" for n in range(inicio, fin + 1)]
