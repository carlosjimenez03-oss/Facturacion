// Cliente de los endpoints de Contabilidad de `erp-api` (fase 5): catálogo
// de cuentas, cuentas por defecto de los asientos automáticos, asientos
// (manuales y generados desde los documentos), reportes (libro diario,
// mayor, balanza de comprobación, estado de resultados, balance general,
// resumen del tablero) y activos fijos con su depreciación. CONTRATO entre
// services/erp_api (app/contabilidad.py, schemas.py) y las pantallas de
// Contabilidad (prototipo Contabilidad.dc.html: pestañas Catálogo de
// cuentas, Asientos, Activos fijos): rutas, campos y tipos deben coincidir
// 1:1 con el backend. Mismas convenciones que erpApi.ts: montos como string
// decimal con 2 decimales (pueden ser negativos), fechas "YYYY-MM-DD",
// período mensual "AAAAMM", año "AAAA".
//
// Asientos automáticos: "Generar asientos del período" (`generarAsientos`)
// crea los que faltan desde los e-CF de venta aceptados, cobros, compras del
// 606, pagos a proveedores, movimientos y transferencias bancarias; es
// idempotente, regenera los que cambiaron (documento o cuentas por defecto)
// y anula los de documentos anulados. Lo que no se puede contabilizar va a
// `no_contabilizados` con el motivo (p. ej. falta una cuenta por defecto).
import { ERP_API_URL, ErpApiError } from "./erpApi";
import { textoDetalle } from "./comprasApi";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${ERP_API_URL}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
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

/** Rango de fechas de los reportes; sin fechas: el mes en curso (hora RD). */
export interface Rango {
  desde?: string;
  hasta?: string;
}

// --- Catálogo de cuentas --------------------------------------------------

export type TipoCuentaContable = "activo" | "pasivo" | "patrimonio" | "ingreso" | "costo" | "gasto";
export type Naturaleza = "deudora" | "acreedora";

export interface CuentaContable {
  id: string;
  /** "1.1.01.03": la jerarquía está en el código (padre = sin el último segmento). */
  codigo: string;
  nombre: string;
  tipo: TipoCuentaContable;
  /** Lado en que aumenta el saldo; las cuentas "contra" (depreciación acumulada, devoluciones) tienen la contraria. */
  naturaleza: Naturaleza;
  /** Solo las hojas llevan líneas de asiento; una cuenta con subcuentas es de grupo. */
  acepta_movimientos: boolean;
  padre_id: string | null;
  padre_codigo: string | null;
  /** 1 = grupo (Activos…), 2, 3… para la sangría del árbol. */
  nivel: number;
  hijos: number;
  activa: boolean;
  /** Sembrada con el catálogo sugerido. */
  sistema: boolean;
  descripcion: string | null;
  tiene_movimientos: boolean;
  /** Saldo con sus subcuentas, según su naturaleza (columna de la derecha del prototipo). */
  saldo: string;
  creado_en: string;
  actualizado_en: string;
}

/**
 * El padre sale del código (422 si no existe: "1.1.01.05" exige "1.1.01"); el tipo, del padre; la naturaleza por
 * defecto, del tipo. Si el padre aceptaba movimientos pasa a ser de grupo: 409 si ya tiene movimientos o es una cuenta
 * por defecto / de un banco. 409 si el código ya existe.
 */
export interface CuentaContablePayload {
  codigo: string;
  nombre: string;
  /** Solo para una cuenta raíz nueva (las demás heredan el del padre). */
  tipo?: TipoCuentaContable | null;
  naturaleza?: Naturaleza | null;
  acepta_movimientos?: boolean;
  activa?: boolean;
  descripcion?: string | null;
}

/** Solo lo enviado cambia. `codigo`: 409 si tiene subcuentas. Desactivar: 409 si es cuenta por defecto o de un banco. */
export interface CuentaContableActualizar {
  codigo?: string;
  nombre?: string;
  naturaleza?: Naturaleza;
  acepta_movimientos?: boolean;
  activa?: boolean;
  descripcion?: string | null;
}

