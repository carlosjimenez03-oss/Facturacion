// Cliente de los endpoints de Bancos de `erp-api` (fase 5): cuentas
// bancarias (corriente, ahorro, caja chica, tarjeta), movimientos,
// transferencias entre cuentas, importación del extracto bancario (CSV) y
// conciliación. CONTRATO entre services/erp_api (app/bancos.py,
// schemas.py) y las pantallas de Bancos (prototipo Bancos.dc.html: pestañas
// Movimientos, Conciliación, Transferencias): rutas, campos y tipos deben
// coincidir 1:1 con el backend. Mismas convenciones que erpApi.ts: montos
// como string decimal con 2 decimales, fechas "YYYY-MM-DD", marcas de
// tiempo ISO 8601.
//
// Modelo: los movimientos de una cuenta son los registrados en Bancos
// (depósitos, retiros, cargos, intereses, comisiones, transferencias) MÁS
// los cobros (erpApi.registrarCobro) y pagos a proveedores
// (comprasApi.registrarPago) que traen `cuenta_bancaria_id`. Cada
// movimiento se identifica por (`origen`, `id`). Montos con signo: + entra
// a la cuenta, − sale. Saldo actual = saldo inicial + movimientos desde
// `fecha_saldo_inicial`. Una conciliación cerrada bloquea (409) lo que
// Bancos registra con fecha <= su corte hasta reabrirla.
import { ERP_API_URL, ErpApiError } from "./erpApi";
import { textoDetalle } from "./comprasApi";

async function respuesta<T>(response: Response): Promise<T> {
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

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  return respuesta<T>(
    await fetch(`${ERP_API_URL}${path}`, { headers: { "Content-Type": "application/json" }, ...init }),
  );
}

/** POST multipart (archivo CSV + campos): sin Content-Type, el navegador pone el boundary. */
async function enviarArchivo<T>(path: string, archivo: File | Blob, campos: Record<string, string | undefined>) {
  const form = new FormData();
  form.append("archivo", archivo, archivo instanceof File ? archivo.name : "extracto.csv");
  for (const [k, v] of Object.entries(campos)) {
    if (v !== undefined && v !== "") form.append(k, v);
  }
  return respuesta<T>(await fetch(`${ERP_API_URL}${path}`, { method: "POST", body: form }));
}

function consulta(params: Record<string, string | number | boolean | undefined | null>): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") q.set(k, String(v));
  }
  const s = q.toString();
  return s ? `?${s}` : "";
}

// --- Cuentas bancarias ----------------------------------------------------

export type TipoCuentaBancaria = "corriente" | "ahorro" | "caja_chica" | "tarjeta";
export type Moneda = "DOP" | "USD";

export interface CuentaBancaria {
  id: string;
  /** "Banco Popular" (por defecto "<banco> · <tipo>"). */
  nombre: string;
  banco: string;
  tipo: TipoCuentaBancaria;
  /** Solo los últimos 4 caracteres: "****4471". El número completo nunca se devuelve. */
  numero_enmascarado: string | null;
  moneda: Moneda;
  saldo_inicial: string;
  fecha_saldo_inicial: string;
  /** Cuenta del catálogo; null = los asientos usan la cuenta por defecto "banco", "caja_chica" o "tarjeta_credito". */
  cuenta_contable_id: string | null;
  cuenta_contable_codigo: string | null;
  cuenta_contable_nombre: string | null;
  activa: boolean;
  notas: string | null;
  /** Saldo inicial + movimientos (en la moneda de la cuenta). */
  saldo_actual: string;
  /** Para la tarjeta del prototipo: "3 transacciones sin conciliar". */
  movimientos_sin_conciliar: number;
  /** Líneas de extractos importados que aún no se conciliaron. */
  lineas_sin_conciliar: number;
  ultimo_movimiento: string | null;
  /** Corte de la última conciliación cerrada ("Conciliada al 30/09"); null si nunca. */
  conciliada_hasta: string | null;
  creado_en: string;
  actualizado_en: string;
}

