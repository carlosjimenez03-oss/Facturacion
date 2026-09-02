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
  creado_en: string;
  actualizado_en: string;
}

export interface FacturaDetalle extends FacturaResumen {
  datos: FacturaDatos;
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

export interface EnviarResponse {
  id: string;
  estado: EstadoFactura;
  track_id: string | null;
  error: string | null;
}
