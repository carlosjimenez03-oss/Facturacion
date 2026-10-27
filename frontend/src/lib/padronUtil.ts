// Reglas de presentación del padrón de RNC de la DGII (sin dependencias:
// las prueba pruebas/padron.test.ts con el test runner de Node).

export interface EstadoVisual {
  /** Texto del badge ("Activo", "Suspendido"...). */
  label: string;
  /** Clase del badge (bd b-acep / b-pend / b-rech / b-borr). */
  cls: string;
  /** Aviso para el usuario si no está ACTIVO (null si está activo). */
  aviso: string | null;
}

const ALERTA = new Set(["SUSPENDIDO", "CESE TEMPORAL"]);
const BAJA = new Set(["DADO DE BAJA", "DE BAJA", "ANULADO", "RECHAZADO", "INACTIVO"]);

function capital(texto: string): string {
  const t = texto.toLowerCase();
  return t.charAt(0).toUpperCase() + t.slice(1);
}

/** Badge y aviso según el estado del contribuyente en el padrón. */
export function estadoPadron(estado: string | null | undefined): EstadoVisual {
  const e = (estado ?? "").trim().toUpperCase();
  if (e === "ACTIVO") return { label: "Activo", cls: "bd b-acep", aviso: null };
  if (!e) return { label: "Sin estado", cls: "bd b-borr", aviso: "El padrón de la DGII no indica el estado de este contribuyente." };
  const cls = ALERTA.has(e) ? "bd b-pend" : BAJA.has(e) ? "bd b-rech" : "bd b-borr";
  return {
    label: capital(e),
    cls,
    aviso: `Según el padrón de la DGII este contribuyente está ${e}: confirme sus datos antes de facturarle.`,
  };
}

/** "NORMAL" -> "Normal", "RST" -> "RST (Régimen Simplificado de Tributación)". */
export function textoRegimen(regimen: string | null | undefined): string | null {
  const r = (regimen ?? "").trim().toUpperCase();
  if (!r) return null;
  if (r === "RST") return "RST (Régimen Simplificado de Tributación)";
  return capital(r);
}

/** "2026-10-27T05:00:00Z" o "2026-10-27" -> "27/10/2026". */
export function fechaPadron(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const [a, m, d] = iso.slice(0, 10).split("-");
  return d ? `${d}/${m}/${a}` : null;
}

/** 9 dígitos (RNC) u 11 (cédula), sin guiones; null si no lo es. */
export function rncParaPadron(texto: string): string | null {
  const d = texto.replace(/\D/g, "");
  return d.length === 9 || d.length === 11 ? d : null;
}

/** Qué no trae el padrón (para decirlo en pantalla). */
export const NO_EN_PADRON = "La DGII no publica dirección, teléfono, correo ni provincia: complételos a mano.";
