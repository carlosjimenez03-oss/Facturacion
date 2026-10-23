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
from typing import Annotated, Any, Literal
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
    # Teléfono del contacto con ese RNC (para el recordatorio por WhatsApp).
    cliente_telefono: str | None = None


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


# --- Impuestos (contrato: frontend/src/lib/impuestosApi.ts) ---------------------------------------------------------

FormularioPreCalculo = Literal["it1", "ir17", "ir2"]
FormularioDeclaracion = Literal["606", "607", "608", "it1", "ir17", "ir2"]
# "no_aplica": 607/608 del emisor 100 % electrónico (EMISOR_100_ELECTRONICO).
EstadoDeclaracion = Literal["pendiente", "presentada", "vencida", "no_aplica"]


class Fila607(BaseModel):
    factura_id: str | None = None
    rnc_cedula: str
    # El contrato lo declara `number`; sin identificación (notas de crédito
    # de consumo) sale 3, como hace la plantilla oficial con una celda vacía
    # que no se valida -- en el TXT ese campo queda vacío.
    tipo_id: int
    ncf: str
    ncf_modificado: str | None = None
    tipo_ingreso: str
    fecha_comprobante: date
    fecha_retencion: date | None = None
    monto_facturado: Monto
    itbis_facturado: Monto
    itbis_retenido_terceros: Monto
    itbis_percibido: Monto
    retencion_renta_terceros: Monto
    isr_percibido: Monto
    impuesto_selectivo_consumo: Monto
    otros_impuestos: Monto
    monto_propina_legal: Monto
    efectivo: Monto
    cheque_transferencia: Monto
    tarjeta: Monto
    venta_credito: Monto
    bonos_certificados: Monto
    permuta: Monto
    otras_formas: Monto
    cliente_nombre: str | None = None
    tipo_ecf: int | None = None


class Totales607(BaseModel):
    cantidad: int
    monto_facturado: Monto
    itbis_facturado: Monto
    itbis_retenido_terceros: Monto
    retencion_renta_terceros: Monto


class Formato607(BaseModel):
    periodo: str
    rnc_emisor: str
    filas: list[Fila607]
    totales: Totales607
    advertencias: list[str]


class Fila608(BaseModel):
    id: str | None = None
    origen: Literal["anulacion_encf", "manual"]
    ncf: str
    fecha_comprobante: date
    tipo_anulacion: str
    tipo_anulacion_label: str


class Formato608(BaseModel):
    periodo: str
    rnc_emisor: str
    filas: list[Fila608]
    advertencias: list[str]


class Anulacion608Payload(_Entrada):
    ncf: str = Field(..., min_length=1)
    fecha_comprobante: date
    tipo_anulacion: str


class TipoAnulacion(BaseModel):
    codigo: str
    descripcion: str


class Casilla(BaseModel):
    # Número de LÍNEA del pre-cálculo ("1", "2", ...), no la casilla del
    # formulario oficial de la DGII (ver precalculos.py).
    codigo: str
    etiqueta: str
    valor: Monto
    tipo: Literal["normal", "subtotal", "total"]
    ayuda: str | None = None


class SeccionPreCalculo(BaseModel):
    numero: str
    titulo: str
    casillas: list[Casilla]


class AjusteEditable(BaseModel):
    clave: str
    etiqueta: str
    # Valor usado en el cálculo: el manual guardado o, si no hay, el sugerido
    # por el sistema (o 0).
    valor: Monto
    # True si `valor` es un valor manual guardado; False si es el sugerido.
    manual: bool
    # Valor calculado por el sistema (saldo a favor del mes anterior,
    # valuación del Kardex); null si el sistema no sugiere ninguno.
    sugerido: Monto | None = None
    ayuda: str | None = None


class ResultadoPreCalculo(BaseModel):
    etiqueta: str
    valor: Monto


class ResumenPreCalculo(BaseModel):
    etiqueta: str
    # Decimal plano ("1234.56") si es un monto; cualquier otro texto se
    # muestra tal cual ("35.2%").
    valor: str


class PreCalculo(BaseModel):
    formulario: FormularioPreCalculo
    periodo: str
    titulo: str
    subtitulo: str
    nota: str
    vence: date
    secciones: list[SeccionPreCalculo]
    resultado: ResultadoPreCalculo
    resumen: list[ResumenPreCalculo]
    ajustes: list[AjusteEditable]
    advertencias: list[str]


class AjustesPayload(BaseModel):
    # Valor vacío (o null) = quitar el valor manual y volver al calculado.
    valores: dict[str, str | None]


class Declaracion(BaseModel):
    formulario: FormularioDeclaracion
    periodo: str
    vence: date
    estado: EstadoDeclaracion
    presentada_en: date | None = None
    monto: Monto | None = None
    referencia: str | None = None


