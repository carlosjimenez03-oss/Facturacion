"""Verificación de firma XML entrante (XML-DSig, enveloped) -- el
complemento inverso de `signing.py::firmar_xml` (que firma, no verifica).

Usado por los servicios `receptor_*` (`services/receptor_autenticacion`,
`services/receptor_recepcion`, `services/receptor_aprobacion_comercial`),
que reciben XML firmados por certificados de OTROS contribuyentes y
necesitan comprobar la firma antes de confiar en el contenido.
"""
from __future__ import annotations

import base64

from cryptography import x509
from cryptography.x509 import Certificate
from cryptography.x509.oid import NameOID
from lxml import etree
from signxml import XMLVerifier

_DS_NS = {"ds": "http://www.w3.org/2000/09/xmldsig#"}


class FirmaInvalidaError(Exception):
    pass


def _extraer_certificado(xml_bytes: bytes) -> Certificate:
    try:
        root = etree.fromstring(xml_bytes)
    except etree.XMLSyntaxError as exc:
        raise FirmaInvalidaError(f"XML mal formado: {exc}") from exc

    nodo = root.find(".//ds:Signature//ds:X509Certificate", _DS_NS)
    if nodo is None or not (nodo.text or "").strip():
        raise FirmaInvalidaError(
            "El XML no incluye un certificado X.509 en su firma (ds:X509Certificate)"
        )
    der = base64.b64decode("".join((nodo.text or "").split()))
    try:
        return x509.load_der_x509_certificate(der)
    except ValueError as exc:
        raise FirmaInvalidaError(f"Certificado X.509 inválido: {exc}") from exc


def verificar_firma(xml_bytes: bytes) -> Certificate:
    """Verifica que `xml_bytes` esté firmado (XML-DSig) con el certificado
    que el propio XML embebe en `ds:X509Certificate`, y retorna ese
    certificado.

    Solo verifica integridad (la firma corresponde efectivamente a ese
    certificado y a este contenido) -- a propósito NO valida la cadena de
    confianza contra la CA raíz de INDOTEL (requeriría su bundle público, no
    incluido en este repo). Ver la limitación documentada en
    docs/dgii/EMISORES_ELECTRONICOS.md, sección "Autenticación (opcional)".
    Pasar `x509_cert=certificado` explícitamente (en vez de dejar que
    signxml use el almacén de CAs del sistema operativo, su comportamiento
    por defecto) es lo que acota la verificación a ese alcance.
    """
    certificado = _extraer_certificado(xml_bytes)
    try:
        XMLVerifier().verify(xml_bytes, x509_cert=certificado)
    except Exception as exc:  # signxml lanza distintos tipos según el fallo
        raise FirmaInvalidaError(f"Firma XML inválida: {exc}") from exc
    return certificado


def rnc_del_certificado(certificado: Certificate) -> str:
    """Extrae el RNC/Cédula/Pasaporte del campo `SN` (serialNumber) del
    Subject del certificado -- el estándar de firmado exige que ese campo
    corresponda al propietario del certificado (ver
    docs/dgii/EMISORES_ELECTRONICOS.md, "Firmado de XML")."""
    valores = certificado.subject.get_attributes_for_oid(NameOID.SERIAL_NUMBER)
    if not valores:
        raise FirmaInvalidaError(
            "El certificado no tiene el campo SN (serialNumber) con el "
            "RNC/Cédula/Pasaporte del firmante"
        )
    return str(valores[0].value)
