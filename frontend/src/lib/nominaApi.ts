// Cliente de los endpoints de Kontara Nómina de `erp-api` (/nomina/*):
// empresa empleadora, parámetros de ley (tasas TSS, topes y escala del ISR
// por fecha de vigencia), empleados, períodos de nómina, pagos, archivo
// para el banco y reportes (TSS, IR-3, IR-13, costo vs pago). CONTRATO con
// services/erp_api/app/nomina.py: rutas, campos y tipos coinciden 1:1.
// Mismas convenciones que erpApi.ts: montos como string decimal con 2
// decimales, tasas como fracción ("0.0287" = 2.87 %), fechas "YYYY-MM-DD"
// y marcas de tiempo ISO 8601. Ver docs/NOMINA.md.
import { textoDetalle } from "./comprasApi";
import { ERP_API_URL, ErpApiError } from "./erpApi";

/** Encabezados comunes de todas las llamadas de Nómina (aquí se agregará la autorización). */
function encabezados(extra?: HeadersInit): HeadersInit {
  return { "Content-Type": "application/json", ...(extra ?? {}) };
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${ERP_API_URL}${path}`, { ...init, headers: encabezados(init?.headers) });
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
  if (response.status === 204) return undefined as T;
  const tipo = response.headers.get("content-type") ?? "";
  if (tipo.includes("text/csv")) return (await response.blob()) as T;
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

// --- Tipos ---------------------------------------------------------

export type TipoPeriodo = "mensual" | "quincenal";
export type EstadoPeriodo = "borrador" | "calculada" | "aprobada" | "pagada" | "cerrada";
export type EstadoEmpleado = "activo" | "inactivo";
export type TipoCuenta = "ahorro" | "corriente";

export interface EmpresaNomina {
  rnc: string | null;
  razon_social: string | null;
  categoria: string | null;
  municipio: string | null;
  /** Nombre de quien gestiona la nómina (saludo de Inicio). */
  contacto_nombre: string | null;
  /** Tasa del Seguro de Riesgos Laborales de la empresa (0.0110 = 1.10 %). */
  tasa_srl: string;
  frecuencia_pago: TipoPeriodo;
  dia_pago: number;
  banco: string | null;
  cuenta_origen: string | null;
  /** RNC, categoría y municipio completos. */
  completa: boolean;
  actualizado_en: string | null;
}

export type EmpresaPayload = Omit<EmpresaNomina, "completa" | "actualizado_en">;

export interface TramoIsr {
  desde: string;
  tasa: string;
  fijo: string;
}

export interface ParametrosNomina {
  vigente_desde: string;
  salario_minimo_cotizable: string;
  afp_empleado: string;
  afp_empleador: string;
  sfs_empleado: string;
  sfs_empleador: string;
  srl_empleador: string;
  infotep_empleador: string;
  infotep_bonificacion_empleado: string;
  tope_afp_salarios: string;
  tope_sfs_salarios: string;
  tope_srl_salarios: string;
  escala_isr: TramoIsr[];
  /** Monto exento de la regalía pascual; null = exenta sin tope. */
  regalia_tope_exento: string | null;
  divisor_salario_diario: string;
  horas_jornada: string;
  recargo_horas_extra: string;
  recargo_horas_extra_exceso: string;
  fuente: string | null;
  tope_afp: string;
  tope_sfs: string;
  tope_srl: string;
  exento_isr_anual: string;
  actualizado_en: string;
}

export type ParametrosPayload = Omit<
  ParametrosNomina,
  "vigente_desde" | "tope_afp" | "tope_sfs" | "tope_srl" | "exento_isr_anual" | "actualizado_en"
>;

export interface Indicadores {
  fecha: string;
  vigente_desde: string;
  salario_minimo_cotizable: string;
  tope_afp: string;
  tope_sfs: string;
  tope_srl: string;
  exento_isr_anual: string;
  exento_isr_mensual: string;
}

export interface Empleado {
  id: string;
  cedula: string;
  nombre: string;
  cargo: string | null;
  departamento: string | null;
  fecha_ingreso: string;
  salario_mensual: string;
  tipo_pago: TipoPeriodo;
  banco: string | null;
  cuenta_bancaria: string | null;
  tipo_cuenta: TipoCuenta | null;
  afp: string | null;
  ars: string | null;
  correo: string | null;
  estado: EstadoEmpleado;
  fecha_salida: string | null;
  /** Días de vacaciones que le corresponden hoy (Código de Trabajo, arts. 177 y 180). */
  dias_vacaciones: number;
  creado_en: string;
  actualizado_en: string;
}

export type EmpleadoPayload = Omit<Empleado, "id" | "dias_vacaciones" | "creado_en" | "actualizado_en">;

export interface ResultadoImportacion {
  creados: number;
  actualizados: number;
  errores: { fila: number; mensaje: string }[];
}

export interface Prestaciones {
  empleado_id: string;
  fecha_salida: string;
  desahucio: boolean;
  meses_servicio: number;
  salario_diario: string;
  dias_preaviso: number;
  preaviso: string;
  dias_cesantia: string;
  cesantia: string;
  dias_vacaciones: number;
  vacaciones: string;
  regalia: string;
  total: string;
}

export interface TotalesNomina {
  salario: string;
  horas_extra: string;
  otros_ingresos: string;
  bonificacion: string;
  ingresos_exentos: string;
  regalia: string;
  total_ingresos: string;
  afp: string;
  sfs: string;
  isr: string;
  infotep_empleado: string;
  otros_descuentos: string;
  total_descuentos: string;
  neto: string;
  afp_empleador: string;
  sfs_empleador: string;
  srl: string;
  infotep: string;
  total_aportes_empleador: string;
  costo_total: string;
}

export interface PeriodoResumen {
  id: string;
  tipo: TipoPeriodo;
  anio: number;
  mes: number;
  quincena: 1 | 2 | null;
  /** "octubre 2026" o "1.ª quincena de octubre 2026". */
  nombre: string;
  fecha_inicio: string;
  fecha_fin: string;
  fecha_pago: string;
  estado: EstadoPeriodo;
  tasa_srl: string | null;
  notas: string | null;
  empleados: number;
  pagados: number;
  totales: TotalesNomina;
  calculado_en: string | null;
  aprobado_en: string | null;
  pagado_en: string | null;
  cerrado_en: string | null;
  creado_en: string;
}

export interface Novedades {
  horas_extra_35: string;
  horas_extra_100: string;
  otros_ingresos: string;
  bonificacion: string;
  ingresos_exentos: string;
  regalia: string;
  otros_descuentos: string;
}

export interface LineaNomina extends Novedades {
  empleado_id: string;
  cedula: string;
  nombre: string;
  cargo: string | null;
  departamento: string | null;
  salario_mensual: string;
  banco: string | null;
  cuenta_bancaria: string | null;
  tipo_cuenta: string | null;
  // Resultado: null mientras la nómina no se calcule.
  salario: string | null;
  horas_extra: string | null;
  total_ingresos: string | null;
  salario_cotizable: string | null;
  base_isr: string | null;
  afp: string | null;
  sfs: string | null;
  isr: string | null;
  infotep_empleado: string | null;
  total_descuentos: string | null;
  neto: string | null;
  afp_empleador: string | null;
  sfs_empleador: string | null;
  srl: string | null;
  infotep: string | null;
  total_aportes_empleador: string | null;
  costo_total: string | null;
  pagado: boolean;
  pagado_en: string | null;
  referencia_pago: string | null;
}

export interface Periodo extends PeriodoResumen {
  /** Copia de los parámetros con que se calculó (strings). */
  parametros: Record<string, unknown> | null;
  lineas: LineaNomina[];
}

export interface RegaliaSugerida {
  empleado_id: string;
  anio: number;
  salario_ordinario_registrado: string;
  regalia_registrada: string;
  regalia: string;
}

export interface FilaTss {
  empleado_id: string;
  cedula: string;
  nombre: string;
  salario_cotizable: string;
  regalia: string;
  otras_remuneraciones: string;
  afp: string;
  sfs: string;
  afp_empleador: string;
  sfs_empleador: string;
  srl: string;
  infotep: string;
  total: string;
}

export interface ReporteTss {
  anio: number;
  mes: number;
  nombre: string;
  /** Nóminas del mes aún sin aprobar (no entran al reporte). */
  nominas_pendientes: number;
  tope_afp: string;
  tope_sfs: string;
  tope_srl: string;
  vence: string;
  empleados: FilaTss[];
  totales: Omit<FilaTss, "empleado_id" | "cedula" | "nombre">;
}

export interface FilaIr3 {
  empleado_id: string;
  cedula: string;
  nombre: string;
  ingresos_gravados: string;
  ingresos_exentos: string;
  tss: string;
  base_isr: string;
  isr: string;
}

export interface ReporteIr3 {
  anio: number;
  mes: number;
  nombre: string;
  nominas_pendientes: number;
  asalariados: number;
  retenidos: number;
  vence: string;
  empleados: FilaIr3[];
  totales: Omit<FilaIr3, "empleado_id" | "cedula" | "nombre">;
}

export interface FilaIr13 {
  empleado_id: string;
  cedula: string;
  nombre: string;
  salarios: string;
  exentos: string;
  tss: string;
  renta_neta: string;
  isr: string;
}

export interface ReporteIr13 {
  anio: number;
  nominas_pendientes: number;
  empleados: FilaIr13[];
  totales: Omit<FilaIr13, "empleado_id" | "cedula" | "nombre">;
}

export interface CostoPagoMes {
  mes: number;
  nombre: string;
  costo: string;
  neto: string;
  ingresos: string;
  empleados: number;
}

export interface ResumenNomina {
  empresa_completa: boolean;
  contacto_nombre: string | null;
  empleados_activos: number;
  nomina_calculada: boolean;
  /** De 4: cuenta creada, empresa, empleados, primera nómina calculada. */
  pasos_completados: number;
  /** El período más antiguo sin pagar; si no hay, el último (o null). */
  periodo: PeriodoResumen | null;
  periodo_sugerido: { tipo: TipoPeriodo; anio: number; mes: number; nombre: string };
  indicadores: Indicadores | null;
}

export interface AvisoNomina {
  id: string;
  tipo: "tss" | "dgii" | "equipo";
  titulo: string;
  detalle: string;
  fecha: string | null;
  enlace: string | null;
}

// --- Llamadas ---------------------------------------------------------

export const nominaApi = {
  resumen: () => request<ResumenNomina>("/nomina/resumen"),
  avisos: () => request<AvisoNomina[]>("/nomina/avisos"),
  indicadores: (fecha?: string) => request<Indicadores>(`/nomina/indicadores${consulta({ fecha })}`),

  empresa: () => request<EmpresaNomina>("/nomina/empresa"),
  guardarEmpresa: (datos: EmpresaPayload) => request<EmpresaNomina>("/nomina/empresa", { method: "PUT", body: json(datos) }),

  parametros: () => request<ParametrosNomina[]>("/nomina/parametros"),
  guardarParametros: (vigenteDesde: string, datos: ParametrosPayload) =>
    request<ParametrosNomina>(`/nomina/parametros/${vigenteDesde}`, { method: "PUT", body: json(datos) }),
  eliminarParametros: (vigenteDesde: string) => request<void>(`/nomina/parametros/${vigenteDesde}`, { method: "DELETE" }),

  empleados: (filtros: { q?: string; estado?: EstadoEmpleado; tipo_pago?: TipoPeriodo } = {}) =>
    request<Empleado[]>(`/nomina/empleados${consulta(filtros)}`),
  empleado: (id: string) => request<Empleado>(`/nomina/empleados/${id}`),
  crearEmpleado: (datos: EmpleadoPayload) => request<Empleado>("/nomina/empleados", { method: "POST", body: json(datos) }),
  actualizarEmpleado: (id: string, datos: EmpleadoPayload) =>
    request<Empleado>(`/nomina/empleados/${id}`, { method: "PUT", body: json(datos) }),
  eliminarEmpleado: (id: string) => request<void>(`/nomina/empleados/${id}`, { method: "DELETE" }),
  importarEmpleados: (csv: string) =>
    request<ResultadoImportacion>("/nomina/empleados/importar", { method: "POST", body: json({ csv }) }),
  prestaciones: (id: string, fechaSalida: string, desahucio: boolean) =>
    request<Prestaciones>(`/nomina/empleados/${id}/prestaciones${consulta({ fecha_salida: fechaSalida, desahucio })}`),

  periodos: (filtros: { anio?: number; estado?: EstadoPeriodo } = {}) =>
    request<PeriodoResumen[]>(`/nomina/periodos${consulta(filtros)}`),
  periodo: (id: string) => request<Periodo>(`/nomina/periodos/${id}`),
  crearPeriodo: (datos: { tipo: TipoPeriodo; anio: number; mes: number; quincena?: 1 | 2 | null; fecha_pago?: string | null; notas?: string | null }) =>
    request<Periodo>("/nomina/periodos", { method: "POST", body: json(datos) }),
  eliminarPeriodo: (id: string) => request<void>(`/nomina/periodos/${id}`, { method: "DELETE" }),
  calcular: (id: string) => request<Periodo>(`/nomina/periodos/${id}/calcular`, { method: "POST" }),
  guardarNovedades: (id: string, empleadoId: string, datos: Partial<Novedades>) =>
    request<Periodo>(`/nomina/periodos/${id}/lineas/${empleadoId}`, { method: "PUT", body: json(datos) }),
  regaliaSugerida: (id: string, empleadoId: string) =>
    request<RegaliaSugerida>(`/nomina/periodos/${id}/lineas/${empleadoId}/regalia`),
  aprobar: (id: string) => request<Periodo>(`/nomina/periodos/${id}/aprobar`, { method: "POST" }),
  reabrir: (id: string) => request<Periodo>(`/nomina/periodos/${id}/reabrir`, { method: "POST" }),
  pagar: (id: string, datos: { empleado_ids?: string[] | null; referencia?: string | null } = {}) =>
    request<Periodo>(`/nomina/periodos/${id}/pagar`, { method: "POST", body: json(datos) }),
  cerrar: (id: string) => request<Periodo>(`/nomina/periodos/${id}/cerrar`, { method: "POST" }),
  archivoBanco: (id: string) => request<Blob>(`/nomina/periodos/${id}/archivo-banco`),

  reporteTss: (anio: number, mes: number) => request<ReporteTss>(`/nomina/reportes/tss${consulta({ anio, mes })}`),
  archivoTss: (anio: number, mes: number) => request<Blob>(`/nomina/reportes/tss/csv${consulta({ anio, mes })}`),
  reporteIr3: (anio: number, mes: number) => request<ReporteIr3>(`/nomina/reportes/ir3${consulta({ anio, mes })}`),
  reporteIr13: (anio: number) => request<ReporteIr13>(`/nomina/reportes/ir13${consulta({ anio })}`),
  costoPago: (anio: number) => request<CostoPagoMes[]>(`/nomina/reportes/costo-pago${consulta({ anio })}`),
};
