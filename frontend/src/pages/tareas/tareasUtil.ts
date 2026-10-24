// Utilidades de Mis tareas (también las usa el resumen de la campana, si se conecta a la barra superior).
import type { CategoriaTarea, Tarea } from "../../lib/operacionApi";
import { formatoMonto } from "../../lib/marca";
import { fechaLegible } from "../ingresos/ingresosUtil";
import { diasHasta } from "../impuestos/impuestosUtil";

export const CATEGORIA: Record<CategoriaTarea, { label: string; badge: string }> = {
  dgii: { label: "DGII", badge: "bd b-env nodot" },
  ecf: { label: "e-CF", badge: "bd b-teal nodot" },
  compras: { label: "Compras", badge: "bd b-proc nodot" },
  cobros: { label: "Cobros", badge: "bd b-cond nodot" },
  pagos: { label: "Pagos", badge: "bd b-pend nodot" },
  ventas: { label: "Ventas", badge: "bd b-acep nodot" },
  inventario: { label: "Inventario", badge: "bd b-proc nodot" },
  gestion: { label: "Gestión", badge: "bd b-borr nodot" },
};

/** "Vence 20/11/2026 · en 27 días", "Venció el 20/10/2026 · hace 4 días", "Pospuesta hasta…", "Completada el…". */
export function metaTarea(t: Tarea): string {
  const partes: string[] = [];
  if (t.estado === "completada" && t.completada_en) partes.push(`Completada el ${new Date(t.completada_en).toLocaleDateString("es-DO")}`);
  else if (t.estado === "pospuesta" && t.pospuesta_hasta) partes.push(`Pospuesta hasta el ${fechaLegible(t.pospuesta_hasta)}`);
  if (t.vence) {
    const dias = diasHasta(t.vence);
    if (t.estado === "completada") partes.push(`Vencía ${fechaLegible(t.vence)}`);
    else if (dias < 0) partes.push(`Venció el ${fechaLegible(t.vence)} · hace ${-dias} ${dias === -1 ? "día" : "días"}`);
    else if (dias === 0) partes.push(`Vence hoy, ${fechaLegible(t.vence)}`);
    else partes.push(`Vence ${fechaLegible(t.vence)} · en ${dias} ${dias === 1 ? "día" : "días"}`);
  } else if (t.creado_en && t.origen === "automatica") {
    partes.push(`Desde el ${new Date(t.creado_en).toLocaleDateString("es-DO")}`);
  }
  if (t.monto) partes.push(`RD$ ${formatoMonto(t.monto)}`);
  return partes.join(" · ");
}
