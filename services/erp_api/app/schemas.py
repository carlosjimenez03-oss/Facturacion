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
    # Cuenta de Bancos (bancos.cuentas) donde entra el dinero: el cobro
    # pasa a ser un movimiento de esa cuenta (conciliable). Opcional.
    cuenta_bancaria_id: UUID | None = None
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
    cuenta_bancaria_id: UUID | None = None
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
    cuenta_bancaria_id: UUID | None = None
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
    # Cuenta de Bancos de la que sale el dinero (opcional; ver CobroPayload).
    cuenta_bancaria_id: UUID | None = None
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


# --- Bancos (contrato: frontend/src/lib/bancosApi.ts) ---------------------------------------------------------

TipoCuentaBancaria = Literal["corriente", "ahorro", "caja_chica", "tarjeta"]
Moneda = Literal["DOP", "USD"]
TipoMovimientoManual = Literal["deposito", "retiro", "cargo_bancario", "interes", "comision"]
TipoMovimiento = Literal[
    "deposito", "retiro", "cargo_bancario", "interes", "comision", "transferencia_entrada", "transferencia_salida",
    "cobro", "pago",
]
OrigenMovimiento = Literal["manual", "cobro", "pago"]


class CuentaBancariaPayload(_Entrada):
    # Vacío: "<banco> · <tipo>".
    nombre: str | None = None
    banco: str = Field(..., min_length=1)
    tipo: TipoCuentaBancaria
    numero: str | None = None
    moneda: Moneda = "DOP"
    saldo_inicial: Decimal = Decimal("0")
    # Por defecto hoy.
    fecha_saldo_inicial: date | None = None
    cuenta_contable_id: UUID | None = None
    activa: bool = True
    notas: str | None = None


class CuentaBancariaActualizar(_Entrada):
    nombre: str | None = None
    banco: str | None = None
    tipo: TipoCuentaBancaria | None = None
    numero: str | None = None
    moneda: Moneda | None = None
    saldo_inicial: Decimal | None = None
    fecha_saldo_inicial: date | None = None
    cuenta_contable_id: UUID | None = None
    activa: bool | None = None
    notas: str | None = None


class CuentaBancaria(BaseModel):
    id: UUID
    nombre: str
    banco: str
    tipo: TipoCuentaBancaria
    numero_enmascarado: str | None = None
    moneda: Moneda
    saldo_inicial: Monto
    fecha_saldo_inicial: date
    cuenta_contable_id: UUID | None = None
    cuenta_contable_codigo: str | None = None
    cuenta_contable_nombre: str | None = None
    activa: bool
    notas: str | None = None
    saldo_actual: Monto
    movimientos_sin_conciliar: int
    lineas_sin_conciliar: int
    ultimo_movimiento: date | None = None
    conciliada_hasta: date | None = None
    creado_en: datetime
    actualizado_en: datetime


class MovimientoBancario(BaseModel):
    origen: OrigenMovimiento
    id: UUID
    cuenta_id: UUID
    fecha: date
    tipo: TipoMovimiento
    # Con signo: + entra a la cuenta, − sale.
    monto: Monto
    descripcion: str
    referencia: str | None = None
    transferencia_id: UUID | None = None
    # Factura (cobro) o renglón del 606 (pago).
    documento_id: UUID | None = None
    conciliado: bool
    extracto_linea_id: UUID | None = None
    # Saldo de la cuenta después del movimiento (solo en el listado de la cuenta).
    saldo: Monto | None = None
    anulado: bool = False
    creado_en: datetime


class MovimientosCuenta(BaseModel):
    cuenta: CuentaBancaria
    desde: date | None = None
    hasta: date | None = None
    saldo_inicial: Monto
    entradas: Monto
    salidas: Monto
    saldo_final: Monto
    movimientos: list[MovimientoBancario]