class MarcarPresentadaPayload(_Entrada):
    monto: Decimal | None = None
    referencia: str | None = None
    presentada_en: date | None = None


# --- Compras (contrato: frontend/src/lib/comprasApi.ts) ---------------------------------------------------------

EstadoAprobacion = Literal["pendiente", "aprobado", "rechazado"]
EstadoCuentaPagar = Literal["pendiente", "vencida", "pagada"]
MetodoPago = Literal["efectivo", "transferencia", "tarjeta", "cheque"]


class EcfRecibidoResumen(BaseModel):
    id: UUID
    rnc_emisor: str
    razon_social_emisor: str | None = None
    rnc_comprador: str | None = None
    encf: str
    tipo_ecf: int | None = None
    fecha_emision: date | None = None
    fecha_limite_pago: date | None = None
    monto_total: Monto
    itbis_total: Monto
    recibido_en: datetime
    estado_aprobacion: EstadoAprobacion
    motivo_rechazo: str | None = None
    aprobado_en: datetime | None = None
    # Último intento fallido de enviar la aprobación/rechazo (sigue pendiente).
    error_aprobacion: str | None = None
    compra606_id: UUID | None = None
    compra_inventario_id: UUID | None = None
    # Firma digital del e-CF verificada al recibirlo (None = no verificada).
    firma_valida: bool | None = None
    firma_detalle: str | None = None
    # La aprobación/rechazo se está enviando a la DGII (estado sigue 'pendiente').
    envio_en_curso: bool = False
    # Entrega del ACECF aceptado por la DGII al proveedor (best-effort).
    enviado_al_emisor: bool = False
    error_envio_emisor: str | None = None


class ItemEcfRecibido(BaseModel):
    linea: int
    descripcion: str
    cantidad: Decimal
    precio_unitario: Decimal
    monto: Monto
    bien_o_servicio: Literal["bien", "servicio"]
    indicador_facturacion: str | None = None


class EcfRecibido(EcfRecibidoResumen):
    items: list[ItemEcfRecibido]
    subtotal: Monto
    xml: str
    respuesta_dgii: dict[str, Any] | None = None


class RechazoPayload(_Entrada):
    motivo: str = Field(..., min_length=1, max_length=250)


class VincularPayload(BaseModel):
    registro606_id: UUID | None = None
    compra_inventario_id: UUID | None = None


class PagoProveedor(BaseModel):
    id: UUID
    registro606_id: UUID
    ncf: str | None = None
    proveedor_rnc: str | None = None
    proveedor_nombre: str
    fecha: date
    monto: Monto
    metodo: MetodoPago
    cuenta_origen: str | None = None
    referencia: str | None = None
    notas: str | None = None
    tipo_retencion_isr: int | None = None
    tipo_retencion_isr_label: str | None = None
    retencion_isr: Monto
    retencion_itbis: Monto
    anulado: bool
    anulado_en: datetime | None = None
    creado_en: datetime


class PagoProveedorPayload(_Entrada):
    registro606_id: UUID
    fecha: date | None = None
    # Lo que sale del banco/caja, neto de retenciones. Sin `ge=0` a
    # propósito: el repositorio valida y responde un 422 con mensaje claro.
    monto: Decimal
    metodo: MetodoPago
    cuenta_origen: str | None = None
    referencia: str | None = None
    notas: str | None = None
    tipo_retencion_isr: int | None = None
    retencion_isr: Decimal = Decimal("0")
    retencion_itbis: Decimal = Decimal("0")


class CuentaPorPagar(BaseModel):
    registro606_id: UUID
    ncf: str
    tipo_ecf: int | None = None
    periodo: str
    origen: str
    proveedor_rnc: str
    proveedor_nombre: str
    contacto_id: UUID | None = None
    compra_inventario_id: UUID | None = None
    ecf_recibido_id: UUID | None = None
    estado_aprobacion: EstadoAprobacion | None = None
    factura_id: UUID | None = None
    concepto: str | None = None
    tipo_bienes_servicios: int | None = None
    fecha_comprobante: date
    vence: date
    monto_facturado: Monto
    itbis_facturado: Monto
    total: Monto
    notas_credito: Monto
    tipo_retencion_isr: int | None = None
    tipo_retencion_isr_label: str | None = None
    retencion_isr: Monto
    retencion_itbis: Monto
    retenciones: Monto
    pagado: Monto
    pendiente: Monto
    estado: EstadoCuentaPagar
    dias_vencida: int
    fecha_pago: date | None = None
    forma_pago: int | None = None
    # Retenciones que declara el propio e-CF (E41 autoemitido), para
    # proponerlas al registrar el pago.
    retencion_isr_ecf: Monto
    retencion_itbis_ecf: Monto
    # Nota de crédito del proveedor (B04/E34): solo en el detalle
    # (`/compras/facturas/{id}`), nunca en el listado de cuentas por pagar;
    # pendiente 0 y descuenta su total de la factura que modifica.
    es_nota_credito: bool = False
    ncf_modificado: str | None = None
    afecta_registro606_id: UUID | None = None


