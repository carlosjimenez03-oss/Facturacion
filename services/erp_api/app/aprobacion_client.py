"""Cliente HTTP del módulo `aprobacion_comercial` (POST /aprobacion): firma
el ACECF con el certificado del emisor y lo envía a la DGII. erp-api no
tiene el certificado; solo arma el XML (compras_calculos.construir_acecf).
"""
from __future__ import annotations

from typing import Any

import httpx


class AprobacionComercialError(Exception):
    """El módulo no respondió o la DGII rechazó el envío -- compras.py lo
    traduce a 502 con el mensaje tal cual."""


async def enviar_aprobacion_comercial(
    url_base: str, *, encf: str, xml: bytes, timeout: float = 60.0
) -> dict[str, Any]:
    """Devuelve la respuesta de la DGII ({mensaje, estado, codigo})."""
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            respuesta = await client.post(
                f"{url_base.rstrip('/')}/aprobacion",
                data={"encf": encf},
                files={"xml": (f"ACECF_{encf}.xml", xml, "text/xml")},
            )
    except httpx.HTTPError as exc:
        raise AprobacionComercialError(f"No se pudo contactar el módulo de aprobación comercial: {exc}") from exc
    if respuesta.status_code >= 400:
        try:
            detalle = respuesta.json().get("detail") or respuesta.text
        except ValueError:
            detalle = respuesta.text
        raise AprobacionComercialError(f"La aprobación comercial no se pudo enviar: {detalle}")
    try:
        return respuesta.json()
    except ValueError as exc:
        raise AprobacionComercialError("Respuesta inválida del módulo de aprobación comercial") from exc