class MovimientoPayload(_Entrada):
    cuenta_id: UUID
    fecha: date | None = None
    tipo: TipoMovimientoManual
    # Positivo: el tipo dice si entra o sale.
    monto: Decimal
    descripcion: str | None = None
    referencia: str | None = None
    # Contrapartida del asiento; sin ella: caja (depósito/retiro), gastos
    # bancarios (cargo/comisión), ingresos financieros (interés).
    cuenta_contable_id: UUID | None = None
    # RD$ por US$; obligatoria en cuentas en dólares.
    tasa_cambio: Decimal | None = Field(None, gt=0)


class TransferenciaPayload(_Entrada):
    cuenta_origen_id: UUID
    cuenta_destino_id: UUID
    fecha: date | None = None
    monto: Decimal
    # Solo entre monedas distintas: lo que llega a la de destino.
    monto_destino: Decimal | None = None
    # Solo entre dos cuentas en dólares (RD$ por US$).
    tasa_cambio: Decimal | None = Field(None, gt=0)
    concepto: str | None = None
    referencia: str | None = None


class Transferencia(BaseModel):
    id: UUID
    cuenta_origen_id: UUID
    cuenta_origen_nombre: str
    moneda_origen: Moneda
    cuenta_destino_id: UUID
    cuenta_destino_nombre: str
    moneda_destino: Moneda
    fecha: date
    monto: Monto
    monto_destino: Monto
    tasa_cambio: Decimal | None = None
    concepto: str | None = None
    referencia: str | None = None
    anulada: bool
    anulada_en: datetime | None = None
    creado_en: datetime


class MapeoExtracto(_Entrada):
    """Columnas del CSV por nombre (como en el encabezado) o por posición
    (0 = primera). Todo opcional: lo que no se indique se detecta."""

    fecha: str | int | None = None
    descripcion: str | int | None = None
    referencia: str | int | None = None
    debito: str | int | None = None
    credito: str | int | None = None
    monto: str | int | None = None
    saldo: str | int | None = None
    tipo: str | int | None = None
    formato_fecha: Literal["DD/MM/AAAA", "MM/DD/AAAA", "AAAA-MM-DD"] | None = None
    separador_decimal: Literal[".", ","] | None = None
    signo_monto: Literal["credito_positivo", "debito_positivo"] | None = None
    fila_encabezado: int | None = Field(None, ge=1)
    separador: Literal[",", ";", "\t", "|"] | None = None


class LineaLeida(BaseModel):
    fila: int
    fecha: date
    descripcion: str
    referencia: str | None = None
    monto: Monto
    saldo: Monto | None = None


class ErrorFila(BaseModel):
    fila: int
    motivo: str


class PrevisualizacionExtracto(BaseModel):
    separador: str
    fila_encabezado: int
    columnas: list[str]
    # Campo -> columna detectada/indicada (nombre del encabezado) o null.
    mapeo: dict[str, str | None]
    total_lineas: int
    lineas: list[LineaLeida]
    errores: list[ErrorFila]


class Extracto(BaseModel):
    id: UUID
    cuenta_id: UUID
    nombre_archivo: str | None = None
    desde: date | None = None
    hasta: date | None = None
    saldo_final: Monto | None = None
    importado_en: datetime
    lineas: int
    conciliadas: int


class ResultadoImportacion(BaseModel):
    separador: str
    fila_encabezado: int
    columnas: list[str]
    mapeo: dict[str, str | None]
    # null si no quedó ninguna línea nueva (todas duplicadas u omitidas).
    extracto: Extracto | None = None
    importadas: int
    duplicadas: int
    errores: list[ErrorFila]


class MovimientoConciliado(BaseModel):
    origen: OrigenMovimiento
    id: UUID
    fecha: date
    tipo: TipoMovimiento
    monto: Monto
    descripcion: str
    referencia: str | None = None


class LineaExtracto(BaseModel):
    id: UUID
    extracto_id: UUID
    cuenta_id: UUID
    fila: int | None = None
    fecha: date
    descripcion: str
    referencia: str | None = None
    # Con signo: + crédito del banco (entra), − débito (sale).
    monto: Monto
    saldo: Monto | None = None
    conciliada: bool
    conciliada_en: datetime | None = None
    movimiento: MovimientoConciliado | None = None


