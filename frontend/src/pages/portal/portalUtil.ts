// Etiquetas y utilidades compartidas por la pantalla del portal y la página pública.
import type { EstadoPagoPortal, MetodoPagoPortal } from "../../lib/solucionesApi";

export const ETIQUETA_METODO: Record<MetodoPagoPortal, string> = {
  tarjeta: "Tarjeta de crédito/débito",
  transferencia: "Transferencia bancaria",
  pago_movil: "Pago móvil",
  deposito: "Depósito en efectivo",
};

export const ESTADO_PAGO: Record<EstadoPagoPortal, { label: string; badge: string }> = {
  contado: { label: "Pagada (contado)", badge: "bd b-acep" },
  pagada: { label: "Pagada", badge: "bd b-acep" },
  pendiente: { label: "Pendiente", badge: "bd b-proc" },
  vencida: { label: "Vencida", badge: "bd b-rech" },
  nota_credito: { label: "Nota de crédito", badge: "bd b-borr" },
};

/** Enlace público del portal (ruta /p/:token de esta misma aplicación). */
export function urlPortal(token: string): string {
  return `${window.location.origin}/p/${token}`;
}