export type GrupoConcepto = "ventas" | "bancos" | "compras" | "activos";

/** Cuenta que usa cada concepto de los asientos automáticos (tabla en docs/ERP_API.md, "Contabilidad"). */
export interface CuentaPorDefecto {
  /** "cxc_clientes", "itbis_por_pagar", "gasto_606_02"… */
  concepto: string;
  grupo: GrupoConcepto;
  etiqueta: string;
  /** Código del catálogo sugerido. */
  codigo_sugerido: string;
  /** null = sin cuenta: los documentos que la necesitan quedan "no contabilizados". */
  cuenta_id: string | null;
  cuenta_codigo: string | null;
  cuenta_nombre: string | null;
}

// --- Asientos -------------------------------------------------------------

export type EstadoAsiento = "borrador" | "contabilizado" | "anulado";
export type OrigenAsiento =
  | "manual"
  | "venta"
  | "nota_credito"
  | "nota_debito"
  | "cobro"
  | "compra"
  | "pago"
  | "movimiento"
  | "transferencia"
  | "depreciacion";

export interface LineaAsiento {
  id: string;
  linea: number;
  cuenta_id: string;
  cuenta_codigo: string;
  cuenta_nombre: string;
  descripcion: string | null;
  debito: string;
  credito: string;
}

export interface AsientoResumen {
  id: string;
  /** "AS-0412". */
  numero: string;
  fecha: string;
  concepto: string;
  referencia: string | null;
  estado: EstadoAsiento;
  origen: OrigenAsiento;
  /** Columna "Origen" del prototipo: "Manual", "Factura", "Compra", "Banco", "Cobro", "Depreciación"… */
  origen_etiqueta: string;
  /** Documento de origen (factura, cobro, renglón del 606, pago, movimiento, transferencia o período AAAAMM). */
  origen_id: string | null;
  automatico: boolean;
  total_debito: string;
  total_credito: string;
  cuadrado: boolean;
  contabilizado_en: string | null;
  anulado_en: string | null;
  motivo_anulacion: string | null;
  creado_en: string;
}

export interface Asiento extends AsientoResumen {
  lineas: LineaAsiento[];
}

export interface LineaAsientoPayload {
  cuenta_id: string;
  descripcion?: string | null;
  /** Uno solo de los dos, > 0. */
  debito?: string;
  credito?: string;
}

/**
 * Asiento manual. 422 si tiene menos de 2 líneas, una línea con débito y crédito (o ninguno), una cuenta de grupo o
 * inactiva, o (`contabilizar: true`) no cuadra: "El asiento no cuadra: débitos RD$ …, créditos RD$ …".
 */
export interface AsientoPayload {
  /** Por defecto hoy. */
  fecha?: string;
  concepto: string;
  referencia?: string | null;
  lineas: LineaAsientoPayload[];
  /** true = "Contabilizar asiento"; false = guardar como borrador (puede quedar descuadrado). */
  contabilizar?: boolean;
}

export interface DocumentoNoContabilizado {
  origen: OrigenAsiento;
  origen_id: string;
  fecha: string | null;
  /** e-NCF / NCF. */
  referencia: string | null;
  descripcion: string;
  monto: string | null;
  /** "Falta la cuenta por defecto «ITBIS por pagar» (Contabilidad → Cuentas por defecto)", "Sin 'Tipo de bienes y servicios' en el 606…". */
  motivo: string;
}

export interface CambioPendiente {
  accion: "crear" | "actualizar" | "anular";
  origen: OrigenAsiento;
  origen_id: string;
  fecha: string | null;
  descripcion: string;
  monto: string | null;
  /** Asiento existente que se actualiza o anula. */
  asiento_numero: string | null;
}

export interface ResultadoGeneracion {
  desde: string;
  hasta: string;
  creados: number;
  actualizados: number;
  anulados: number;
  sin_cambios: number;
  /** Lo que se hizo (generar) o lo que se haría (pendientes). */
  pendientes: CambioPendiente[];
  no_contabilizados: DocumentoNoContabilizado[];
}

