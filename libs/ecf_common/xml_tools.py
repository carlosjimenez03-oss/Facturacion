"""Utilidades comunes para el manejo de archivos XML del estándar e-CF."""

XML_DECLARATION = b'<?xml version="1.0" encoding="UTF-8"?>\n'


def nombre_archivo_ecf(rnc_emisor: str, encf: str) -> str:
    """Nombre de archivo estándar para e-CF/RFCE: RNC+e-NCF.xml.

    Ejemplo: nombre_archivo_ecf("101672919", "E3100000001") -> "101672919E3100000001.xml"
    """
    return f"{rnc_emisor}{encf}.xml"


def asegurar_declaracion_utf8(xml_bytes: bytes) -> bytes:
    """Garantiza que el XML tenga la declaración `<?xml version="1.0" encoding="UTF-8"?>`.

    La DGII especifica UTF-8 como estándar de codificación de caracteres para
    todos los formatos XML intercambiados.
    """
    stripped = xml_bytes.lstrip()
    if stripped.startswith(b"<?xml"):
        return xml_bytes
    return XML_DECLARATION + xml_bytes
