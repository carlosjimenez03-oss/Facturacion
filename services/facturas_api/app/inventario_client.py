"""Cliente HTTP hacia `inventario_api` para descontar stock al vender.

Dos llamadas, con dos políticas de fallo distintas y a propósito:

- `validar_disponibilidad` se llama ANTES de enviar el e-CF a la DGII. Si no
  hay stock suficiente (o si inventario_api no responde y por lo tanto no se
  puede confirmar que sí hay stock), la operación se bloquea -- según lo
  pedido: no facturar mercancía que no está disponible.
- `registrar_salida_por_factura` se llama DESPUÉS de un envío exitoso a la
  DGII (documento fiscal ya irreversible). Un fallo aquí NUNCA debe
  propagarse como error de la respuesta -- ver `app.main.enviar_factura`,
  que solo registra el fallo (`inventario_sincronizado`/`inventario_error`)
  para poder detectarlo y resolverlo manualmente.
"""
from __future__ import annotations

from typing import Any

import httpx


class InventarioApiError(Exception):
    """No se pudo contactar a inventario_api o respondió con un error inesperado."""


class StockInsuficienteError(Exception):
    def __init__(self, faltantes: list[dict[str, Any]]):
        self.faltantes = faltantes
        super().__init__(f"Stock insuficiente para {len(faltantes)} producto(s)")


def _items_venta(items_inventario: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"producto_id": item["producto_id"], "cantidad": item["cantidad"]} for item in items_inventario]


async def validar_disponibilidad(base_url: str, items_inventario: list[dict[str, Any]]) -> None:
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            respuesta = await client.post(
                f"{base_url}/movimientos/validar-disponibilidad",
                json={"items": _items_venta(items_inventario)},
            )
    except httpx.HTTPError as exc:
        raise InventarioApiError(f"No se pudo contactar inventario_api: {exc}") from exc

    if respuesta.status_code >= 400:
        raise InventarioApiError(f"inventario_api respondió {respuesta.status_code}: {respuesta.text}")

    cuerpo = respuesta.json()
    if not cuerpo.get("disponible", False):
        raise StockInsuficienteError(cuerpo.get("faltantes", []))


async def registrar_salida_por_factura(
    base_url: str, factura_id: str, items_inventario: list[dict[str, Any]]
) -> None:
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            respuesta = await client.post(
                f"{base_url}/movimientos/salida-por-factura",
                json={"factura_id": factura_id, "items": _items_venta(items_inventario)},
            )
    except httpx.HTTPError as exc:
        raise InventarioApiError(f"No se pudo contactar inventario_api: {exc}") from exc

    if respuesta.status_code >= 400:
        raise InventarioApiError(f"inventario_api respondió {respuesta.status_code}: {respuesta.text}")
