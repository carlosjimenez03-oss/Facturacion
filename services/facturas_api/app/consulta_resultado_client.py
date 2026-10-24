"""Cliente HTTP hacia `consulta_resultado` (services/consulta_resultado).

La DGII no responde el resultado de un e-CF en el mismo envío: `recepcion`
solo devuelve un trackId y la validación (estructura, firma, datos del
comprador, secuencia) ocurre de forma asíncrona. El resultado se obtiene
consultando ese trackId -- `consulta_resultado` ya implementa esa llamada
(token + GET /api/consultas/estado), así que este servicio solo le pregunta
por la red interna (GET /resultado/{track_id}) y traduce la respuesta al
estado de la factura (ver `app.estado_dgii`).
"""
from __future__ import annotations

from typing import Any

import httpx

from libs.ecf_common.empresa import cabeceras_empresa


class ConsultaResultadoError(Exception):
    """No se pudo contactar a consulta_resultado o respondió con un error inesperado."""


async def consultar_resultado(base_url: str, track_id: str, *, ambiente: str | None = None) -> dict[str, Any]:
    """`ambiente`: el de la factura (test|qa|production); sin él,
    consulta_resultado usa el ambiente activo."""
    try:
        async with httpx.AsyncClient(timeout=30.0, headers=cabeceras_empresa()) as client:
            respuesta = await client.get(
                f"{base_url}/resultado/{track_id}", params={"ambiente": ambiente} if ambiente else None
            )
    except httpx.HTTPError as exc:
        raise ConsultaResultadoError(f"No se pudo contactar consulta_resultado: {exc}") from exc

    if respuesta.status_code >= 400:
        raise ConsultaResultadoError(
            f"consulta_resultado respondió {respuesta.status_code}: {respuesta.text}"
        )

    try:
        cuerpo = respuesta.json()
    except ValueError as exc:
        raise ConsultaResultadoError(
            f"consulta_resultado devolvió una respuesta que no es JSON: {respuesta.text[:200]}"
        ) from exc
    if not isinstance(cuerpo, dict):
        raise ConsultaResultadoError("consulta_resultado devolvió una respuesta inesperada")
    return cuerpo
