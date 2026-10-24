"""Esquemas de los pagos con tarjeta en línea (fase 7). Contrato con el
frontend: `frontend/src/lib/pagosApi.ts` (mismos nombres y tipos). Montos
como string con 2 decimales, marcas de tiempo ISO 8601."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from .schemas import Monto, _Entrada

ClaveProveedor = Literal["azul", "simulado"]
Ambiente = Literal["pruebas", "produccion"]
EstadoPago = Literal["pendiente", "aprobado", "declinado", "cancelado", "expirado"]


class ProveedorDisponible(BaseModel):
    clave: ClaveProveedor
    nombre: str


class ConfigPagos(BaseModel):
    """Lo que ve la pantalla de Configuración. El AuthKey NUNCA sale de aquí:
    solo si está configurada y de dónde (variable de entorno o guardada
    cifrada)."""

    activo: bool
    proveedor: ClaveProveedor
    ambiente: Ambiente
    merchant_id: str
    merchant_name: str
    merchant_type: str
    permitir_parcial: bool
    cuenta_bancaria_id: str | None = None
    auth_key_configurada: bool
    auth_key_origen: Literal["variable", "guardada"] | None = None
    proveedores: list[ProveedorDisponible]
    listo: bool
    faltantes: list[str]
    url_retorno: str | None = None
    cifrado_disponible: bool
    puede_editar: bool
    produccion: bool


class ConfigPagosPayload(_Entrada):
    activo: bool = False
    proveedor: ClaveProveedor = "azul"
    ambiente: Ambiente = "pruebas"
    merchant_id: str = Field("", max_length=20)
    merchant_name: str = Field("", max_length=60)
    merchant_type: str = Field("", max_length=40)
    permitir_parcial: bool = False
    cuenta_bancaria_id: UUID | None = None
    # Solo de escritura: None/"" conserva la guardada.
    auth_key: str | None = Field(None, max_length=500)
    borrar_auth_key: bool = False


class PagoEnLineaPortal(BaseModel):
    """Lo que necesita la página pública para mostrar «Pagar con tarjeta»."""

    proveedor: ClaveProveedor
    nombre: str
    pruebas: bool
    permitir_parcial: bool


class IniciarPagoPayload(_Entrada):
    factura_id: UUID
    # None = todo el saldo pendiente.
    monto: Decimal | None = Field(None, gt=0, max_digits=14, decimal_places=2)


class PagoIniciado(BaseModel):
    """El formulario que el navegador publica en la página del banco."""

    referencia: str
    accion: str
    metodo: Literal["POST"] = "POST"
    campos: dict[str, str]
    monto: Monto
    itbis: Monto
    encf: str | None = None
    expira_en: datetime
    proveedor: ClaveProveedor


class PagoPublico(BaseModel):
    """Resultado para el cliente (página /p/pago/<referencia>)."""

    referencia: str
    estado: EstadoPago
    monto: Monto
    encf: str | None = None
    empresa: str
    proveedor: str
    pruebas: bool
    codigo_autorizacion: str | None = None
    mensaje: str | None = None
    cobro_registrado: bool
    requiere_revision: bool
    respondido_en: datetime | None = None
    creado_en: datetime


class PagoEnLinea(BaseModel):
    id: str
    referencia: str
    orden: str
    proveedor: ClaveProveedor
    ambiente: Ambiente
    factura_id: str
    encf: str | None = None
    cliente_rnc: str | None = None
    cliente_nombre: str | None = None
    monto: Monto
    itbis: Monto
    estado: EstadoPago
    expira_en: datetime
    codigo_autorizacion: str | None = None
    rrn: str | None = None
    orden_proveedor: str | None = None
    iso_code: str | None = None
    respuesta: str | None = None
    error_descripcion: str | None = None
    cobro_id: str | None = None
    cobro_anulado: bool | None = None
    incidencia: str | None = None
    respondido_en: datetime | None = None
    creado_en: datetime


class EventoPago(BaseModel):
    tipo: str
    detalle: dict
    creado_en: datetime
