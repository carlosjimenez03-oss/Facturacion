"""Cálculos puros de Bancos (fase 5): lectura flexible del extracto bancario
en CSV y sugerencias de conciliación (monto + ventana de fechas +
referencia). Sin base de datos; ver `bancos_repository.py`.

**Extracto CSV.** Los bancos dominicanos exportan el estado de cuenta con
encabezados distintos ("Fecha Posteo", "Descripción", "No. Referencia",
"Débito"/"Crédito" o un solo "Monto" con signo o con una columna D/C,
"Balance"), separados por coma, punto y coma o tabulador, a veces con
líneas de título antes del encabezado y en UTF-8 o Windows-1252. Por eso:

- se detecta el separador y la fila de encabezado (la primera que tiene una
  columna de fecha y una de monto/débito/crédito), y cada columna por
  sinónimos (`SINONIMOS`);
- `mapeo` (opcional, en la solicitud) fija cualquier columna por su nombre
  o por su posición (0 = primera), el formato de fecha, el separador
  decimal y el signo de una columna única de monto.

Monto de una línea: con signo desde el punto de vista de la cuenta
(+ entra dinero / crédito del banco, − sale / débito del banco).
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

_CERO = Decimal("0")

CAMPOS = ("fecha", "descripcion", "referencia", "debito", "credito", "monto", "saldo", "tipo")

SINONIMOS: dict[str, tuple[str, ...]] = {
    "fecha": (
        "fecha", "fecha posteo", "fecha de posteo", "fecha transaccion", "fecha de transaccion", "fecha efectiva",
        "fecha valor", "fecha operacion", "fecha movimiento", "date", "posting date", "transaction date",
    ),
    "descripcion": (
        "descripcion", "descripcion corta", "descripcion larga", "concepto", "detalle", "transaccion",
        "description", "narrativa", "comentario", "detalle transaccion",
    ),
    "referencia": (
        "referencia", "ref", "no referencia", "numero referencia", "num referencia", "no. referencia",
        "numero de referencia", "documento", "no documento", "num documento", "numero documento", "serial",
        "cheque", "no cheque", "reference", "secuencia", "numero de transaccion",
    ),
    "debito": (
        "debito", "debitos", "retiro", "retiros", "cargo", "cargos", "debit", "debits", "monto debito",
        "valor debito", "salidas",
    ),
    "credito": (
        "credito", "creditos", "deposito", "depositos", "abono", "abonos", "credit", "credits", "monto credito",
        "valor credito", "entradas",
    ),
    "monto": ("monto", "monto transaccion", "importe", "valor", "amount", "monto rd", "monto rd$"),
    "saldo": ("saldo", "balance", "saldo disponible", "disponible", "saldo final", "balance disponible"),
    "tipo": ("tipo", "tipo transaccion", "tipo de transaccion", "d/c", "db/cr", "dc", "naturaleza", "tipo movimiento"),
}

# "ND"/"NC" = nota de débito / nota de crédito del banco.
_TIPO_DEBITO = ("d", "db", "deb", "debito", "cargo", "retiro", "dr", "debit", "nd", "-")
_TIPO_CREDITO = ("c", "cr", "cre", "credito", "abono", "deposito", "credit", "nc", "+")

FORMATOS_FECHA = {
    "DD/MM/AAAA": ("%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%y", "%d-%m-%y"),
    "MM/DD/AAAA": ("%m/%d/%Y", "%m-%d-%Y", "%m/%d/%y"),
    "AAAA-MM-DD": ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"),
}
# Sin formato indicado: día primero (como en RD), luego ISO.
_FORMATOS_AUTO = FORMATOS_FECHA["DD/MM/AAAA"] + FORMATOS_FECHA["AAAA-MM-DD"] + ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M")


class ExtractoInvalidoError(Exception):
    """El archivo no se puede leer como extracto (422 con el mensaje)."""


def normalizar(texto: Any) -> str:
    """"Débito (RD$)" -> "debito rd$": minúsculas, sin acentos ni espacios dobles."""
    sin_acentos = unicodedata.normalize("NFKD", str(texto or "")).encode("ascii", "ignore").decode()
    sin_acentos = re.sub(r"[()\[\]:#º°]", " ", sin_acentos.lower())
    return re.sub(r"\s+", " ", sin_acentos.replace(".", ". ").replace(" .", ".")).strip(" .")


def decodificar(contenido: bytes) -> str:
    """UTF-16 con BOM (lo que guarda Excel como "Texto Unicode"), UTF-8 (con
    o sin BOM), Windows-1252 o Latin-1."""
    if contenido.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            return contenido.decode("utf-16")
        except UnicodeDecodeError as exc:
            raise ExtractoInvalidoError("El archivo dice ser UTF-16 pero no se pudo leer") from exc
    for codificacion in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return contenido.decode(codificacion)
        except UnicodeDecodeError:
            continue
    raise ExtractoInvalidoError("No se pudo leer el archivo como texto")  # pragma: no cover (latin-1 no falla)


def _separador(texto: str) -> str:
    muestra = "\n".join(texto.splitlines()[:40])
    try:
        return csv.Sniffer().sniff(muestra, delimiters=",;\t|").delimiter
    except csv.Error:
        conteos = {d: muestra.count(d) for d in (";", ",", "\t", "|")}
        return max(conteos, key=conteos.get) if any(conteos.values()) else ","


# Montos que solo se leen de una forma: con miles de punto y decimales de
# coma ("1.250,00", "1.250.000", "1250,5") o al revés ("1,250.00",
# "1,250,000", "1250.50"). "1.250" y "1,250" son ambiguos.
_ESTILO_COMA = re.compile(r"\d{1,3}(\.\d{3}){2,}(,\d+)?|\d{1,3}(\.\d{3})+,\d+|\d+,\d{1,2}")
_ESTILO_PUNTO = re.compile(r"\d{1,3}(,\d{3}){2,}(\.\d+)?|\d{1,3}(,\d{3})+\.\d+|\d+\.\d{1,2}")


def separador_decimal_de(valores: list[str]) -> str | None:
    """Separador decimal de una columna de montos, por los valores que no son
    ambiguos: "," si todos esos usan miles de punto / decimales de coma, "."
    si todos usan el estilo contrario, None si no hay ninguno o se mezclan
    (cada valor se lee solo, ver `parsear_monto`)."""
    estilos = set()
    for valor in valores:
        texto = re.sub(r"[^\d.,]", "", str(valor or ""))
        if _ESTILO_COMA.fullmatch(texto):
            estilos.add(",")
        elif _ESTILO_PUNTO.fullmatch(texto):
            estilos.add(".")
    return estilos.pop() if len(estilos) == 1 else None


def parsear_monto(valor: Any, separador_decimal: str | None = None) -> Decimal | None:
    """"RD$ 1,234.56" / "1.234,56" / "(1,250.00)" / "1250.00-" -> Decimal
    (None si está vacío). Lanza ValueError si no es un número. Sin
    `separador_decimal`, un solo punto es decimal ("1.250" = 1.25) y una
    sola coma seguida de 3 dígitos es de miles ("1,250" = 1250): el estilo
    de la columna lo decide `leer_extracto` con `separador_decimal_de`."""
    texto = str(valor or "").strip()
    if not texto or texto in ("-", "--"):
        return None
    negativo = False
    if texto.startswith("(") and texto.endswith(")"):
        negativo, texto = True, texto[1:-1]
    texto = re.sub(r"(?i)(rd\$|us\$|dop|usd|\$|\s)", "", texto)
    if texto.endswith("-"):
        negativo, texto = True, texto[:-1]
    if texto.startswith("-"):
        negativo, texto = not negativo, texto[1:]
    elif texto.startswith("+"):
        texto = texto[1:]
    if not texto:
        return None
    if separador_decimal == ",":
        texto = texto.replace(".", "").replace(",", ".")
    elif separador_decimal == ".":
        texto = texto.replace(",", "")
    elif "," in texto and "." in texto:
        # El último separador es el decimal.
        texto = texto.replace(".", "").replace(",", ".") if texto.rfind(",") > texto.rfind(".") else texto.replace(",", "")
    elif "," in texto:
        # "1250,50" -> decimal; "1,250" o "1,250,000" -> miles.
        texto = texto.replace(",", ".") if re.fullmatch(r"\d+,\d{1,2}", texto) else texto.replace(",", "")
    if not re.fullmatch(r"\d+(\.\d+)?", texto):
        raise ValueError(f"«{valor}» no es un monto")
    try:
        monto = Decimal(texto)
    except InvalidOperation as exc:  # pragma: no cover (la regex ya lo filtra)
        raise ValueError(f"«{valor}» no es un monto") from exc
    return -monto if negativo else monto


def parsear_fecha(valor: Any, formato: str | None = None) -> date:
    texto = str(valor or "").strip()
    if not texto:
        raise ValueError("sin fecha")
    formatos = FORMATOS_FECHA.get(formato or "", _FORMATOS_AUTO) if formato else _FORMATOS_AUTO
    for f in formatos:
        try:
            return datetime.strptime(texto, f).date()
        except ValueError:
            continue
    # "23/10/2026 10:42" con un formato de solo fecha: se prueba la parte de la fecha.
    if " " in texto:
        return parsear_fecha(texto.split(" ", 1)[0], formato)
    raise ValueError(f"«{texto}» no es una fecha{f' {formato}' if formato else ''}")


@dataclass
class LineaExtracto:
    fila: int                  # número de fila en el archivo (1 = primera)
    fecha: date
    descripcion: str
    referencia: str | None
    monto: Decimal             # + entra a la cuenta, − sale
    saldo: Decimal | None
    huella: str = ""


@dataclass
class Extracto:
    separador: str
    fila_encabezado: int
    columnas: list[str]
    mapeo: dict[str, int | None]
    lineas: list[LineaExtracto] = field(default_factory=list)
    errores: list[dict[str, Any]] = field(default_factory=list)

    def mapeo_por_nombre(self) -> dict[str, str | None]:
        return {
            campo: (self.columnas[indice] if indice is not None and indice < len(self.columnas) else None)
            for campo, indice in self.mapeo.items()
        }


def _columna_de(campo: str, encabezado_norm: list[str], usadas: set[int]) -> int | None:
    sinonimos = SINONIMOS[campo]
    for i, nombre in enumerate(encabezado_norm):
        if i not in usadas and nombre in sinonimos:
            return i
    for i, nombre in enumerate(encabezado_norm):
        # "debito rd$", "monto de la transaccion": contiene un sinónimo como palabra.
        if i not in usadas and any(re.search(rf"(^|\s){re.escape(s)}(\s|$)", nombre) for s in sinonimos):
            return i
    return None


def _es_encabezado(fila: list[str]) -> bool:
    norm = [normalizar(c) for c in fila]
    tiene_fecha = _columna_de("fecha", norm, set()) is not None
    tiene_monto = any(_columna_de(c, norm, set()) is not None for c in ("monto", "debito", "credito"))
    return tiene_fecha and tiene_monto


def _indice(valor: Any, encabezado: list[str], campo: str) -> int | None:
    if valor is None or valor == "":
        return None
    if isinstance(valor, int) or (isinstance(valor, str) and valor.strip().isdigit()):
        indice = int(valor)
        if indice < 0 or indice >= len(encabezado):
            raise ExtractoInvalidoError(f"La columna {indice} de «{campo}» no existe (el archivo tiene {len(encabezado)})")
        return indice
    buscado = normalizar(valor)
    for i, nombre in enumerate(encabezado):
        if normalizar(nombre) == buscado:
            return i
    raise ExtractoInvalidoError(f"No hay una columna «{valor}» para «{campo}» en el archivo")


def leer_extracto(contenido: bytes, mapeo: dict[str, Any] | None = None, *, cuenta: str = "") -> Extracto:
    """Lee el CSV y devuelve sus líneas (monto con signo) y los errores por
    fila. `mapeo`: {fecha, descripcion, referencia, debito, credito, monto,
    saldo, tipo: nombre o posición de la columna; formato_fecha:
    "DD/MM/AAAA" | "MM/DD/AAAA" | "AAAA-MM-DD"; separador_decimal: "." | ",";
    signo_monto: "credito_positivo" (por defecto) | "debito_positivo";
    fila_encabezado: número de fila (1 = primera)}. Lanza
    ExtractoInvalidoError si no encuentra el encabezado o faltan columnas."""
    mapeo = dict(mapeo or {})
    texto = decodificar(contenido)
    if not texto.strip():
        raise ExtractoInvalidoError("El archivo está vacío")
    separador = mapeo.get("separador") or _separador(texto)
    try:
        filas = list(csv.reader(io.StringIO(texto), delimiter=separador))
    except csv.Error as exc:
        raise ExtractoInvalidoError(f"El archivo no es un CSV válido: {exc}") from exc
    if mapeo.get("fila_encabezado"):
        fila_encabezado = int(mapeo["fila_encabezado"]) - 1
        if not 0 <= fila_encabezado < len(filas):
            raise ExtractoInvalidoError("La fila de encabezado indicada no existe")
    else:
        fila_encabezado = next((i for i, fila in enumerate(filas[:40]) if _es_encabezado(fila)), -1)
        if fila_encabezado < 0:
            nombres = [mapeo.get(c) for c in ("fecha",) if isinstance(mapeo.get(c), str)]
            fila_encabezado = next(
                (i for i, fila in enumerate(filas[:40]) if nombres and normalizar(nombres[0]) in map(normalizar, fila)),
                -1,
            )
        if fila_encabezado < 0:
            raise ExtractoInvalidoError(
                "No se encontró la fila de encabezado (una columna de fecha y otra de monto, débito o crédito): "
                "indique las columnas en el mapeo"
            )
    encabezado = [c.strip() for c in filas[fila_encabezado]]
    norm = [normalizar(c) for c in encabezado]
    indices: dict[str, int | None] = {}
    usadas: set[int] = set()
    for campo in CAMPOS:
        if campo in mapeo:
            indices[campo] = _indice(mapeo[campo], encabezado, campo)
        else:
            indices[campo] = None
    usadas = {i for i in indices.values() if i is not None}
    # Orden de detección: lo más específico primero ("monto débito" antes que "monto").
    for campo in ("fecha", "debito", "credito", "saldo", "referencia", "tipo", "descripcion", "monto"):
        if campo not in mapeo and indices[campo] is None:
            indices[campo] = _columna_de(campo, norm, usadas)
            if indices[campo] is not None:
                usadas.add(indices[campo])
    if indices["fecha"] is None:
        raise ExtractoInvalidoError("Falta la columna de fecha: indíquela en el mapeo")
    if indices["monto"] is None and indices["debito"] is None and indices["credito"] is None:
        raise ExtractoInvalidoError("Falta la columna de monto (o las de débito y crédito): indíquela en el mapeo")
    extracto = Extracto(separador=separador, fila_encabezado=fila_encabezado + 1, columnas=encabezado, mapeo=indices)
    formato_fecha = mapeo.get("formato_fecha")
    if formato_fecha and formato_fecha not in FORMATOS_FECHA:
        raise ExtractoInvalidoError(f"Formato de fecha desconocido: {formato_fecha} (use {', '.join(FORMATOS_FECHA)})")
    debito_positivo = mapeo.get("signo_monto") == "debito_positivo"

    def celda(fila: list[str], campo: str) -> str:
        i = indices.get(campo)
        return fila[i].strip() if i is not None and i < len(fila) else ""

    # Separador decimal: el del mapeo o, por columna, el estilo de sus
    # valores no ambiguos ("1.250" con otros "2.300,50" en la columna = 1250).
    datos = filas[fila_encabezado + 1:]
    decimales = {
        campo: mapeo.get("separador_decimal") or separador_decimal_de([celda(f, campo) for f in datos])
        for campo in ("debito", "credito", "monto", "saldo")
    }

    repetidas: dict[tuple, int] = {}
    for numero, fila in enumerate(filas[fila_encabezado + 1:], start=fila_encabezado + 2):
        if not any(c.strip() for c in fila):
            continue
        texto_fecha = celda(fila, "fecha")
        if not texto_fecha:
            continue  # totales, notas al pie
        try:
            fecha = parsear_fecha(texto_fecha, formato_fecha)
        except ValueError as exc:
            extracto.errores.append({"fila": numero, "motivo": f"Fecha inválida: {exc}"})
            continue
        try:
            if indices["monto"] is not None and not (celda(fila, "debito") or celda(fila, "credito")):
                monto = parsear_monto(celda(fila, "monto"), decimales["monto"]) or _CERO
                tipo = normalizar(celda(fila, "tipo"))
                if tipo in _TIPO_DEBITO:
                    monto = -abs(monto)
                elif tipo in _TIPO_CREDITO:
                    monto = abs(monto)
                elif debito_positivo:
                    monto = -monto
            else:
                debito = parsear_monto(celda(fila, "debito"), decimales["debito"]) or _CERO
                credito = parsear_monto(celda(fila, "credito"), decimales["credito"]) or _CERO
                monto = abs(credito) - abs(debito)
            saldo = parsear_monto(celda(fila, "saldo"), decimales["saldo"]) if indices["saldo"] is not None else None
        except ValueError as exc:
            extracto.errores.append({"fila": numero, "motivo": str(exc)})
            continue
        if monto == 0:
            extracto.errores.append({"fila": numero, "motivo": "Monto cero: se omitió"})
            continue
        descripcion = celda(fila, "descripcion") or celda(fila, "referencia") or "(sin descripción)"
        referencia = celda(fila, "referencia") or None
        clave = (fecha, monto, referencia, descripcion)
        repetidas[clave] = repetidas.get(clave, 0) + 1
        linea = LineaExtracto(numero, fecha, descripcion[:500], (referencia or None), monto.quantize(Decimal("0.01")), saldo)
        # Huella estable: volver a importar el mismo archivo no duplica líneas;
        # dos líneas idénticas del mismo archivo se distinguen por su orden.
        linea.huella = hashlib.sha256(
            f"{cuenta}|{fecha.isoformat()}|{linea.monto}|{referencia or ''}|{descripcion}|{repetidas[clave]}".encode()
        ).hexdigest()[:40]
        extracto.lineas.append(linea)
    return extracto


# --- Sugerencias de conciliación ---------------------------------------------------------

def _alfanumerico(texto: str | None) -> str:
    return re.sub(r"[^0-9A-Za-z]", "", texto or "").upper()


def coincide_referencia(linea: dict[str, Any], movimiento: dict[str, Any]) -> bool:
    """La referencia del movimiento (o la de la línea) aparece en la otra
    (referencia o descripción), sin espacios ni guiones; mínimo 3 caracteres."""
    ref_mov = _alfanumerico(movimiento.get("referencia"))
    ref_lin = _alfanumerico(linea.get("referencia"))
    texto_lin = ref_lin + _alfanumerico(linea.get("descripcion"))
    texto_mov = ref_mov + _alfanumerico(movimiento.get("descripcion"))
    return (len(ref_mov) >= 3 and ref_mov in texto_lin) or (len(ref_lin) >= 3 and ref_lin in texto_mov)


def sugerir_conciliacion(
    lineas: list[dict[str, Any]], movimientos: list[dict[str, Any]], *, dias: int = 3, hasta: date | None = None
) -> list[dict[str, Any]]:
    """Pares (línea del extracto, movimiento) sin conciliar con el MISMO
    monto (con signo) y fechas a no más de `dias` días. Puntaje: 100 − 10
    por día de diferencia, + 50 si coincide la referencia. Se asignan de
    mayor a menor puntaje, cada línea y cada movimiento a lo sumo una vez.
    Con el corte `hasta`, una línea de fecha <= corte no se empareja con un
    movimiento posterior al corte (quedaría pendiente y no dejaría cerrar)."""
    candidatos = []
    for linea in lineas:
        for mov in movimientos:
            if Decimal(linea["monto"]) != Decimal(mov["monto"]):
                continue
            if hasta is not None and linea["fecha"] <= hasta < mov["fecha"]:
                continue
            diferencia = abs((linea["fecha"] - mov["fecha"]).days)
            if diferencia > dias:
                continue
            referencia = coincide_referencia(linea, mov)
            puntaje = 100 - 10 * diferencia + (50 if referencia else 0)
            motivos = ["Mismo monto", "misma fecha" if diferencia == 0 else f"{diferencia} día(s) de diferencia"]
            if referencia:
                motivos.append("referencia coincide")
            candidatos.append((puntaje, diferencia, linea, mov, ", ".join(motivos)))
    candidatos.sort(key=lambda c: (-c[0], c[1], c[2]["fecha"], str(c[2]["id"])))
    usadas_lineas: set[str] = set()
    usados_movs: set[tuple[str, str]] = set()
    sugerencias = []
    for puntaje, _, linea, mov, motivo in candidatos:
        clave_mov = (mov["origen"], str(mov["id"]))
        if str(linea["id"]) in usadas_lineas or clave_mov in usados_movs:
            continue
        usadas_lineas.add(str(linea["id"]))
        usados_movs.add(clave_mov)
        sugerencias.append({"linea": linea, "movimiento": mov, "puntaje": puntaje, "motivo": motivo})
    return sugerencias


def saldo_al_corte(lineas: list[dict[str, Any]], hasta: date) -> tuple[Decimal | None, str | None]:
    """(saldo, advertencia) de un extracto: el saldo de su última línea, en
    el tiempo, con fecha <= `hasta`. `lineas` en el orden del archivo
    ({fecha, monto, saldo}). Hay bancos que exportan las más antiguas
    primero y otros las más recientes primero; el orden sale del saldo
    corrido (antiguas primero: saldo anterior + monto = saldo; recientes
    primero: saldo siguiente + monto = saldo) o, si no alcanza, de las
    fechas. Si nada lo decide se asume antiguas primero y se avisa."""
    con_saldo = [x for x in lineas if x.get("saldo") is not None]
    if not con_saldo:
        return None, None
    ascendente = descendente = 0
    for anterior, siguiente in zip(lineas, lineas[1:]):
        if anterior.get("saldo") is None or siguiente.get("saldo") is None:
            continue
        ascendente += Decimal(anterior["saldo"]) + Decimal(siguiente["monto"]) == Decimal(siguiente["saldo"])
        descendente += Decimal(siguiente["saldo"]) + Decimal(anterior["monto"]) == Decimal(anterior["saldo"])
    advertencia = None
    if ascendente != descendente:
        recientes_primero = descendente > ascendente
    elif lineas[0]["fecha"] != lineas[-1]["fecha"]:
        recientes_primero = lineas[0]["fecha"] > lineas[-1]["fecha"]
    else:
        recientes_primero = False
        if len(con_saldo) > 1:
            advertencia = (
                "No se pudo saber si el extracto lista primero las líneas más antiguas o las más recientes (el saldo "
                "de cada línea no sigue a la anterior): se tomó el saldo de la última fila. Verifique el saldo según "
                "el banco antes de cerrar"
            )
    cronologico = list(reversed(lineas)) if recientes_primero else lineas
    saldo = None
    for linea in cronologico:
        if linea["fecha"] <= hasta and linea.get("saldo") is not None:
            saldo = Decimal(linea["saldo"])
    return saldo, advertencia


def enmascarar_numero(numero: str | None) -> str | None:
    """"001-234567-8" -> "****5678"."""
    limpio = _alfanumerico(numero)
    if not limpio:
        return None
    return "****" + limpio[-4:]