export interface CuentaBancariaPayload {
  nombre?: string | null;
  banco: string;
  tipo: TipoCuentaBancaria;
  numero?: string | null;
  /** Por defecto DOP. La caja chica solo puede ser en DOP (422). */
  moneda?: Moneda;
  saldo_inicial?: string;
  /** Saldo al empezar ese día; por defecto hoy. Los movimientos no pueden ser anteriores. */
  fecha_saldo_inicial?: string;
  /** Debe ser una cuenta de activo o pasivo que acepte movimientos (422). */
  cuenta_contable_id?: string | null;
  activa?: boolean;
  notas?: string | null;
}

/** Solo los campos enviados cambian (`numero` omitido = se conserva). */
export type CuentaBancariaActualizar = Partial<CuentaBancariaPayload>;

// --- Movimientos ----------------------------------------------------------

export type TipoMovimientoManual = "deposito" | "retiro" | "cargo_bancario" | "interes" | "comision";
export type TipoMovimiento =
  | TipoMovimientoManual
  | "transferencia_entrada"
  | "transferencia_salida"
  | "cobro"
  | "pago";
/** "manual" = registrado en Bancos (incluye las transferencias); "cobro" / "pago" = de Ventas / Compras. */
export type OrigenMovimiento = "manual" | "cobro" | "pago";

export interface MovimientoBancario {
  origen: OrigenMovimiento;
  id: string;
  cuenta_id: string;
  fecha: string;
  tipo: TipoMovimiento;
  /** Con signo: + entra (columna Crédito del prototipo), − sale (Débito). */
  monto: string;
  descripcion: string;
  referencia: string | null;
  transferencia_id: string | null;
  /** Factura (cobro) o renglón del 606 (pago). */
  documento_id: string | null;
  conciliado: boolean;
  extracto_linea_id: string | null;
  /** Balance después del movimiento (solo en `listarMovimientos`). */
  saldo: string | null;
  anulado: boolean;
  creado_en: string;
}

export interface MovimientosCuenta {
  cuenta: CuentaBancaria;
  desde: string | null;
  hasta: string | null;
  /** Saldo al empezar `desde`. */
  saldo_inicial: string;
  entradas: string;
  salidas: string;
  /** Saldo al terminar `hasta` (sin `hasta`: el actual). */
  saldo_final: string;
  /** Más recientes primero. */
  movimientos: MovimientoBancario[];
}

export interface MovimientoPayload {
  cuenta_id: string;
  /** Por defecto hoy. 409 si cae en una conciliación cerrada; 422 si es anterior al saldo inicial. */
  fecha?: string;
  tipo: TipoMovimientoManual;
  /** Positivo: el tipo dice si entra (depósito, interés) o sale (retiro, cargo, comisión). */
  monto: string;
  descripcion?: string | null;
  referencia?: string | null;
  /** Contrapartida del asiento; sin ella: Caja (depósito/retiro), gastos bancarios (cargo/comisión), ingresos financieros (interés). */
  cuenta_contable_id?: string | null;
  /** RD$ por US$: obligatoria en cuentas en dólares (422). */
  tasa_cambio?: string | null;
}

// --- Transferencias -------------------------------------------------------

export interface Transferencia {
  id: string;
  cuenta_origen_id: string;
  /** "Banco Popular ****4471". */
  cuenta_origen_nombre: string;
  moneda_origen: Moneda;
  cuenta_destino_id: string;
  cuenta_destino_nombre: string;
  moneda_destino: Moneda;
  fecha: string;
  monto: string;
  monto_destino: string;
  tasa_cambio: string | null;
  concepto: string | null;
  referencia: string | null;
  anulada: boolean;
  anulada_en: string | null;
  creado_en: string;
}

/**
 * Crea la transferencia y sus dos movimientos en una sola transacción (o los dos o ninguno). 422 si las cuentas son
 * la misma, alguna está inactiva o la fecha es anterior a su saldo inicial; entre monedas distintas exige
 * `monto_destino` (la tasa sale de los dos montos) y entre dos cuentas en USD, `tasa_cambio`. 409 si alguna está
 * conciliada en esa fecha.
 */
export interface TransferenciaPayload {
  cuenta_origen_id: string;
  cuenta_destino_id: string;
  fecha?: string;
  monto: string;
  monto_destino?: string | null;
  tasa_cambio?: string | null;
  concepto?: string | null;
  referencia?: string | null;
}

// --- Extractos (CSV) ------------------------------------------------------