class ConciliacionCerrada(BaseModel):
    id: UUID
    cuenta_id: UUID
    hasta: date
    saldo_banco: Monto
    saldo_libros: Monto
    transito_entradas: Monto
    transito_salidas: Monto
    diferencia: Monto
    lineas_conciliadas: int
    movimientos_en_transito: int
    notas: str | None = None
    cerrada_en: datetime


class ResumenConciliacion(BaseModel):
    saldo_libros: Monto
    # Entradas/salidas registradas en libros que el banco aún no refleja al corte.
    transito_entradas: Monto
    transito_salidas: Monto
    movimientos_en_transito: int
    # saldo_libros − tránsito: debe ser igual al saldo según el banco.
    libros_ajustado: Monto
    lineas_total: int
    lineas_conciliadas: int
    # Líneas del extracto sin conciliar hasta el corte (impiden cerrar).
    lineas_pendientes: int
    saldo_banco: Monto
    # De dónde sale saldo_banco: "extracto" (saldo final indicado al importar),
    # "ultima_linea" (columna de saldo del CSV) o "calculado".
    origen_saldo_banco: Literal["extracto", "ultima_linea", "calculado"]
    diferencia: Monto
    puede_cerrar: bool
    # P. ej. no se pudo saber el orden de las líneas del extracto para tomar su saldo.
    advertencias: list[str] = []


class Conciliacion(BaseModel):
    cuenta: CuentaBancaria
    conciliada_hasta: date | None = None
    hasta: date
    lineas: list[LineaExtracto]
    movimientos_sin_conciliar: list[MovimientoBancario]
    resumen: ResumenConciliacion
    ultima_conciliacion: ConciliacionCerrada | None = None


class SugerenciaConciliacion(BaseModel):
    linea: LineaExtracto
    movimiento: MovimientoBancario
    puntaje: int
    motivo: str


class ConciliarPayload(BaseModel):
    origen: OrigenMovimiento
    movimiento_id: UUID


class ConciliacionAutoPayload(BaseModel):
    dias: int = Field(3, ge=0, le=31)
    hasta: date | None = None
    # Solo estas líneas; sin ellas, todas las sugerencias.
    lineas: list[UUID] | None = None


class ResultadoConciliacionAuto(BaseModel):
    conciliadas: int
    sugerencias: list[SugerenciaConciliacion]


class MovimientoDesdeLineaPayload(_Entrada):
    # Por defecto: depósito si la línea es un crédito, cargo bancario si es un débito.
    tipo: TipoMovimientoManual | None = None
    descripcion: str | None = None
    cuenta_contable_id: UUID | None = None
    tasa_cambio: Decimal | None = Field(None, gt=0)


class CerrarConciliacionPayload(_Entrada):
    hasta: date
    # Sin él: el saldo final del extracto, el de su última línea o el calculado.
    saldo_banco: Decimal | None = None
    notas: str | None = None


# --- Contabilidad (contrato: frontend/src/lib/contabilidadApi.ts) ---------------------------------------------------------

TipoCuentaContable = Literal["activo", "pasivo", "patrimonio", "ingreso", "costo", "gasto"]
Naturaleza = Literal["deudora", "acreedora"]
EstadoAsiento = Literal["borrador", "contabilizado", "anulado"]
OrigenAsiento = Literal[
    "manual", "venta", "nota_credito", "nota_debito", "cobro", "compra", "pago", "movimiento", "transferencia",
    "proporcionalidad", "depreciacion",
]


class CuentaContablePayload(_Entrada):
    codigo: str = Field(..., min_length=1)
    nombre: str = Field(..., min_length=1)
    # Sin ellos: el del padre / el primer dígito; la naturaleza del tipo.
    tipo: TipoCuentaContable | None = None
    naturaleza: Naturaleza | None = None
    acepta_movimientos: bool = True
    activa: bool = True
    descripcion: str | None = None


