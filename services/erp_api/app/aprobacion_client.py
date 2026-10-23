"""Cliente HTTP del módulo `aprobacion_comercial` (POST /aprobacion): firma
el ACECF con el certificado del emisor, lo envía a la DGII y devuelve el
XML firmado. erp-api no tiene el certificado; solo arma el XML
(compras_calculos.construir_acecf). También la entrega (best-effort) de ese
ACECF firmado al proveedor, con la URL de `consulta_directorio`.
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
    """Devuelve la respuesta de la DGII ({mensaje, estado, codigo}) más
    `xml_firmado` (el ACECF firmado). Un 2xx NO significa que la DGII lo
    aceptó: lo decide `codigo` (compras_calculos.resultado_aprobacion_dgii)."""
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


class EntregaEmisorError(Exception):
    """No se pudo entregar el ACECF al proveedor. Nunca anula la aprobación
    (la DGII ya la recibió): queda en `error_envio_emisor`."""


_RUTA_ACEPTACION = "/fe/aprobacioncomercial/api/ecf"


def url_aprobacion_comercial_emisor(url_aceptacion: str) -> str:
    """URL del servicio "Aprobación comercial" del proveedor a partir del
    `urlAceptacion` de Consulta Directorio: si ya trae la ruta del estándar
    (`.../fe/aprobacioncomercial/api/ecf`) se usa tal cual; si es solo la
    URL base, se le agrega."""
    url = url_aceptacion.strip().rstrip("/")
    if url.lower().endswith("/api/ecf"):
        return url
    return f"{url}{_RUTA_ACEPTACION}"


async def entregar_acecf_al_emisor(
    directorio_url: str, *, rnc_emisor: str, nombre_archivo: str, xml: bytes, timeout: float = 30.0
) -> str:
    """Envía el ACECF firmado al proveedor (docs/dgii/EMISORES_ELECTRONICOS.md,
    "Aprobación comercial": todo receptor/emisor electrónico expone
    `POST /fe/aprobacioncomercial/api/ecf`). Busca su `urlAceptacion` en
    `consulta_directorio` (GET /directorio/{rnc}) y hace POST multipart
    (campo `xml`). Devuelve la URL usada; EntregaEmisorError si algo falla.

    No se autentica ante el proveedor (semilla/token de su servicio de
    autenticación, que el estándar hace opcional): si su servicio exige
    Bearer, la entrega falla con su 401 y queda registrada."""
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            directorio = await client.get(f"{directorio_url.rstrip('/')}/directorio/{rnc_emisor}")
            if directorio.status_code >= 400:
                raise EntregaEmisorError(
                    f"Consulta Directorio respondió {directorio.status_code}: {directorio.text[:300]}"
                )
            try:
                entradas = directorio.json()
            except ValueError as exc:
                raise EntregaEmisorError("Respuesta inválida de Consulta Directorio") from exc
            url_aceptacion = next(
                (
                    e.get("urlAceptacion")
                    for e in (entradas if isinstance(entradas, list) else [])
                    if isinstance(e, dict) and e.get("urlAceptacion")
                ),
                None,
            )
            if not url_aceptacion:
                raise EntregaEmisorError(f"El RNC {rnc_emisor} no tiene URL de aprobación comercial en el directorio")
            url = url_aprobacion_comercial_emisor(url_aceptacion)
            respuesta = await client.post(url, files={"xml": (nombre_archivo, xml, "text/xml")})
    except httpx.HTTPError as exc:
        raise EntregaEmisorError(f"No se pudo contactar al proveedor: {exc}") from exc
    if respuesta.status_code != 200:
        raise EntregaEmisorError(f"El proveedor respondió {respuesta.status_code}: {respuesta.text[:300]}")
    return url
