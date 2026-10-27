"""Lectura del padrón de RNC de la DGII (`DGII_RNC.zip`, ver docs/PADRON_RNC.md).

El ZIP trae un TXT (`DGII_RNC.TXT`) sin encabezado, una línea por
contribuyente, campos separados por `|`, en Latin-1 / Windows-1252:

    0 RNC o cédula | 1 razón social | 2 nombre comercial | 3 actividad
    económica | 4-7 sin uso | 8 fecha de inicio (dd/mm/aaaa) | 9 estado |
    10 régimen de pago

La DGII no publica el formato y lo ha cambiado sin aviso, así que el lector
es tolerante: sin encabezado o con él (se descarta), separadores sobrantes
al final, columnas de más o de menos (el estado se busca por su valor,
desde la derecha), comillas sueltas, líneas vacías, CRLF, NUL y cualquier
codificación entre UTF-8 y Windows-1252 (línea por línea).

Seguridad: el archivo puede venir de internet o subirlo un usuario. Se lee
en streaming (nunca entero en memoria), cada línea tiene un tope, y el ZIP
se rechaza si su contenido declarado o real pasa del límite o si comprime
de forma imposible para un texto (bomba de ZIP).
"""
from __future__ import annotations

import io
import re
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import IO

# Estados que publica la DGII (dgii-ts, src/bulk/types.ts; foro de ayuda de
# la DGII). Se aceptan otros (se guardan tal cual), pero estos sirven para
# encontrar la columna del estado si cambia la cantidad de columnas.
ESTADOS_CONOCIDOS = frozenset(
    {"ACTIVO", "SUSPENDIDO", "DADO DE BAJA", "CESE TEMPORAL", "ANULADO", "RECHAZADO", "INACTIVO", "DE BAJA"}
)
ESTADO_ACTIVO = "ACTIVO"

# Diseño de 11 columnas (posiciones 0..10).
_COL_FECHA, _COL_ESTADO, _COL_REGIMEN = 8, 9, 10

MAX_LINEA = 8192
MAX_CAMPO = 250
# Un TXT de verdad comprime ~5-8 veces; 100 ya no es texto normal.
MAX_PROPORCION_ZIP = 100
# Después de tantas líneas, si casi ninguna es un contribuyente, no es el padrón.
_MUESTRA = 2000

_ESPACIOS = re.compile(r"\s+")
_NO_DIGITOS = re.compile(r"\D")
_FECHA_DMA = re.compile(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{4})$")
_FECHA_AMD = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})")
_BOM = b"\xef\xbb\xbf"


class ArchivoPadronError(Exception):
    """El archivo no es el padrón, está dañado o pasa de los límites."""


@dataclass(frozen=True)
class FilaPadron:
    rnc: str
    razon_social: str
    nombre_comercial: str | None
    actividad: str | None
    estado: str | None
    regimen: str | None
    fecha_inicio: date | None

    def tupla(self) -> tuple:
        return (
            self.rnc,
            self.razon_social,
            self.nombre_comercial,
            self.actividad,
            self.estado,
            self.regimen,
            self.fecha_inicio,
        )


COLUMNAS = ("rnc", "razon_social", "nombre_comercial", "actividad", "estado", "regimen", "fecha_inicio")


def decodificar(linea: bytes) -> str:
    """UTF-8 si lo es; si no Windows-1252 (el del archivo de la DGII), y si
    trae bytes que cp1252 no define, Latin-1 (nunca falla)."""
    for codificacion in ("utf-8", "cp1252"):
        try:
            return linea.decode(codificacion)
        except UnicodeDecodeError:
            continue
    return linea.decode("latin-1")


def _limpiar(valor: str) -> str:
    valor = valor.replace("\x00", "").strip().strip('"').strip("'")
    return _ESPACIOS.sub(" ", valor).strip()[:MAX_CAMPO]


def _o_none(valor: str) -> str | None:
    return valor or None


def leer_fecha(valor: str) -> date | None:
    valor = valor.strip()
    m = _FECHA_DMA.match(valor)
    if m:
        dia, mes, anio = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        m = _FECHA_AMD.match(valor)
        if not m:
            return None
        anio, mes, dia = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if not 1800 <= anio <= 2200:
        return None
    try:
        return date(anio, mes, dia)
    except ValueError:
        return None


def limpiar_rnc(valor: str | None) -> str:
    return _NO_DIGITOS.sub("", valor or "")


def parsear_linea(texto: str) -> FilaPadron | None:
    """Una línea del TXT -> FilaPadron, o None si no es un contribuyente
    (encabezado, vacía, RNC que no es de 9 u 11 dígitos, sin nombre)."""
    texto = texto.lstrip("﻿").rstrip("\r\n")
    if not texto.strip():
        return None
    campos = [_limpiar(c) for c in texto.split("|")]
    rnc = limpiar_rnc(campos[0])
    if len(rnc) not in (9, 11) or len(campos) < 2 or not campos[1]:
        return None
    # Un "RNC" con letras (encabezado "RNC|NOMBRE...") no es un contribuyente.
    if re.search(r"[A-Za-z]", campos[0]):
        return None
    nombre_comercial = campos[2] if len(campos) > 2 else ""
    actividad = campos[3] if len(campos) > 3 else ""
    resto = campos[4:]
    estado = regimen = ""
    fecha: date | None = None
    # El estado por su valor, desde la derecha (columnas de más o de menos).
    k = next((i for i in range(len(resto) - 1, -1, -1) if resto[i].upper() in ESTADOS_CONOCIDOS), None)
    if k is not None:
        estado = resto[k]
        regimen = next((v for v in resto[k + 1 :] if v), "")
        fecha = next((f for f in (leer_fecha(v) for v in reversed(resto[:k])) if f), None)
    elif len(campos) > _COL_ESTADO:
        # Estado desconocido: el diseño de 11 columnas.
        estado = campos[_COL_ESTADO]
        regimen = campos[_COL_REGIMEN] if len(campos) > _COL_REGIMEN else ""
        fecha = leer_fecha(campos[_COL_FECHA])
    else:
        fecha = next((f for f in (leer_fecha(v) for v in resto) if f), None)
    return FilaPadron(
        rnc=rnc,
        razon_social=campos[1],
        nombre_comercial=_o_none(nombre_comercial),
        actividad=_o_none(actividad),
        estado=_o_none(estado.upper()),
        regimen=_o_none(regimen.upper()),
        fecha_inicio=fecha,
    )