class CuentaContableActualizar(_Entrada):
    codigo: str | None = None
    nombre: str | None = None
    naturaleza: Naturaleza | None = None
    acepta_movimientos: bool | None = None
    activa: bool | None = None
    descripcion: str | None = None


class CuentaContable(BaseModel):
    id: UUID
    codigo: str
    nombre: str
    tipo: TipoCuentaContable
    naturaleza: Naturaleza
    acepta_movimientos: bool
    padre_id: UUID | None = None
    padre_codigo: str | None = None
    nivel: int
    hijos: int
    activa: bool
    sistema: bool
    descripcion: str | None = None
    tiene_movimientos: bool
    # Saldo (con sus subcuentas) según su naturaleza.
    saldo: Monto
    creado_en: datetime
    actualizado_en: datetime


class CuentaPorDefecto(BaseModel):
    concepto: str
    grupo: Literal["ventas", "bancos", "compras", "activos"]
    etiqueta: str
    codigo_sugerido: str
    cuenta_id: UUID | None = None
    cuenta_codigo: str | None = None
    cuenta_nombre: str | None = None


class CuentasPorDefectoPayload(BaseModel):
    # concepto -> cuenta (null = sin cuenta).
    valores: dict[str, UUID | None]


class LineaAsientoPayload(_Entrada):
    cuenta_id: UUID
    descripcion: str | None = None
    debito: Decimal = Decimal("0")
    credito: Decimal = Decimal("0")


class AsientoPayload(_Entrada):
    fecha: date | None = None
    concepto: str = Field(..., min_length=1)
    referencia: str | None = None
    lineas: list[LineaAsientoPayload]
    # true = contabilizar (debe cuadrar); false = guardar como borrador.
    contabilizar: bool = False


class AnularAsientoPayload(_Entrada):
    motivo: str | None = None


class LineaAsiento(BaseModel):
    id: UUID
    linea: int
    cuenta_id: UUID
    cuenta_codigo: str
    cuenta_nombre: str
    descripcion: str | None = None
    debito: Monto
    credito: Monto


class AsientoResumen(BaseModel):
    id: UUID
    # "AS-0001".
    numero: str
    fecha: date
    concepto: str
    referencia: str | None = None
    estado: EstadoAsiento
    origen: OrigenAsiento
    origen_etiqueta: str
    origen_id: str | None = None
    automatico: bool
    total_debito: Monto
    total_credito: Monto
    cuadrado: bool
    contabilizado_en: datetime | None = None
    anulado_en: datetime | None = None
    motivo_anulacion: str | None = None
    creado_en: datetime


class Asiento(AsientoResumen):
    lineas: list[LineaAsiento]


class GenerarAsientosPayload(BaseModel):
    desde: date
    hasta: date


class DocumentoNoContabilizado(BaseModel):
    origen: OrigenAsiento
    origen_id: str
    fecha: date | None = None
    referencia: str | None = None
    descripcion: str
    monto: Monto | None = None
    motivo: str


class CambioPendiente(BaseModel):
    accion: Literal["crear", "actualizar", "anular"]
    origen: OrigenAsiento
    origen_id: str
    fecha: date | None = None
    descripcion: str
    monto: Monto | None = None
    asiento_numero: str | None = None


class ResultadoGeneracion(BaseModel):
    desde: date
    hasta: date
    creados: int
    actualizados: int
    anulados: int
    sin_cambios: int
    # Lo que se hizo (generar) o lo que haría (pendientes).
    pendientes: list[CambioPendiente]
    no_contabilizados: list[DocumentoNoContabilizado]


class LibroDiario(BaseModel):
    desde: date
    hasta: date
    asientos: list[Asiento]
    total_asientos: int
    total_debitos: Monto
    total_creditos: Monto


