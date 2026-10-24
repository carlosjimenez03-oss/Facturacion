"""Esquemas de la API de inventario (catálogo, proveedores, compras, kardex,
almacenes, lotes, ajustes, transferencias y valuación).

`ItemVenta`/`ValidarDisponibilidadRequest`/`SalidaPorFacturaRequest` son el
contrato interno que usa `facturas_api` (ver
`services/facturas_api/app/inventario_client.py`) para descontar stock al
enviar una factura -- no se exponen en el frontend directamente.

El contrato con el frontend es `frontend/src/lib/inventarioApi.ts` (ver
docs/INVENTARIO.md). Cantidades, costos y montos viajan como string
decimal; fechas como YYYY-MM-DD y marcas de tiempo en ISO 8601.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

TasaItbis = Literal["18", "16", "0", "exento"]
TipoProducto = Literal["bien", "servicio"]
EstadoStock = Literal["disponible", "bajo_minimo", "sin_existencia", "no_aplica"]
EstadoLote = Literal["vigente", "por_vencer", "vencido", "bloqueado"]
MotivoAjuste = Literal["conteo_fisico", "merma", "dano", "vencimiento", "otro"]
TipoMovimiento = Literal["entrada", "salida"]


class ProductoCrear(BaseModel):
    sku: str
    nombre: str
    descripcion: str | None = None
    unidad_medida: str | None = None
    precio_venta: Decimal = Decimal("0")
    stock_inicial: Decimal = Field(Decimal("0"), ge=0)
    tipo: TipoProducto = "bien"
    tasa_itbis: TasaItbis = "18"
    codigo_barras: str | None = None
    categoria: str | None = None
    stock_minimo: Decimal = Field(Decimal("0"), ge=0)
    maneja_lotes: bool = False
    # Costo del stock inicial (fija el costo promedio) y almacén donde entra
    # (None = principal). Solo aplican si stock_inicial > 0.
    costo_unitario_inicial: Decimal | None = Field(None, ge=0)
    almacen_id: UUID | None = None


class ProductoActualizar(BaseModel):
    """Los campos de la Fase 4 (tipo...maneja_lotes) son opcionales: si no
    vienen en el JSON se conservan (main.py usa `model_fields_set`), para
    que un cliente de antes no los borre."""

    sku: str
    nombre: str
    descripcion: str | None = None
    unidad_medida: str | None = None
    precio_venta: Decimal
    activo: bool = True
    tipo: TipoProducto | None = None
    tasa_itbis: TasaItbis | None = None
    codigo_barras: str | None = None
    categoria: str | None = None
    stock_minimo: Decimal | None = Field(None, ge=0)
    maneja_lotes: bool | None = None


class Producto(BaseModel):
    id: UUID
    sku: str
    nombre: str
    descripcion: str | None = None
    unidad_medida: str | None = None
    precio_venta: Decimal
    costo_promedio: Decimal
    stock_actual: Decimal  # total de todos los almacenes
    activo: bool
    creado_en: datetime
    actualizado_en: datetime
    tipo: TipoProducto = "bien"
    tasa_itbis: TasaItbis = "18"
    codigo_barras: str | None = None
    categoria: str | None = None
    stock_minimo: Decimal = Decimal("0")
    maneja_lotes: bool = False
    estado_stock: EstadoStock = "disponible"


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
    # Lote de la línea: al confirmar se crea (producto, número) si no existe.
    lote_numero: str | None = None
    lote_vencimiento: date | None = None


class CompraCrear(BaseModel):
    proveedor_id: UUID | None = None
    numero_documento: str | None = None
    notas: str | None = None
    almacen_id: UUID | None = None  # almacén que recibe; None = principal
    items: list[CompraItemCrear] = Field(default_factory=list)


class CompraItem(BaseModel):
    id: UUID
    producto_id: UUID
    cantidad: Decimal
    costo_unitario: Decimal
    lote_numero: str | None = None
    lote_vencimiento: date | None = None


class Compra(BaseModel):
    id: UUID
    proveedor_id: UUID | None = None
    numero_documento: str | None = None
    estado: str
    notas: str | None = None
    almacen_id: UUID | None = None
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
    almacen_id: UUID | None = None
    lote_id: UUID | None = None
    referencia_linea: str | None = None
    documento: str | None = None


class ItemVenta(BaseModel):
    producto_id: UUID
    cantidad: Decimal = Field(..., gt=0)
    # Fase 4 (opcionales): NumeroLinea de la factura, almacén de donde sale
    # (None = principal) y lote (None = FEFO si el producto maneja lotes).
    numero_linea: str | None = None
    almacen_id: UUID | None = None
    lote_id: UUID | None = None


class ValidarDisponibilidadRequest(BaseModel):
    items: list[ItemVenta]


class Faltante(BaseModel):
    producto_id: UUID
    nombre: str
    solicitado: Decimal
    disponible: Decimal
    almacen_id: UUID | None = None
    lote_id: UUID | None = None
    motivo: Literal["stock", "lote_vencido", "lote_bloqueado", "almacen_inactivo"] = "stock"


class ValidarDisponibilidadResponse(BaseModel):
    disponible: bool
    faltantes: list[Faltante] = Field(default_factory=list)


class SalidaPorFacturaRequest(BaseModel):
    factura_id: str
    items: list[ItemVenta]
    documento: str | None = None  # e-NCF, para la columna Documento del Kardex


class SalidaPorFacturaResponse(BaseModel):
    ok: bool
    movimientos: list[Movimiento] = Field(default_factory=list)


# --- Almacenes ---------------------------------------------------------

class AlmacenCrear(BaseModel):
    nombre: str = Field(..., min_length=1)
    direccion: str | None = None
    permite_negativo: bool = False


class AlmacenActualizar(AlmacenCrear):
    activo: bool = True
    # True = pasa a ser el principal (el anterior deja de serlo). None = sin cambio.
    es_principal: bool | None = None


class Almacen(BaseModel):
    id: UUID
    nombre: str
    direccion: str | None = None
    es_principal: bool
    permite_negativo: bool
    activo: bool
    creado_en: datetime
    actualizado_en: datetime
    productos: int  # productos con existencia distinta de cero
    cantidad: Decimal
    valor: Decimal  # existencia (no negativa) × costo promedio


class ExistenciaAlmacen(BaseModel):
    producto_id: UUID
    sku: str
    nombre: str
    unidad_medida: str | None = None
    categoria: str | None = None
    stock_minimo: Decimal
    cantidad: Decimal
    costo_promedio: Decimal
    valor: Decimal


class ExistenciaLoteResumen(BaseModel):
    lote_id: UUID
    numero: str
    fecha_vencimiento: date | None = None
    cantidad: Decimal
    estado: EstadoLote


class ExistenciaProductoAlmacen(BaseModel):
    almacen_id: UUID
    almacen_nombre: str
    activo: bool
    cantidad: Decimal
    sin_lote: Decimal
    valor: Decimal
    lotes: list[ExistenciaLoteResumen] = Field(default_factory=list)


class ExistenciasProducto(BaseModel):
    producto_id: UUID
    stock_actual: Decimal
    costo_promedio: Decimal
    almacenes: list[ExistenciaProductoAlmacen] = Field(default_factory=list)


class TransferenciaItemCrear(BaseModel):
    producto_id: UUID
    cantidad: Decimal = Field(..., gt=0)
    lote_id: UUID | None = None


class TransferenciaCrear(BaseModel):
    almacen_origen_id: UUID
    almacen_destino_id: UUID
    notas: str | None = None
    items: list[TransferenciaItemCrear] = Field(..., min_length=1)


class TransferenciaItem(BaseModel):
    id: UUID
    producto_id: UUID
    producto_sku: str
    producto_nombre: str
    cantidad: Decimal
    lote_id: UUID | None = None


class Transferencia(BaseModel):
    id: UUID
    numero: str
    almacen_origen_id: UUID
    almacen_destino_id: UUID
    notas: str | None = None
    creado_en: datetime
    items: list[TransferenciaItem] = Field(default_factory=list)


# --- Lotes ---------------------------------------------------------

class LoteCrear(BaseModel):
    producto_id: UUID
    numero: str = Field(..., min_length=1)
    fecha_vencimiento: date | None = None
    notas: str | None = None
    # Entrada manual opcional al registrar el lote.
    cantidad_inicial: Decimal = Field(Decimal("0"), ge=0)
    almacen_id: UUID | None = None
    costo_unitario: Decimal | None = Field(None, ge=0)


class LoteActualizar(BaseModel):
    numero: str = Field(..., min_length=1)
    fecha_vencimiento: date | None = None
    bloqueado: bool = False
    notas: str | None = None


class LoteExistencia(BaseModel):
    almacen_id: UUID
    almacen_nombre: str
    cantidad: Decimal


class Lote(BaseModel):
    id: UUID
    producto_id: UUID
    producto_sku: str
    producto_nombre: str
    numero: str
    fecha_vencimiento: date | None = None
    bloqueado: bool
    notas: str | None = None
    cantidad: Decimal  # total en todos los almacenes
    estado: EstadoLote
    dias_para_vencer: int | None = None  # negativo = vencido hace N días
    existencias: list[LoteExistencia] = Field(default_factory=list)
    creado_en: datetime
    actualizado_en: datetime


class AlertasLotes(BaseModel):
    dias: int
    por_vencer: list[Lote] = Field(default_factory=list)
    vencidos: list[Lote] = Field(default_factory=list)


class TomaFefo(BaseModel):
    lote_id: UUID
    numero: str
    fecha_vencimiento: date | None = None
    disponible: Decimal
    cantidad: Decimal


class SugerenciaFefo(BaseModel):
    producto_id: UUID
    almacen_id: UUID
    cantidad: Decimal
    tomas: list[TomaFefo] = Field(default_factory=list)
    sin_lote: Decimal  # lo que saldría de la mercancía sin lote
    faltante: Decimal  # lo que no alcanza


# --- Ajustes ---------------------------------------------------------

class AjusteItemCrear(BaseModel):
    producto_id: UUID
    tipo: TipoMovimiento
    cantidad: Decimal = Field(..., gt=0)
    costo_unitario: Decimal | None = Field(None, ge=0)  # solo entradas; None = costo promedio
    lote_id: UUID | None = None


class AjusteCrear(BaseModel):
    almacen_id: UUID | None = None  # None = principal
    motivo: MotivoAjuste
    fecha: date | None = None  # None = hoy
    notas: str | None = None
    items: list[AjusteItemCrear] = Field(..., min_length=1)


class AjusteItem(BaseModel):
    id: UUID
    producto_id: UUID
    producto_sku: str
    producto_nombre: str
    tipo: TipoMovimiento
    cantidad: Decimal
    costo_unitario: Decimal | None = None
    lote_id: UUID | None = None
    lote_numero: str | None = None


class Ajuste(BaseModel):
    id: UUID
    numero: str
    fecha: date
    almacen_id: UUID
    almacen_nombre: str
    motivo: MotivoAjuste
    notas: str | None = None
    estado: Literal["borrador", "aplicado", "anulado"]
    creado_en: datetime
    actualizado_en: datetime
    aplicado_en: datetime | None = None
    anulado_en: datetime | None = None
    items: list[AjusteItem] = Field(default_factory=list)


# --- Kardex y valuación ---------------------------------------------------------

class KardexSaldo(BaseModel):
    cantidad: Decimal
    costo_promedio: Decimal
    valor: Decimal


class KardexFila(BaseModel):
    movimiento_id: UUID
    fecha: datetime
    tipo: TipoMovimiento
    concepto: str
    documento: str | None = None
    motivo: str | None = None
    referencia_tipo: str | None = None
    referencia_id: str | None = None
    almacen_id: UUID | None = None
    almacen_nombre: str | None = None
    lote_id: UUID | None = None
    lote_numero: str | None = None
    cantidad: Decimal  # con signo: + entrada, - salida
    costo_unitario: Decimal
    costo_total: Decimal  # con signo
    saldo_cantidad: Decimal
    costo_promedio: Decimal
    saldo_valor: Decimal


class Kardex(BaseModel):
    producto_id: UUID
    producto_sku: str
    producto_nombre: str
    unidad_medida: str | None = None
    stock_minimo: Decimal
    almacen_id: UUID | None = None
    almacen_nombre: str | None = None
    desde: date | None = None
    hasta: date | None = None
    metodo: Literal["promedio", "peps"]
    saldo_inicial: KardexSaldo
    saldo_final: KardexSaldo
    entradas: Decimal  # cantidades del período
    salidas: Decimal
    filas: list[KardexFila] = Field(default_factory=list)


class ValuacionAlmacen(BaseModel):
    almacen_id: UUID
    nombre: str
    activo: bool
    productos: int
    cantidad: Decimal
    valor: Decimal


class ValuacionCategoria(BaseModel):
    categoria: str | None = None  # None = sin categoría
    productos: int
    cantidad: Decimal
    valor: Decimal


class Valuacion(BaseModel):
    valor_total: Decimal
    productos: int  # bienes activos
    con_existencia: int
    sin_existencia: int
    bajo_minimo: int
    por_almacen: list[ValuacionAlmacen] = Field(default_factory=list)
    por_categoria: list[ValuacionCategoria] = Field(default_factory=list)
