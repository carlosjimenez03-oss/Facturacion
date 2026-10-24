import { fetchConSesion } from "./sesion";
import type {
  EnviarResponse,
  EstadoContingenciaApi,
  ReintentoContingencia,
  FacturaDatos,
  FacturaDetalle,
  FacturaResumen,
  GenerarXmlResponse,
  ItemInventarioLink,
} from "./types";

export const API_URL = (import.meta.env.VITE_API_URL as string | undefined) ?? "http://localhost:8011";

class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetchConSesion(`${API_URL}${path}`, {
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
    throw new ApiError(response.status, typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return response.json() as Promise<T>;
}

export const api = {
  listar: (params?: { tipo_ecf?: number; estado?: string }) => {
    const query = new URLSearchParams();
    if (params?.tipo_ecf) query.set("tipo_ecf", String(params.tipo_ecf));
    if (params?.estado) query.set("estado", params.estado);
    const qs = query.toString();
    return request<FacturaResumen[]>(`/facturas${qs ? `?${qs}` : ""}`);
  },

  obtener: (id: string) => request<FacturaDetalle>(`/facturas/${id}`),

  /** `asignar_encf` (o `encf` null): el servidor toma el siguiente e-NCF de la secuencia de Configuración, sin duplicados (409 si está agotada, vencida o desactivada). */
  crear: (payload: {
    tipo_ecf: number;
    encf: string | null;
    asignar_encf?: boolean;
    datos: FacturaDatos;
    items_inventario?: ItemInventarioLink[];
    vendedor_id?: string | null;
  }) =>
    request<FacturaDetalle>("/facturas", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  actualizar: (
    id: string,
    payload: { encf: string | null; datos: FacturaDatos; items_inventario?: ItemInventarioLink[]; vendedor_id?: string | null },
  ) =>
    request<FacturaDetalle>(`/facturas/${id}`, {
      method: "PUT",
      body: JSON.stringify(payload),
    }),

  eliminar: (id: string) => request<void>(`/facturas/${id}`, { method: "DELETE" }),

  generarXml: (id: string) => request<GenerarXmlResponse>(`/facturas/${id}/generar-xml`, { method: "POST" }),

  firmar: (id: string) => request<FacturaDetalle>(`/facturas/${id}/firmar`, { method: "POST" }),

  enviar: (id: string) => request<EnviarResponse>(`/facturas/${id}/enviar`, { method: "POST" }),

  /** Consulta a la DGII (por trackId) el resultado de una factura enviada y lo aplica. */
  actualizarEstado: (id: string) =>
    request<FacturaDetalle>(`/facturas/${id}/actualizar-estado`, { method: "POST" }),

  representacionImpresaUrl: (id: string) => `${API_URL}/facturas/${id}/representacion-impresa`,

  /** Modo contingencia: estado, pendientes de envío y últimos lotes. */
  contingencia: () => request<EstadoContingenciaApi>("/contingencia"),

  /** "Reintentar conexión ahora": sonda corta y, si la DGII responde, lanza el envío por lotes. */
  reintentarContingencia: () => request<ReintentoContingencia>("/contingencia/reintentar", { method: "POST" }),
};

export { ApiError };
