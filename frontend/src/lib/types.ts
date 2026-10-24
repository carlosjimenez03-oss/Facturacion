export interface FormaPago {
  FormaPago: string;
  MontoPago: string;
}

export interface ItemFactura {
  NumeroLinea: string;
  IndicadorFacturacion: string;
  NombreItem: string;
  IndicadorBienoServicio: string;
  DescripcionItem?: string;
  CantidadItem: string;
  UnidadMedida?: string;
  PrecioUnitarioItem: string;
  DescuentoMonto?: string;
  MontoItem: string;
  /** Producto del catálogo de inventario vinculado a esta línea. NO es un
   * campo del XSD del e-CF -- `construirDatos` (buildDatos.ts) arma
   * explícitamente el objeto que sí va al XML y deja este campo afuera, ver
   * el comentario ahí. Se usa solo para armar `items_inventario` (payload
   * aparte que descuenta stock al enviar la factura, ver
   * `construirItemsInventario`). */
  productoId?: string;
}

export interface FacturaDatos {
  Encabezado: {
    Version: string;
    IdDoc: Record<string, unknown>;
    Emisor: Record<string, unknown>;
    Comprador?: Record<string, unknown>;
    Totales: Record<string, unknown>;
  };
  DetallesItems: { Item: ItemFactura[] };
  InformacionReferencia?: Record<string, unknown>;
}

export type EstadoFactura =
  | "borrador"
  | "generado"
  | "firmado"
  | "enviado"
  | "aceptado"
  | "rechazado"
  | "error";

export interface ItemInventarioLink {
  numero_linea: string;
  producto_id: string;
  cantidad: string;
  /** Almacén de donde sale la mercancía (selector "Almacén" de la factura).
   * Ausente/null = el almacén principal. */
  almacen_id?: string | null;
  /** Lote del que sale. Ausente/null = inventario-api lo toma por FEFO si el
   * producto maneja lotes (ver `inventarioApi.sugerirFefo`). */
  lote_id?: string | null;
}

export interface FacturaResumen {
  id: string;
  tipo_ecf: number;
  ambiente: string;
  rnc_emisor: string;
  encf: string | null;
  estado: EstadoFactura;
  track_id: string | null;
  dgii_estado: string | null;
  dgii_codigo: string | null;
  error: string | null;
  inventario_sincronizado: boolean;
  inventario_error: string | null;
  creado_en: string;
  actualizado_en: string;
}

export interface FacturaDetalle extends FacturaResumen {
  datos: FacturaDatos;
  items_inventario: ItemInventarioLink[] | null;
  xml_generado: string | null;
  xml_firmado: string | null;
  dgii_respuesta: Record<string, unknown> | null;
}

export interface GenerarXmlResponse {
  id: string;
  estado: EstadoFactura;
  xml_generado: string | null;
  errores: string[];
}

// --- Inventario ---------------------------------------------------------
// Contrato con services/inventario_api (ver docs/INVENTARIO.md y las
// funciones de lib/inventarioApi.ts). Cantidades, costos y montos viajan
// como string decimal ("545.0000"), fechas "YYYY-MM-DD", marcas de tiempo
// ISO 8601.

/** Tasa de ITBIS con que se factura el producto. */
export type TasaItbis = "18" | "16" | "0" | "exento";

/** "servicio" no lleva existencia: no genera movimientos ni se valida stock. */
export type TipoProducto = "bien" | "servicio";

/** Calculado por el backend: "sin_existencia" si stock_actual <= 0,
 * "bajo_minimo" si stock_minimo > 0 y stock_actual <= stock_minimo,
 * "no_aplica" para servicios. */
export type EstadoStock = "disponible" | "bajo_minimo" | "sin_existencia" | "no_aplica";

