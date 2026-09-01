from libs.ecf_common.xml_tools import asegurar_declaracion_utf8, nombre_archivo_ecf


def test_nombre_archivo_ecf():
    assert nombre_archivo_ecf("101672919", "E3100000001") == "101672919E3100000001.xml"


def test_asegurar_declaracion_utf8_agrega_si_falta():
    xml = b"<SemillaModel><valor>abc</valor></SemillaModel>"
    resultado = asegurar_declaracion_utf8(xml)
    assert resultado.startswith(b'<?xml version="1.0" encoding="UTF-8"?>')
    assert b"<SemillaModel>" in resultado


def test_asegurar_declaracion_utf8_no_duplica():
    xml = b'<?xml version="1.0" encoding="utf-8"?><SemillaModel/>'
    resultado = asegurar_declaracion_utf8(xml)
    assert resultado == xml
