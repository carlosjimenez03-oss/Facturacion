// Padrón de RNC de la DGII: contrato con services/erp_api/app/padron.py
// (docs/PADRON_RNC.md). Es el mismo listado para todas las empresas.
import { ERP_API_URL, ErpApiError } from "./erpApi";
import { fetchConSesion } from "./sesion";

export interface Contribuyente {
  rnc: string;
  razon_social: string;
  nombre_comercial: string | null;
  actividad: string | null;
  estado: string | null;
  regimen: string | null;
  /** Fecha de inicio de operaciones / constitución (YYYY-MM-DD). */
  fecha_inicio: string | null;
  activo: boolean;
  fuente: "padron";
  /** Cuándo se importó el padrón (de qué día son los datos). */
  padron_actualizado_en: string | null;
}

export interface EstadoPadron {
  importado_en: string | null;
  filas: number | null;
  origen: string | null;
  nuevas: number | null;
  cambiadas: number | null;
  eliminadas: number | null;
  duracion_segundos: number | null;
  intentado_en: string | null;
  error: string | null;
  error_en: string | null;
  en_curso: boolean;
  automatico: boolean;
  cada_horas: number;
  url: string;
  max_mb: number;
  /** Administrador de la instalación (el padrón es de todas las empresas). */
  puede_actualizar: boolean;
}

/** Resultado de una consulta: encontrado, no aparece (404) o el padrón no está listo (503). */
export type ResultadoPadron =
  | { tipo: "encontrado"; contribuyente: Contribuyente }
  | { tipo: "no_encontrado"; mensaje: string }
  | { tipo: "no_disponible"; mensaje: string };

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetchConSesion(`${ERP_API_URL}${path}`, init);
  if (!r.ok) {
    let detalle = r.statusText;
    try {
      const cuerpo = await r.json();
      detalle = typeof cuerpo.detail === "string" ? cuerpo.detail : JSON.stringify(cuerpo.detail ?? cuerpo);
    } catch {
      // sin cuerpo JSON
    }
    throw new ErpApiError(r.status, detalle);
  }
  return r.json() as Promise<T>;
}

export const padronApi = {
  /** Busca un RNC (9) o cédula (11). Solo lanza si erp-api no responde o el número es inválido. */
  async consultar(rnc: string): Promise<ResultadoPadron> {
    try {
      const contribuyente = await request<Contribuyente>(`/padron/${encodeURIComponent(rnc.replace(/\D/g, ""))}`);
      return { tipo: "encontrado", contribuyente };
    } catch (err) {
      if (err instanceof ErpApiError && err.status === 404) return { tipo: "no_encontrado", mensaje: err.message };
      if (err instanceof ErpApiError && err.status === 503) return { tipo: "no_disponible", mensaje: err.message };
      throw err;
    }
  },
  estado: () => request<EstadoPadron>("/padron/estado"),
  actualizar: () => request<EstadoPadron>("/padron/actualizar", { method: "POST" }),
  /** Sube el DGII_RNC.zip tal cual (binario, sin multipart). */
  subir: (archivo: File) =>
    request<EstadoPadron>("/padron/archivo", {
      method: "POST",
      body: archivo,
      headers: { "Content-Type": "application/zip", "X-Nombre-Archivo": encodeURIComponent(archivo.name) },
    }),
};
