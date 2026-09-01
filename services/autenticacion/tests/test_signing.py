from lxml import etree
from signxml import XMLVerifier

from services.autenticacion.app.signing import cargar_certificado_p12, firmar_xml

XML_SEMILLA = (
    b'<?xml version="1.0" encoding="UTF-8"?>'
    b"<SemillaModel><valor>0000000-0000</valor><fecha>2026-01-01T00:00:00Z</fecha></SemillaModel>"
)


def test_cargar_certificado_p12(p12_prueba):
    p12_path, password = p12_prueba

    credencial = cargar_certificado_p12(str(p12_path), password)

    assert credencial.private_key is not None
    assert credencial.certificate is not None


def test_cargar_certificado_p12_password_incorrecta(p12_prueba):
    p12_path, _ = p12_prueba

    import pytest

    with pytest.raises(ValueError):
        cargar_certificado_p12(str(p12_path), "password-incorrecta")


def test_firmar_xml_produce_firma_verificable(credencial_prueba):
    signed = firmar_xml(XML_SEMILLA, credencial_prueba)

    root = etree.fromstring(signed)
    assert root.find(".//{http://www.w3.org/2000/09/xmldsig#}Signature") is not None

    result = XMLVerifier().verify(root, x509_cert=credencial_prueba.certificate)
    assert result.signed_xml is not None


def test_firmar_xml_preserva_contenido_original(credencial_prueba):
    signed = firmar_xml(XML_SEMILLA, credencial_prueba)

    root = etree.fromstring(signed)
    assert root.findtext("valor") == "0000000-0000"
    assert root.findtext("fecha") == "2026-01-01T00:00:00Z"