// --- Reportes -------------------------------------------------------------

export interface LibroDiario {
  desde: string;
  hasta: string;
  asientos: Asiento[];
  total_asientos: number;
  total_debitos: string;
  total_creditos: string;
}

export interface MovimientoMayor {
  asiento_id: string;
  numero: string;
  fecha: string;
  concepto: string;
  origen: OrigenAsiento;
  referencia: string | null;
  descripcion: string | null;
  /** La subcuenta de la línea (el mayor de una cuenta de grupo incluye sus subcuentas). */
  cuenta_codigo: string;
  cuenta_nombre: string;
  debito: string;
  credito: string;
  /** Saldo según la naturaleza de la cuenta, después de la línea. */
  saldo: string;
}

export interface Mayor {
  cuenta: CuentaContable;
  desde: string;
  hasta: string;
  saldo_inicial: string;
  total_debitos: string;
  total_creditos: string;
  saldo_final: string;
  movimientos: MovimientoMayor[];
}

export interface FilaBalanza {
  cuenta_id: string;
  codigo: string;
  nombre: string;
  tipo: TipoCuentaContable;
  naturaleza: Naturaleza;
  nivel: number;
  acepta_movimientos: boolean;
  saldo_inicial: string;
  debitos: string;
  creditos: string;
  saldo_final: string;
  saldo_final_deudor: string;
  saldo_final_acreedor: string;
}

export interface Balanza {
  desde: string;
  hasta: string;
  /** Cuentas con saldo o movimiento (las de grupo acumulan a sus subcuentas). */
  cuentas: FilaBalanza[];
  /** Solo cuentas de movimiento. */
  totales: {
    saldo_inicial_deudor: string;
    saldo_inicial_acreedor: string;
    debitos: string;
    creditos: string;
    saldo_final_deudor: string;
    saldo_final_acreedor: string;
  };
  /** Débitos = créditos y saldos deudores = acreedores. */
  cuadrada: boolean;
}

export interface FilaEstado {
  cuenta_id: string;
  codigo: string;
  nombre: string;
  nivel: number;
  acepta_movimientos: boolean;
  /** Positivo = lo normal del grupo (ingreso, gasto, activo…); una cuenta "contra" sale negativa. */
  monto: string;
}

export interface SeccionEstado {
  cuentas: FilaEstado[];
  total: string;
}

export interface EstadoResultados {
  desde: string;
  hasta: string;
  /** Netos de devoluciones (notas de crédito): coincide con el total de ingresos del IT-1 de los mismos meses. */
  ingresos: SeccionEstado;
  costos: SeccionEstado;
  gastos: SeccionEstado;
  utilidad_bruta: string;
  utilidad_neta: string;
  margen_neto_pct: string;
}

export interface BalanceGeneral {
  fecha: string;
  activos: SeccionEstado;
  pasivos: SeccionEstado;
  patrimonio: SeccionEstado;
  /** Sin asientos de cierre: ingresos − costos − gastos desde el 1 de enero. */
  resultado_ejercicio: string;
  resultados_anteriores_no_cerrados: string;
  total_patrimonio: string;
  total_pasivo_patrimonio: string;
  cuadrado: boolean;
}

export interface ResumenContabilidad {
  desde: string;
  hasta: string;
  /** Al `hasta`. */
  activos: string;
  pasivos: string;
  patrimonio: string;
  /** Del período. */
  ingresos: string;
  costos: string;
  gastos: string;
  utilidad_neta: string;
  asientos_contabilizados: number;
  asientos_manuales: number;
  borradores: number;
  /** Documentos del período cuyo asiento falta o cambió ("Generar asientos del período"). */
  documentos_por_contabilizar: number;
  documentos_no_contabilizables: number;
}

// --- Activos fijos --------------------------------------------------------

