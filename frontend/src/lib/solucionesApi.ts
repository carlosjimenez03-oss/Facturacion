// Cliente de Soluciones en `erp-api` (fase 6): planes y suscripción, portal de
// clientes, Kontara Ventas y POS. Este archivo es el CONTRATO con
// services/erp_api/app/soluciones.py y soluciones_schemas.py (mismos nombres
// y tipos). Montos como string decimal ("1250.00"), fechas "YYYY-MM-DD" y
// marcas de tiempo ISO 8601.
import { useEffect, useState } from "react";
import { ERP_API_URL, ErpApiError } from "./erpApi";
import { fetchConSesion } from "./sesion";

/** Rutas del portal que el cliente abre sin sesión (nginx no las protege). */
const RUTA_PUBLICA = "/portal/publico/";

/** Único punto de salida HTTP de este cliente: todo va con la sesión
 * (`fetchConSesion`) salvo el portal público, que sale con `fetch` sin
 * token: así un 401 ahí nunca cierra la sesión de quien lo abra. */
async function request<T>(path: string, init?: RequestInit): Promise<T> {
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

const json = (body: unknown) => JSON.stringify(body);

// --- Planes y suscripción ------------------------------------------------

export type PlanClave = "emprendedor" | "pyme" | "empresarial";
export type Periodicidad = "mensual" | "anual";

export interface Plan {
  clave: PlanClave;
  nombre: string;
  descripcion: string;
  precio_mensual: string;
  precio_anual: string;
  /** null = ilimitados. */
  ecf_mes: number | null;
  usuarios: number | null;
  almacenamiento_bytes: number;
  popular: boolean;
  caracteristicas: string[];
}

export interface SolicitudPlan {
  id: string;
  plan: PlanClave;
  periodicidad: Periodicidad;
  forma_pago: "transferencia" | "tarjeta";
  subtotal: string;
  itbis: string;
  total: string;
  contacto_nombre: string | null;
  contacto_correo: string | null;
  contacto_telefono: string | null;
  notas: string | null;
  estado: "pendiente" | "atendida" | "cancelada";
  creado_en: string;
}

export interface Suscripcion {
  plan: PlanClave | null;
  plan_nombre: string | null;
  periodicidad: Periodicidad | null;
  estado: "prueba" | "activa" | "prueba_vencida";
  prueba_inicio: string;
  prueba_fin: string;
  /** Días que quedan de prueba (null con un plan activo). */
  dias_restantes: number | null;
  proxima_factura: string | null;
  /** Plan cuyos límites aplican (en prueba: Pyme). */
  plan_limites: PlanClave;
  solicitud_pendiente: SolicitudPlan | null;
}

export interface Consumo {
  mes: string;
  plan_limites: PlanClave;
  ecf_emitidos: number;
  ecf_limite: number | null;
  ecf_porcentaje: number | null;
  /** null = todavía no hay tabla de usuarios de la plataforma. */
  usuarios: number | null;
  usuarios_limite: number | null;
  usuarios_porcentaje: number | null;
  almacenamiento_bytes: number;
  almacenamiento_limite_bytes: number;
  almacenamiento_texto: string;
  almacenamiento_limite_texto: string;
  almacenamiento_porcentaje: number | null;
}

export interface SolicitudPlanPayload {
  plan: PlanClave;
  periodicidad: Periodicidad;
  forma_pago: "transferencia" | "tarjeta";
  contacto_nombre?: string | null;
  contacto_correo?: string | null;
  contacto_telefono?: string | null;
  notas?: string | null;
}

export interface Canales {
  correo: boolean;
  whatsapp: boolean;
}

// --- Portal de clientes ------------------------------------------------

export type MetodoPagoPortal = "tarjeta" | "transferencia" | "pago_movil" | "deposito";

export interface EnlacePortal {
  id: string;
  cliente_id: string;
  cliente_nombre: string;
  cliente_rnc: string | null;
  cliente_correo: string | null;
  cliente_telefono: string | null;
  metodos_pago: MetodoPagoPortal[];
  expira_en: string;
  revocado_en: string | null;
  ultimo_acceso_en: string | null;
  accesos: number;
  estado: "activo" | "vencido" | "revocado";
  creado_en: string;
  /** Solo al crearlo: el token no se guarda en claro. */
  token: string | null;
}

export interface ConfigPortal {
  banco: string | null;
  tipo_cuenta: string | null;
  numero_cuenta: string | null;
  titular: string | null;
  rnc_titular: string | null;
  instrucciones: string | null;
  actualizado_en?: string | null;
}

export type EstadoPagoPortal = "contado" | "pagada" | "pendiente" | "vencida" | "nota_credito";

export interface FacturaPortal {
  id: string;
  encf: string | null;
  tipo_ecf: number;
  fecha_emision: string;
  total: string;
  pendiente: string;
  vence: string | null;
  estado_pago: EstadoPagoPortal;
}

export interface PortalPublico {
  empresa: string;
  cliente_nombre: string;
  cliente_rnc: string | null;
  expira_en: string;
  metodos_pago: MetodoPagoPortal[];
  transferencia: Omit<ConfigPortal, "actualizado_en"> | null;
  saldo_pendiente: string;
  facturas: FacturaPortal[];
}

// --- Kontara Ventas ------------------------------------------------

export type ClaveAutomatizacion = "motivar" | "cobranza" | "fidelizacion" | "reactivacion";
export type CanalEnvio = "whatsapp" | "correo" | "ambos";
export type EstadoEnvio = "pendiente_canal" | "enviado" | "enviado_manual" | "error" | "descartado";

export interface Automatizacion {
  clave: ClaveAutomatizacion;
  categoria: string;
  titulo: string;
  descripcion: string;
  variables: string[];
  activa: boolean;
  canal: CanalEnvio;
  dias: number;
  asunto: string;
  plantilla: string;
  activada_en: string | null;
  /** Envíos por estado, p. ej. {"pendiente_canal": 3}. */
  envios: Partial<Record<EstadoEnvio, number>>;
}

export interface ResultadoEjecucion {
  creados: number;
  omitidos_sin_contacto: number;
  por_automatizacion: Partial<Record<ClaveAutomatizacion, number>>;
  motivo: string | null;
  entrega: { enviados: number; errores: number } | null;
}

export interface EstadoVentas {
  activo: boolean;
  activado_en: string | null;
  prueba_hasta: string | null;
  /** Activo y en prueba (o con plan Pyme/Empresarial). */
  habilitado: boolean;
  ultima_ejecucion: string | null;
  ultimo_resultado: ResultadoEjecucion | null;
  automatizaciones: Automatizacion[];
  canales: Canales | null;
}

export interface AutomatizacionPayload {
  activa?: boolean;
  canal?: CanalEnvio;
  dias?: number;
  asunto?: string;
  plantilla?: string;
}

export interface Envio {
  id: string;
  automatizacion: ClaveAutomatizacion;
  canal: "whatsapp" | "correo";
  cliente_id: string | null;
  cliente_nombre: string;
  /** Teléfono internacional (WhatsApp) o correo. */
  destino: string;
  factura_id: string | null;
  encf: string | null;
  asunto: string;
  mensaje: string;
  estado: EstadoEnvio;
  error: string | null;
  intentos: number;
  enviado_en: string | null;
  creado_en: string;
}

// --- POS ------------------------------------------------

export type MetodoPos = "efectivo" | "tarjeta" | "transferencia";

export interface PagoPos {
  metodo: MetodoPos;
  monto: string;
  referencia?: string | null;
}

export interface VentaPos {
  id: string;
  sesion_id: string;
  factura_id: string;
  encf: string | null;
  tipo_ecf: number;
  cliente_rnc: string | null;
  cliente_nombre: string | null;
  total: string;
  pagos: PagoPos[];
  recibido: string;
  cambio: string;
  creado_en: string;
}

export type MontosPorMetodo = Record<MetodoPos, string>;

export interface SesionCaja {
  id: string;
  caja: string;
  almacen_id: string | null;
  cajero: string | null;
  fondo_inicial: string;
  abierta_en: string;
  cerrada_en: string | null;
  estado: "abierta" | "cerrada";
  esperado: MontosPorMetodo | null;
  contado: MontosPorMetodo | null;
  diferencias: MontosPorMetodo | null;
  notas_cierre: string | null;
  cantidad_ventas: number;
  total_ventas: string;
  ventas: VentaPos[];
}

export interface Timbre {
  encf: string | null;
  codigo_seguridad: string | null;
  fecha_firma: string | null;
  url_qr: string | null;
  qr_svg: string | null;
}

export const CAJA_PRINCIPAL = "Caja principal";

export const solucionesApi = {
  canales: () => request<Canales>("/soluciones/canales"),

  // Planes
  listarPlanes: () => request<Plan[]>("/planes"),
  suscripcion: () => request<Suscripcion>("/suscripcion"),
  consumo: () => request<Consumo>("/suscripcion/consumo"),
  /** No cobra: registra la solicitud y devuelve qué sigue (pago por transferencia con un asesor). */
  solicitarPlan: (payload: SolicitudPlanPayload) =>
    request<{ solicitud: SolicitudPlan; instrucciones: string[] }>("/suscripcion/solicitudes", {
      method: "POST",
      body: json(payload),
    }),

  // Portal de clientes
  listarEnlaces: (params?: { cliente_id?: string }) => request<EnlacePortal[]>(`/portal/enlaces${consulta({ ...params })}`),
  crearEnlace: (payload: { cliente_id: string; metodos_pago: MetodoPagoPortal[]; dias_validez?: number }) =>
    request<EnlacePortal>("/portal/enlaces", { method: "POST", body: json(payload) }),
  revocarEnlace: (id: string) => request<EnlacePortal>(`/portal/enlaces/${id}/revocar`, { method: "POST" }),
  configPortal: () => request<ConfigPortal>("/portal/config"),
  guardarConfigPortal: (payload: ConfigPortal) => request<ConfigPortal>("/portal/config", { method: "PUT", body: json(payload) }),
  /** 409 si no hay SMTP configurado. */
  enviarCorreo: (payload: { para: string; asunto: string; mensaje: string }) =>
    request<{ enviado: boolean }>("/portal/enviar-correo", { method: "POST", body: json(payload) }),

  // Portal público (sin sesión)
  /** `vistaPrevia`: lo abre el usuario desde la pantalla del portal (no cuenta como acceso del cliente). */
  portalPublico: (token: string, vistaPrevia = false) =>
    request<PortalPublico>(`/portal/publico/${encodeURIComponent(token)}${vistaPrevia ? "?vista_previa=true" : ""}`),
  portalPdfUrl: (token: string, facturaId: string) => `${ERP_API_URL}/portal/publico/${encodeURIComponent(token)}/facturas/${facturaId}/pdf`,
  portalXmlUrl: (token: string, facturaId: string) => `${ERP_API_URL}/portal/publico/${encodeURIComponent(token)}/facturas/${facturaId}/xml`,

  // Kontara Ventas
  estadoVentas: () => request<EstadoVentas>("/ventas-auto"),
  activarVentas: (activo: boolean) => request<EstadoVentas>("/ventas-auto/activar", { method: "POST", body: json({ activo }) }),
  actualizarAutomatizacion: (clave: ClaveAutomatizacion, payload: AutomatizacionPayload) =>
    request<Automatizacion>(`/ventas-auto/automatizaciones/${clave}`, { method: "PUT", body: json(payload) }),
  ejecutarAutomatizaciones: () => request<ResultadoEjecucion>("/ventas-auto/ejecutar", { method: "POST" }),
  listarEnvios: (params?: { automatizacion?: ClaveAutomatizacion; estado?: EstadoEnvio; limit?: number }) =>
    request<Envio[]>(`/ventas-auto/envios${consulta({ ...params })}`),
  marcarEnvio: (id: string, estado: "enviado_manual" | "descartado" | "pendiente_canal") =>
    request<Envio>(`/ventas-auto/envios/${id}/marcar`, { method: "POST", body: json({ estado }) }),

  // POS
  sesionAbierta: (caja = CAJA_PRINCIPAL) => request<SesionCaja | null>(`/pos/sesiones/abierta${consulta({ caja })}`),
  listarSesiones: (limit = 30) => request<SesionCaja[]>(`/pos/sesiones${consulta({ limit })}`),
  obtenerSesion: (id: string) => request<SesionCaja>(`/pos/sesiones/${id}`),
  /** 409 si la caja ya tiene una sesión abierta. */
  abrirSesion: (payload: { caja?: string; fondo_inicial: string; cajero?: string | null; almacen_id?: string | null }) =>
    request<SesionCaja>("/pos/sesiones", { method: "POST", body: json(payload) }),
  /** Registra en la caja un e-CF ya enviado a la DGII; el total lo toma del e-CF guardado. */
  registrarVenta: (sesionId: string, payload: { factura_id: string; pagos: PagoPos[] }) =>
    request<VentaPos>(`/pos/sesiones/${sesionId}/ventas`, { method: "POST", body: json(payload) }),
  cerrarSesion: (sesionId: string, payload: { contado: MontosPorMetodo; notas?: string | null }) =>
    request<SesionCaja>(`/pos/sesiones/${sesionId}/cerrar`, { method: "POST", body: json(payload) }),
  timbre: (facturaId: string) => request<Timbre>(`/pos/facturas/${facturaId}/timbre`),
};

export { ErpApiError };

// --- suscripción compartida (tarjeta de prueba de los menús laterales) ---

let suscripcionPromesa: Promise<Suscripcion | null> | null = null;

/** GET /suscripcion una vez por carga de página (null si erp-api no responde). */
function cargarSuscripcion(): Promise<Suscripcion | null> {
  suscripcionPromesa ??= solucionesApi.suscripcion().catch(() => {
    suscripcionPromesa = null;
    return null;
  });
  return suscripcionPromesa;
}

export function useSuscripcion(): Suscripcion | null {
  const [suscripcion, setSuscripcion] = useState<Suscripcion | null>(null);
  useEffect(() => {
    let vivo = true;
    void cargarSuscripcion().then((s) => vivo && setSuscripcion(s));
    return () => {
      vivo = false;
    };
  }, []);
  return suscripcion;
}

/** Texto, avance (0-100) y botón de la tarjeta de prueba gratis; null = no mostrarla. */
export function tarjetaPrueba(s: Suscripcion | null): { titulo: string; avance: number; boton: string } | null {
  if (!s) return null;
  if (s.estado === "activa") return { titulo: `Plan ${s.plan_nombre ?? "activo"}`, avance: 100, boton: "Ver plan y consumo" };
  if (s.estado === "prueba_vencida" || !s.dias_restantes) return { titulo: "Su prueba gratis terminó", avance: 0, boton: "Elegir plan" };
  const total = Math.max(1, Math.round((Date.parse(s.prueba_fin) - Date.parse(s.prueba_inicio)) / 86_400_000) + 1);
  return {
    titulo: s.dias_restantes === 1 ? "Te queda 1 día gratis" : `Te quedan ${s.dias_restantes} días gratis`,
    avance: Math.min(100, Math.max(0, Math.round((s.dias_restantes / total) * 100))),
    boton: "Elegir plan",
  };
}
