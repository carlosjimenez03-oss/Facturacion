import type { EstadoFactura } from "./types";

export const ESTADO_LABEL: Record<EstadoFactura, string> = {
  borrador: "Borrador",
  generado: "XML generado",
  firmado: "Firmado",
  enviado: "Enviada a la DGII",
  aceptado: "Aceptada",
  rechazado: "Rechazada",
  error: "Error",
};

/** Clase del badge de estado (colores del sistema de diseño). */
export const ESTADO_BADGE: Record<EstadoFactura, string> = {
  borrador: "bd b-borr",
  generado: "bd b-borr",
  firmado: "bd b-proc",
  enviado: "bd b-env",
  aceptado: "bd b-acep",
  rechazado: "bd b-rech",
  error: "bd b-rech",
};

export const TIPO_ECF_LABEL: Record<number, string> = {
  31: "Crédito fiscal",
  32: "Consumo",
  33: "Nota de débito",
  34: "Nota de crédito",
  41: "Compras",
  43: "Gastos menores",
  44: "Regímenes especiales",
  45: "Gubernamental",
  46: "Exportaciones",
  47: "Pagos al exterior",
};

export const ESTADOS_PENDIENTES: EstadoFactura[] = ["borrador", "generado", "firmado", "enviado"];
