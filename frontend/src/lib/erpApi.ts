// Cliente del servicio `erp-api` (clientes, cotizaciones, cobros y cuentas
// por cobrar, resúmenes de documentos de venta). Este archivo es el
// CONTRATO entre el backend (services/erp_api) y las pantallas: los tipos y
// rutas de aquí deben coincidir 1:1 con services/erp_api/app/schemas.py y
// main.py. Montos y cantidades viajan como string decimal ("1250.00"),
// fechas como "YYYY-MM-DD" y marcas de tiempo como ISO 8601.

import { fetchConSesion } from "./sesion";

export const ERP_API_URL = (import.meta.env.VITE_ERP_API_URL as string | undefined) ?? "http://localhost:8019";

export class ErpApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetchConSesion(`${ERP_API_URL}${path}`, {
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
    throw new ErpApiError(response.status, typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return response.json() as Promise<T>;
}

function consulta(params: Record<string, string | number | boolean | undefined | null>): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") q.set(k, String(v));
  }
  const s = q.toString();
  return s ? `?${s}` : "";
}

// --- Clientes / contactos ------------------------------------------------

export type TipoContacto = "cliente" | "proveedor" | "ambos";
export type TipoIdentificacion = "rnc" | "cedula" | "pasaporte";

export interface Cliente {
  id: string;
  tipo: TipoContacto;
  tipo_identificacion: TipoIdentificacion | null;
  /** Solo dígitos (9 = RNC, 11 = cédula); null para consumidores sin identificar o extranjeros. */
  rnc: string | null;
  nombre: string;
  correo: string | null;
  telefono: string | null;
  direccion: string | null;
  /** Días de crédito por defecto al facturarle (0 = contado). */
  condicion_pago_dias: number;
  limite_credito: string | null;
  notas: string | null;
  activo: boolean;
  /** Suma de lo pendiente de cobro en sus facturas a crédito (calculado). */
  saldo: string;
  creado_en: string;
  actualizado_en: string;
}

export interface ClientePayload {
  tipo: TipoContacto;
  tipo_identificacion?: TipoIdentificacion | null;
  rnc?: string | null;
  nombre: string;
  correo?: string | null;
  telefono?: string | null;
  direccion?: string | null;
  condicion_pago_dias?: number;
  limite_credito?: string | null;
  notas?: string | null;
  activo?: boolean;
}

// --- Cotizaciones ---------------------------------------------------------

/** "vencida" no se guarda: el backend la devuelve cuando una cotización abierta pasó su fecha `vence`. */
export type EstadoCotizacion = "abierta" | "aceptada" | "rechazada" | "vencida" | "facturada";

/** Tasa de ITBIS de una línea. Equivale a IndicadorFacturacion 1 / 2 / 3 / 4 del e-CF. */
export type TasaItbis = "18" | "16" | "0" | "exento";

export interface CotizacionItem {
  producto_id: string | null;
  descripcion: string;
  cantidad: string;
  precio_unitario: string;
  /** Porcentaje 0-100. */
  descuento_pct: string;
  itbis: TasaItbis;
  /** Calculado por el backend: cantidad * precio - descuento. */
  importe: string;
}

export interface CotizacionItemPayload {
  producto_id?: string | null;
  descripcion: string;
  cantidad: string;
  precio_unitario: string;
  descuento_pct?: string;
  itbis: TasaItbis;
}

export interface CotizacionResumen {
  id: string;
  /** Correlativo legible: "COT-0001". */
  numero: string;
  cliente_id: string | null;
  cliente_nombre: string;
  cliente_rnc: string | null;
  fecha: string;
  vence: string;
  total: string;
  estado: EstadoCotizacion;
  factura_id: string | null;
  creado_en: string;
}

export interface Cotizacion extends CotizacionResumen {
  items: CotizacionItem[];
  notas: string | null;
  subtotal: string;
  descuentos: string;
  itbis_total: string;
  actualizado_en: string;
}

export interface CotizacionPayload {
  cliente_id?: string | null;
  /** Obligatorio si no hay cliente_id. */
  cliente_nombre?: string | null;
  cliente_rnc?: string | null;
  fecha?: string;
  /** Por defecto fecha + 15 días. */
  vence?: string;
  items: CotizacionItemPayload[];
  notas?: string | null;
}

// --- Cuentas por cobrar y cobros -----------------------------------------

export type EstadoCuentaCobrar = "pendiente" | "vencida" | "cobrada";
export type MetodoCobro = "efectivo" | "transferencia" | "tarjeta" | "cheque";

/** Una factura a crédito (TipoPago 2) enviada/aceptada, con lo cobrado y lo pendiente
 * (total − notas de crédito que la referencian − cobros). */
export interface CuentaPorCobrar {
  factura_id: string;
  encf: string | null;
  tipo_ecf: number;
  cliente_rnc: string | null;
  cliente_nombre: string;
  fecha_emision: string;
  vence: string;
  total: string;
  cobrado: string;
  pendiente: string;
  estado: EstadoCuentaCobrar;
  /** Días desde el vencimiento (0 si no está vencida). */
  dias_vencida: number;
  /** Teléfono del contacto con ese RNC, si está registrado. */
  cliente_telefono: string | null;
}

