"""Esquemas de la API de inventario (catálogo, proveedores, compras, kardex).

`ItemVenta`/`ValidarDisponibilidadRequest`/`SalidaPorFacturaRequest` son el
contrato interno que usa `facturas_api` (ver
`services/facturas_api/app/inventario_client.py`) para descontar stock al
enviar una factura -- no se exponen en el frontend directamente.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field


class ProductoCrear(BaseModel):
    sku: str
    nombre: str
    descripcion: str | None = None
    unidad_medida: str | None = None
    precio_venta: Decimal = Decimal("0")
    stock_inicial: Decimal = Decimal("0")


class ProductoActualizar(BaseModel):
    sku: str
    nombre: str
    descripcion: str | None = None
    unidad_medida: str | None = None
    precio_venta: Decimal
    activo: bool = True


class Producto(BaseModel):
    id: UUID
    sku: str
    nombre: str
    descripcion: str | None = None
    unidad_medida: str | None = None
    precio_venta: Decimal
    costo_promedio: Decimal
    stock_actual: Decimal
    activo: bool
    creado_en: datetime
    actualizado_en: datetime


class ProveedorCrear(BaseModel):
    nombre: str
    rnc: str | None = None
    telefono: str | None = None
    correo: str | None = None
    direccion: str | None = None


class ProveedorActualizar(ProveedorCrear):
    activo: bool = True


class Proveedor(BaseModel):
    id: UUID
    nombre: str
    rnc: str | None = None
    telefono: str | None = None
    correo: str | None = None
    direccion: str | None = None
    activo: bool
    creado_en: datetime
    actualizado_en: datetime


class CompraItemCrear(BaseModel):
    producto_id: UUID
    cantidad: Decimal = Field(..., gt=0)
    costo_unitario: Decimal = Field(..., ge=0)


class CompraCrear(BaseModel):
    proveedor_id: UUID | None = None
    numero_documento: str | None = None
    notas: str | None = None
    items: list[CompraItemCrear] = Field(default_factory=list)


class CompraItem(BaseModel):
    id: UUID
    producto_id: UUID
    cantidad: Decimal
    costo_unitario: Decimal


class Compra(BaseModel):
    id: UUID
    proveedor_id: UUID | None = None
    numero_documento: str | None = None
    estado: str
    notas: str | None = None
    creado_en: datetime
    actualizado_en: datetime
    confirmada_en: datetime | None = None
    items: list[CompraItem] = Field(default_factory=list)


class Movimiento(BaseModel):
    id: UUID
    producto_id: UUID
    tipo: str
    cantidad: Decimal
    costo_unitario: Decimal | None = None
    motivo: str | None = None
    referencia_tipo: str | None = None
    referencia_id: str | None = None
    creado_en: datetime


class ItemVenta(BaseModel):
    producto_id: UUID
    cantidad: Decimal = Field(..., gt=0)


class ValidarDisponibilidadRequest(BaseModel):
    items: list[ItemVenta]


class Faltante(BaseModel):
    producto_id: UUID
    nombre: str
    solicitado: Decimal
    disponible: Decimal


class ValidarDisponibilidadResponse(BaseModel):
    disponible: bool
    faltantes: list[Faltante] = Field(default_factory=list)


class SalidaPorFacturaRequest(BaseModel):
    factura_id: str
    items: list[ItemVenta]


class SalidaPorFacturaResponse(BaseModel):
    ok: bool
    movimientos: list[Movimiento] = Field(default_factory=list)