class MovimientoMayor(BaseModel):
    asiento_id: UUID
    numero: str
    fecha: date
    concepto: str
    origen: OrigenAsiento
    referencia: str | None = None
    descripcion: str | None = None
    cuenta_codigo: str
    cuenta_nombre: str
    debito: Monto
    credito: Monto
    saldo: Monto


class Mayor(BaseModel):
    cuenta: CuentaContable
    desde: date
    hasta: date
    saldo_inicial: Monto
    total_debitos: Monto
    total_creditos: Monto
    saldo_final: Monto
    movimientos: list[MovimientoMayor]


class FilaBalanza(BaseModel):
    cuenta_id: UUID
    codigo: str
    nombre: str
    tipo: TipoCuentaContable
    naturaleza: Naturaleza
    nivel: int
    acepta_movimientos: bool
    saldo_inicial: Monto
    debitos: Monto
    creditos: Monto
    saldo_final: Monto
    saldo_final_deudor: Monto
    saldo_final_acreedor: Monto


class TotalesBalanza(BaseModel):
    saldo_inicial_deudor: Monto
    saldo_inicial_acreedor: Monto
    debitos: Monto
    creditos: Monto
    saldo_final_deudor: Monto
    saldo_final_acreedor: Monto


class Balanza(BaseModel):
    desde: date
    hasta: date
    cuentas: list[FilaBalanza]
    # Solo cuentas de movimiento (las de grupo ya suman a sus hijas).
    totales: TotalesBalanza
    cuadrada: bool


class FilaEstado(BaseModel):
    cuenta_id: UUID
    codigo: str
    nombre: str
    nivel: int
    acepta_movimientos: bool
    monto: Monto


class SeccionEstado(BaseModel):
    cuentas: list[FilaEstado]
    total: Monto


class EstadoResultados(BaseModel):
    desde: date
    hasta: date
    ingresos: SeccionEstado
    costos: SeccionEstado
    gastos: SeccionEstado
    utilidad_bruta: Monto
    utilidad_neta: Monto
    margen_neto_pct: Monto


class BalanceGeneral(BaseModel):
    fecha: date
    activos: SeccionEstado
    pasivos: SeccionEstado
    patrimonio: SeccionEstado
    resultado_ejercicio: Monto
    resultados_anteriores_no_cerrados: Monto
    total_patrimonio: Monto
    total_pasivo_patrimonio: Monto
    cuadrado: bool


class ResumenContabilidad(BaseModel):
    desde: date
    hasta: date
    activos: Monto
    pasivos: Monto
    patrimonio: Monto
    ingresos: Monto
    costos: Monto
    gastos: Monto
    utilidad_neta: Monto
    asientos_contabilizados: int
    asientos_manuales: int
    borradores: int
    documentos_por_contabilizar: int
    documentos_no_contabilizables: int


MetodoDepreciacion = Literal["linea_recta", "dgii"]


class ActivoFijoPayload(_Entrada):
    descripcion: str = Field(..., min_length=1)
    categoria: Literal[1, 2, 3]
    fecha_adquisicion: date
    costo: Decimal
    valor_residual: Decimal = Decimal("0")
    metodo: MetodoDepreciacion = "dgii"
    vida_util_meses: int | None = Field(None, gt=0)
    # AAAAMM; por defecto el mes siguiente al de adquisición.
    depreciar_desde: str | None = None
    depreciacion_acumulada_inicial: Decimal = Decimal("0")
    cuenta_activo_id: UUID | None = None
    registro606_id: UUID | None = None
    numero_serie: str | None = None
    ubicacion: str | None = None
    notas: str | None = None


class ActivoFijoActualizar(_Entrada):
    descripcion: str | None = None
    categoria: Literal[1, 2, 3] | None = None
    fecha_adquisicion: date | None = None
    costo: Decimal | None = None
    valor_residual: Decimal | None = None
    metodo: MetodoDepreciacion | None = None
    vida_util_meses: int | None = Field(None, gt=0)
    depreciar_desde: str | None = None
    depreciacion_acumulada_inicial: Decimal | None = None
    cuenta_activo_id: UUID | None = None
    registro606_id: UUID | None = None
    numero_serie: str | None = None
    ubicacion: str | None = None
    notas: str | None = None


