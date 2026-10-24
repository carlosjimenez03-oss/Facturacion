"""Firma digital XML con el certificado del emisor (INDOTEL, formato .p12).

Nota importante: este documento ("Descripción Técnica Servicios DGII") solo
describe los servicios REST — no especifica el perfil exacto de firma digital
(XAdES-BES completo con SignedProperties/política de firma, vs. XML-DSig
simple). Aquí se implementa una firma XML-DSig "enveloped" estándar
(RSA-SHA256, C14N exclusivo, certificado embebido en KeyInfo/X509Data), que
es el mecanismo base sobre el que se construye XAdES y el usado por
implementaciones de referencia del e-CF dominicano. Antes de certificar con
la DGII, validar el XML firmado contra el estándar de firma digital y los
XSD oficiales (documento de Estándar de Facturación Electrónica), y ajustar
aquí si se requieren las propiedades calificadas adicionales de XAdES-BES.
"""
from dataclasses import dataclass

from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509 import Certificate
from lxml import etree
from signxml import XMLSigner
from signxml.algorithms import (
    CanonicalizationMethod,
    DigestAlgorithm,
    SignatureConstructionMethod,
    SignatureMethod,
)


@dataclass
class CredencialFirma:
    private_key: RSAPrivateKey
    certificate: Certificate


def cargar_certificado_p12(path: str, password: str, *, preferir_activo: bool = True) -> CredencialFirma:
    """Carga la llave privada y el certificado X.509 desde un archivo .p12/.pfx.

    Si en Configuración se cargó un certificado nuevo (ver
    `certificado_activo.py`), se usa ese en vez de `path`/`password`; si no
    existe o no se puede abrir, se usa `path` como siempre -- solo en la
    empresa 1: las demás firman únicamente con el que se les cargó."""
    if preferir_activo:
        from .certificado_activo import (
            SIN_CERTIFICADO_EMPRESA,
            CertificadoInvalidoError,
            abrir_p12,
            leer_activo,
            usa_certificado_de_variables,
        )

        activo = leer_activo()
        if activo is not None:
            try:
                private_key, certificate = abrir_p12(activo[0], activo[1])
                return CredencialFirma(private_key=private_key, certificate=certificate)
            except CertificadoInvalidoError:
                if not usa_certificado_de_variables():
                    raise ValueError(SIN_CERTIFICADO_EMPRESA) from None
        if not usa_certificado_de_variables():
            raise FileNotFoundError(SIN_CERTIFICADO_EMPRESA)
    with open(path, "rb") as fh:
        p12_bytes = fh.read()
    private_key, certificate, _ = pkcs12.load_key_and_certificates(
        p12_bytes, password.encode("utf-8")
    )
    if private_key is None or certificate is None:
        raise ValueError(f"El archivo {path} no contiene una llave privada y certificado válidos")
    if not isinstance(private_key, RSAPrivateKey):
        raise ValueError("Solo se soportan llaves privadas RSA para la firma de e-CF")
    return CredencialFirma(private_key=private_key, certificate=certificate)


def firmar_xml(xml_bytes: bytes, credencial: CredencialFirma) -> bytes:
    """Firma un XML (enveloped XML-DSig, RSA-SHA256, C14N exclusivo).

    Retorna el XML firmado, listo para enviarlo a los servicios de la DGII
    que reciben archivos "firmados digitalmente" (semilla, e-CF, RFCE,
    aprobación comercial, anulación de rangos).
    """
    root = etree.fromstring(xml_bytes)

    signed_root = XMLSigner(
        method=SignatureConstructionMethod.enveloped,
        signature_algorithm=SignatureMethod.RSA_SHA256,
        digest_algorithm=DigestAlgorithm.SHA256,
        c14n_algorithm=CanonicalizationMethod.EXCLUSIVE_XML_CANONICALIZATION_1_0,
    ).sign(root, key=credencial.private_key, cert=[credencial.certificate])

    return etree.tostring(
        signed_root, xml_declaration=True, encoding="UTF-8", standalone=False
    )