export interface Producto {
  id: string;
  sku: string;
  nombre: string;
  descripcion: string | null;
  unidad_medida: string | null;
  precio_venta: string;
  costo_promedio: string;
  /** Existencia TOTAL (suma de todos los almacenes). */
  stock_actual: string;
  activo: boolean;
  creado_en: string;
  actualizado_en: string;
  tipo: TipoProducto;
  tasa_itbis: TasaItbis;
  codigo_barras: string | null;
  categoria: string | null;
  stock_minimo: string;
  /** true = las salidas sin lote se toman por FEFO de sus lotes. Se activa
   * sola al registrar un lote o al entrar mercancía a un lote (p. ej. una
   * compra con lote). */
  maneja_lotes: boolean;
  estado_stock: EstadoStock;
}

export interface Proveedor {
  id: string;
  nombre: string;
  rnc: string | null;
  telefono: string | null;
  correo: string | null;
  direccion: string | null;
  activo: boolean;
  creado_en: string;
  actualizado_en: string;
}

export interface CompraItem {
  producto_id: string;
  cantidad: string;
  costo_unitario: string;
  /** Lote de la línea: al confirmar la compra se crea (producto, número) si
   * no existe y la mercancía entra a ese lote. */
  lote_numero?: string | null;
  /** "YYYY-MM-DD". */
  lote_vencimiento?: string | null;
}

export type EstadoCompra = "borrador" | "confirmada";

export interface Compra {
  id: string;
  proveedor_id: string | null;
  numero_documento: string | null;
  estado: EstadoCompra;
  notas: string | null;
  /** Almacén que recibe la mercancía; null = el principal. */
  almacen_id?: string | null;
  creado_en: string;
  actualizado_en: string;
  confirmada_en: string | null;
  items: (CompraItem & { id: string })[];
}

/** Origen de un movimiento del kardex. */
export type ReferenciaMovimiento =
  | "compra"
  | "factura"
  | "ajuste"
  | "anulacion_ajuste"
  | "transferencia"
  | "lote";

export interface Movimiento {
  id: string;
  producto_id: string;
  tipo: "entrada" | "salida";
  cantidad: string;
  /** Las salidas se graban al costo promedio vigente (null solo en
   * movimientos de antes de la Fase 4). */
  costo_unitario: string | null;
  motivo: string | null;
  referencia_tipo: ReferenciaMovimiento | string | null;
  referencia_id: string | null;
  creado_en: string;
  almacen_id?: string | null;
  lote_id?: string | null;
  /** NumeroLinea de la factura (salidas por venta) o id de la línea del ajuste. */
  referencia_linea?: string | null;
  /** Número legible del documento: e-NCF, NCF de la compra, AJ-0001, TR-0001... */
  documento?: string | null;
}

// --- Almacenes ---------------------------------------------------------

export interface Almacen {
  id: string;
  nombre: string;
  direccion: string | null;
  /** Siempre hay exactamente uno: el que se usa cuando una operación no indica almacén. */
  es_principal: boolean;
  /** true = transferencias y ajustes de salida pueden dejar existencia negativa. */
  permite_negativo: boolean;
  activo: boolean;
  creado_en: string;
  actualizado_en: string;
  /** Columna "Productos": productos con existencia distinta de cero. */
  productos: number;
  cantidad: string;
  /** Columna "Valor del inventario (RD$)": existencia (no negativa) × costo promedio, 2 decimales. */
  valor: string;
}

/** Fila de `GET /almacenes/{id}/existencias`. */
export interface ExistenciaAlmacen {
  producto_id: string;
  sku: string;
  nombre: string;
  unidad_medida: string | null;
  categoria: string | null;
  stock_minimo: string;
  cantidad: string;
  costo_promedio: string;
  valor: string;
}

export interface ExistenciaLoteResumen {
  lote_id: string;
  numero: string;
  fecha_vencimiento: string | null;
  cantidad: string;
  estado: EstadoLote;
}

export interface ExistenciaProductoAlmacen {
  almacen_id: string;
  almacen_nombre: string;
  activo: boolean;
  cantidad: string;
  /** Existencia del almacén que no está en ningún lote. */
  sin_lote: string;
  valor: string;
  lotes: ExistenciaLoteResumen[];
}