/**
 * Columnas del CSV por nombre (como en el encabezado) o por posición (0 = primera). Todo opcional: lo que no se indica
 * se detecta por sinónimos ("Fecha Posteo", "Descripción", "No. Referencia", "Débito"/"Crédito" o "Monto", "Balance",
 * columna D/C). Sin `formato_fecha` se lee día primero (DD/MM/AAAA) o ISO.
 */
export interface MapeoExtracto {
  fecha?: string | number;
  descripcion?: string | number;
  referencia?: string | number;
  debito?: string | number;
  credito?: string | number;
  /** Una sola columna con signo (o con `tipo` D/C). */
  monto?: string | number;
  saldo?: string | number;
  tipo?: string | number;
  formato_fecha?: "DD/MM/AAAA" | "MM/DD/AAAA" | "AAAA-MM-DD";
  separador_decimal?: "." | ",";
  /** Para `monto`: por defecto positivo = crédito (entra). */
  signo_monto?: "credito_positivo" | "debito_positivo";
  /** Número de fila del encabezado (1 = primera), si hay títulos antes. */
  fila_encabezado?: number;
  separador?: "," | ";" | "\t" | "|";
}

export interface LineaLeida {
  fila: number;
  fecha: string;
  descripcion: string;
  referencia: string | null;
  monto: string;
  saldo: string | null;
}

export interface ErrorFila {
  fila: number;
  motivo: string;
}

export interface PrevisualizacionExtracto {
  /** ",", ";", "tabulador" o "|". */
  separador: string;
  fila_encabezado: number;
  columnas: string[];
  /** Campo -> columna usada (nombre del encabezado) o null. */
  mapeo: Record<string, string | null>;
  total_lineas: number;
  /** Las primeras 20. */
  lineas: LineaLeida[];
  errores: ErrorFila[];
}

export interface Extracto {
  id: string;
  cuenta_id: string;
  nombre_archivo: string | null;
  desde: string | null;
  hasta: string | null;
  saldo_final: string | null;
  importado_en: string;
  lineas: number;
  conciliadas: number;
}

export interface ResultadoImportacion {
  separador: string;
  fila_encabezado: number;
  columnas: string[];
  mapeo: Record<string, string | null>;
  /** null si no quedó ninguna línea nueva (todas ya importadas u omitidas). */
  extracto: Extracto | null;
  importadas: number;
  /** Ya estaban (volver a subir el mismo archivo no duplica). */
  duplicadas: number;
  /** Filas que no se pudieron leer o de fechas ya conciliadas. */
  errores: ErrorFila[];
}

// --- Conciliación ---------------------------------------------------------

export interface MovimientoConciliado {
  origen: OrigenMovimiento;
  id: string;
  fecha: string;
  tipo: TipoMovimiento;
  monto: string;
  descripcion: string;
  referencia: string | null;
}

export interface LineaExtracto {
  id: string;
  extracto_id: string;
  cuenta_id: string;
  fila: number | null;
  fecha: string;
  descripcion: string;
  referencia: string | null;
  /** + crédito del banco (entra), − débito (sale). */
  monto: string;
  saldo: string | null;
  conciliada: boolean;
  conciliada_en: string | null;
  /** "Registro en el sistema" del prototipo; null = sin conciliar. */
  movimiento: MovimientoConciliado | null;
}

export interface ConciliacionCerrada {
  id: string;
  cuenta_id: string;
  hasta: string;
  saldo_banco: string;
  saldo_libros: string;
  transito_entradas: string;
  transito_salidas: string;
  diferencia: string;
  lineas_conciliadas: number;
  movimientos_en_transito: number;
  notas: string | null;
  cerrada_en: string;
}

export interface ResumenConciliacion {
  /** Saldo en libros al corte. */
  saldo_libros: string;
  /** Registrado en libros y aún no reflejado por el banco al corte (depósitos en tránsito / cheques en circulación). */
  transito_entradas: string;
  transito_salidas: string;
  movimientos_en_transito: number;
  /** saldo_libros − tránsito: debe ser igual a saldo_banco. */
  libros_ajustado: string;
  lineas_total: number;
  /** "N de M conciliados" del prototipo: lineas_conciliadas de lineas_total. */
  lineas_conciliadas: number;
  /** Líneas del banco sin conciliar al corte: impiden cerrar. */
  lineas_pendientes: number;
  saldo_banco: string;
  origen_saldo_banco: "extracto" | "ultima_linea" | "calculado";
  diferencia: string;
  /** Sin líneas pendientes, diferencia 0 y corte posterior a la última conciliación. */
  puede_cerrar: boolean;
}

