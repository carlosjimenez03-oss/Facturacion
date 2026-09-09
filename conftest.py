"""Fixtures compartidas por TODOS los módulos y libs (pytest las descubre
automáticamente por vivir en el conftest.py de la raíz del repo)."""
import datetime
import os
from pathlib import Path

os.environ.setdefault("APP_ENV", "test")

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID

from libs.ecf_common.auth.signing import CredencialFirma


def _generar_par_llave_certificado(serial_number: str | None = None):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    atributos = [x509.NameAttribute(NameOID.COMMON_NAME, "Emisor de Prueba")]
    if serial_number is not None:
        # El campo "SN" (serialNumber) del certificado debe corresponder al
        # RNC/Cédula/Pasaporte del propietario (ver docs/dgii/EMISORES_ELECTRONICOS.md,
        # "Firmado de XML") -- lo usan los tests de los servicios receptor_*
        # que extraen el RNC del certificado del llamante.
        atributos.append(x509.NameAttribute(NameOID.SERIAL_NUMBER, serial_number))
    name = x509.Name(atributos)
    ahora = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(ahora)
        .not_valid_after(ahora + datetime.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    return key, cert


@pytest.fixture
def credencial_prueba() -> CredencialFirma:
    key, cert = _generar_par_llave_certificado()
    return CredencialFirma(private_key=key, certificate=cert)


@pytest.fixture
def credencial_con_rnc():
    """Factory: `credencial_con_rnc("131234567")` -> CredencialFirma cuyo
    certificado lleva ese valor en el campo SN (serialNumber) del Subject."""

    def _crear(rnc: str) -> CredencialFirma:
        key, cert = _generar_par_llave_certificado(serial_number=rnc)
        return CredencialFirma(private_key=key, certificate=cert)

    return _crear


@pytest.fixture
def p12_prueba(tmp_path: Path) -> tuple[Path, str]:
    key, cert = _generar_par_llave_certificado()
    password = "clave-prueba"
    p12_bytes = pkcs12.serialize_key_and_certificates(
        name=b"emisor-prueba",
        key=key,
        cert=cert,
        cas=None,
        encryption_algorithm=serialization.BestAvailableEncryption(password.encode()),
    )
    p12_path = tmp_path / "emisor.p12"
    p12_path.write_bytes(p12_bytes)
    return p12_path, password
