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

export interface Producto {
  id: string;
  sku: string;
  nombre: string;
  descripcion: string | null;
  unidad_medida: string | null;
  precio_venta: string;
  costo_promedio: string;
  stock_actual: string;
  activo: boolean;
  creado_en: string;
  actualizado_en: string;
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
}

export type EstadoCompra = "borrador" | "confirmada";

export interface Compra {
  id: string;
  proveedor_id: string | null;
  numero_documento: string | null;
  estado: EstadoCompra;
  notas: string | null;
  creado_en: string;
  actualizado_en: string;
  confirmada_en: string | null;
  items: (CompraItem & { id: string })[];
}

export interface Movimiento {
  id: string;
  producto_id: string;
  tipo: "entrada" | "salida";
  cantidad: string;
  costo_unitario: string | null;
  motivo: string | null;
  referencia_tipo: string | null;
  referencia_id: string | null;
  creado_en: string;
}

export interface EnviarResponse {
  id: string;
  estado: EstadoFactura;
  track_id: string | null;
  error: string | null;
}
