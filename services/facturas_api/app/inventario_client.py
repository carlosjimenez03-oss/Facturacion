"""Cliente HTTP hacia `inventario_api` para descontar stock al vender.

Las llamadas, con políticas de fallo distintas y a propósito:

- `validar_disponibilidad` se llama ANTES de enviar el e-CF a la DGII. Si no
  hay stock suficiente (o si inventario_api no responde y por lo tanto no se
  puede confirmar que sí hay stock), la operación se bloquea -- según lo
  pedido: no facturar mercancía que no está disponible.
- `registrar_salida_por_factura` se llama DESPUÉS de un envío exitoso a la
  DGII (documento fiscal ya irreversible). Un fallo aquí NUNCA debe
  propagarse como error de la respuesta -- ver `app.main.enviar_factura`,
  que solo registra el fallo (`inventario_sincronizado`/`inventario_error`)
  para poder detectarlo y resolverlo manualmente. También se llama al
  emitir la factura en contingencia (la venta ya se entregó al comprador).
- `registrar_reverso_por_factura` devuelve la mercancía de una factura que
  la DGII rechazó (idempotente; ver `app.envio.revertir_inventario`).

Todas llevan la cabecera de llamada interna (`X-Kontara-Interno`, ver
libs/ecf_common/auth/sesion_supabase.py) para que funcionen aunque
inventario-api tenga REQUIRE_AUTH=true.
"""
from __future__ import annotations

from typing import Any

import httpx

from libs.ecf_common.auth.sesion_supabase import cabeceras_internas

from .config import get_settings


def _cabeceras() -> dict[str, str]:
    return cabeceras_internas(get_settings().kontara_token_interno)


class InventarioApiError(Exception):
    """No se pudo contactar a inventario_api o respondió con un error inesperado."""


class StockInsuficienteError(Exception):
    def __init__(self, faltantes: list[dict[str, Any]]):
        self.faltantes = faltantes
        super().__init__(f"Stock insuficiente para {len(faltantes)} producto(s)")


def _items_venta(items_inventario: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Cada línea va con su NumeroLinea y, si la factura los indica, el
    almacén y el lote de donde sale (inventario_api usa el principal / FEFO
    cuando faltan)."""
    items = []
    for item in items_inventario:
        venta = {"producto_id": item["producto_id"], "cantidad": item["cantidad"]}
        for campo in ("numero_linea", "almacen_id", "lote_id"):
            if item.get(campo) is not None:
                venta[campo] = str(item[campo])
        items.append(venta)
    return items


async def validar_disponibilidad(base_url: str, items_inventario: list[dict[str, Any]]) -> None:
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            respuesta = await client.post(
                f"{base_url}/movimientos/validar-disponibilidad",
                json={"items": _items_venta(items_inventario)},
                headers=_cabeceras(),
            )
    except httpx.HTTPError as exc:
        raise InventarioApiError(f"No se pudo contactar inventario_api: {exc}") from exc

    if respuesta.status_code >= 400:
        raise InventarioApiError(f"inventario_api respondió {respuesta.status_code}: {respuesta.text}")

    cuerpo = respuesta.json()
    if not cuerpo.get("disponible", False):
        raise StockInsuficienteError(cuerpo.get("faltantes", []))


async def registrar_salida_por_factura(
    base_url: str, factura_id: str, items_inventario: list[dict[str, Any]], *, documento: str | None = None
) -> None:
    """`documento` (el e-NCF) es lo que el Kardex muestra como documento."""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            respuesta = await client.post(
                f"{base_url}/movimientos/salida-por-factura",
                json={"factura_id": factura_id, "items": _items_venta(items_inventario), "documento": documento},
                headers=_cabeceras(),
            )
    except httpx.HTTPError as exc:
        raise InventarioApiError(f"No se pudo contactar inventario_api: {exc}") from exc

    if respuesta.status_code >= 400:
        raise InventarioApiError(f"inventario_api respondió {respuesta.status_code}: {respuesta.text}")


async def registrar_reverso_por_factura(base_url: str, factura_id: str, *, documento: str | None = None) -> None:
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            respuesta = await client.post(
                f"{base_url}/movimientos/reverso-por-factura",
                json={"factura_id": factura_id, "documento": documento},
                headers=_cabeceras(),
            )
    except httpx.HTTPError as exc:
        raise InventarioApiError(f"No se pudo contactar inventario_api: {exc}") from exc

    if respuesta.status_code >= 400:
        raise InventarioApiError(f"inventario_api respondió {respuesta.status_code}: {respuesta.text}")
