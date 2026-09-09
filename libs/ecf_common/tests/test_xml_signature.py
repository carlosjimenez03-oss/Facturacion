import pytest

from libs.ecf_common.auth.signing import firmar_xml
from libs.ecf_common.auth.xml_signature import (
    FirmaInvalidaError,
    rnc_del_certificado,
    verificar_firma,
)

XML_EJEMPLO = (
    b'<?xml version="1.0" encoding="UTF-8"?>'
    b"<SemillaModel><valor>0000000-0000</valor><fecha>2026-01-01T00:00:00Z</fecha></SemillaModel>"
)


def test_verificar_firma_retorna_el_certificado_firmante(credencial_con_rnc):
    credencial = credencial_con_rnc("101672919")
    firmado = firmar_xml(XML_EJEMPLO, credencial)

    certificado = verificar_firma(firmado)

    assert certificado.public_key().public_numbers() == credencial.certificate.public_key().public_numbers()


def test_rnc_del_certificado_extrae_el_campo_sn(credencial_con_rnc):
    credencial = credencial_con_rnc("101672919")
    firmado = firmar_xml(XML_EJEMPLO, credencial)

    certificado = verificar_firma(firmado)

    assert rnc_del_certificado(certificado) == "101672919"


def test_rnc_del_certificado_sin_sn_lanza_error(credencial_prueba):
    firmado = firmar_xml(XML_EJEMPLO, credencial_prueba)

    certificado = verificar_firma(firmado)

    with pytest.raises(FirmaInvalidaError):
        rnc_del_certificado(certificado)


def test_verificar_firma_contenido_alterado_lanza_error(credencial_con_rnc):
    credencial = credencial_con_rnc("101672919")
    firmado = firmar_xml(XML_EJEMPLO, credencial)
    alterado = firmado.replace(b"0000000-0000", b"1111111-1111")

    with pytest.raises(FirmaInvalidaError):
        verificar_firma(alterado)


def test_verificar_firma_sin_certificado_embebido_lanza_error():
    with pytest.raises(FirmaInvalidaError):
        verificar_firma(XML_EJEMPLO)


def test_verificar_firma_xml_mal_formado_lanza_error():
    with pytest.raises(FirmaInvalidaError):
        verificar_firma(b"no es xml")