export interface Conciliacion {
  cuenta: CuentaBancaria;
  conciliada_hasta: string | null;
  /** Corte usado (por defecto la fecha de la última línea importada, o hoy). */
  hasta: string;
  /** Líneas posteriores a la última conciliación cerrada, hasta el corte. */
  lineas: LineaExtracto[];
  movimientos_sin_conciliar: MovimientoBancario[];
  resumen: ResumenConciliacion;
  ultima_conciliacion: ConciliacionCerrada | null;
}

export interface SugerenciaConciliacion {
  linea: LineaExtracto;
  movimiento: MovimientoBancario;
  /** 100 − 10 por día de diferencia, + 50 si coincide la referencia. */
  puntaje: number;
  /** "Mismo monto, 1 día(s) de diferencia, referencia coincide". */
  motivo: string;
}

export interface ResultadoConciliacionAuto {
  conciliadas: number;
  sugerencias: SugerenciaConciliacion[];
}

export interface MovimientoDesdeLineaPayload {
  /** Por defecto: depósito si la línea es un crédito, cargo bancario si es un débito (422 si el tipo no corresponde). */
  tipo?: TipoMovimientoManual;
  descripcion?: string | null;
  cuenta_contable_id?: string | null;
  tasa_cambio?: string | null;
}

export interface CerrarConciliacionPayload {
  /** Corte (no futuro; posterior a la última conciliación). */
  hasta: string;
  /** Sin él: el saldo final indicado al importar, el de la última línea del CSV o el calculado. */
  saldo_banco?: string | null;
  notas?: string | null;
}

