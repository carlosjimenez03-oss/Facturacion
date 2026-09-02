"""Generador genérico de XML de e-CF a partir de un dict estructurado.

Construye el árbol XML recorriendo `datos` en el mismo orden en que aparecen
las claves del dict (Python 3.7+ preserva el orden de inserción), que debe
coincidir con el orden de la secuencia (`xs:sequence`) del XSD del tipo de
e-CF correspondiente. Es responsabilidad del llamador (la API/el frontend)
enviar las claves en ese orden -- este módulo NO reordena ni valida reglas
de negocio, solo arma el árbol; `validar_contra_xsd` es lo que efectivamente
hace cumplir el orden y las reglas de cada tipo, usando el XSD oficial.

Todos los valores (fechas, decimales, etc.) deben venir ya formateados como
texto exactamente como el XSD los espera -- por ejemplo, fechas
"DD-MM-YYYY" y "DD-MM-YYYY HH:MM:SS", montos con la cantidad de decimales
requerida. Este módulo no aplica ningún formateo ni redondeo: dado el tamaño
y la cantidad de reglas de cada XSD (~150+ campos por tipo), delegar el
formateo al llamador y la validación de reglas al propio XSD (vía
`validar_contra_xsd`) es más confiable que reimplementarlas a mano.
"""
from __future__ import annotations

import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from lxml import etree

XSD_DIR = Path(__file__).resolve().parent.parent / "xsd"

TIPOS_SOPORTADOS: frozenset[int] = frozenset({31, 32, 33, 34, 41, 43, 44, 45, 46, 47})


def _xsd_path(tipo_ecf: int) -> Path:
    if tipo_ecf not in TIPOS_SOPORTADOS:
        raise ValueError(
            f"Tipo de e-CF no soportado: {tipo_ecf}. Soportados: {sorted(TIPOS_SOPORTADOS)}"
        )
    return XSD_DIR / f"eCF_{tipo_ecf}.xsd"


@lru_cache(maxsize=None)
def _cargar_schema(tipo_ecf: int) -> etree.XMLSchema:
    return etree.XMLSchema(etree.parse(str(_xsd_path(tipo_ecf))))


def _agregar_nodo(padre: etree._Element, nombre: str, valor: Any) -> None:
    """Agrega recursivamente `nombre` como hijo de `padre` según el tipo de `valor`.

    - dict: un elemento `nombre` con un hijo por cada (clave, valor) del dict.
    - list: un elemento `nombre` repetido por cada ítem de la lista (p. ej.
      `DetallesItems: {"Item": [...]}` produce múltiples `<Item>`).
    - None: se omite (campo opcional no provisto).
    - cualquier otro valor: un elemento `nombre` con ese valor como texto.
    """
    if valor is None:
        return
    if isinstance(valor, dict):
        nodo = etree.SubElement(padre, nombre)
        for clave, sub_valor in valor.items():
            _agregar_nodo(nodo, clave, sub_valor)
    elif isinstance(valor, list):
        for item in valor:
            _agregar_nodo(padre, nombre, item)
    else:
        nodo = etree.SubElement(padre, nombre)
        nodo.text = str(valor)


def construir_xml_ecf(tipo_ecf: int, datos: dict[str, Any]) -> bytes:
    """Arma el XML `<ECF>` (sin firmar) a partir de `datos`.

    `datos` debe tener como claves de primer nivel las secciones del XSD, en
    el orden que exige el tipo (Encabezado, Emisor, Comprador,
    InformacionesAdicionales, Transporte, Totales, OtraMoneda dentro de
    Encabezado; luego DetallesItems, Subtotales, DescuentosORecargos,
    Paginacion, InformacionReferencia como hermanos de Encabezado). No
    incluir `FechaHoraFirma` en `datos` -- se agrega automáticamente al
    final con la fecha/hora actual, como exige el estándar (hora de la
    firma digital).
    """
    if tipo_ecf not in TIPOS_SOPORTADOS:
        raise ValueError(
            f"Tipo de e-CF no soportado: {tipo_ecf}. Soportados: {sorted(TIPOS_SOPORTADOS)}"
        )

    root = etree.Element("ECF")
    for clave, valor in datos.items():
        _agregar_nodo(root, clave, valor)

    ahora = datetime.datetime.now()
    etree.SubElement(root, "FechaHoraFirma").text = ahora.strftime("%d-%m-%Y %H:%M:%S")

    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=False)


def validar_contra_xsd(tipo_ecf: int, xml_bytes: bytes) -> list[str]:
    """Valida `xml_bytes` contra el XSD oficial del tipo de e-CF.

    Usar sobre el XML ya FIRMADO (el `<Signature>` que agrega `firmar_xml`
    satisface el `xs:any` final que exige cada XSD de e-CF). Sobre el XML
    sin firmar, usar `validar_estructura` en su lugar.

    Retorna una lista de mensajes de error (vacía si el XML es válido).
    """
    schema = _cargar_schema(tipo_ecf)
    xml_doc = etree.fromstring(xml_bytes)
    if schema.validate(xml_doc):
        return []
    return [str(error) for error in schema.error_log]


def validar_estructura(tipo_ecf: int, xml_sin_firmar: bytes) -> list[str]:
    """Valida el XML SIN firmar contra el XSD, tolerando la falta de `<Signature>`.

    Cada XSD de e-CF termina su secuencia con un `xs:any` obligatorio
    (`minOccurs="1"`) que en el documento real ocupa el `<Signature>` que
    agrega la firma digital (XML-DSig enveloped). Antes de firmar ese nodo
    no existe todavía, así que se agrega un elemento `<Signature/>` vacío
    solo para esta validación (el `xs:any` con `processContents="skip"` no
    valida su contenido, cualquier elemento la satisface) -- así se valida
    todo lo demás (orden, campos requeridos, patrones, enumeraciones) antes
    de firmar, sin falsos negativos por la firma faltante.
    """
    xml_doc = etree.fromstring(xml_sin_firmar)
    etree.SubElement(xml_doc, "Signature")
    schema = _cargar_schema(tipo_ecf)
    if schema.validate(xml_doc):
        return []
    return [str(error) for error in schema.error_log]


def generar_y_validar(tipo_ecf: int, datos: dict[str, Any]) -> tuple[bytes, list[str]]:
    """Construye el XML (sin firmar) y valida su estructura contra el XSD."""
    xml_bytes = construir_xml_ecf(tipo_ecf, datos)
    errores = validar_estructura(tipo_ecf, xml_bytes)
    return xml_bytes, errores
