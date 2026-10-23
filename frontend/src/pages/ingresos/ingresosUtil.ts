// Utilidades compartidas por las pantallas de Ingresos (notas, cotizaciones y cobros).
import { ErpApiError } from "../../lib/erpApi";
import type { EstadoCotizacion, TasaItbis } from "../../lib/erpApi";

/** Fecha local de hoy como YYYY-MM-DD (para <input type="date">). */
export function hoyIso(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

export function sumarDias(fechaIso: string, dias: number): string {
  const d = new Date(`${fechaIso}T12:00:00`);
  d.setDate(d.getDate() + dias);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

/** YYYY-MM-DD (o DD-MM-YYYY, como lo guarda la DGII) -> DD/MM/YYYY. */
export function fechaLegible(fecha: string | null | undefined): string {
  if (!fecha) return "—";
  const f = fecha.slice(0, 10);
  if (/^\d{4}-\d{2}-\d{2}$/.test(f)) {
    const [a, m, d] = f.split("-");
    return `${d}/${m}/${a}`;
  }
  if (/^\d{2}-\d{2}-\d{4}$/.test(f)) return f.replace(/-/g, "/");
  return fecha;
}

/** Normaliza una fecha (YYYY-MM-DD o DD-MM-YYYY) a YYYY-MM-DD para compararla. */
export function fechaIso(fecha: string | null | undefined): string {
  if (!fecha) return "";
  const f = fecha.slice(0, 10);
  if (/^\d{2}-\d{2}-\d{4}$/.test(f)) {
    const [d, m, a] = f.split("-");
    return `${a}-${m}-${d}`;
  }
  return f;
}

export function mesActualLegible(): string {
  const s = new Date().toLocaleDateString("es-DO", { month: "long", year: "numeric" });
  return s.charAt(0).toUpperCase() + s.slice(1);
}

export function mensajeError(err: unknown): string {
  if (err instanceof ErpApiError) return err.message;
  if (err instanceof TypeError) return "No se pudo conectar con el servidor. Verifique su conexión e intente de nuevo.";
  return err instanceof Error ? err.message : String(err);
}

/** Redondeo a centavos (mitad hacia arriba), como el backend. */
export function centavos(n: number): number {
  return Math.round((n + Number.EPSILON) * 100) / 100;
}

export const TASAS_ITBIS: { value: TasaItbis; label: string; tasa: number }[] = [
  { value: "18", label: "18%", tasa: 0.18 },
  { value: "16", label: "16%", tasa: 0.16 },
  { value: "0", label: "0%", tasa: 0 },
  { value: "exento", label: "Exento", tasa: 0 },
];

export interface LineaCalculo {
  cantidad: string;
  precio_unitario: string;
  descuento_pct?: string;
  itbis: TasaItbis;
}

/** Importe de una línea: cantidad × precio × (1 − descuento / 100). */
export function importeLinea(l: LineaCalculo): number {
  const bruto = (Number(l.cantidad) || 0) * (Number(l.precio_unitario) || 0);
  const pct = Math.min(Math.max(Number(l.descuento_pct) || 0, 0), 100);
  return centavos(bruto * (1 - pct / 100));
}

/** Totales de una cotización, igual que el backend (services/erp_api): el ITBIS
 * se calcula sobre la suma de importes de cada tasa, como en los Totales del e-CF. */
export function totalesCotizacion(lineas: LineaCalculo[]) {
  let bruto = 0;
  let importes = 0;
  let gravado18 = 0;
  let gravado16 = 0;
  for (const l of lineas) {
    const importe = importeLinea(l);
    bruto += (Number(l.cantidad) || 0) * (Number(l.precio_unitario) || 0);
    importes += importe;
    if (l.itbis === "18") gravado18 += importe;
    if (l.itbis === "16") gravado16 += importe;
  }
  const subtotal = centavos(bruto);
  const i18 = centavos(gravado18 * 0.18);
  const i16 = centavos(gravado16 * 0.16);
  importes = centavos(importes);
  return { subtotal, descuentos: centavos(subtotal - importes), i18, i16, total: centavos(importes + i18 + i16) };
}

/** Descarga un CSV (con BOM para que Excel respete los acentos). */
export function descargarCsv(nombre: string, filas: (string | number | null | undefined)[][]) {
  const celda = (v: string | number | null | undefined) => {
    const s = v == null ? "" : String(v);
    return /[",\n;]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const texto = "﻿" + filas.map((f) => f.map(celda).join(",")).join("\r\n");
  const url = URL.createObjectURL(new Blob([texto], { type: "text/csv;charset=utf-8" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = nombre;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 8000);
}

/** Enlace de WhatsApp con el texto ya escrito (el usuario elige el contacto y lo envía). */
export function enlaceWhatsApp(texto: string, telefono?: string | null): string {
  let tel = (telefono ?? "").replace(/\D/g, "");
  if (tel.length === 10) tel = `1${tel}`; // números dominicanos (809/829/849)
  return `https://wa.me/${tel}?text=${encodeURIComponent(texto)}`;
}

export function enlaceCorreo(asunto: string, cuerpo: string, para?: string | null): string {
  return `mailto:${para ?? ""}?subject=${encodeURIComponent(asunto)}&body=${encodeURIComponent(cuerpo)}`;
}

export const ESTADO_COTIZACION: Record<EstadoCotizacion, { label: string; badge: string }> = {
  abierta: { label: "Abierta", badge: "bd b-env" },
  aceptada: { label: "Aceptada", badge: "bd b-acep" },
  vencida: { label: "Vencida", badge: "bd b-rech" },
  facturada: { label: "Facturada", badge: "bd b-teal" },
  rechazada: { label: "Rechazada", badge: "bd b-borr" },
};

export function puedeFacturar(c: { estado: EstadoCotizacion }): boolean {
  return c.estado === "abierta" || c.estado === "aceptada";
}

