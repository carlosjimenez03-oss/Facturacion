"""Certificado activo cargado desde Configuración: cifrado de la
contraseña, cambio atómico, preferencia sobre CERTIFICADO_PATH y respaldo
cuando no se puede usar."""
import datetime
import json

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID

from libs.ecf_common.auth import certificado_activo as ca
from libs.ecf_common.auth.signing import cargar_certificado_p12
from libs.ecf_common.auth.token_provider import TokenProvider
from libs.ecf_common.environments import DGIIEnvironment


def crear_p12(password: str = "clave", rnc: str = "131897542", dias: int = 365, cn: str = "María Rosario",
              org: str | None = "Caribe Verde SRL") -> bytes:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    attrs = [x509.NameAttribute(NameOID.COMMON_NAME, cn), x509.NameAttribute(NameOID.SERIAL_NUMBER, rnc)]
    if org:
        attrs.append(x509.NameAttribute(NameOID.ORGANIZATION_NAME, org))
    nombre = x509.Name(attrs)
    emisor = x509.Name([x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Avansi CA")])
    ahora = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder().subject_name(nombre).issuer_name(emisor).public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(ahora - datetime.timedelta(days=10))
        .not_valid_after(ahora + datetime.timedelta(days=dias))
        .sign(key, hashes.SHA256())
    )
    return pkcs12.serialize_key_and_certificates(
        b"emisor", key, cert, None, serialization.BestAvailableEncryption(password.encode())
    )


@pytest.fixture
def entorno(tmp_path, monkeypatch):
    monkeypatch.setenv("CERTIFICADO_ACTIVO_PATH", str(tmp_path / "activos" / "activo.json"))
    monkeypatch.setenv("CERTIFICADOS_CLAVE", "una-clave-larga-de-prueba-de-32-caracteres-o-mas")
    return tmp_path


def test_cifrar_y_descifrar(entorno):
    token = ca.cifrar("secreta")
    assert "secreta" not in token
    assert ca.descifrar(token) == "secreta"


def test_cifrado_nuevo_usa_scrypt_con_sal(entorno):
    a, b = ca.cifrar("secreta"), ca.cifrar("secreta")
    assert a.startswith("v2:") and b.startswith("v2:")
    assert a.split(":")[1] != b.split(":")[1]  # sal al azar por secreto
    assert ca.descifrar(a) == ca.descifrar(b) == "secreta"
    assert not ca.es_formato_antiguo(a)


def test_formato_antiguo_se_sigue_leyendo_y_se_recifra(entorno):
    antiguo = ca._fernet_antiguo(ca._clave()).encrypt(b"secreta").decode("ascii")
    assert ca.es_formato_antiguo(antiguo)
    assert ca.descifrar(antiguo) == "secreta"
    nuevo = ca.recifrar_si_antiguo(antiguo)
    assert nuevo.startswith("v2:") and ca.descifrar(nuevo) == "secreta"
    assert ca.recifrar_si_antiguo(nuevo) == nuevo


def test_clave_corta_no_cifra_pero_lee_lo_anterior(entorno, monkeypatch):
    monkeypatch.setenv("CERTIFICADOS_CLAVE", "corta")
    antiguo = ca._fernet_antiguo("corta").encrypt(b"secreta").decode("ascii")
    assert ca.cifrado_disponible() is False
    assert "al menos 32" in ca.motivo_sin_cifrado()
    with pytest.raises(ca.CifradoNoConfiguradoError, match="al menos 32"):
        ca.cifrar("x")
    # Lo que ya se guardó con una clave corta no se pierde al actualizar.
    assert ca.descifrar(antiguo) == "secreta"
    assert ca.recifrar_si_antiguo(antiguo) == antiguo


def test_guardar_activo_usa_el_formato_nuevo(entorno):
    ruta = ca.guardar_activo(crear_p12("clave"), "clave", {})
    assert json.loads(ruta.read_text())["contrasena"].startswith("v2:")


def test_sin_clave_no_hay_cifrado(monkeypatch):
    monkeypatch.delenv("CERTIFICADOS_CLAVE", raising=False)
    assert ca.cifrado_disponible() is False
    with pytest.raises(ca.CifradoNoConfiguradoError):
        ca.cifrar("x")


def test_abrir_p12_valida_contrasena():
    p12 = crear_p12("buena")
    _, cert = ca.abrir_p12(p12, "buena")
    assert ca.pertenece_al_rnc(cert, "1-31-89754-2")
    assert not ca.pertenece_al_rnc(cert, "101000000")
    # Coincidencia EXACTA: un RNC contenido en otro número no basta.
    assert not ca.pertenece_al_rnc(cert, "31897542")
    assert not ca.pertenece_al_rnc(cert, "")
    with pytest.raises(ca.CertificadoInvalidoError, match="contraseña"):
        ca.abrir_p12(p12, "mala")
    with pytest.raises(ca.CertificadoInvalidoError):
        ca.abrir_p12(b"no es un p12", "x")


def _cert_con(*atributos):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    nombre = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "X"), *atributos])
    ahora = datetime.datetime.now(datetime.timezone.utc)
    return (
        x509.CertificateBuilder().subject_name(nombre).issuer_name(nombre).public_key(key.public_key())
        .serial_number(1).not_valid_before(ahora).not_valid_after(ahora + datetime.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )


def test_pertenece_al_rnc_solo_en_serialnumber_ou_u_organization_identifier():
    oid_org_id = x509.ObjectIdentifier("2.5.4.97")
    assert ca.pertenece_al_rnc(_cert_con(x509.NameAttribute(NameOID.SERIAL_NUMBER, "IDCDO-131897542")), "131897542")
    assert ca.pertenece_al_rnc(_cert_con(x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, "RNC 131-89754-2")), "131897542")
    assert ca.pertenece_al_rnc(_cert_con(x509.NameAttribute(oid_org_id, "VATDO-131897542")), "131897542")
    # Un número más largo que lo contiene (p. ej. una cédula) no es el RNC.
    assert not ca.pertenece_al_rnc(_cert_con(x509.NameAttribute(NameOID.SERIAL_NUMBER, "IDCDO-01318975423")), "131897542")
    # Ni el RNC en otro campo (p. ej. el CN o el correo).
    assert not ca.pertenece_al_rnc(
        _cert_con(x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Empresa 131897542 SRL")), "131897542"
    )


def test_datos_certificado():
    _, cert = ca.abrir_p12(crear_p12(dias=40), "clave")
    datos = ca.datos_certificado(cert)
    assert datos["estado"] == "por_vencer"
    assert datos["titular"] == "Caribe Verde SRL"
    assert datos["firmante"] == "María Rosario"
    assert datos["documento_firmante"] == "131897542"
    assert datos["emisor"] == "Avansi CA"
    assert 38 <= datos["dias_para_vencer"] <= 40
    assert 0 < datos["vigencia_consumida_pct"] < 100


def test_guardar_activo_es_atomico_y_no_guarda_la_contrasena_en_claro(entorno):
    p12 = crear_p12("clave-secreta")
    ruta = ca.guardar_activo(p12, "clave-secreta", {"archivo": "nuevo.p12"})
    contenido = ruta.read_text()
    assert "clave-secreta" not in contenido
    assert json.loads(contenido)["metadatos"]["archivo"] == "nuevo.p12"
    assert [p.name for p in ruta.parent.iterdir()] == ["activo.json"]  # sin temporales
    leido = ca.leer_activo()
    assert leido[0] == p12 and leido[1] == "clave-secreta"


def test_cargar_certificado_p12_prefiere_el_activo(entorno):
    viejo = entorno / "emisor.p12"
    viejo.write_bytes(crear_p12("vieja", cn="Viejo"))
    assert cargar_certificado_p12(str(viejo), "vieja").certificate.subject.get_attributes_for_oid(
        NameOID.COMMON_NAME)[0].value == "Viejo"
    ca.guardar_activo(crear_p12("nueva", cn="Nuevo"), "nueva", {})
    cred = cargar_certificado_p12(str(viejo), "vieja")
    assert cred.certificate.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value == "Nuevo"
    # Se puede pedir explícitamente el del archivo.
    cred = cargar_certificado_p12(str(viejo), "vieja", preferir_activo=False)
    assert cred.certificate.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value == "Viejo"


def test_si_cambia_la_clave_se_usa_el_de_siempre(entorno, monkeypatch):
    viejo = entorno / "emisor.p12"
    viejo.write_bytes(crear_p12("vieja", cn="Viejo"))
    ca.guardar_activo(crear_p12("nueva", cn="Nuevo"), "nueva", {})
    monkeypatch.setenv("CERTIFICADOS_CLAVE", "otra-clave-distinta-de-32-caracteres-o-mas")
    cred = cargar_certificado_p12(str(viejo), "vieja")
    assert cred.certificate.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value == "Viejo"


def test_estado_certificado(entorno):
    sin = ca.estado_certificado(str(entorno / "no-existe.p12"), "", "131897542")
    assert sin["estado"] == "no_configurado"
    viejo = entorno / "emisor.p12"
    viejo.write_bytes(crear_p12("vieja"))
    e = ca.estado_certificado(str(viejo), "vieja", "131897542")
    assert e["origen"] == "variables" and e["estado"] == "vigente" and e["coincide_rnc_emisor"] is True
    assert ca.estado_certificado(str(viejo), "mala", "")["estado"] == "error"
    ca.guardar_activo(crear_p12("nueva", rnc="101000000"), "nueva", {"archivo": "otro.p12"})
    e = ca.estado_certificado(str(viejo), "vieja", "131897542")
    assert e["origen"] == "cargado" and e["archivo"] == "otro.p12" and e["coincide_rnc_emisor"] is False


def test_token_provider_recarga_si_cambia_el_certificado(entorno):
    viejo = entorno / "emisor.p12"
    viejo.write_bytes(crear_p12("vieja", cn="Viejo"))
    tp = TokenProvider(DGIIEnvironment.TESTECF, str(viejo), "vieja")
    assert tp._credencial_firma().certificate.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value == "Viejo"
    ca.guardar_activo(crear_p12("nueva", cn="Nuevo"), "nueva", {})
    assert tp._credencial_firma().certificate.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value == "Nuevo"
