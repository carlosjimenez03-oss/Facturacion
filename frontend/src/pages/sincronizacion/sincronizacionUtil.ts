// Utilidades del Centro de sincronización.
import type { CanalSincronizacion } from "../../lib/operacionApi";

export const CANAL: Record<CanalSincronizacion, { label: string; corto: string }> = {
  dgii_envio: { label: "Envío de e-CF a la DGII", corto: "Envío DGII" },
  dgii_resultado: { label: "Resultado de la DGII", corto: "Resultado DGII" },
  inventario: { label: "Salidas de inventario", corto: "Inventario" },
  aprobacion_comercial: { label: "Aprobaciones comerciales", corto: "Aprobación ACECF" },
  entrega_emisor: { label: "Entrega del ACECF al proveedor", corto: "Entrega ACECF" },
};

/** "10:53 a. m." (hora local). */
export function hora(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleTimeString("es-DO", { hour: "numeric", minute: "2-digit" });
}

/** "23/10/2026 10:11". */
export function fechaHora(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${p(d.getDate())}/${p(d.getMonth() + 1)}/${d.getFullYear()} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

/** "0 h 41 min" desde `desde` hasta `ahora` (ms). */
export function duracion(desde: string | null | undefined, ahora: number): string {
  if (!desde) return "—";
  const minutos = Math.max(0, Math.floor((ahora - new Date(desde).getTime()) / 60_000));
  return `${Math.floor(minutos / 60)} h ${minutos % 60} min`;
}

/** Formas de pago del 607 (Norma General 07-2018). */
export const FORMAS_PAGO_607: { value: number; label: string }[] = [
  { value: 1, label: "Efectivo" },
  { value: 2, label: "Cheque, transferencia o depósito" },
  { value: 3, label: "Tarjeta de crédito o débito" },
  { value: 4, label: "Venta a crédito" },
  { value: 5, label: "Bonos o certificados de regalo" },
  { value: 6, label: "Permuta" },
  { value: 7, label: "Otras formas de venta" },
];
