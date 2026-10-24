import type { EstadoFactura } from "./types";

export const ESTADO_LABEL: Record<EstadoFactura, string> = {
  borrador: "Borrador",
  generado: "XML generado",
  firmado: "Firmado",
  enviado: "Enviada a la DGII",
  aceptado: "Aceptada",
  rechazado: "Rechazada",
  error: "Error",
  contingencia: "Emitida en contingencia",
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
  contingencia: "bd b-pend",
};

/** Mensaje de una factura emitida en contingencia (POST /enviar sin conexión con la DGII). */
export const MENSAJE_CONTINGENCIA = "Emitida en contingencia — se enviará automáticamente a la DGII al restablecerse la conexión.";

/**
 * Leyenda que exige la DGII en la representación impresa de un e-CF emitido en contingencia
 * (Instructivo de Contingencia de Facturación Electrónica; la misma que imprime facturas-api en el PDF).
 * Ver docs/dgii/CONTINGENCIA.md.
 */
export const LEYENDA_CONTINGENCIA =
  "e-CF emitido en modalidad de Contingencia, el cual podrá ser consultado para su validez fiscal, a partir de las setenta y dos (72) horas.";

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

export const ESTADOS_PENDIENTES: EstadoFactura[] = ["borrador", "generado", "firmado", "enviado", "contingencia"];

/** Estados en que el e-CF ya se emitió (firmado y entregable): enviado, aceptado o en contingencia. */
export const ESTADOS_EMITIDOS: EstadoFactura[] = ["enviado", "aceptado", "contingencia"];
