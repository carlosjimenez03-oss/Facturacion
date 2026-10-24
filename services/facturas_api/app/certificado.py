"""Estado del certificado digital con que firma facturas_api (GET
/certificado). La lógica vive en `libs/ecf_common/auth/certificado_activo`
(la comparte erp-api para Configuración → Certificado digital): solo datos
públicos del X.509, nunca la llave ni la contraseña."""
from __future__ import annotations

from typing import Any

from libs.ecf_common.auth import certificado_activo

from .config import FacturasApiSettings


def estado_certificado(settings: FacturasApiSettings, rnc_emisor: str | None = None) -> dict[str, Any]:
    """`rnc_emisor`: el de la empresa en curso (por defecto EMISOR_RNC)."""
    return certificado_activo.estado_certificado(
        settings.certificado_path, settings.certificado_password, settings.emisor_rnc if rnc_emisor is None else rnc_emisor
    )
