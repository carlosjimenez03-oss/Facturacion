"""Construcción de las URLs públicas de "Consulta timbre (QR)" de la DGII
(e-CF y FC) -- son las que se codifican en el código QR de la Representación
Impresa. Sin dependencias de I/O para poder usarse desde cualquier servicio
(consulta_timbre, facturas_api) sin arrastrar el resto de un módulo ajeno.

Ver docs/dgii/SERVICIOS_DGII.md, secciones "11. Consulta timbre (QR)" y
"12. Consulta timbre FC (QR)".
"""
from __future__ import annotations

from urllib.parse import urlencode

from .environments import DGIIEnvironment, DGIIService, dgii_service_base_url


def construir_url_timbre_ecf(
    *,
    ambiente: DGIIEnvironment,
    rnc_emisor: str,
    rnc_comprador: str,
    encf: str,
    fecha_emision: str,
    monto_total: str,
    fecha_firma: str,
    codigo_seguridad: str,
) -> str:
    base_url = dgii_service_base_url(DGIIService.CONSULTA_TIMBRE, ambiente)
    params = {
        "rncemisor": rnc_emisor,
        "rnccomprador": rnc_comprador,
        "encf": encf,
        "fechaemision": fecha_emision,
        "montototal": monto_total,
        "fechafirma": fecha_firma,
        "codigoseguridad": codigo_seguridad,
    }
    return f"{base_url}?{urlencode(params)}"


def construir_url_timbre_fc(
    *, ambiente: DGIIEnvironment, rnc_emisor: str, encf: str, monto_total: str, codigo_seguridad: str
) -> str:
    base_url = dgii_service_base_url(DGIIService.CONSULTA_TIMBRE_FC, ambiente)
    params = {
        "rncemisor": rnc_emisor,
        "encf": encf,
        "montototal": monto_total,
        "codigoseguridad": codigo_seguridad,
    }
    return f"{base_url}?{urlencode(params)}"