/** `GET /productos/{id}/existencias`: reparto por almacén y lote. */
export interface ExistenciasProducto {
  producto_id: string;
  stock_actual: string;
  costo_promedio: string;
  almacenes: ExistenciaProductoAlmacen[];
}

export interface TransferenciaItem {
  id: string;
  producto_id: string;
  producto_sku: string;
  producto_nombre: string;
  cantidad: string;
  lote_id: string | null;
}

/** Las transferencias se aplican al crearse (no hay borrador). */
export interface Transferencia {
  id: string;
  /** "TR-0001" (puede haber saltos: una transferencia rechazada consume número). */
  numero: string;
  almacen_origen_id: string;
  almacen_destino_id: string;
  notas: string | null;
  creado_en: string;
  items: TransferenciaItem[];
}

// --- Lotes ---------------------------------------------------------

/** Calculado al leer: "vencido" si la fecha ya pasó, "por_vencer" si vence
 * dentro de los días de alerta (30 por defecto, o `?dias=`). */
export type EstadoLote = "vigente" | "por_vencer" | "vencido" | "bloqueado";

export interface Lote {
  id: string;
  producto_id: string;
  producto_sku: string;
  producto_nombre: string;
  numero: string;
  fecha_vencimiento: string | null;
  bloqueado: boolean;
  notas: string | null;
  /** Total en todos los almacenes. */
  cantidad: string;
  estado: EstadoLote;
  /** Negativo = vencido hace N días; null = sin vencimiento. */
  dias_para_vencer: number | null;
  /** Columna "Almacén" (un lote puede estar repartido en varios). */
  existencias: { almacen_id: string; almacen_nombre: string; cantidad: string }[];
  creado_en: string;
  actualizado_en: string;
}

export interface AlertasLotes {
  dias: number;
  por_vencer: Lote[];
  vencidos: Lote[];
}

export interface SugerenciaFefo {
  producto_id: string;
  almacen_id: string;
  cantidad: string;
  /** Lotes a usar, primero el que vence antes (sin vencidos ni bloqueados). */
  tomas: { lote_id: string; numero: string; fecha_vencimiento: string | null; disponible: string; cantidad: string }[];
  /** Lo que saldría de la mercancía sin lote. */
  sin_lote: string;
  /** Lo que no alcanza. */
  faltante: string;
}

// --- Ajustes de inventario ---------------------------------------------------------

export type MotivoAjuste = "conteo_fisico" | "merma" | "dano" | "vencimiento" | "otro";
export type EstadoAjuste = "borrador" | "aplicado" | "anulado";

export interface AjusteItemPayload {
  producto_id: string;
  tipo: "entrada" | "salida";
  /** Siempre positiva; el signo lo da `tipo`. */
  cantidad: string;
  /** Solo entradas; null = al costo promedio vigente. */
  costo_unitario?: string | null;
  lote_id?: string | null;
}

export interface AjusteItem extends AjusteItemPayload {
  id: string;
  producto_sku: string;
  producto_nombre: string;
  costo_unitario: string | null;
  lote_id: string | null;
  lote_numero: string | null;
}

export interface Ajuste {
  id: string;
  /** "AJ-0001" (puede haber saltos). */
  numero: string;
  fecha: string;
  almacen_id: string;
  almacen_nombre: string;
  motivo: MotivoAjuste;
  notas: string | null;
  estado: EstadoAjuste;
  creado_en: string;
  actualizado_en: string;
  aplicado_en: string | null;
  anulado_en: string | null;
  items: AjusteItem[];
}

// --- Kardex y valuación ---------------------------------------------------------

export type MetodoValuacion = "promedio" | "peps";

export interface KardexSaldo {
  cantidad: string;
  costo_promedio: string;
  valor: string;
}

