"""Certificado digital "activo" cargado desde Configuración → Certificado
digital, y cifrado de los secretos que guarda la aplicación.

Hasta la fase 6 el .p12 solo se podía cambiar subiéndolo por SSH a
`./certs/emisor.p12` (montado de solo lectura) y cambiando
CERTIFICADO_PASSWORD en Dokploy. Ahora erp-api valida el .p12 que sube el
usuario (contraseña, vigencia, RNC) y lo deja en un volumen compartido
(`CERTIFICADO_ACTIVO_PATH`, por defecto /app/certs-activos/activo.json):
un único JSON con el .p12 en base64 y la contraseña CIFRADA (Fernet) con
una llave derivada de `CERTIFICADOS_CLAVE` (variable de entorno, nunca en
la base ni en el volumen). Se escribe en un archivo temporal y se cambia
con `os.replace` (atómico): un servicio que lee mientras tanto ve el
certificado anterior completo o el nuevo completo, nunca uno a medias.

`signing.cargar_certificado_p12` prefiere este certificado cuando existe y
se puede abrir; si no (sin volumen, sin CERTIFICADOS_CLAVE, archivo dañado)
usa como siempre CERTIFICADO_PATH/CERTIFICADO_PASSWORD. Ver docs/AUTH.md,
"Certificado digital".
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509 import Certificate
from cryptography.x509.oid import NameOID

logger = logging.getLogger(__name__)

RUTA_POR_DEFECTO = "/app/certs-activos/activo.json"
# Aviso de renovación: recordatorios a 60, 30 y 7 días.
DIAS_POR_VENCER = 60


class CifradoNoConfiguradoError(Exception):
    """Falta CERTIFICADOS_CLAVE: no se pueden guardar ni leer secretos."""


class CertificadoInvalidoError(Exception):
    pass


def ruta_activo() -> Path:
    return Path(os.environ.get("CERTIFICADO_ACTIVO_PATH") or RUTA_POR_DEFECTO)


def _fernet() -> Fernet:
    clave = os.environ.get("CERTIFICADOS_CLAVE", "")
    if not clave:
        raise CifradoNoConfiguradoError("CERTIFICADOS_CLAVE no está configurada")
    # Cualquier texto largo sirve: se deriva una llave Fernet de 32 bytes.
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(clave.encode("utf-8")).digest()))


def cifrado_disponible() -> bool:
    return bool(os.environ.get("CERTIFICADOS_CLAVE"))


def cifrar(texto: str) -> str:
    return _fernet().encrypt(texto.encode("utf-8")).decode("ascii")


def descifrar(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise CifradoNoConfiguradoError("No se pudo descifrar: CERTIFICADOS_CLAVE cambió") from exc


def abrir_p12(p12_bytes: bytes, password: str) -> tuple[RSAPrivateKey, Certificate]:
    try:
        private_key, certificate, _ = pkcs12.load_key_and_certificates(p12_bytes, password.encode("utf-8"))
    except ValueError as exc:
        raise CertificadoInvalidoError("La contraseña no corresponde al certificado o el archivo está dañado") from exc
    if private_key is None or certificate is None:
        raise CertificadoInvalidoError("El archivo no contiene una llave privada y un certificado")
    if not isinstance(private_key, RSAPrivateKey):
        raise CertificadoInvalidoError("Solo se aceptan certificados con llave RSA para firmar e-CF")
    return private_key, certificate


def leer_activo() -> tuple[bytes, str, dict[str, Any]] | None:
    """(.p12, contraseña, metadatos) del certificado cargado, o None si no
    hay o no se puede usar (se registra el motivo y se usa el de siempre)."""
    ruta = ruta_activo()
    if not ruta.is_file():
        return None
    try:
        datos = json.loads(ruta.read_text(encoding="utf-8"))
        return base64.b64decode(datos["p12"]), descifrar(datos["contrasena"]), datos.get("metadatos", {})
    except (OSError, ValueError, KeyError, CifradoNoConfiguradoError) as exc:
        logger.error("Certificado activo %s inutilizable, se usa CERTIFICADO_PATH: %s", ruta, exc)
        return None


def huella_activo() -> float | None:
    """Cambia cada vez que se carga un certificado nuevo (para cachés)."""
    try:
        return ruta_activo().stat().st_mtime
    except OSError:
        return None


def guardar_activo(p12_bytes: bytes, password: str, metadatos: dict[str, Any]) -> Path:
    """Escribe el certificado activo de forma atómica (temporal + os.replace)."""
    contenido = json.dumps(
        {
            "p12": base64.b64encode(p12_bytes).decode("ascii"),
            "contrasena": cifrar(password),
            "metadatos": metadatos,
            "cargado_en": datetime.now(timezone.utc).isoformat(),
        }
    )
    ruta = ruta_activo()
    ruta.parent.mkdir(parents=True, exist_ok=True)
    fd, temporal = tempfile.mkstemp(dir=ruta.parent, prefix=".activo-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(contenido)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(temporal, 0o640)
        os.replace(temporal, ruta)
    except BaseException:
        Path(temporal).unlink(missing_ok=True)
        raise
    return ruta


# --- Datos públicos del certificado ---------------------------------------------------------

def _attr(nombre, oid) -> str | None:
    valores = nombre.get_attributes_for_oid(oid)
    return str(valores[0].value) if valores else None


def digitos_del_sujeto(cert: Certificate) -> list[str]:
    """Todos los números que aparecen en el Subject (SN, organizationIdentifier, ...)."""
    return ["".join(c for c in str(a.value) if c.isdigit()) for a in cert.subject]


def pertenece_al_rnc(cert: Certificate, rnc: str) -> bool:
    rnc = "".join(c for c in rnc if c.isdigit())
    return bool(rnc) and any(rnc in d for d in digitos_del_sujeto(cert))


def datos_certificado(cert: Certificate, ahora: datetime | None = None) -> dict[str, Any]:
    """Titular, firmante, emisor y vigencia. Nunca incluye la llave."""
    ahora = ahora or datetime.now(timezone.utc)
    vence = cert.not_valid_after_utc
    desde = cert.not_valid_before_utc
    total = max((vence - desde).total_seconds(), 1)
    consumido = min(max((ahora - desde).total_seconds() / total, 0), 1)
    dias = (vence - ahora).days
    if vence <= ahora:
        estado = "vencido"
    elif dias <= DIAS_POR_VENCER:
        estado = "por_vencer"
    else:
        estado = "vigente"
    cn = _attr(cert.subject, NameOID.COMMON_NAME)
    organizacion = _attr(cert.subject, NameOID.ORGANIZATION_NAME)
    return {
        "estado": estado,
        "titular": organizacion or cn,
        "firmante": cn if organizacion else None,
        "documento_firmante": _attr(cert.subject, NameOID.SERIAL_NUMBER),
        "sujeto": cert.subject.rfc4514_string(),
        "emisor": _attr(cert.issuer, NameOID.ORGANIZATION_NAME) or _attr(cert.issuer, NameOID.COMMON_NAME)
        or cert.issuer.rfc4514_string(),
        "rnc": _attr(cert.subject, NameOID.SERIAL_NUMBER),
        "numero_serie": format(cert.serial_number, "X"),
        "valido_desde": desde,
        "vence": vence,
        "dias_restantes": max(dias, 0) if vence > ahora else 0,
        "dias_para_vencer": dias,
        "vigencia_consumida_pct": round(consumido * 100),
    }


def estado_certificado(certificado_path: str, certificado_password: str, emisor_rnc: str) -> dict[str, Any]:
    """Estado del certificado con que se firma hoy: el activo (cargado desde
    Configuración) o, si no hay, el de CERTIFICADO_PATH."""
    activo = leer_activo()
    if activo is not None:
        p12, password, metadatos = activo
        archivo, origen = metadatos.get("archivo") or "certificado.p12", "cargado"
    else:
        archivo, origen = (os.path.basename(certificado_path) if certificado_path else None), "variables"
        if not certificado_path or not os.path.exists(certificado_path):
            return {
                "estado": "no_configurado",
                "origen": origen,
                "archivo": archivo,
                "error": "No hay certificado: cárguelo aquí o súbalo a certs/emisor.p12 (ver docs/DOKPLOY.md).",
            }
        try:
            with open(certificado_path, "rb") as fh:
                p12 = fh.read()
        except OSError as exc:
            return {"estado": "error", "origen": origen, "archivo": archivo, "error": f"No se pudo leer: {exc}"}
        password = certificado_password
    try:
        _, cert = abrir_p12(p12, password)
    except CertificadoInvalidoError as exc:
        return {"estado": "error", "origen": origen, "archivo": archivo, "error": str(exc)}
    rnc = "".join(c for c in emisor_rnc if c.isdigit())
    return {
        **datos_certificado(cert),
        "origen": origen,
        "archivo": archivo,
        "coincide_rnc_emisor": pertenece_al_rnc(cert, rnc) if rnc else None,
    }
