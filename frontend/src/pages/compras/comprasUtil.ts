// Utilidades compartidas por las pantallas de Compras (bandeja, facturas de compra, pagos,
// comprobantes E41/E43 y retenciones).
import { ErpApiError } from "../../lib/erpApi";
import type { CuentaPorPagar, EstadoAprobacion, EstadoCuentaPagar, MetodoPago } from "../../lib/comprasApi";
import { TIPO_ECF_LABEL } from "../../lib/estados";
import { TIPOS_BIENES_SERVICIOS, TIPOS_RETENCION_ISR } from "../../lib/formato606Api";
import { mensajeError } from "../ingresos/ingresosUtil";

/** Catálogo del 606 (1-11) como opciones de un <select>: "01 · Gastos de personal". */
export const OPCIONES_BIENES_SERVICIOS = Object.entries(TIPOS_BIENES_SERVICIOS).map(([v, label]) => ({
  value: Number(v),
  label: label.replace(" - ", " · "),
}));

/** Tipos de retención de ISR del 606 (1-9). */
export const OPCIONES_RETENCION_ISR = Object.entries(TIPOS_RETENCION_ISR).map(([v, label]) => ({
  value: Number(v),
  label: label.replace(" - ", " · "),
}));

export function etiquetaBienesServicios(tipo: number | null | undefined): string {
  if (tipo == null) return "Sin clasificar";
  return OPCIONES_BIENES_SERVICIOS.find((o) => o.value === tipo)?.label ?? `Tipo ${tipo}`;
}

export const METODOS_PAGO: { value: MetodoPago; label: string }[] = [
  { value: "transferencia", label: "Transferencia" },
  { value: "cheque", label: "Cheque" },
  { value: "efectivo", label: "Efectivo" },
  { value: "tarjeta", label: "Tarjeta" },
];

export const METODO_LABEL = Object.fromEntries(METODOS_PAGO.map((m) => [m.value, m.label])) as Record<MetodoPago, string>;

export const ESTADO_CXP: Record<EstadoCuentaPagar, { label: string; badge: string }> = {
  pendiente: { label: "Pendiente", badge: "bd b-proc" },
  vencida: { label: "Vencida", badge: "bd b-rech" },
  pagada: { label: "Pagada", badge: "bd b-acep" },
};

export const ESTADO_APROBACION: Record<EstadoAprobacion, { label: string; badge: string }> = {
  pendiente: { label: "Pendiente", badge: "bd b-proc" },
  aprobado: { label: "Aprobada", badge: "bd b-acep" },
  rechazado: { label: "Rechazada", badge: "bd b-rech" },
};

/** Persona física (cédula, 11 dígitos) o jurídica (RNC, 9). */
export function personaDeRnc(rnc: string | null | undefined): "fisica" | "juridica" {
  return (rnc ?? "").replace(/\D/g, "").length === 9 ? "juridica" : "fisica";
}

/** Suma de montos decimales que llegan como string. */
export function suma<T>(lista: T[], valor: (x: T) => string | number | null | undefined): number {
  return lista.reduce((s, x) => s + (Number(valor(x)) || 0), 0);
}

export function tieneRetenciones(x: { retencion_isr: string; retencion_itbis: string }): boolean {
  return Number(x.retencion_isr) > 0 || Number(x.retencion_itbis) > 0;
}

/**
 * Mensaje legible de un error de erp-api. Los 422 de validación de FastAPI llegan como una lista
 * de objetos ({loc, msg}); se muestran como frases.
 */
export function mensajeCompras(err: unknown): string {
  if (err instanceof ErpApiError && err.message.startsWith("[")) {
    try {
      const lista = JSON.parse(err.message) as { loc?: (string | number)[]; msg?: string }[];
      const textos = lista.map((e) => {
        const campo = (e.loc ?? []).filter((x) => x !== "body").join(".");
        const msg = (e.msg ?? "").replace(/^Value error, /, "");
        return campo ? `${campo}: ${msg}` : msg;
      });
      if (textos.length) return textos.join(" · ");
    } catch {
      // no era JSON: se muestra tal cual
    }
  }
  return mensajeError(err);
}

/** "202610" del mes en curso. */
export function periodoActual(): string {
  const d = new Date();
  return `${d.getFullYear()}${String(d.getMonth() + 1).padStart(2, "0")}`;
}

/** "31 · Crédito fiscal". */
export function tipoEcfTexto(tipo: number | null | undefined): string {
  if (tipo == null) return "—";
  return `${tipo} · ${TIPO_ECF_LABEL[tipo] ?? "e-CF"}`;
}

/** Solo las registradas desde Compras y sin pagos vigentes se pueden eliminar (las demás vienen de un e-CF). */
export function sePuedeEliminar(c: CuentaPorPagar): boolean {
  return c.origen === "compras" && Number(c.pagado) === 0;
}
