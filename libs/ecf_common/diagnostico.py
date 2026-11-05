"""Detección de valores de relleno o inseguros en la configuración.

Motivo: el 2026-11-05 el entorno de producción arrancó sin un solo error con
`APP_ENV=test`, un `CERTIFICADO_PASSWORD` que era literalmente un placeholder
(`<deje la que ya tiene>`) y un `EMISOR_RNC` de relleno (`101000000`). Nada de
eso impedía arrancar: el fallo aparecía después, al firmar el XML o al enviar a
la DGII, con un mensaje que no decía qué variable había que corregir.

Aquí no se bloquea el arranque a propósito: tumbar un servicio que ya está
sirviendo es peor que arrancar avisando. `EcfBaseSettings.model_post_init`
registra estas advertencias en el log de cada servicio (Dokploy → Logs) y
`libs/ecf_common.auth.signing` sí falla en firme cuando la contraseña del
certificado es un placeholder, porque en ese caso la firma no puede funcionar
de ninguna manera.
"""
from __future__ import annotations

from collections.abc import Sequence

MINIMO_TOKEN_INTERNO = 32
"""Longitud mínima de KONTARA_TOKEN_INTERNO (32+ caracteres)."""

APP_ENVS_VALIDOS = frozenset({"test", "qa", "production"})

RNC_DE_RELLENO = frozenset({"101000000", "000000000", "999999999"})
"""RNC que aparecen en ejemplos y plantillas; nunca son de un emisor real."""

_FRAGMENTOS_DE_RELLENO = (
    "<",  # <deje la que ya tiene>, <pendiente>, <tu-contraseña>
    "cambiame",
    "cámbiame",
    "changeme",
    "change-me",
    "placeholder",
    "tu-password",
    "tu_password",
    "your-password",
    "your_password",
    "xxxxx",
)


def es_placeholder(valor: str | None) -> bool:
    """True si `valor` parece un texto de ejemplo y no un dato real.

    Cadena vacía no cuenta como placeholder: hay variables opcionales.
    """
    texto = (valor or "").strip().lower()
    if not texto:
        return False
    return any(fragmento in texto for fragmento in _FRAGMENTOS_DE_RELLENO)


def rnc_valido(rnc: str | None) -> bool:
    """True si `rnc` tiene la forma de un RNC dominicano: 9 dígitos.

    A propósito no se valida el dígito verificador: un RNC con formato correcto
    y verificador malo es un dato del usuario, no un error de configuración, y
    equivocarse aquí bloquearía empresas reales.
    """
    texto = (rnc or "").strip()
    return len(texto) == 9 and texto.isdigit()


def advertencias_de_configuracion(
    *,
    app_env: str,
    allowed_origins: Sequence[str] | None = None,
    certificado_password: str = "",
    emisor_rnc: str = "",
    kontara_token_interno: str = "",
    require_auth: bool | None = None,
) -> list[str]:
    """Problemas detectados, en español y nombrando la variable a corregir.

    Devuelve `[]` si no hay nada que reportar.
    """
    avisos: list[str] = []

    if app_env not in APP_ENVS_VALIDOS:
        avisos.append(
            f"APP_ENV={app_env!r} no es un ambiente conocido (test|qa|production)."
        )
    elif app_env == "test":
        avisos.append(
            "APP_ENV=test: mientras Configuración → Ambiente DGII no tenga una "
            "fila guardada, los envíos van a TesteCF (sin validez fiscal)."
        )

    if allowed_origins is not None and "*" in allowed_origins:
        avisos.append(
            'ALLOWED_ORIGINS contiene "*": CORS queda abierto a cualquier origen. '
            "En producción debe ser la lista de dominios reales."
        )

    if es_placeholder(certificado_password):
        avisos.append(
            "CERTIFICADO_PASSWORD contiene un texto de relleno, no la contraseña "
            "del .p12: la firma de comprobantes va a fallar."
        )

    if not emisor_rnc.strip():
        avisos.append("EMISOR_RNC está vacío: la empresa 1 no tiene RNC emisor.")
    elif emisor_rnc.strip() in RNC_DE_RELLENO or es_placeholder(emisor_rnc):
        avisos.append(
            f"EMISOR_RNC={emisor_rnc.strip()} es un RNC de relleno: los e-CF "
            "saldrían a nombre de un emisor que no existe."
        )
    elif not rnc_valido(emisor_rnc):
        avisos.append("EMISOR_RNC no tiene 9 dígitos.")

    if not kontara_token_interno.strip():
        avisos.append(
            "KONTARA_TOKEN_INTERNO está vacío: las llamadas internas entre "
            "servicios no podrán saltarse REQUIRE_AUTH."
        )
    elif len(kontara_token_interno.strip()) < MINIMO_TOKEN_INTERNO:
        avisos.append(
            f"KONTARA_TOKEN_INTERNO tiene menos de {MINIMO_TOKEN_INTERNO} "
            "caracteres (genéralo con: openssl rand -hex 32)."
        )

    if require_auth is False:
        avisos.append(
            "REQUIRE_AUTH=false: la API acepta peticiones sin sesión (nginx sí "
            "la exige)."
        )

    return avisos