export interface KardexFila {
  movimiento_id: string;
  fecha: string;
  tipo: "entrada" | "salida";
  /** Columna "Tipo de movimiento": "Compra", "Venta", "Ajuste de salida",
   * "Transferencia enviada", "Stock inicial", "Anulación de ajuste"... */
  concepto: string;
  /** Columna "Documento" (e-NCF, NCF, AJ-0001, TR-0001; null en movimientos viejos). */
  documento: string | null;
  motivo: string | null;
  referencia_tipo: string | null;
  referencia_id: string | null;
  almacen_id: string | null;
  almacen_nombre: string | null;
  lote_id: string | null;
  lote_numero: string | null;
  /** Con signo: positiva = entrada, negativa = salida. */
  cantidad: string;
  costo_unitario: string;
  /** "Costo del movimiento", con signo. */
  costo_total: string;
  /** "Existencia" acumulada tras el movimiento. */
  saldo_cantidad: string;
  costo_promedio: string;
  /** "Valor total" acumulado. */
  saldo_valor: string;
}

export interface Kardex {
  producto_id: string;
  producto_sku: string;
  producto_nombre: string;
  unidad_medida: string | null;
  stock_minimo: string;
  almacen_id: string | null;
  almacen_nombre: string | null;
  desde: string | null;
  hasta: string | null;
  metodo: MetodoValuacion;
  /** Saldo al empezar `desde` (todos los movimientos anteriores). */
  saldo_inicial: KardexSaldo;
  saldo_final: KardexSaldo;
  /** Cantidades entradas / salidas del período (tarjeta "Movimientos del período"). */
  entradas: string;
  salidas: string;
  filas: KardexFila[];
}

export interface Valuacion {
  /** Igual al inventario final que usa el IR-2: Σ max(stock, 0) × costo promedio. */
  valor_total: string;
  /** Bienes activos. */
  productos: number;
  con_existencia: number;
  sin_existencia: number;
  bajo_minimo: number;
  /** `valor` = Σ max(existencia del almacén, 0) × costo promedio. Con algún
   * almacén en negativo, la suma de estos valores es MAYOR que `valor_total`
   * (que aplica el max al total del producto, como el IR-2). */
  por_almacen: { almacen_id: string; nombre: string; activo: boolean; productos: number; cantidad: string; valor: string }[];
  /** `categoria` null = sin categoría. */
  por_categoria: { categoria: string | null; productos: number; cantidad: string; valor: string }[];
}

export interface EnviarResponse {
  id: string;
  estado: EstadoFactura;
  track_id: string | null;
  error: string | null;
}

// --- Formato 606 (Compras de Bienes y Servicios) ------------------------

export interface PeriodoResumen606 {
  periodo: string;
  total: number;
  pendientes: number;
}

export interface Registro606 {
  id: string;
  periodo: string;
  origen: "receptor_recepcion" | "auto_emitido" | "manual";
  referencia_tipo: string | null;
  referencia_id: string | null;

  rnc_cedula: string;
  tipo_id: number;
  tipo_bienes_servicios: number | null;
  ncf: string;
  ncf_modificado: string | null;
  fecha_comprobante: string;
  fecha_pago: string | null;

  monto_servicios: string;
  monto_bienes: string;
  monto_facturado: string;
  itbis_facturado: string;
  itbis_retenido: string;
  itbis_proporcionalidad: string;
  itbis_costo: string;
  itbis_adelantar: string;
  itbis_percibido: string;
  tipo_retencion_isr: number | null;
  monto_retencion_renta: string;
  isr_percibido: string;
  impuesto_selectivo_consumo: string;
  otros_impuestos: string;
  monto_propina_legal: string;
  forma_pago: number | null;

  creado_en: string;
  actualizado_en: string;
}

export interface ErrorValidacion606 {
  registro_id: string;
  ncf: string;
  mensaje: string;
}

export interface ValidacionResponse606 {
  valido: boolean;
  total_registros: number;
  errores: ErrorValidacion606[];
}
