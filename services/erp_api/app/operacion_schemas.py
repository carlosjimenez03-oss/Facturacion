"""Esquemas de la Fase 6 · Operación de erp-api: Mis tareas (`tareas.py`),
Centro de sincronización (`sincronizacion.py`) y Reportes (`reportes.py`).

El contrato con el frontend vive en `frontend/src/lib/operacionApi.ts`: los
nombres de campo y tipos de aquí deben coincidir 1:1 con esa interfaz. Mismas
convenciones que schemas.py: montos como string decimal con 2 decimales,
cantidades como string decimal, fechas "YYYY-MM-DD" y marcas de tiempo ISO 8601.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from .schemas import Monto, _Entrada

# --- Mis tareas ---------------------------------------------------------

PrioridadTarea = Literal["alta", "media", "baja"]
CategoriaTarea = Literal["dgii", "ecf", "compras", "cobros", "pagos", "ventas", "inventario", "gestion"]
EstadoTarea = Literal["pendiente", "pospuesta", "completada"]
OrigenTarea = Literal["manual", "automatica"]


class Tarea(BaseModel):
    # uuid de una tarea manual o clave estable de una automática
    # ('declaracion:it1:202609', 'factura_rechazada:<uuid>'...).
    id: str
    origen: OrigenTarea
    # 'manual', 'declaracion', 'factura_rechazada', 'factura_error',
    # 'dgii_en_proceso', 'ecf_por_aprobar', 'cxc_vencida', 'cxp_por_vencer',
    # 'cxp_vencida', 'cotizacion_por_vencer', 'lote_por_vencer', 'lote_vencido',
    # 'stock_bajo', 'inventario_sin_sincronizar', 'papel_sin_reemplazo'.
    tipo: str
    categoria: CategoriaTarea
    titulo: str
    descripcion: str | None = None
    prioridad: PrioridadTarea
    vence: date | None = None
    vencida: bool = False
    estado: EstadoTarea
    completada_en: datetime | None = None
    pospuesta_hasta: date | None = None
    # Ruta del frontend que resuelve la tarea ("/bandeja/<id>").
    enlace: str | None = None
    monto: Monto | None = None
    creado_en: datetime | None = None


class TareaPayload(_Entrada):
    titulo: str = Field(..., min_length=1, max_length=300)
    descripcion: str | None = Field(None, max_length=4000)
    vence: date | None = None
    prioridad: PrioridadTarea = "media"


class PosponerPayload(BaseModel):
    hasta: date


class ResumenTareas(BaseModel):
    """Contadores para la campana de la barra superior."""

    pendientes: int
    vencidas: int
    vencen_hoy: int
    alta: int
    por_categoria: dict[str, int]
    # Las 5 pendientes más urgentes (vencidas y prioridad alta primero).
    proximas: list[Tarea]


# --- Centro de sincronización ---------------------------------------------------------

CanalSincronizacion = Literal["dgii_envio", "dgii_resultado", "inventario", "aprobacion_comercial", "entrega_emisor"]
EstadoCanal = Literal["ok", "pendiente", "error"]
AccionSincronizacion = Literal[
    "actualizar_estado", "reenviar", "reintentar_aprobacion", "decidir_aprobacion", "reintentar_entrega"
]
ResultadoEvento = Literal["ok", "error", "rechazado", "en_proceso"]


class CanalResumen(BaseModel):
    canal: CanalSincronizacion
    titulo: str
    descripcion: str
    pendientes: int
    errores: int
    estado: EstadoCanal
    # Último movimiento conocido del canal (último envío, última consulta...).
    ultima_actividad: datetime | None = None
    detalle: str | None = None


class LoteContingencia(BaseModel):
    """Lote de envío de lo pendiente de una contingencia
    (facturacion.lotes_contingencia de facturas-api, p. ej. A-0001).
    `pendientes` = sin enviar todavía; 'con_rechazos' = completado con
    rechazos de la DGII."""

    id: str
    documentos: int
    aceptados: int
    rechazados: int
    pendientes: int
    estado: Literal["en_cola", "enviando", "completado", "interrumpido", "con_rechazos"]
    creado_en: datetime | None = None
    enviados: int = 0
    iniciado_en: datetime | None = None
    completado_en: datetime | None = None
    ultimo_error: str | None = None


class Contingencia(BaseModel):
    """Modo contingencia: el estado que guarda facturas-api
    (facturacion.contingencia, `fuente = 'facturas_api'`) o, si sus tablas no
    existen, el calculado de la auditoría (`fuente = 'calculada'`: los
    últimos envíos/consultas en facturacion.envios_dgii fallaron seguidos).
    Ver `sincronizacion_repository._contingencia`."""

    activa: bool
    desde: datetime | None = None
    errores_consecutivos: int
    ultimo_intento: datetime | None = None
    ultimo_error: str | None = None
    # facturas-api reintenta solo cada `sincronizar_estado_cada_segundos` (120 s por defecto).
    proximo_intento: datetime | None = None
    # e-CF emitidos en contingencia (estado 'contingencia'; en la fuente
    # calculada, 'error' sin trackId): se envían solos al reconectar.
    pendientes: int
    monto_pendiente: Monto
    facturas_papel: int  # registradas desde el inicio de la contingencia (o en el mes si no hay)
    lotes: list[LoteContingencia] = Field(default_factory=list)
    fuente: Literal["facturas_api", "calculada"] = "calculada"
    ultimo_exito: datetime | None = None
    # Pendiente más antigua y su plazo de envío (72 horas, Decreto 587-24).
    mas_antigua: datetime | None = None
    vence_en: datetime | None = None
    horas_limite: int = 72


class EnviosDia(BaseModel):
    fecha: date
    documentos: int  # e-CF enviados (módulo recepcion)
    aceptados: int
    rechazados: int
    errores: int  # envíos o consultas que fallaron
    origen: Literal["en_linea", "con_fallas"]
    resultado: Literal["completado", "con_rechazos", "con_errores", "sin_envios"]


class ResumenSincronizacion(BaseModel):
    estado: Literal["ok", "atencion", "error"]
    contingencia: Contingencia
    canales: list[CanalResumen]
    pendientes_total: int
    errores_total: int
    # e-CF en 'enviado' (sin resultado de la DGII): cuántos, por cuánto y desde cuándo.
    en_proceso_dgii: int
    monto_en_proceso: Monto
    en_proceso_desde: datetime | None = None
    ultimo_envio: datetime | None = None
    ultima_consulta_resultado: datetime | None = None
    # Auditoría de facturacion.envios_dgii de los últimos 7 días.
    eventos_7d: int
    errores_7d: int
    rechazos_7d: int


class ItemSincronizacion(BaseModel):
    id: str  # '<canal>:<referencia_id>'
    canal: CanalSincronizacion
    referencia_id: str  # facturacion.facturas.id o recepcion.ecf_recibidos.id
    documento: str | None = None  # e-NCF
    tipo_ecf: int | None = None
    contraparte: str | None = None
    fecha: datetime | None = None  # desde cuándo está pendiente
    monto: Monto | None = None
    estado: Literal["pendiente", "en_curso", "error"]
    detalle: str | None = None
    accion: AccionSincronizacion | None = None
    # Decisión y motivo de una aprobación comercial que quedó a medio enviar
    # (`reintentar_aprobacion` la repite tal cual).
    decision: Literal["aprobado", "rechazado"] | None = None
    motivo: str | None = None
    # Un proceso de fondo lo reintenta solo (facturas-api).
    automatico: bool = False
    enlace: str | None = None


class EventoSincronizacion(BaseModel):
    id: int
    creado_en: datetime
    modulo: str
    modulo_label: str
    ambiente: str
    encf: str | None = None
    track_id: str | None = None
    estado: str | None = None
    codigo: str | None = None
    error: str | None = None
    resultado: ResultadoEvento


FormaPago607 = Annotated[int, Field(ge=1, le=7)]


class FacturaPapelPayload(_Entrada):
    ncf: str = Field(..., min_length=11, max_length=19)
    fecha: date
    cliente_rnc: str | None = None
    cliente_nombre: str | None = Field(None, max_length=300)
    gravado_18: Decimal = Field(Decimal("0"), ge=0)
    gravado_16: Decimal = Field(Decimal("0"), ge=0)
    gravado_0: Decimal = Field(Decimal("0"), ge=0)
    exento: Decimal = Field(Decimal("0"), ge=0)
    # Sin indicarlo se calcula (18 % / 16 % del gravado).
    itbis_18: Decimal | None = Field(None, ge=0)
    itbis_16: Decimal | None = Field(None, ge=0)
    forma_pago: FormaPago607 = 1
    notas: str | None = Field(None, max_length=2000)
    encf_reemplazo: str | None = None


class FacturaPapel(BaseModel):
    id: str
    ncf: str
    tipo_comprobante: str  # '01' crédito fiscal, '02' consumo... (posiciones 2-3 del NCF)
    fecha: date
    cliente_rnc: str | None = None
    cliente_nombre: str | None = None
    gravado_18: Monto
    gravado_16: Monto
    gravado_0: Monto
    exento: Monto
    itbis_18: Monto
    itbis_16: Monto
    itbis: Monto
    total: Monto
    forma_pago: int
    notas: str | None = None
    encf_reemplazo: str | None = None
    creado_en: datetime


class ResultadoEntrega(BaseModel):
    id: str
    encf: str
    enviado_al_emisor: bool
    error_envio_emisor: str | None = None


# --- Reportes ---------------------------------------------------------

class _Reporte(BaseModel):
    desde: date
    hasta: date
    advertencias: list[str] = Field(default_factory=list)


class FilaVentas(BaseModel):
    clave: str
    etiqueta: str
    detalle: str | None = None  # RNC del cliente, código del tipo...
    documentos: int
    cantidad: Decimal | None = None  # solo por producto
    gravado_18: Monto
    gravado_16: Monto
    gravado_0: Monto
    exento: Monto
    itbis_18: Monto
    itbis_16: Monto
    subtotal: Monto  # sin ITBIS
    itbis: Monto
    total: Monto


class TotalesVentas(BaseModel):
    documentos: int
    gravado_18: Monto
    gravado_16: Monto
    gravado_0: Monto
    exento: Monto
    itbis_18: Monto
    itbis_16: Monto
    subtotal: Monto
    itbis: Monto
    total: Monto


class ReporteVentas(_Reporte):
    agrupar: Literal["mes", "dia", "cliente", "producto", "tipo_ecf"]
    filas: list[FilaVentas]
    totales: TotalesVentas


class FilaItbis(BaseModel):
    periodo: str  # AAAAMM
    ventas_gravadas: Monto
    itbis_ventas: Monto
    compras: Monto  # monto facturado del 606 (sin ITBIS; las notas de crédito restan)
    itbis_compras: Monto  # ITBIS facturado en el 606
    itbis_costo: Monto  # llevado al costo
    itbis_adelantado: Monto  # facturado − llevado al costo
    itbis_retenido: Monto  # retenido a proveedores (por fecha de pago)
    diferencia: Monto  # ITBIS de ventas − adelantado (antes de saldos y retenciones)


class ReporteItbis(_Reporte):
    filas: list[FilaItbis]
    totales: FilaItbis


class FilaCompras(BaseModel):
    clave: str
    etiqueta: str
    detalle: str | None = None
    documentos: int
    monto_servicios: Monto
    monto_bienes: Monto
    monto_facturado: Monto
    itbis_facturado: Monto
    itbis_retenido: Monto
    isr_retenido: Monto
    total: Monto


class ReporteCompras(_Reporte):
    agrupar: Literal["proveedor", "categoria", "mes"]
    filas: list[FilaCompras]
    totales: FilaCompras


class FilaAntiguedad(BaseModel):
    rnc: str | None = None
    nombre: str
    documentos: int
    por_vencer: Monto
    d1_30: Monto
    d31_60: Monto
    d61_90: Monto
    mas_90: Monto
    total: Monto


class DocumentoAntiguedad(BaseModel):
    id: str  # factura_id (CxC) o registro606_id (CxP)
    documento: str | None = None
    rnc: str | None = None
    nombre: str
    fecha_emision: date | None = None
    vence: date | None = None
    dias_vencida: int
    tramo: Literal["por_vencer", "d1_30", "d31_60", "d61_90", "mas_90"]
    pendiente: Monto


class ReporteAntiguedad(BaseModel):
    tipo: Literal["cxc", "cxp"]
    fecha_corte: date
    filas: list[FilaAntiguedad]
    documentos: list[DocumentoAntiguedad]
    totales: FilaAntiguedad


class FilaCobrosPagos(BaseModel):
    periodo: str  # AAAAMM
    cobros: Monto
    cantidad_cobros: int
    pagos: Monto
    cantidad_pagos: int
    neto: Monto


class MovimientoCobroPago(BaseModel):
    id: str
    tipo: Literal["cobro", "pago"]
    fecha: date
    documento: str | None = None
    contraparte: str
    metodo: str
    referencia: str | None = None
    monto: Monto


class TotalMetodo(BaseModel):
    metodo: str
    cobros: Monto
    pagos: Monto


class ReporteCobrosPagos(_Reporte):
    filas: list[FilaCobrosPagos]
    por_metodo: list[TotalMetodo]
    movimientos: list[MovimientoCobroPago]
    totales: FilaCobrosPagos


class FilaInventarioValorizado(BaseModel):
    producto_id: str
    sku: str
    nombre: str
    categoria: str | None = None
    unidad_medida: str | None = None
    existencia: Decimal
    costo_promedio: Decimal
    valor: Monto
    precio_venta: Decimal
    valor_venta: Monto
    stock_minimo: Decimal
    bajo_minimo: bool


class TotalesInventarioValorizado(BaseModel):
    productos: int
    existencia: Decimal
    valor: Monto
    valor_venta: Monto


class ReporteInventarioValorizado(BaseModel):
    fecha_corte: date
    almacen_id: str | None = None
    filas: list[FilaInventarioValorizado]
    totales: TotalesInventarioValorizado
    advertencias: list[str] = Field(default_factory=list)


class FilaRotacion(BaseModel):
    producto_id: str
    sku: str
    nombre: str
    categoria: str | None = None
    existencia_inicial: Decimal
    entradas: Decimal
    salidas: Decimal
    existencia_final: Decimal
    costo_salidas: Monto
    inventario_promedio: Decimal
    rotacion: Decimal | None = None  # veces en el período (None sin inventario promedio)
    dias_inventario: Decimal | None = None


class ReporteRotacion(_Reporte):
    filas: list[FilaRotacion]
    totales: FilaRotacion


class FilaMasVendido(BaseModel):
    posicion: int
    producto_id: str
    sku: str
    nombre: str
    categoria: str | None = None
    cantidad: Decimal
    facturas: int
    costo: Monto
    participacion: Decimal  # % de la cantidad vendida total


class ReporteMasVendidos(_Reporte):
    filas: list[FilaMasVendido]
    total_cantidad: Decimal
    total_costo: Monto


class FilaRetencion(BaseModel):
    clave: str
    etiqueta: str
    detalle: str | None = None
    documentos: int
    monto_base: Monto
    isr_retenido: Monto
    itbis_retenido: Monto
    total: Monto


class ReporteRetenciones(_Reporte):
    # Efectuadas: las que retuvimos a proveedores (606, por fecha de pago).
    efectuadas: list[FilaRetencion]
    por_tipo: list[FilaRetencion]
    totales_efectuadas: FilaRetencion
    # Sufridas: las que nos retuvieron los clientes en e-CF de venta aceptados.
    sufridas: list[FilaRetencion]
    totales_sufridas: FilaRetencion
