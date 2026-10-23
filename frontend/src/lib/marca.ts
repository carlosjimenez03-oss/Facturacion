/** Identidad de la aplicación. Cambie estos valores (o las variables VITE_*) para personalizar la marca. */
export const MARCA = (import.meta.env.VITE_MARCA as string | undefined) || "Kontara";
export const EMPRESA_NOMBRE = (import.meta.env.VITE_EMPRESA_NOMBRE as string | undefined) || "Mi empresa";
export const EMPRESA_RNC = (import.meta.env.VITE_EMPRESA_RNC as string | undefined) || "";

/** Nombre legible del ambiente DGII a partir del valor guardado en la factura. */
export function nombreAmbiente(ambiente: string | null | undefined): "Pruebas" | "Certificación" | "Producción" {
  const a = (ambiente || (import.meta.env.VITE_AMBIENTE as string | undefined) || "test").toLowerCase();
  if (a.includes("cert") || a === "qa") return "Certificación";
  if (a === "ecf" || a.startsWith("prod")) return "Producción";
  return "Pruebas";
}

/** Formatea un RNC de 9 dígitos como 1-31-89754-2 y una cédula de 11 como 001-1234567-8. */
export function formatoRnc(valor: string | null | undefined): string {
  const d = (valor ?? "").replace(/\D/g, "");
  if (d.length === 9) return `${d[0]}-${d.slice(1, 3)}-${d.slice(3, 8)}-${d[8]}`;
  if (d.length === 11) return `${d.slice(0, 3)}-${d.slice(3, 10)}-${d[10]}`;
  return valor ?? "";
}

/** Montos en formato dominicano: 1,250,000.00 */
export function formatoMonto(valor: number | string | null | undefined): string {
  const n = typeof valor === "number" ? valor : Number(valor ?? 0);
  return (Number.isFinite(n) ? n : 0).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
