"""Errores de dominio del servicio de inventario. main.py los traduce a
códigos HTTP (404 no encontrado, 409 estado/stock, 422 datos inválidos)."""
from __future__ import annotations

from decimal import Decimal


class ProductoNoEncontradoError(Exception):
    pass


class ProveedorNoEncontradoError(Exception):
    pass


class CompraNoEncontradaError(Exception):
    pass


class CompraEstadoInvalidoError(Exception):
    """La compra ya está confirmada (o en otro estado) y no admite la operación pedida."""


class AlmacenNoEncontradoError(Exception):
    pass


class LoteNoEncontradoError(Exception):
    pass


class AjusteNoEncontradoError(Exception):
    pass


class TransferenciaNoEncontradaError(Exception):
    pass


class OperacionInvalidaError(Exception):
    """La operación no aplica en el estado actual (ajuste ya aplicado,
    almacén inactivo, producto que es un servicio, lote de otro
    producto...). Se responde 409 con el mensaje."""


class StockInsuficienteError(Exception):
    """Una salida (ajuste, transferencia) dejaría la existencia negativa en
    un almacén que no lo permite."""

    def __init__(
        self,
        *,
        producto_id: str,
        nombre: str,
        almacen_id: str,
        solicitado: Decimal,
        disponible: Decimal,
        lote_id: str | None = None,
    ):
        self.producto_id = producto_id
        self.nombre = nombre
        self.almacen_id = almacen_id
        self.lote_id = lote_id
        self.solicitado = solicitado
        self.disponible = disponible
        donde = f"del lote {lote_id}" if lote_id else "en el almacén"
        super().__init__(
            f"Stock insuficiente de '{nombre}' {donde}: solicitado {solicitado}, disponible {disponible}"
        )

    def como_dict(self) -> dict:
        return {
            "producto_id": self.producto_id,
            "nombre": self.nombre,
            "almacen_id": self.almacen_id,
            "lote_id": self.lote_id,
            "solicitado": str(self.solicitado),
            "disponible": str(self.disponible),
        }