export interface Cobro {
  id: string;
  factura_id: string;
  encf: string | null;
  cliente_nombre: string;
  fecha: string;
  monto: string;
  metodo: MetodoCobro;
  cuenta_destino: string | null;
  referencia: string | null;
  notas: string | null;
  anulado: boolean;
  creado_en: string;
}

export interface CobroPayload {
  factura_id: string;
  fecha?: string;
  monto: string;
  metodo: MetodoCobro;
  cuenta_destino?: string | null;
  referencia?: string | null;
  notas?: string | null;
}

// --- Resumen de documentos de venta (lee facturacion.facturas) -----------

/** Fila de listado con los datos del e-CF que el listado de facturas_api no trae (cliente, total, referencia). */
export interface DocumentoVenta {
  id: string;
  tipo_ecf: number;
  encf: string | null;
  estado: string;
  cliente_rnc: string | null;
  cliente_nombre: string | null;
  fecha_emision: string | null;
  total: string;
  /** Solo notas de crédito/débito: e-NCF que modifican y código de modificación. */
  encf_afectado: string | null;
  codigo_modificacion: string | null;
  dgii_codigo: string | null;
  error: string | null;
  creado_en: string;
}

export const erpApi = {
  // Clientes
  listarClientes: (p?: { q?: string; tipo?: TipoContacto; activo?: boolean }) =>
    request<Cliente[]>(`/clientes${consulta({ ...p })}`),
  obtenerCliente: (id: string) => request<Cliente>(`/clientes/${id}`),
  /** 404 si no existe ningún contacto con ese RNC/cédula. */
  clientePorRnc: (rnc: string) =>
    request<Cliente>(`/clientes/por-rnc/${encodeURIComponent(rnc.replace(/[^0-9A-Za-z]/g, "").toUpperCase())}`),
  crearCliente: (payload: ClientePayload) =>
    request<Cliente>("/clientes", { method: "POST", body: JSON.stringify(payload) }),
  actualizarCliente: (id: string, payload: ClientePayload) =>
    request<Cliente>(`/clientes/${id}`, { method: "PUT", body: JSON.stringify(payload) }),
  /** 409 si el cliente tiene cotizaciones; en ese caso desactívelo con activo=false. */
  eliminarCliente: (id: string) => request<void>(`/clientes/${id}`, { method: "DELETE" }),

  // Cotizaciones
  listarCotizaciones: (p?: { q?: string; estado?: EstadoCotizacion }) =>
    request<CotizacionResumen[]>(`/cotizaciones${consulta({ ...p })}`),
  obtenerCotizacion: (id: string) => request<Cotizacion>(`/cotizaciones/${id}`),
  crearCotizacion: (payload: CotizacionPayload) =>
    request<Cotizacion>("/cotizaciones", { method: "POST", body: JSON.stringify(payload) }),
  /** Solo cotizaciones abiertas (409 en otro estado). */
  actualizarCotizacion: (id: string, payload: CotizacionPayload) =>
    request<Cotizacion>(`/cotizaciones/${id}`, { method: "PUT", body: JSON.stringify(payload) }),
  cambiarEstadoCotizacion: (id: string, estado: "abierta" | "aceptada" | "rechazada") =>
    request<Cotizacion>(`/cotizaciones/${id}/estado`, { method: "POST", body: JSON.stringify({ estado }) }),
  /** La marca como facturada y la vincula a la factura creada a partir de ella. */
  marcarCotizacionFacturada: (id: string, factura_id: string) =>
    request<Cotizacion>(`/cotizaciones/${id}/facturada`, { method: "POST", body: JSON.stringify({ factura_id }) }),
  /** Solo cotizaciones abiertas (409 en otro estado). */
  eliminarCotizacion: (id: string) => request<void>(`/cotizaciones/${id}`, { method: "DELETE" }),

  // Cuentas por cobrar y cobros
  listarCuentasPorCobrar: (p?: { estado?: EstadoCuentaCobrar | "todas"; cliente_rnc?: string }) =>
    request<CuentaPorCobrar[]>(`/cuentas-por-cobrar${consulta({ ...p })}`),
  listarCobros: (p?: { factura_id?: string }) => request<Cobro[]>(`/cobros${consulta({ ...p })}`),
  /** 422 si el monto supera lo pendiente o la factura no es a crédito. */
  registrarCobro: (payload: CobroPayload) =>
    request<Cobro>("/cobros", { method: "POST", body: JSON.stringify(payload) }),
  anularCobro: (id: string) => request<Cobro>(`/cobros/${id}/anular`, { method: "POST" }),

  // Documentos de venta
  listarDocumentosVenta: (p?: { tipo_ecf?: number; estado?: string; q?: string }) =>
    request<DocumentoVenta[]>(`/documentos-venta${consulta({ ...p })}`),
};
