// Cliente de los pagos con tarjeta en línea en `erp-api` (fase 7). Este
// archivo es el CONTRATO con services/erp_api/app/pagos.py y pagos_schemas.py
// (mismos nombres y tipos). Montos como string decimal ("1250.00") y marcas
// de tiempo ISO 8601. Ver docs/PAGOS.md.
import { ERP_API_URL, ErpApiError } from "./erpApi";
import { fetchConSesion } from "./sesion";

/** Rutas que el cliente abre sin sesión desde su portal (nginx no las protege). */
const RUTA_PUBLICA = "/portal/publico/";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  // El portal público sale con `fetch` sin token: un 401 ahí nunca cierra la sesión de nadie.
  const enviar = path.startsWith(RUTA_PUBLICA) ? fetch : fetchConSesion;
  const response = await enviar(`${ERP_API_URL}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = body.detail ?? JSON.stringify(body);
    } catch {
      // sin cuerpo JSON, se mantiene statusText
    }
    throw new ErpApiError(response.status, typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return response.json() as Promise<T>;
}

export type ClaveProveedor = "azul" | "simulado";
export type AmbientePago = "pruebas" | "produccion";
export type EstadoPagoEnLinea = "pendiente" | "aprobado" | "declinado" | "cancelado" | "expirado";

export interface ConfigPagos {
  activo: boolean;
  proveedor: ClaveProveedor;
  ambiente: AmbientePago;
  merchant_id: string;
  merchant_name: string;
  merchant_type: string;
  permitir_parcial: boolean;
  cuenta_bancaria_id: string | null;
  /** El AuthKey nunca llega aquí: solo si está configurada y de dónde. */
  auth_key_configurada: boolean;
  auth_key_origen: "variable" | "guardada" | null;
  proveedores: { clave: ClaveProveedor; nombre: string }[];
  listo: boolean;
  faltantes: string[];
  url_retorno: string | null;
  cifrado_disponible: boolean;
  puede_editar: boolean;
  produccion: boolean;
}

export interface ConfigPagosPayload {
  activo: boolean;
  proveedor: ClaveProveedor;
  ambiente: AmbientePago;
  merchant_id: string;
  merchant_name: string;
  merchant_type: string;
  permitir_parcial: boolean;
  cuenta_bancaria_id: string | null;
  /** Solo de escritura; null/"" conserva la guardada. */
  auth_key?: string | null;
  borrar_auth_key?: boolean;
}

/** Lo que trae el portal público cuando se puede pagar con tarjeta. */
export interface PagoEnLineaPortal {
  proveedor: ClaveProveedor;
  nombre: string;
  pruebas: boolean;
  permitir_parcial: boolean;
}

export interface PagoIniciado {
  referencia: string;
  /** URL de la página de pago del banco (el navegador le publica `campos`). */
  accion: string;
  metodo: "POST";
  campos: Record<string, string>;
  monto: string;
  itbis: string;
  encf: string | null;
  expira_en: string;
  proveedor: ClaveProveedor;
}

export interface PagoPublico {
  referencia: string;
  estado: EstadoPagoEnLinea;
  monto: string;
  encf: string | null;
  empresa: string;
  proveedor: string;
  pruebas: boolean;
  codigo_autorizacion: string | null;
  mensaje: string | null;
  cobro_registrado: boolean;
  requiere_revision: boolean;
  respondido_en: string | null;
  creado_en: string;
}

export interface PagoEnLinea {
  id: string;
  referencia: string;
  orden: string;
  proveedor: ClaveProveedor;
  ambiente: AmbientePago;
  factura_id: string;
  encf: string | null;
  cliente_rnc: string | null;
  cliente_nombre: string | null;
  monto: string;
  itbis: string;
  estado: EstadoPagoEnLinea;
  expira_en: string;
  codigo_autorizacion: string | null;
  rrn: string | null;
  orden_proveedor: string | null;
  iso_code: string | null;
  respuesta: string | null;
  error_descripcion: string | null;
  cobro_id: string | null;
  cobro_anulado: boolean | null;
  incidencia: string | null;
  respondido_en: string | null;
  creado_en: string;
}

export const ESTADO_PAGO_EN_LINEA: Record<EstadoPagoEnLinea, { label: string; badge: string }> = {
  pendiente: { label: "Pendiente", badge: "bd b-proc" },
  aprobado: { label: "Aprobado", badge: "bd b-acep" },
  declinado: { label: "Declinado", badge: "bd b-rech" },
  cancelado: { label: "Cancelado", badge: "bd b-borr" },
  expirado: { label: "Expirado", badge: "bd b-borr" },
};

const enc = encodeURIComponent;

export const pagosApi = {
  config: () => request<ConfigPagos>("/configuracion/pagos-en-linea"),
  guardarConfig: (payload: ConfigPagosPayload) =>
    request<ConfigPagos>("/configuracion/pagos-en-linea", { method: "PUT", body: JSON.stringify(payload) }),
  listar: (params: { factura_id?: string; estado?: EstadoPagoEnLinea } = {}) => {
    const q = new URLSearchParams(Object.entries(params).filter(([, v]) => v) as [string, string][]).toString();
    return request<PagoEnLinea[]>(`/pagos-en-linea${q ? `?${q}` : ""}`);
  },
  // Portal público (sin sesión)
  iniciar: (token: string, facturaId: string, monto: string | null) =>
    request<PagoIniciado>(`/portal/publico/${enc(token)}/pagos`, {
      method: "POST",
      body: JSON.stringify({ factura_id: facturaId, monto }),
    }),
  estado: (referencia: string) => request<PagoPublico>(`/portal/publico/pagos/${enc(referencia)}`),
};

/** Lleva al cliente a la página de pago del banco: el NAVEGADOR publica el
 * formulario firmado (Kontara nunca ve la tarjeta). */
export function enviarAPasarela(pago: PagoIniciado): void {
  const form = document.createElement("form");
  form.method = "POST";
  form.action = pago.accion;
  form.style.display = "none";
  for (const [nombre, valor] of Object.entries(pago.campos)) {
    const input = document.createElement("input");
    input.type = "hidden";
    input.name = nombre;
    input.value = valor;
    form.appendChild(input);
  }
  document.body.appendChild(form);
  form.submit();
}

const CLAVE_VUELTA = "kontara.pago.portal.";

/** Recuerda (solo en esta pestaña) a qué portal volver desde la página del resultado. */
export function recordarPortal(referencia: string, token: string): void {
  try {
    sessionStorage.setItem(CLAVE_VUELTA + referencia, token);
  } catch {
    // sin sessionStorage: la página del resultado no ofrece volver al portal
  }
}

export function portalDeVuelta(referencia: string): string | null {
  try {
    return sessionStorage.getItem(CLAVE_VUELTA + referencia);
  } catch {
    return null;
  }
}
