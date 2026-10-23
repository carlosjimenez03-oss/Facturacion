"""Esquemas de la API del ERP (contactos, cotizaciones, cobros, cuentas por
cobrar y resumen de documentos de venta).

El contrato con el frontend vive en `frontend/src/lib/erpApi.ts`: los
nombres de campo y tipos de aquí deben coincidir 1:1 con esa interfaz.
Montos viajan como string decimal con 2 decimales ("1250.00"), fechas como
"YYYY-MM-DD" y marcas de tiempo como ISO 8601.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer


def _dos_decimales(valor: Decimal) -> str:
    return str(Decimal(valor).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


# Monto de dinero: en JSON siempre sale con 2 decimales ("0.00", no "0").
Monto = Annotated[Decimal, PlainSerializer(_dos_decimales, return_type=str, when_used="json")]

TipoContacto = Literal["cliente", "proveedor", "ambos"]
TipoIdentificacion = Literal["rnc", "cedula", "pasaporte"]
EstadoCotizacion = Literal["abierta", "aceptada", "rechazada", "vencida", "facturada"]
TasaItbis = Literal["18", "16", "0", "exento"]
EstadoCuentaCobrar = Literal["pendiente", "vencida", "cobrada"]
MetodoCobro = Literal["efectivo", "transferencia", "tarjeta", "cheque"]


class _Entrada(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)


# --- Clientes / contactos ---------------------------------------------------------

class ClientePayload(_Entrada):
    tipo: TipoContacto
    tipo_identificacion: TipoIdentificacion | None = None
    rnc: str | None = None
    nombre: str = Field(..., min_length=1)
    correo: str | None = None
    telefono: str | None = None
    direccion: str | None = None
    condicion_pago_dias: int = Field(0, ge=0)
    limite_credito: Decimal | None = Field(None, ge=0)
    notas: str | None = None
    activo: bool = True


class Cliente(BaseModel):
    id: UUID
    tipo: TipoContacto
    tipo_identificacion: TipoIdentificacion | None = None
    rnc: str | None = None
    nombre: str
    correo: str | None = None
    telefono: str | None = None
    direccion: str | None = None
    condicion_pago_dias: int
    limite_credito: Monto | None = None
    notas: str | None = None
    activo: bool
    saldo: Monto
    creado_en: datetime
    actualizado_en: datetime


# --- Cotizaciones ---------------------------------------------------------

class CotizacionItemPayload(_Entrada):
    producto_id: UUID | None = None
    descripcion: str = Field(..., min_length=1)
    cantidad: Decimal = Field(..., gt=0)
    precio_unitario: Decimal = Field(..., ge=0)
    descuento_pct: Decimal = Field(Decimal("0"), ge=0, le=100)
    itbis: TasaItbis


class CotizacionPayload(_Entrada):
    cliente_id: UUID | None = None
    cliente_nombre: str | None = None
    cliente_rnc: str | None = None
    fecha: date | None = None
    vence: date | None = None
    items: list[CotizacionItemPayload] = Field(..., min_length=1)
    notas: str | None = None


class CotizacionItem(BaseModel):
    producto_id: UUID | None = None
    descripcion: str
    cantidad: Decimal
    precio_unitario: Decimal
    descuento_pct: Decimal
    itbis: TasaItbis
    importe: Monto


class CotizacionResumen(BaseModel):
    id: UUID
    numero: str
    cliente_id: UUID | None = None
    cliente_nombre: str
    cliente_rnc: str | None = None
    fecha: date
    vence: date
    total: Monto
    estado: EstadoCotizacion
    factura_id: UUID | None = None
    creado_en: datetime


class Cotizacion(CotizacionResumen):
    items: list[CotizacionItem] = Field(default_factory=list)
    notas: str | None = None
    subtotal: Monto
    descuentos: Monto
    itbis_total: Monto
    actualizado_en: datetime


class CambioEstadoCotizacion(BaseModel):
    estado: Literal["abierta", "aceptada", "rechazada"]


class MarcarFacturada(BaseModel):
    factura_id: UUID


# --- Cuentas por cobrar y cobros ---------------------------------------------------------

class CuentaPorCobrar(BaseModel):
    factura_id: UUID
    encf: str | None = None
    tipo_ecf: int
    cliente_rnc: str | None = None
    cliente_nombre: str
    fecha_emision: date
    vence: date
    total: Monto
    cobrado: Monto
    pendiente: Monto
    estado: EstadoCuentaCobrar
    dias_vencida: int


class CobroPayload(_Entrada):
    factura_id: UUID
    fecha: date | None = None
    # Sin `gt=0` a propósito: repository.registrar_cobro valida el monto
    # (> 0 y <= pendiente) y responde un 422 con un mensaje claro.
    monto: Decimal
    metodo: MetodoCobro
    cuenta_destino: str | None = None
    referencia: str | None = None
    notas: str | None = None


class Cobro(BaseModel):
    id: UUID
    factura_id: UUID
    encf: str | None = None
    cliente_nombre: str
    fecha: date
    monto: Monto
    metodo: MetodoCobro
    cuenta_destino: str | None = None
    referencia: str | None = None
    notas: str | None = None
    anulado: bool
    creado_en: datetime


# --- Resumen de documentos de venta (lee facturacion.facturas) ---------------------------------------------------------

class DocumentoVenta(BaseModel):
    id: UUID
    tipo_ecf: int
    encf: str | None = None
    estado: str
    cliente_rnc: str | None = None
    cliente_nombre: str | None = None
    fecha_emision: date | None = None
    total: Monto
    encf_afectado: str | None = None
    codigo_modificacion: str | None = None
    dgii_codigo: str | None = None
    error: str | None = None
    creado_en: datetime