export const bancosApi = {
  // Cuentas
  listarCuentas: (p?: { activa?: boolean }) => request<CuentaBancaria[]>(`/bancos/cuentas${consulta({ ...p })}`),
  obtenerCuenta: (id: string) => request<CuentaBancaria>(`/bancos/cuentas/${id}`),
  crearCuenta: (payload: CuentaBancariaPayload) =>
    request<CuentaBancaria>("/bancos/cuentas", { method: "POST", body: JSON.stringify(payload) }),
  /** 409 si cambia el saldo inicial con conciliaciones cerradas, la moneda con movimientos o la fecha queda después de un movimiento. */
  actualizarCuenta: (id: string, payload: CuentaBancariaActualizar) =>
    request<CuentaBancaria>(`/bancos/cuentas/${id}`, { method: "PUT", body: JSON.stringify(payload) }),
  /** 409 si tiene movimientos, cobros, pagos o extractos: desactívela (`activa: false`). */
  eliminarCuenta: (id: string) => request<void>(`/bancos/cuentas/${id}`, { method: "DELETE" }),

  // Movimientos
  listarMovimientos: (
    cuentaId: string,
    p?: { desde?: string; hasta?: string; conciliado?: boolean; q?: string; limit?: number; offset?: number },
  ) => request<MovimientosCuenta>(`/bancos/cuentas/${cuentaId}/movimientos${consulta({ ...p })}`),
  registrarMovimiento: (payload: MovimientoPayload) =>
    request<MovimientoBancario>("/bancos/movimientos", { method: "POST", body: JSON.stringify(payload) }),
  /** Solo los registrados en Bancos que no son de una transferencia (409); 409 si su fecha está conciliada. */
  anularMovimiento: (id: string) => request<MovimientoBancario>(`/bancos/movimientos/${id}/anular`, { method: "POST" }),

  // Transferencias
  listarTransferencias: (p?: { cuenta_id?: string }) =>
    request<Transferencia[]>(`/bancos/transferencias${consulta({ ...p })}`),
  registrarTransferencia: (payload: TransferenciaPayload) =>
    request<Transferencia>("/bancos/transferencias", { method: "POST", body: JSON.stringify(payload) }),
  /** Anula los dos movimientos juntos. 409 si ya estaba anulada o alguna cuenta está conciliada en esa fecha. */
  anularTransferencia: (id: string) =>
    request<Transferencia>(`/bancos/transferencias/${id}/anular`, { method: "POST" }),

  // Extractos
  /** Lee el CSV sin guardarlo (pantalla de mapeo de columnas). 422 si no encuentra el encabezado; 413 si pasa de 5 MB. */
  previsualizarExtracto: (archivo: File | Blob, mapeo?: MapeoExtracto) =>
    enviarArchivo<PrevisualizacionExtracto>("/bancos/extractos/previsualizar", archivo, {
      mapeo: mapeo ? JSON.stringify(mapeo) : undefined,
    }),
  /** Importa las líneas a la cuenta; `saldo_final` = saldo del estado de cuenta al cierre (opcional). */
  importarExtracto: (cuentaId: string, archivo: File | Blob, p?: { mapeo?: MapeoExtracto; saldo_final?: string }) =>
    enviarArchivo<ResultadoImportacion>(`/bancos/cuentas/${cuentaId}/extractos`, archivo, {
      mapeo: p?.mapeo ? JSON.stringify(p.mapeo) : undefined,
      saldo_final: p?.saldo_final,
    }),
  listarExtractos: (cuentaId: string) => request<Extracto[]>(`/bancos/cuentas/${cuentaId}/extractos`),
  /** 409 si tiene líneas conciliadas o dentro de una conciliación cerrada. */
  eliminarExtracto: (id: string) => request<void>(`/bancos/extractos/${id}`, { method: "DELETE" }),

  // Conciliación
  conciliacion: (cuentaId: string, p?: { hasta?: string }) =>
    request<Conciliacion>(`/bancos/cuentas/${cuentaId}/conciliacion${consulta({ ...p })}`),
  /** Pares línea ↔ movimiento con el mismo monto y fechas a ±`dias` (3 por defecto), sin aplicarlos. */
  sugerencias: (cuentaId: string, p?: { dias?: number; hasta?: string }) =>
    request<SugerenciaConciliacion[]>(`/bancos/cuentas/${cuentaId}/conciliacion/sugerencias${consulta({ ...p })}`),
  /** Aplica las sugerencias (todas o solo las de `lineas`). */
  conciliarAutomatico: (cuentaId: string, p?: { dias?: number; hasta?: string; lineas?: string[] }) =>
    request<ResultadoConciliacionAuto>(`/bancos/cuentas/${cuentaId}/conciliacion/auto`, {
      method: "POST",
      body: JSON.stringify(p ?? {}),
    }),
  /**
   * Concilia una línea con un movimiento de la misma cuenta. 422 si los montos no coinciden (registre la diferencia
   * aparte) o el movimiento es de otra cuenta; 409 si la línea o el movimiento ya están conciliados o la línea está
   * en una conciliación cerrada.
   */
  conciliarLinea: (lineaId: string, origen: OrigenMovimiento, movimientoId: string) =>
    request<LineaExtracto>(`/bancos/extracto-lineas/${lineaId}/conciliar`, {
      method: "POST",
      body: JSON.stringify({ origen, movimiento_id: movimientoId }),
    }),
  desconciliarLinea: (lineaId: string) =>
    request<LineaExtracto>(`/bancos/extracto-lineas/${lineaId}/desconciliar`, { method: "POST" }),
  /** "Sin registro: crear gasto bancario": registra la línea como movimiento y la deja conciliada. */
  crearMovimientoDesdeLinea: (lineaId: string, payload: MovimientoDesdeLineaPayload = {}) =>
    request<LineaExtracto>(`/bancos/extracto-lineas/${lineaId}/movimiento`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  listarConciliaciones: (cuentaId: string) =>
    request<ConciliacionCerrada[]>(`/bancos/cuentas/${cuentaId}/conciliaciones`),
  /**
   * "Cerrar conciliación". 409 si quedan líneas del extracto sin conciliar hasta el corte, si el saldo según el banco no
   * cuadra con libros menos las partidas en tránsito, o si ya está conciliada hasta esa fecha; 422 si el corte es futuro.
   */
  cerrarConciliacion: (cuentaId: string, payload: CerrarConciliacionPayload) =>
    request<ConciliacionCerrada>(`/bancos/cuentas/${cuentaId}/conciliaciones`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  /** Reabre (borra) la conciliación; solo la última de la cuenta (409). */
  reabrirConciliacion: (id: string) => request<void>(`/bancos/conciliaciones/${id}`, { method: "DELETE" }),
};
