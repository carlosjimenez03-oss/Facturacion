"""Emisión y validación de tokens JWT propios, para los servicios
`receptor_*` (donde ESTE contribuyente es el servidor de autenticación en
vez del cliente).

Firmados con RS256 usando la misma llave privada del certificado .p12 del
emisor (`CredencialFirma`, ver `signing.py`) -- todos los módulos
`receptor_*` cargan el mismo certificado (mismas variables de entorno
CERTIFICADO_PATH/CERTIFICADO_PASSWORD que el resto del repo), así que
cualquiera de ellos puede validar un token emitido por
`receptor_autenticacion` usando la llave pública del mismo certificado, sin
necesidad de una llamada de red entre servicios.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import jwt

from .signing import CredencialFirma

ALGORITMO = "RS256"
DURACION_TOKEN = timedelta(hours=1)

_FORMATO_FECHA = "%Y-%m-%dT%H:%M:%SZ"


class TokenInvalidoError(Exception):
    pass


@dataclass
class TokenEmitido:
    token: str
    expedido: datetime
    expira: datetime

    @property
    def expedido_str(self) -> str:
        return self.expedido.strftime(_FORMATO_FECHA)

    @property
    def expira_str(self) -> str:
        return self.expira.strftime(_FORMATO_FECHA)


def emitir_token(credencial: CredencialFirma, subject: str) -> TokenEmitido:
    """Emite un JWT identificando a `subject` (el RNC/Cédula/Pasaporte
    extraído del certificado del llamante, ver `xml_signature.rnc_del_certificado`)."""
    expedido = datetime.now(timezone.utc)
    expira = expedido + DURACION_TOKEN
    token = jwt.encode(
        {"sub": subject, "iat": expedido, "exp": expira},
        credencial.private_key,
        algorithm=ALGORITMO,
    )
    return TokenEmitido(token=token, expedido=expedido, expira=expira)


def validar_token(credencial: CredencialFirma, token: str) -> str:
    """Valida un JWT emitido por `emitir_token` y retorna su `subject`.

    Lanza `TokenInvalidoError` si el token es inválido, fue alterado, o ya
    expiró.
    """
    try:
        claims = jwt.decode(token, credencial.certificate.public_key(), algorithms=[ALGORITMO])
    except jwt.InvalidTokenError as exc:
        raise TokenInvalidoError(str(exc)) from exc
    sub = claims.get("sub")
    if not sub:
        raise TokenInvalidoError("El token no tiene claim 'sub'")
    return sub
