// Utilidades de presentación del detalle de una factura.
import { fechaDeDgii } from "../../lib/format";

/** "21-10-2026" (formato DGII) -> "21/10/2026". */
export function fechaDgiiLegible(valor: unknown): string {
  if (typeof valor !== "string" || !valor) return "";
  return valor.replace(/-/g, "/");
}

/** "21-10-2026" -> "2026-10-21" (para enlaces y cálculos). */
export function fechaDgiiIso(valor: unknown): string {
  if (typeof valor !== "string" || !/^\d{2}-\d{2}-\d{4}$/.test(valor)) return "";
  return fechaDeDgii(valor);
}

/** Marca de tiempo ISO -> "21/10/2026 · 15:07:44" en hora local. */
export function fechaHora(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const p = (n: number) => String(n).padStart(2, "0");
  return `${p(d.getDate())}/${p(d.getMonth() + 1)}/${d.getFullYear()} · ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

export function diasEntre(desdeIso: string, hastaIso: string): number | null {
  if (!desdeIso || !hastaIso) return null;
  const ms = new Date(`${hastaIso}T12:00:00`).getTime() - new Date(`${desdeIso}T12:00:00`).getTime();
  return Number.isFinite(ms) ? Math.round(ms / 86400000) : null;
}

/** Contenido de un elemento en un XML (primera aparición, con o sin prefijo de espacio de nombres). */
export function valorXml(xml: string | null, elemento: string): string | null {
  if (!xml) return null;
  const m = xml.match(new RegExp(`<(?:\\w+:)?${elemento}(?:\\s[^>]*)?>([^<]*)</(?:\\w+:)?${elemento}>`));
  return m ? m[1].trim() : null;
}

/** Descarga un texto como archivo (Blob + enlace temporal). */
export function descargarArchivo(nombre: string, contenido: string, tipo = "application/xml;charset=utf-8") {
  const url = URL.createObjectURL(new Blob([contenido], { type: tipo }));
  const a = document.createElement("a");
  a.href = url;
  a.download = nombre;
  a.style.display = "none";
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 10000);
}
