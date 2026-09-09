import type {
  ErrorValidacion606,
  PeriodoResumen606,
  Registro606,
  ValidacionResponse606,
} from "./types";

export const FORMATO_606_API_URL =
  (import.meta.env.VITE_FORMATO_606_API_URL as string | undefined) ?? "http://localhost:8016";

class Formato606ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${FORMATO_606_API_URL}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = body.detail ?? JSON.stringify(body);
    } catch {
      // sin cuerpo JSON, se mantiene statusText
    }
    throw new Formato606ApiError(response.status, typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return response.json() as Promise<T>;
}

// Catálogos de la DGII -- ver docs/dgii/FORMATO_606.md, sección "Catálogos".

export const TIPOS_BIENES_SERVICIOS: Record<number, string> = {
  1: "01 - Gastos de personal",
  2: "02 - Gastos por trabajos, suministros y servicios",
  3: "03 - Arrendamientos",
  4: "04 - Gastos de activos fijos",
  5: "05 - Gastos de representación",
  6: "06 - Otras deducciones admitidas",
  7: "07 - Gastos financieros",
  8: "08 - Gastos extraordinarios",
  9: "09 - Compras y gastos que formarán parte del costo de venta",
  10: "10 - Adquisiciones de activos",
  11: "11 - Gastos de seguros",
};

export const TIPOS_RETENCION_ISR: Record<number, string> = {
  1: "01 - Alquileres",
  2: "02 - Honorarios por servicios",
  3: "03 - Otras rentas",
  4: "04 - Otras rentas (rentas presuntas)",
  5: "05 - Intereses pagados a personas jurídicas residentes",
  6: "06 - Intereses pagados a personas físicas residentes",
  7: "07 - Retención por proveedores del Estado",
  8: "08 - Juegos telefónicos",
  9: "09 - Retenciones subsector de ganadería de carne bovina",
};

export const FORMAS_PAGO: Record<number, string> = {
  1: "01 - Efectivo",
  2: "02 - Cheques/Transferencias/Depósito",
  3: "03 - Tarjeta crédito/débito",
  4: "04 - Compra a crédito",
  5: "05 - Permuta",
  6: "06 - Notas de crédito",
  7: "07 - Mixto",
};

export interface RegistroClasificarPayload {
  tipo_bienes_servicios?: number | null;
  fecha_pago?: string | null;
  itbis_retenido?: string;
  itbis_proporcionalidad?: string;
  itbis_costo?: string;
  itbis_percibido?: string;
  tipo_retencion_isr?: number | null;
  monto_retencion_renta?: string;
  isr_percibido?: string;
  impuesto_selectivo_consumo?: string;
  otros_impuestos?: string;
  monto_propina_legal?: string;
  forma_pago?: number | null;
}

export interface RegistroCrearManualPayload extends RegistroClasificarPayload {
  rnc_cedula: string;
  ncf: string;
  ncf_modificado?: string | null;
  fecha_comprobante: string;
  monto_servicios?: string;
  monto_bienes?: string;
  itbis_facturado?: string;
}

export const formato606Api = {
  listarPeriodos: () => request<PeriodoResumen606[]>("/periodos"),

  listarRegistros: (periodo: string) =>
    request<Registro606[]>(`/registros?${new URLSearchParams({ periodo }).toString()}`),

  obtenerRegistro: (id: string) => request<Registro606>(`/registros/${id}`),

  crearRegistroManual: (payload: RegistroCrearManualPayload) =>
    request<Registro606>("/registros", { method: "POST", body: JSON.stringify(payload) }),

  clasificarRegistro: (id: string, payload: RegistroClasificarPayload) =>
    request<Registro606>(`/registros/${id}`, { method: "PUT", body: JSON.stringify(payload) }),

  eliminarRegistro: (id: string) => request<void>(`/registros/${id}`, { method: "DELETE" }),

  validarPeriodo: (periodo: string) =>
    request<ValidacionResponse606>(`/periodos/${periodo}/validar`, { method: "POST" }),

  async generarArchivo(periodo: string): Promise<{ nombreArchivo: string; contenido: Blob }> {
    const response = await fetch(`${FORMATO_606_API_URL}/periodos/${periodo}/generar`);
    if (!response.ok) {
      let detail = response.statusText;
      try {
        const body = await response.json();
        detail = body.detail ?? JSON.stringify(body);
      } catch {
        // sin cuerpo JSON, se mantiene statusText
      }
      throw new Formato606ApiError(response.status, typeof detail === "string" ? detail : JSON.stringify(detail));
    }
    const disposition = response.headers.get("Content-Disposition") ?? "";
    const match = /filename="?([^"]+)"?/.exec(disposition);
    const nombreArchivo = match?.[1] ?? `DGII_F_606_${periodo}.TXT`;
    return { nombreArchivo, contenido: await response.blob() };
  },
};

export type { ErrorValidacion606 };
export { Formato606ApiError };
