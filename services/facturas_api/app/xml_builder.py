"""Generador genérico de XML de e-CF a partir de un dict estructurado.

Construye el árbol XML recorriendo `datos`, reordenando primero cada nivel
del dict según el orden real de la secuencia (`xs:sequence`) del XSD del
tipo de e-CF, derivado automáticamente del propio archivo XSD (ver
`_orden_elementos`) -- no depende de que el llamador (la API/el frontend)
ni la capa de persistencia (Postgres, json vs jsonb, cualquier round-trip
futuro) preserven el orden de inserción de las claves. Esto corrige de raíz
una clase de bug ya vista en producción: `jsonb` en Postgres NO preserva el
orden de las claves de un objeto, así que si la columna `datos` llegara a
ser (o volver a ser) `jsonb` en vez de `json`, el XML generado antes salía
con las claves en el orden que Postgres decidiera reordenarlas, violando el
`xs:sequence` del XSD (p. ej. "Element 'IdDoc': This element is not
expected. Expected is (Version)"). Reordenar aquí, con la fuente de verdad
siendo el propio XSD, hace que ese tipo de error ya no pueda volver a pasar
sin importar qué le pase al dict en el camino.

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

_XS_NS = {"xs": "http://www.w3.org/2001/XMLSchema"}


def _xsd_path(tipo_ecf: int) -> Path:
    if tipo_ecf not in TIPOS_SOPORTADOS:
        raise ValueError(
            f"Tipo de e-CF no soportado: {tipo_ecf}. Soportados: {sorted(TIPOS_SOPORTADOS)}"
        )
    return XSD_DIR / f"eCF_{tipo_ecf}.xsd"


@lru_cache(maxsize=None)
def _cargar_schema(tipo_ecf: int) -> etree.XMLSchema:
    return etree.XMLSchema(etree.parse(str(_xsd_path(tipo_ecf))))


@lru_cache(maxsize=None)
def _orden_elementos(tipo_ecf: int) -> dict[str, tuple[str, ...]]:
    """Deriva, del XSD del tipo, el orden de secuencia de cada elemento
    complejo: `{nombre_elemento: (nombre_hijo_1, nombre_hijo_2, ...)}`.

    Todos los XSD de e-CF de la DGII son autocontenidos (sin `xs:include`)
    y cada tipo complejo se declara inline dentro de un `xs:sequence`
    directo (sin `xs:choice`, sin `xs:element ref=`, sin herencia vía
    `xs:complexContent`) -- así que basta con recorrer recursivamente los
    `xs:element` desde la raíz `ECF`, sin necesidad de resolver tipos
    complejos con nombre por separado.
    """
    raiz = etree.parse(str(_xsd_path(tipo_ecf))).getroot()
    tipos_nombrados = {
        el.get("name"): el for el in raiz.findall("xs:complexType", _XS_NS)
    }
    orden: dict[str, tuple[str, ...]] = {}

    def _procesar(elemento: etree._Element) -> None:
        nombre = elemento.get("name")
        if not nombre or nombre in orden:
            return
        tipo_complejo = elemento.find("xs:complexType", _XS_NS)
        if tipo_complejo is None:
            tipo_attr = elemento.get("type")
            tipo_complejo = tipos_nombrados.get(tipo_attr) if tipo_attr else None
        if tipo_complejo is None:
            return  # tipo simple (texto/número) -- sin hijos que ordenar
        secuencia = tipo_complejo.find("xs:sequence", _XS_NS)
        if secuencia is None:
            return
        hijos = [h for h in secuencia.findall("xs:element", _XS_NS) if h.get("name")]
        orden[nombre] = tuple(h.get("name") for h in hijos)
        for hijo in hijos:
            _procesar(hijo)

    elemento_raiz = raiz.find(".//xs:element[@name='ECF']", _XS_NS)
    if elemento_raiz is not None:
        _procesar(elemento_raiz)
    return orden


def _reordenar_datos(valor: Any, elemento: str, orden: dict[str, tuple[str, ...]]) -> Any:
    """Reordena recursivamente `valor` según `orden[elemento]` (derivado del
    XSD). Claves presentes en `valor` pero no en el XSD (no debería pasar
    con datos válidos) se preservan al final, en su orden original, en vez
    de perderse silenciosamente."""
    if isinstance(valor, dict):
        hijos_ordenados = orden.get(elemento, ())
        resultado: dict[str, Any] = {}
        for clave in hijos_ordenados:
            if clave in valor:
                resultado[clave] = _reordenar_datos(valor[clave], clave, orden)
        for clave, sub_valor in valor.items():
            if clave not in resultado:
                resultado[clave] = _reordenar_datos(sub_valor, clave, orden)
        return resultado
    if isinstance(valor, list):
        return [_reordenar_datos(item, elemento, orden) for item in valor]
    return valor


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

    `datos` debe tener como claves de primer nivel las secciones del XSD
    (Encabezado, DetallesItems, Subtotales, DescuentosORecargos, Paginacion,
    InformacionReferencia), en cualquier orden -- este módulo reordena cada
    nivel del dict según la secuencia real del XSD (ver `_orden_elementos`)
    antes de construir el árbol, así que el orden de inserción del dict ya
    no es load-bearing. No incluir `FechaHoraFirma` en `datos` -- se agrega
    automáticamente al final con la fecha/hora actual, como exige el
    estándar (hora de la firma digital).
    """
    if tipo_ecf not in TIPOS_SOPORTADOS:
        raise ValueError(
            f"Tipo de e-CF no soportado: {tipo_ecf}. Soportados: {sorted(TIPOS_SOPORTADOS)}"
        )

    datos_ordenados = _reordenar_datos(datos, "ECF", _orden_elementos(tipo_ecf))

    root = etree.Element("ECF")
    for clave, valor in datos_ordenados.items():
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