/** Art. 287 literal e) del Código Tributario: 1 = edificaciones (5 %), 2 = vehículos livianos, mobiliario y equipos de oficina y cómputo (25 %), 3 = otros (15 %). */
export type CategoriaDgii = 1 | 2 | 3;
/** "dgii" = método fiscal (cat. 1 sobre el costo; 2 y 3 saldo decreciente); "linea_recta" = (costo − residual) / vida útil. */
export type MetodoDepreciacion = "linea_recta" | "dgii";

export interface ActivoFijo {
  id: string;
  descripcion: string;
  categoria: CategoriaDgii;
  /** "Categoría 2 · vehículos livianos, mobiliario y equipos de oficina y cómputo". */
  categoria_nombre: string;
  /** "25.00". */
  tasa_dgii: string;
  fecha_adquisicion: string;
  costo: string;
  valor_residual: string;
  metodo: MetodoDepreciacion;
  vida_util_meses: number | null;
  /** AAAAMM del primer mes que se deprecia. */
  depreciar_desde: string;
  depreciacion_acumulada_inicial: string;
  depreciacion_registrada: string;
  /** Inicial + registrada ("Dep. acumulada" del prototipo). */
  depreciacion_acumulada: string;
  valor_en_libros: string;
  /** Cuota del próximo mes por depreciar. */
  cuota_mensual: string;
  ultimo_periodo_depreciado: string | null;
  estado: "activo" | "depreciado" | "baja";
  cuenta_activo_id: string | null;
  cuenta_activo_codigo: string | null;
  cuenta_activo_nombre: string | null;
  registro606_id: string | null;
  numero_serie: string | null;
  ubicacion: string | null;
  fecha_baja: string | null;
  notas: string | null;
  creado_en: string;
  actualizado_en: string;
}

/** 422 si el residual >= costo, falta la vida útil en línea recta o se deprecia antes de la adquisición. */
export interface ActivoFijoPayload {
  descripcion: string;
  categoria: CategoriaDgii;
  fecha_adquisicion: string;
  costo: string;
  valor_residual?: string;
  /** Por defecto "dgii". */
  metodo?: MetodoDepreciacion;
  /** Obligatoria en línea recta. */
  vida_util_meses?: number | null;
  /** Por defecto el mes siguiente al de adquisición. */
  depreciar_desde?: string | null;
  /** Ya depreciado al registrarlo (hasta el mes anterior a `depreciar_desde`). */
  depreciacion_acumulada_inicial?: string;
  cuenta_activo_id?: string | null;
  /** Renglón del 606 de la compra (606 · 10 Adquisiciones de activos). */
  registro606_id?: string | null;
  numero_serie?: string | null;
  ubicacion?: string | null;
  notas?: string | null;
}

/** Solo lo enviado cambia; con depreciación registrada, costo/categoría/método/fechas dan 409. */
export type ActivoFijoActualizar = Partial<ActivoFijoPayload>;

export interface CuotaDepreciacion {
  periodo: string;
  cuota: string;
  acumulada: string;
  valor_en_libros: string;
  registrada: boolean;
  monto_registrado: string | null;
  asiento_numero: string | null;
}

export interface TablaDepreciacion {
  activo: ActivoFijo;
  hasta: string;
  cuotas: CuotaDepreciacion[];
  /** Depreciación fiscal (método DGII) de cada año: lo que suma al IR-2. */
  fiscal: { anio: string; depreciacion: string }[];
}

export interface CuotaActivoPeriodo {
  activo_id: string;
  descripcion: string;
  categoria: number;
  metodo: MetodoDepreciacion;
  cuota: string;
  registrada: boolean;
  /** Meses anteriores sin depreciación registrada (se avisa en `advertencias`). */
  meses_sin_registrar_antes: number;
}

export interface DepreciacionPeriodo {
  periodo: string;
  /** "octubre 2026". */
  nombre_periodo: string;
  total: string;
  /** Botón "Depreciación de octubre registrada". */
  registrada: boolean;
  asiento_id: string | null;
  asiento_numero: string | null;
  activos: CuotaActivoPeriodo[];
  advertencias: string[];
}