def _lineas(binario: IO[bytes], limite_bytes: int) -> Iterator[bytes]:
    """Líneas de a lo sumo MAX_LINEA bytes (una más larga se descarta
    entera) sin leer más de `limite_bytes` en total."""
    leidos = 0
    primera = True
    while True:
        linea = binario.readline(MAX_LINEA)
        if not linea:
            return
        leidos += len(linea)
        if leidos > limite_bytes:
            raise ArchivoPadronError(
                f"El contenido del archivo pasa de {limite_bytes // (1024 * 1024)} MB: no parece el padrón de la DGII"
            )
        if primera:
            linea = linea.removeprefix(_BOM)
            primera = False
        if len(linea) == MAX_LINEA and not linea.endswith(b"\n"):
            # Línea demasiado larga: se salta hasta el próximo salto.
            while True:
                resto = binario.readline(MAX_LINEA)
                leidos += len(resto)
                if leidos > limite_bytes:
                    raise ArchivoPadronError("El archivo pasa del tamaño máximo permitido")
                if not resto or resto.endswith(b"\n"):
                    break
            continue
        yield linea


class LectorPadron:
    """Filas válidas del archivo (ZIP o TXT), con contadores para el
    resultado y la comprobación de formato."""

    def __init__(self, ruta: str | Path, *, max_descomprimido: int) -> None:
        self.ruta = Path(ruta)
        self.max_descomprimido = max_descomprimido
        self.lineas = 0
        self.validas = 0
        self.descartadas = 0
        self.miembro: str | None = None

    def _abrir_miembro(self, zf: zipfile.ZipFile) -> IO[bytes]:
        candidatos = [i for i in zf.infolist() if not i.is_dir()]
        if not candidatos:
            raise ArchivoPadronError("El ZIP está vacío")
        textos = [i for i in candidatos if i.filename.lower().endswith((".txt", ".csv"))]
        info = max(textos or candidatos, key=lambda i: i.file_size)
        if info.flag_bits & 0x1:
            raise ArchivoPadronError("El ZIP está protegido con contraseña")
        if info.file_size > self.max_descomprimido:
            raise ArchivoPadronError(
                f"El TXT del ZIP ocupa {info.file_size // (1024 * 1024)} MB descomprimido: pasa del máximo "
                f"({self.max_descomprimido // (1024 * 1024)} MB)"
            )
        if info.compress_size and info.file_size / info.compress_size > MAX_PROPORCION_ZIP:
            raise ArchivoPadronError("El ZIP comprime demasiado para ser un listado de texto: se rechaza")
        self.miembro = info.filename
        return zf.open(info)

    def filas(self) -> Iterator[FilaPadron]:
        with open(self.ruta, "rb") as fh:
            es_zip = fh.read(4) == b"PK\x03\x04"
        if es_zip:
            try:
                zf = zipfile.ZipFile(self.ruta)
            except zipfile.BadZipFile as exc:
                raise ArchivoPadronError("El ZIP está dañado o incompleto") from exc
            with zf:
                try:
                    with self._abrir_miembro(zf) as miembro:
                        yield from self._leer(io.BufferedReader(miembro, buffer_size=1 << 16))  # type: ignore[arg-type]
                except (zipfile.BadZipFile, zipfile.LargeZipFile, EOFError, NotImplementedError, RuntimeError) as exc:
                    raise ArchivoPadronError(f"No se pudo descomprimir el ZIP: {exc}") from exc
        else:
            with open(self.ruta, "rb") as fh:
                yield from self._leer(fh)
        if self.validas == 0:
            raise ArchivoPadronError("El archivo no trae ningún contribuyente: no parece el padrón de RNC de la DGII")
        if self.descartadas > self.validas:
            raise ArchivoPadronError(
                f"La mayoría de las líneas no tienen el formato del padrón ({self.descartadas} de {self.lineas}): "
                "¿cambió el formato del archivo de la DGII?"
            )

    def _leer(self, binario: IO[bytes]) -> Iterator[FilaPadron]:
        for cruda in _lineas(binario, self.max_descomprimido):
            if not cruda.strip():
                continue
            self.lineas += 1
            fila = parsear_linea(decodificar(cruda))
            if fila is None:
                self.descartadas += 1
                if self.lineas == _MUESTRA and self.validas < _MUESTRA // 10:
                    raise ArchivoPadronError(
                        "Las primeras líneas del archivo no tienen el formato del padrón de RNC de la DGII"
                    )
                continue
            self.validas += 1
            yield fila