class BajaActivoPayload(BaseModel):
    # null = revertir la baja.
    fecha: date | None
    # Lo cobrado si se vendió (rebaja la cuenta fiscal de su categoría en el IR-2).
    valor_venta: Decimal | None = None


class ActivoFijo(BaseModel):
    id: UUID
    descripcion: str
    categoria: Literal[1, 2, 3]
    categoria_nombre: str
    # Tasa anual del Art. 287 para la categoría (5, 25 o 15).
    tasa_dgii: Monto
    fecha_adquisicion: date
    costo: Monto
    valor_residual: Monto
    metodo: MetodoDepreciacion
    vida_util_meses: int | None = None
    depreciar_desde: str
    depreciacion_acumulada_inicial: Monto
    depreciacion_registrada: Monto
    depreciacion_acumulada: Monto
    valor_en_libros: Monto
    # Cuota del próximo mes por depreciar.
    cuota_mensual: Monto
    ultimo_periodo_depreciado: str | None = None
    estado: Literal["activo", "depreciado", "baja"]
    cuenta_activo_id: UUID | None = None
    cuenta_activo_codigo: str | None = None
    cuenta_activo_nombre: str | None = None
    registro606_id: UUID | None = None
    numero_serie: str | None = None
    ubicacion: str | None = None
    fecha_baja: date | None = None
    valor_venta: Monto | None = None
    notas: str | None = None
    creado_en: datetime
    actualizado_en: datetime


class CuotaDepreciacion(BaseModel):
    periodo: str
    cuota: Monto
    acumulada: Monto
    valor_en_libros: Monto
    registrada: bool
    monto_registrado: Monto | None = None
    asiento_numero: str | None = None


class DepreciacionFiscalAnio(BaseModel):
    anio: str
    depreciacion: Monto


class TablaDepreciacion(BaseModel):
    activo: ActivoFijo
    hasta: str
    cuotas: list[CuotaDepreciacion]
    fiscal: list[DepreciacionFiscalAnio]


class CuotaActivoPeriodo(BaseModel):
    activo_id: UUID
    descripcion: str
    categoria: int
    metodo: MetodoDepreciacion
    cuota: Monto
    registrada: bool
    meses_sin_registrar_antes: int


class DepreciacionPeriodo(BaseModel):
    periodo: str
    nombre_periodo: str
    total: Monto
    registrada: bool
    asiento_id: UUID | None = None
    asiento_numero: str | None = None
    activos: list[CuotaActivoPeriodo]
    advertencias: list[str]


class DepreciacionPayload(BaseModel):
    periodo: str


class ResultadoDepreciacion(BaseModel):
    asiento: Asiento
    total: Monto
    activos: list[CuotaActivoPeriodo]
    advertencias: list[str]


class DepreciacionFiscalActivo(BaseModel):
    activo_id: UUID
    descripcion: str
    categoria: int
    tasa_dgii: Monto
    depreciacion: Monto


class CuentaCategoriaFiscal(BaseModel):
    """Cuenta de la categoría 2 o 3 en el año (Art. 287 lit. e)."""

    categoria: int
    nombre: str
    tasa_dgii: Monto
    # Valor fiscal al cierre del año anterior.
    valor_inicio: Monto
    adiciones: Monto
    # Valor de venta de los activos dados de baja en el año.
    retiros: Monto
    base: Monto
    depreciacion: Monto
    valor_final: Monto
    # Ventas que superan la cuenta: renta gravable del año.
    ingreso_gravable: Monto


class DepreciacionFiscal(BaseModel):
    anio: str
    total: Monto
    # Por activo (en las categorías 2 y 3, su parte de la cuenta de la categoría).
    activos: list[DepreciacionFiscalActivo]
    categorias: list[CuentaCategoriaFiscal] = []
    ingreso_gravable: Monto = Decimal("0")