export interface ResultadoDepreciacion {
  asiento: Asiento;
  total: string;
  activos: CuotaActivoPeriodo[];
  advertencias: string[];
}

export interface DepreciacionFiscal {
  anio: string;
  /** Sugerencia del ajuste "Depreciación del ejercicio" del IR-2 (impuestosApi). */
  total: string;
  activos: { activo_id: string; descripcion: string; categoria: number; tasa_dgii: string; depreciacion: string }[];
}

export const contabilidadApi = {
  // Catálogo
  /** Árbol aplanado ordenado por código, con el saldo de cada cuenta al `hasta` (por defecto hoy). */
  listarCuentas: (p?: { hasta?: string; q?: string; solo_movimiento?: boolean; activa?: boolean }) =>
    request<CuentaContable[]>(`/contabilidad/cuentas${consulta({ ...p })}`),
  obtenerCuenta: (id: string) => request<CuentaContable>(`/contabilidad/cuentas/${id}`),
  crearCuenta: (payload: CuentaContablePayload) =>
    request<CuentaContable>("/contabilidad/cuentas", { method: "POST", body: JSON.stringify(payload) }),
  actualizarCuenta: (id: string, payload: CuentaContableActualizar) =>
    request<CuentaContable>(`/contabilidad/cuentas/${id}`, { method: "PUT", body: JSON.stringify(payload) }),
  /** 409 si tiene subcuentas, movimientos o es cuenta por defecto / de un banco / de un activo. */
  eliminarCuenta: (id: string) => request<void>(`/contabilidad/cuentas/${id}`, { method: "DELETE" }),
  listarCuentasPorDefecto: () => request<CuentaPorDefecto[]>("/contabilidad/cuentas-por-defecto"),
  /** concepto -> cuenta (null = quitar). 422 si la cuenta es de grupo o inactiva. Los asientos se actualizan al volver a generarlos. */
  actualizarCuentasPorDefecto: (valores: Record<string, string | null>) =>
    request<CuentaPorDefecto[]>("/contabilidad/cuentas-por-defecto", {
      method: "PUT",
      body: JSON.stringify({ valores }),
    }),

  // Asientos
  listarAsientos: (
    p?: Rango & {
      origen?: OrigenAsiento | "automatico";
      estado?: EstadoAsiento;
      q?: string;
      cuenta_id?: string;
      limit?: number;
      offset?: number;
    },
  ) => request<AsientoResumen[]>(`/contabilidad/asientos${consulta({ ...p })}`),
  obtenerAsiento: (id: string) => request<Asiento>(`/contabilidad/asientos/${id}`),
  crearAsiento: (payload: AsientoPayload) =>
    request<Asiento>("/contabilidad/asientos", { method: "POST", body: JSON.stringify(payload) }),
  /** Solo borradores manuales (409). */
  actualizarAsiento: (id: string, payload: AsientoPayload) =>
    request<Asiento>(`/contabilidad/asientos/${id}`, { method: "PUT", body: JSON.stringify(payload) }),
  /** 422 si no cuadra; 409 si no es borrador. */
  contabilizarAsiento: (id: string) =>
    request<Asiento>(`/contabilidad/asientos/${id}/contabilizar`, { method: "POST" }),
  /**
   * Manuales contabilizados y depreciaciones (permite recalcular el mes). 409 para los demás automáticos: se anulan
   * solos al anular el documento y volver a generar.
   */
  anularAsiento: (id: string, motivo?: string) =>
    request<Asiento>(`/contabilidad/asientos/${id}/anular`, {
      method: "POST",
      body: JSON.stringify({ motivo: motivo ?? null }),
    }),
  /** Solo borradores (409). */
  eliminarAsiento: (id: string) => request<void>(`/contabilidad/asientos/${id}`, { method: "DELETE" }),
  /** "Generar asientos del período". */
  generarAsientos: (desde: string, hasta: string) =>
    request<ResultadoGeneracion>("/contabilidad/asientos/generar", {
      method: "POST",
      body: JSON.stringify({ desde, hasta }),
    }),
  /** Lo que haría generarAsientos, sin hacerlo, y los documentos no contabilizables con su motivo. */
  pendientesDeContabilizar: (p?: Rango) =>
    request<ResultadoGeneracion>(`/contabilidad/asientos/pendientes${consulta({ ...p })}`),

  // Reportes
  resumen: (p?: Rango) => request<ResumenContabilidad>(`/contabilidad/resumen${consulta({ ...p })}`),
  libroDiario: (p?: Rango & { limit?: number; offset?: number }) =>
    request<LibroDiario>(`/contabilidad/libro-diario${consulta({ ...p })}`),
  mayor: (cuentaId: string, p?: Rango) =>
    request<Mayor>(`/contabilidad/mayor${consulta({ cuenta_id: cuentaId, ...p })}`),
  /** `nivel` = profundidad máxima de las filas (los totales no cambian). */
  balanza: (p?: Rango & { nivel?: number }) => request<Balanza>(`/contabilidad/balanza${consulta({ ...p })}`),
  estadoResultados: (p?: Rango) =>
    request<EstadoResultados>(`/contabilidad/estado-resultados${consulta({ ...p })}`),
  balanceGeneral: (fecha?: string) =>
    request<BalanceGeneral>(`/contabilidad/balance-general${consulta({ fecha })}`),

  // Activos fijos
  listarActivos: (p?: { estado?: "activo" | "depreciado" | "baja" }) =>
    request<ActivoFijo[]>(`/contabilidad/activos-fijos${consulta({ ...p })}`),
  obtenerActivo: (id: string) => request<ActivoFijo>(`/contabilidad/activos-fijos/${id}`),
  crearActivo: (payload: ActivoFijoPayload) =>
    request<ActivoFijo>("/contabilidad/activos-fijos", { method: "POST", body: JSON.stringify(payload) }),
  actualizarActivo: (id: string, payload: ActivoFijoActualizar) =>
    request<ActivoFijo>(`/contabilidad/activos-fijos/${id}`, { method: "PUT", body: JSON.stringify(payload) }),
  /** 409 si tiene depreciación registrada (dele de baja). */
  eliminarActivo: (id: string) => request<void>(`/contabilidad/activos-fijos/${id}`, { method: "DELETE" }),
  /** Deja de depreciarse desde el mes siguiente; `fecha: null` revierte la baja. 409 si hay depreciación registrada después. */
  darBajaActivo: (id: string, fecha: string | null) =>
    request<ActivoFijo>(`/contabilidad/activos-fijos/${id}/baja`, { method: "POST", body: JSON.stringify({ fecha }) }),
  /** Cuadro mensual hasta `hasta` (AAAAMM; por defecto diciembre del año en curso). */
  tablaDepreciacion: (id: string, hasta?: string) =>
    request<TablaDepreciacion>(`/contabilidad/activos-fijos/${id}/depreciacion${consulta({ hasta })}`),
  /** Vista previa del botón "Calcular depreciación de <mes>". */
  depreciacionDelPeriodo: (periodo: string) =>
    request<DepreciacionPeriodo>(`/contabilidad/depreciacion${consulta({ periodo })}`),
  /** Asiento de depreciación del mes. 409 si ya está registrada; 422 si es un mes futuro o no hay nada que depreciar. */
  registrarDepreciacion: (periodo: string) =>
    request<ResultadoDepreciacion>("/contabilidad/depreciacion", { method: "POST", body: JSON.stringify({ periodo }) }),
  /** Depreciación fiscal del año (Art. 287): es el valor sugerido del ajuste "depreciacion" del IR-2. */
  depreciacionFiscal: (anio: string) =>
    request<DepreciacionFiscal>(`/contabilidad/depreciacion/fiscal${consulta({ anio })}`),
};
