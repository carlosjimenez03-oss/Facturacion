"""Esquemas del Formato 606 -- ver docs/dgii/FORMATO_606.md para el
significado de cada campo (numerados igual que en el instructivo oficial).
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field


class Registro606(BaseModel):
    id: UUID
    periodo: str
    origen: str
    referencia_tipo: str | None = None
    referencia_id: str | None = None

    rnc_cedula: str
    tipo_id: int
    tipo_bienes_servicios: int | None = None
    ncf: str
    ncf_modificado: str | None = None
    fecha_comprobante: str
    fecha_pago: str | None = None

    monto_servicios: Decimal
    monto_bienes: Decimal
    monto_facturado: Decimal  # calculado: monto_servicios + monto_bienes
    itbis_facturado: Decimal
    itbis_retenido: Decimal
    itbis_proporcionalidad: Decimal
    itbis_costo: Decimal
    itbis_adelantar: Decimal  # calculado: itbis_facturado - itbis_costo
    itbis_percibido: Decimal
    tipo_retencion_isr: int | None = None
    monto_retencion_renta: Decimal
    isr_percibido: Decimal
    impuesto_selectivo_consumo: Decimal
    otros_impuestos: Decimal
    monto_propina_legal: Decimal
    forma_pago: int | None = None

    creado_en: datetime
    actualizado_en: datetime


class RegistroActualizar(BaseModel):
    """Los campos que ningún e-CF trae -- lo único que el usuario clasifica
    a mano sobre un renglón auto-poblado (ver docs/dgii/FORMATO_606.md,
    "Campos que solo el usuario puede completar")."""

    tipo_bienes_servicios: int | None = None
    fecha_pago: str | None = None
    itbis_retenido: Decimal = Decimal("0")
    itbis_proporcionalidad: Decimal = Decimal("0")
    itbis_costo: Decimal = Decimal("0")
    itbis_percibido: Decimal = Decimal("0")
    tipo_retencion_isr: int | None = None
    monto_retencion_renta: Decimal = Decimal("0")
    isr_percibido: Decimal = Decimal("0")
    impuesto_selectivo_consumo: Decimal = Decimal("0")
    otros_impuestos: Decimal = Decimal("0")
    monto_propina_legal: Decimal = Decimal("0")
    forma_pago: int | None = None


class RegistroCrearManual(BaseModel):
    """Para una compra que no pasó por receptor_recepcion ni por facturas_api
    (tipo 41/43/47) -- el usuario la agrega a mano."""

    rnc_cedula: str
    tipo_bienes_servicios: int | None = None
    ncf: str
    ncf_modificado: str | None = None
    fecha_comprobante: str
    fecha_pago: str | None = None
    monto_servicios: Decimal = Decimal("0")
    monto_bienes: Decimal = Decimal("0")
    itbis_facturado: Decimal = Decimal("0")
    itbis_retenido: Decimal = Decimal("0")
    itbis_proporcionalidad: Decimal = Decimal("0")
    itbis_costo: Decimal = Decimal("0")
    itbis_percibido: Decimal = Decimal("0")
    tipo_retencion_isr: int | None = None
    monto_retencion_renta: Decimal = Decimal("0")
    isr_percibido: Decimal = Decimal("0")
    impuesto_selectivo_consumo: Decimal = Decimal("0")
    otros_impuestos: Decimal = Decimal("0")
    monto_propina_legal: Decimal = Decimal("0")
    forma_pago: int | None = None


class ErrorValidacion(BaseModel):
    registro_id: UUID
    ncf: str
    mensaje: str


class ValidacionResponse(BaseModel):
    valido: bool
    total_registros: int
    errores: list[ErrorValidacion] = Field(default_factory=list)
