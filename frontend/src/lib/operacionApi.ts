// Cliente de los endpoints de la Fase 6 · Operación de `erp-api`: Mis tareas (tareas automáticas
// calculadas de los datos de cada módulo + tareas manuales), Centro de sincronización (estado de cada
// sincronización con la DGII, inventario y proveedores; contingencia calculada; facturas en papel) y
// Reportes. CONTRATO entre services/erp_api (app/tareas.py, sincronizacion.py, reportes.py y
// operacion_schemas.py) y las pantallas: rutas, campos y tipos deben coincidir 1:1 con el backend.
// Mismas convenciones que erpApi.ts: montos como string decimal con 2 decimales, cantidades como string
// decimal, fechas "YYYY-MM-DD", marcas de tiempo ISO 8601 y períodos "AAAAMM".
import { ERP_API_URL, ErpApiError } from "./erpApi";
import { textoDetalle } from "./comprasApi";
import { authHeaders, fetchConSesion } from "./sesion";

/** Cabeceras de todas las peticiones de este cliente (la sesión la pone y renueva `fetchConSesion`). */
function cabeceras(): HeadersInit {
  return { "Content-Type": "application/json", ...authHeaders() };
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetchConSesion(`${ERP_API_URL}${path}`, { ...init, headers: { ...cabeceras(), ...init?.headers } });
  if (!response.ok) {
    let detail: unknown = response.statusText;
    try {
      const body = await response.json();
      detail = body.detail ?? JSON.stringify(body);
    } catch {
      // sin cuerpo JSON, se mantiene statusText
    }
    throw new ErpApiError(response.status, textoDetalle(detail));
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

const json = (method: string, body?: unknown): RequestInit => ({ method, body: body === undefined ? undefined : JSON.stringify(body) });

// --- Mis tareas ------------------------------------------------

export type PrioridadTarea = "alta" | "media" | "baja";
export type CategoriaTarea = "dgii" | "ecf" | "compras" | "cobros" | "pagos" | "ventas" | "inventario" | "gestion";
export type EstadoTarea = "pendiente" | "pospuesta" | "completada";

export interface Tarea {
  /** uuid de una tarea manual, o clave estable de una automática ("declaracion:it1:202609"). */
  id: string;
  origen: "manual" | "automatica";
  /** 'manual', 'declaracion', 'factura_rechazada', 'factura_error', 'dgii_en_proceso', 'ecf_por_aprobar',
   * 'cxc_vencida', 'cxp_por_vencer', 'cxp_vencida', 'cotizacion_por_vencer', 'lote_por_vencer', 'lote_vencido',
   * 'stock_bajo', 'inventario_sin_sincronizar', 'papel_sin_reemplazo'. */
  tipo: string;
  categoria: CategoriaTarea;
  titulo: string;
  descripcion: string | null;
  prioridad: PrioridadTarea;
  vence: string | null;
  vencida: boolean;
  estado: EstadoTarea;
  completada_en: string | null;
  pospuesta_hasta: string | null;
  /** Ruta de esta aplicación que resuelve la tarea. */
  enlace: string | null;
  monto: string | null;
  creado_en: string | null;
}

export interface TareaPayload {
  titulo: string;
  descripcion?: string | null;
  vence?: string | null;
  prioridad?: PrioridadTarea;
}

/** Contadores para la campana de la barra superior (GET /tareas/resumen). */
export interface ResumenTareas {
  pendientes: number;
  vencidas: number;
  vencen_hoy: number;
  alta: number;
  por_categoria: Partial<Record<CategoriaTarea, number>>;
  proximas: Tarea[];
}

const tarea = (id: string) => `/tareas/${encodeURIComponent(id)}`;

export const tareasApi = {
  listar: (params?: { estado?: EstadoTarea; categoria?: CategoriaTarea }) => request<Tarea[]>(`/tareas${consulta({ ...params })}`),
  resumen: () => request<ResumenTareas>("/tareas/resumen"),
  crear: (payload: TareaPayload) => request<Tarea>("/tareas", json("POST", payload)),
  actualizar: (id: string, payload: TareaPayload) => request<Tarea>(tarea(id), json("PUT", payload)),
  eliminar: (id: string) => request<void>(tarea(id), { method: "DELETE" }),
  completar: (id: string) => request<Tarea>(`${tarea(id)}/completar`, { method: "POST" }),
  reabrir: (id: string) => request<Tarea>(`${tarea(id)}/reabrir`, { method: "POST" }),
  posponer: (id: string, hasta: string) => request<Tarea>(`${tarea(id)}/posponer`, json("POST", { hasta })),
};

// --- Centro de sincronización ------------------------------------------------

export type CanalSincronizacion = "dgii_envio" | "dgii_resultado" | "inventario" | "aprobacion_comercial" | "entrega_emisor";
export type AccionSincronizacion = "actualizar_estado" | "reenviar" | "reintentar_aprobacion" | "decidir_aprobacion" | "reintentar_entrega";
export type ResultadoEvento = "ok" | "error" | "rechazado" | "en_proceso";

export interface CanalResumen {
  canal: CanalSincronizacion;
  titulo: string;
  descripcion: string;
  pendientes: number;
  errores: number;
  estado: "ok" | "pendiente" | "error";
  ultima_actividad: string | null;
  detalle: string | null;
}

/** Lote de envío en contingencia. facturas-api todavía no envía por lotes: llega vacío. */
export interface LoteContingencia {
  id: string;
  documentos: number;
  aceptados: number;
  rechazados: number;
  pendientes: number;
  estado: "en_cola" | "enviando" | "completado" | "interrumpido" | "con_rechazos";
  creado_en: string | null;
}

/** Modo contingencia calculado: fallos seguidos en los últimos envíos/consultas a la DGII. */
export interface Contingencia {
  activa: boolean;
  desde: string | null;
  errores_consecutivos: number;
  ultimo_intento: string | null;
  ultimo_error: string | null;
  proximo_intento: string | null;
  /** e-CF que no llegaron a la DGII (estado 'error' sin trackId). */
  pendientes: number;
  monto_pendiente: string;
  facturas_papel: number;
  lotes: LoteContingencia[];
}

export interface ResumenSincronizacion {
  estado: "ok" | "atencion" | "error";
  contingencia: Contingencia;
  canales: CanalResumen[];
  pendientes_total: number;
  errores_total: number;
  en_proceso_dgii: number;
  monto_en_proceso: string;
  en_proceso_desde: string | null;
  ultimo_envio: string | null;
  ultima_consulta_resultado: string | null;
  eventos_7d: number;
  errores_7d: number;
  rechazos_7d: number;
}

export interface ItemSincronizacion {
  id: string;
  canal: CanalSincronizacion;
  /** facturacion.facturas.id (canales dgii_* e inventario) o recepcion.ecf_recibidos.id. */
  referencia_id: string;
  documento: string | null;
  tipo_ecf: number | null;
  contraparte: string | null;
  fecha: string | null;
  monto: string | null;
  estado: "pendiente" | "en_curso" | "error";
  detalle: string | null;
  accion: AccionSincronizacion | null;
  decision: "aprobado" | "rechazado" | null;
  motivo: string | null;
  /** facturas-api lo reintenta solo. */
  automatico: boolean;
  enlace: string | null;
}

export interface EventoSincronizacion {
  id: number;
  creado_en: string;
  modulo: string;
  modulo_label: string;
  ambiente: string;
  encf: string | null;
  track_id: string | null;
  estado: string | null;
  codigo: string | null;
  error: string | null;
  resultado: ResultadoEvento;
}

export interface EnviosDia {
  fecha: string;
  documentos: number;
  aceptados: number;
  rechazados: number;
  errores: number;
  origen: "en_linea" | "con_fallas";
  resultado: "completado" | "con_rechazos" | "con_errores" | "sin_envios";
}

export interface ResultadoEntrega {
  id: string;
  encf: string;
  enviado_al_emisor: boolean;
  error_envio_emisor: string | null;
}

export interface FacturaPapelPayload {
  ncf: string;
  fecha: string;
  cliente_rnc?: string | null;
  cliente_nombre?: string | null;
  gravado_18?: string;
  gravado_16?: string;
  gravado_0?: string;
  exento?: string;
  /** Sin indicarlo se calcula (18 % / 16 % del gravado). */
  itbis_18?: string | null;
  itbis_16?: string | null;
  /** Forma de pago del 607: 1 efectivo, 2 cheque/transferencia, 3 tarjeta, 4 crédito, 5 bonos, 6 permuta, 7 otras. */
  forma_pago?: number;
  notas?: string | null;
  encf_reemplazo?: string | null;
}

export interface FacturaPapel {
  id: string;
  ncf: string;
  tipo_comprobante: string;
  fecha: string;
  cliente_rnc: string | null;
  cliente_nombre: string | null;
  gravado_18: string;
  gravado_16: string;
  gravado_0: string;
  exento: string;
  itbis_18: string;
  itbis_16: string;
  itbis: string;
  total: string;
  forma_pago: number;
  notas: string | null;
  encf_reemplazo: string | null;
  creado_en: string;
}

export const sincronizacionApi = {
  resumen: () => request<ResumenSincronizacion>("/sincronizacion/resumen"),
  pendientes: (canal?: CanalSincronizacion) => request<ItemSincronizacion[]>(`/sincronizacion/pendientes${consulta({ canal })}`),
  historial: (params?: {
    modulo?: string;
    resultado?: ResultadoEvento;
    q?: string;
    desde?: string;
    hasta?: string;
    limit?: number;
    offset?: number;
  }) => request<EventoSincronizacion[]>(`/sincronizacion/historial${consulta({ ...params })}`),
  enviosPorDia: (dias = 7) => request<EnviosDia[]>(`/sincronizacion/envios-por-dia${consulta({ dias })}`),
  reintentarEntrega: (ecfId: string) => request<ResultadoEntrega>(`/sincronizacion/entregas/${ecfId}/reintentar`, { method: "POST" }),
  facturasPapel: (params?: { desde?: string; hasta?: string; q?: string }) =>
    request<FacturaPapel[]>(`/sincronizacion/facturas-papel${consulta({ ...params })}`),
  crearFacturaPapel: (payload: FacturaPapelPayload) => request<FacturaPapel>("/sincronizacion/facturas-papel", json("POST", payload)),
  actualizarFacturaPapel: (id: string, payload: FacturaPapelPayload) =>
    request<FacturaPapel>(`/sincronizacion/facturas-papel/${id}`, json("PUT", payload)),
  eliminarFacturaPapel: (id: string) => request<void>(`/sincronizacion/facturas-papel/${id}`, { method: "DELETE" }),
};

// --- Reportes ------------------------------------------------

export interface Periodo {
  desde: string;
  hasta: string;
}

interface ReporteBase extends Periodo {
  advertencias: string[];
}

export interface MontosVenta {
  gravado_18: string;
  gravado_16: string;
  gravado_0: string;
  exento: string;
  itbis_18: string;
  itbis_16: string;
  /** Sin ITBIS. */
  subtotal: string;
  itbis: string;
  /** Total del e-CF (con selectivo, otros impuestos y propina); por producto, subtotal + ITBIS. */
  total: string;
}

export interface FilaVentas extends MontosVenta {
  clave: string;
  etiqueta: string;
  detalle: string | null;
  documentos: number;
  cantidad: string | null;
}

export type AgruparVentas = "mes" | "dia" | "cliente" | "producto" | "tipo_ecf";

export interface ReporteVentas extends ReporteBase {
  agrupar: AgruparVentas;
  filas: FilaVentas[];
  totales: MontosVenta & { documentos: number };
}

export interface FilaItbis {
  periodo: string;
  ventas_gravadas: string;
  itbis_ventas: string;
  /** Monto facturado del 606 (sin ITBIS; las notas de crédito restan). */
  compras: string;
  itbis_compras: string;
  itbis_costo: string;
  itbis_adelantado: string;
  itbis_retenido: string;
  diferencia: string;
}

export interface ReporteItbis extends ReporteBase {
  filas: FilaItbis[];
  totales: FilaItbis;
}

export interface FilaCompras {
  clave: string;
  etiqueta: string;
  detalle: string | null;
  documentos: number;
  monto_servicios: string;
  monto_bienes: string;
  monto_facturado: string;
  itbis_facturado: string;
  itbis_retenido: string;
  isr_retenido: string;
  total: string;
}

export type AgruparCompras = "proveedor" | "categoria" | "mes";

export interface ReporteCompras extends ReporteBase {
  agrupar: AgruparCompras;
  filas: FilaCompras[];
  totales: FilaCompras;
}

export type TramoAntiguedad = "por_vencer" | "d1_30" | "d31_60" | "d61_90" | "mas_90";

export interface FilaAntiguedad extends Record<TramoAntiguedad, string> {
  rnc: string | null;
  nombre: string;
  documentos: number;
  total: string;
}

export interface DocumentoAntiguedad {
  id: string;
  documento: string | null;
  rnc: string | null;
  nombre: string;
  fecha_emision: string | null;
  vence: string | null;
  dias_vencida: number;
  tramo: TramoAntiguedad;
  pendiente: string;
}

export interface ReporteAntiguedad {
  tipo: "cxc" | "cxp";
  fecha_corte: string;
  filas: FilaAntiguedad[];
  documentos: DocumentoAntiguedad[];
  totales: FilaAntiguedad;
}

export interface FilaCobrosPagos {
  periodo: string;
  cobros: string;
  cantidad_cobros: number;
  pagos: string;
  cantidad_pagos: number;
  neto: string;
}

export interface MovimientoCobroPago {
  id: string;
  tipo: "cobro" | "pago";
  fecha: string;
  documento: string | null;
  contraparte: string;
  metodo: string;
  referencia: string | null;
  monto: string;
}

export interface ReporteCobrosPagos extends ReporteBase {
  filas: FilaCobrosPagos[];
  por_metodo: { metodo: string; cobros: string; pagos: string }[];
  movimientos: MovimientoCobroPago[];
  totales: FilaCobrosPagos;
}

export interface FilaInventarioValorizado {
  producto_id: string;
  sku: string;
  nombre: string;
  categoria: string | null;
  unidad_medida: string | null;
  existencia: string;
  costo_promedio: string;
  valor: string;
  precio_venta: string;
  valor_venta: string;
  stock_minimo: string;
  bajo_minimo: boolean;
}

export interface ReporteInventarioValorizado {
  fecha_corte: string;
  almacen_id: string | null;
  filas: FilaInventarioValorizado[];
  totales: { productos: number; existencia: string; valor: string; valor_venta: string };
  advertencias: string[];
}

export interface FilaRotacion {
  producto_id: string;
  sku: string;
  nombre: string;
  categoria: string | null;
  existencia_inicial: string;
  entradas: string;
  salidas: string;
  existencia_final: string;
  costo_salidas: string;
  inventario_promedio: string;
  /** Veces en el período; null sin existencia promedio. */
  rotacion: string | null;
  dias_inventario: string | null;
}

export interface ReporteRotacion extends ReporteBase {
  filas: FilaRotacion[];
  totales: FilaRotacion;
}

export interface FilaMasVendido {
  posicion: number;
  producto_id: string;
  sku: string;
  nombre: string;
  categoria: string | null;
  cantidad: string;
  facturas: number;
  costo: string;
  /** % de la cantidad vendida del período. */
  participacion: string;
}

export interface ReporteMasVendidos extends ReporteBase {
  filas: FilaMasVendido[];
  total_cantidad: string;
  total_costo: string;
}

export interface FilaRetencion {
  clave: string;
  etiqueta: string;
  detalle: string | null;
  documentos: number;
  monto_base: string;
  isr_retenido: string;
  itbis_retenido: string;
  total: string;
}

export interface ReporteRetenciones extends ReporteBase {
  efectuadas: FilaRetencion[];
  por_tipo: FilaRetencion[];
  totales_efectuadas: FilaRetencion;
  sufridas: FilaRetencion[];
  totales_sufridas: FilaRetencion;
}

export const reportesApi = {
  ventas: (p: Periodo & { agrupar?: AgruparVentas; cliente_rnc?: string; tipo_ecf?: number }) =>
    request<ReporteVentas>(`/reportes/ventas${consulta({ ...p })}`),
  itbis: (p: Periodo) => request<ReporteItbis>(`/reportes/itbis${consulta({ ...p })}`),
  compras: (p: Periodo & { agrupar?: AgruparCompras; proveedor_rnc?: string; tipo_bienes_servicios?: number }) =>
    request<ReporteCompras>(`/reportes/compras${consulta({ ...p })}`),
  antiguedadCxc: (cliente_rnc?: string) => request<ReporteAntiguedad>(`/reportes/antiguedad-cxc${consulta({ cliente_rnc })}`),
  antiguedadCxp: (proveedor_rnc?: string) => request<ReporteAntiguedad>(`/reportes/antiguedad-cxp${consulta({ proveedor_rnc })}`),
  cobrosPagos: (p: Periodo) => request<ReporteCobrosPagos>(`/reportes/cobros-pagos${consulta({ ...p })}`),
  inventarioValorizado: (p?: { almacen_id?: string; categoria?: string; incluir_sin_existencia?: boolean }) =>
    request<ReporteInventarioValorizado>(`/reportes/inventario-valorizado${consulta({ ...p })}`),
  rotacion: (p: Periodo & { categoria?: string }) => request<ReporteRotacion>(`/reportes/rotacion-inventario${consulta({ ...p })}`),
  masVendidos: (p: Periodo & { limite?: number; categoria?: string }) =>
    request<ReporteMasVendidos>(`/reportes/mas-vendidos${consulta({ ...p })}`),
  retenciones: (p: Periodo) => request<ReporteRetenciones>(`/reportes/retenciones${consulta({ ...p })}`),
};
