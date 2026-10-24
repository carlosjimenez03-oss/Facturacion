import { ErpApiError } from "../../lib/erpApi";

export function mensajeError(err: unknown): string {
  if (err instanceof ErpApiError) {
    if (err.status === 401) return "Su sesión expiró. Inicie sesión de nuevo.";
    return err.message;
  }
  return "El servicio de configuración (erp-api) no responde.";
}

export function fechaCorta(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso.length === 10 ? `${iso}T12:00:00` : iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString("es-DO", { day: "2-digit", month: "2-digit", year: "numeric" });
}

export const numero = (n: number | null | undefined) => (n ?? 0).toLocaleString("en-US");

/** e-NCF de un tipo y número: E31 + 10 dígitos. */
export const encf = (tipo: number, n: number) => `E${tipo}${String(n).padStart(10, "0")}`;

export type Avisar = (texto: string, opciones?: { detalle?: string; error?: boolean }) => void;