class FacturaCompra(CuentaPorPagar):
    pagos: list[PagoProveedor]


class FacturaCompraPayload(_Entrada):
    proveedor_rnc: str = Field(..., min_length=1)
    proveedor_nombre: str | None = None
    contacto_id: UUID | None = None
    compra_inventario_id: UUID | None = None
    ncf: str = Field(..., min_length=1)
    ncf_modificado: str | None = None
    fecha_comprobante: date
    vence: date | None = None
    tipo_bienes_servicios: int
    concepto: str | None = None
    monto_servicios: Decimal = Decimal("0")
    monto_bienes: Decimal = Decimal("0")
    itbis_facturado: Decimal = Decimal("0")
    impuesto_selectivo_consumo: Decimal = Decimal("0")
    otros_impuestos: Decimal = Decimal("0")
    monto_propina_legal: Decimal = Decimal("0")
    # Forma de pago del 606 hasta que se pague (por defecto 4, compra a crédito).
    forma_pago: int | None = None


class FacturaCompraActualizar(_Entrada):
    """Solo los campos enviados se cambian (null borra el valor)."""

    proveedor_nombre: str | None = None
    contacto_id: UUID | None = None
    compra_inventario_id: UUID | None = None
    concepto: str | None = None
    vence: date | None = None
    tipo_bienes_servicios: int | None = None


class ComprobanteCompra(BaseModel):
    id: UUID
    tipo_ecf: Literal[41, 43]
    encf: str | None = None
    estado: str
    dgii_codigo: str | None = None
    error: str | None = None
    proveedor_rnc: str | None = None
    proveedor_nombre: str | None = None
    concepto: str | None = None
    lineas: int
    fecha_emision: date | None = None
    monto: Monto
    itbis: Monto
    total: Monto
    itbis_retenido: Monto
    isr_retenido: Monto
    registro606_id: UUID | None = None
    fecha_pago_606: date | None = None
    itbis_retenido_606: Monto | None = None
    isr_retenido_606: Monto | None = None
    creado_en: datetime


class FilaRetencion(BaseModel):
    registro606_id: UUID
    proveedor_rnc: str
    proveedor_nombre: str
    ncf: str
    fecha_comprobante: date | None = None
    fecha_pago: date | None = None
    monto_facturado: Monto
    itbis_facturado: Monto
    tipo_retencion_isr: int | None = None
    tipo_retencion_isr_label: str | None = None
    isr_retenido: Monto
    itbis_retenido: Monto
    declaraciones: list[Literal["ir17", "it1"]]
    estado: Literal["por_declarar", "declarada"]


class RetencionesProveedor(BaseModel):
    proveedor_rnc: str
    proveedor_nombre: str
    proveedor_correo: str | None = None
    proveedor_telefono: str | None = None
    comprobantes: int
    isr_retenido: Monto
    itbis_retenido: Monto


class TotalesRetencion(BaseModel):
    isr_retenido: Monto
    itbis_retenido: Monto


class RetencionesMes(BaseModel):
    periodo: str
    filas: list[FilaRetencion]
    proveedores: list[RetencionesProveedor]
    totales: TotalesRetencion
    it1_presentado: bool
    ir17_presentado: bool


class ComprobanteCertificado(BaseModel):
    registro606_id: UUID
    ncf: str
    fecha_comprobante: date | None = None
    fecha_pago: date | None = None
    monto_facturado: Monto
    itbis_facturado: Monto
    tipo_retencion_isr: int | None = None
    tipo_retencion_isr_label: str | None = None
    isr_retenido: Monto
    itbis_retenido: Monto


class AgenteRetencion(BaseModel):
    rnc: str
    nombre: str | None = None


class ProveedorCertificado(BaseModel):
    rnc: str
    tipo_identificacion: Literal["rnc", "cedula"]
    nombre: str
    correo: str | None = None


class TotalesCertificado(BaseModel):
    monto_facturado: Monto
    itbis_facturado: Monto
    isr_retenido: Monto
    itbis_retenido: Monto
    total_retenido: Monto


class CertificadoRetencion(BaseModel):
    periodo: str
    periodo_nombre: str
    fecha_emision: date
    agente: AgenteRetencion
    proveedor: ProveedorCertificado
    comprobantes: list[ComprobanteCertificado]
    totales: TotalesCertificado


class SugerenciaRetencion(BaseModel):
    tipo_retencion_isr: int | None = None
    tipo_retencion_isr_label: str | None = None
    isr_pct: Monto
    retencion_isr: Monto
    itbis_pct: Monto
    retencion_itbis: Monto
    total: Monto
    neto_a_pagar: Monto
    regla: str
