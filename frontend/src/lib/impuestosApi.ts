// Cliente de los endpoints de impuestos de `erp-api` (formatos 607 y 608,
// pre-cálculos IT-1, IR-17 e IR-2 y el registro de declaraciones
// presentadas). CONTRATO entre services/erp_api (router de impuestos) y las
// pantallas de src/pages/impuestos: rutas, campos y tipos deben coincidir
// 1:1 con el backend. Mismas convenciones que erpApi.ts: montos como string
// decimal con 2 decimales (pueden ser negativos en los pre-cálculos),
// fechas "YYYY-MM-DD", periodo mensual "AAAAMM", año "AAAA".
import { fetchConSesion } from "./sesion";
import { ERP_API_URL, ErpApiError } from "./erpApi";

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

/** Descarga un archivo de texto del backend: devuelve el contenido y el nombre que propone el servidor (Content-Disposition). */
async function descargar(path: string): Promise<{ nombre: string; contenido: string }> {
  const response = await fetchConSesion(`${ERP_API_URL}${path}`);
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = body.detail ?? JSON.stringify(body);
    } catch {
      // sin cuerpo JSON
    }
    throw new ErpApiError(response.status, typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  const disposicion = response.headers.get("Content-Disposition") ?? "";
  const nombre = /filename="?([^";]+)"?/.exec(disposicion)?.[1] ?? "formato.txt";
  return { nombre, contenido: await response.text() };
}

// --- Formato 607 (ventas) -------------------------------------------------

/** Un renglón del 607 (Norma General 07-2018): los 23 campos del detalle, en el orden del archivo. */
export interface Fila607 {
  factura_id: string | null;
  rnc_cedula: string;
  /** 1 = RNC, 2 = cédula, 3 = pasaporte / identificador tributario. */
  tipo_id: number;
  ncf: string;
  ncf_modificado: string | null;
  /** 01–06, el TipoIngresos del e-CF. */
  tipo_ingreso: string;
  fecha_comprobante: string;
  fecha_retencion: string | null;
  monto_facturado: string;
  itbis_facturado: string;
  itbis_retenido_terceros: string;
  itbis_percibido: string;
  retencion_renta_terceros: string;
  isr_percibido: string;
  impuesto_selectivo_consumo: string;
  otros_impuestos: string;
  monto_propina_legal: string;
  efectivo: string;
  cheque_transferencia: string;
  tarjeta: string;
  venta_credito: string;
  bonos_certificados: string;
  permuta: string;
  otras_formas: string;
  /** Datos de presentación que no van al archivo. */
  cliente_nombre: string | null;
  tipo_ecf: number | null;
}

export interface Formato607 {
  periodo: string;
  rnc_emisor: string;
  filas: Fila607[];
  totales: {
    cantidad: number;
    monto_facturado: string;
    itbis_facturado: string;
    itbis_retenido_terceros: string;
    retencion_renta_terceros: string;
  };
  /** Problemas que impiden o desaconsejan enviar el archivo (p. ej. comprobantes sin RNC de comprador sobre el tope). */
  advertencias: string[];
}

// --- Formato 608 (comprobantes anulados) ----------------------------------

export type OrigenAnulacion = "anulacion_encf" | "manual";

export interface Fila608 {
  /** id del registro manual (null para los que vienen de anulaciones e-NCF enviadas a la DGII). */
  id: string | null;
  origen: OrigenAnulacion;
  ncf: string;
  fecha_comprobante: string;
  /** "01".."10" según la tabla de tipos de anulación de la DGII. */
  tipo_anulacion: string;
  tipo_anulacion_label: string;
}

export interface Formato608 {
  periodo: string;
  rnc_emisor: string;
  filas: Fila608[];
  advertencias: string[];
}

export interface Anulacion608Payload {
  ncf: string;
  fecha_comprobante: string;
  tipo_anulacion: string;
}

export interface TipoAnulacion {
  codigo: string;
  descripcion: string;
}

// --- Pre-cálculos (IT-1, IR-17, IR-2) --------------------------------------

export type FormularioPreCalculo = "it1" | "ir17" | "ir2";

export interface Casilla {
  /** Número de LÍNEA del pre-cálculo ("1", "2", …): no es la casilla del formulario oficial de la DGII. */
  codigo: string;
  etiqueta: string;
  /** Puede ser negativo (restas). */
  valor: string;
  /** "detalle": "de ellos" de la línea anterior (ya incluido en ella; p. ej. facturas en papel, serie B). */
  tipo: "normal" | "subtotal" | "total" | "detalle";
  ayuda: string | null;
}

export interface SeccionPreCalculo {
  /** Numeral romano: "I", "II", … */
  numero: string;
  titulo: string;
  casillas: Casilla[];
}

/** Valor que el sistema no puede saber y el usuario ingresa (saldo a favor anterior, anticipos, inventario inicial, ajustes fiscales…). */
export interface AjusteEditable {
  clave: string;
  etiqueta: string;
  /** Valor usado en el cálculo: el manual guardado o, si no hay, el sugerido (o 0). */
  valor: string;
  /** true si `valor` es un valor manual guardado; false si es el sugerido por el sistema. */
  manual: boolean;
  /** Valor que calcula el sistema (saldo a favor del mes anterior, valuación del Kardex); null si no sugiere ninguno. */
  sugerido: string | null;
  ayuda: string | null;
}

export interface PreCalculo {
  formulario: FormularioPreCalculo;
  /** "AAAAMM" para IT-1 e IR-17; "AAAA" para IR-2. */
  periodo: string;
  titulo: string;
  subtitulo: string;
  nota: string;
  vence: string;
  secciones: SeccionPreCalculo[];
  resultado: { etiqueta: string; valor: string };
  /** Tarjeta lateral de resumen del diseño. */
  resumen: { etiqueta: string; valor: string }[];
  ajustes: AjusteEditable[];
  advertencias: string[];
}

// --- Declaraciones (calendario y registro de presentadas) -----------------

export type FormularioDeclaracion = "606" | "607" | "608" | "it1" | "ir17" | "ir2";
/** "no_aplica": 607/608 de un emisor 100 % electrónico (EMISOR_100_ELECTRONICO de erp-api); nunca vence. */
export type EstadoDeclaracion = "pendiente" | "presentada" | "vencida" | "no_aplica";

export interface Declaracion {
  formulario: FormularioDeclaracion;
  periodo: string;
  vence: string;
  estado: EstadoDeclaracion;
  presentada_en: string | null;
  monto: string | null;
  referencia: string | null;
}

export interface MarcarPresentadaPayload {
  monto?: string | null;
  /** Número de confirmación / acuse de la Oficina Virtual. */
  referencia?: string | null;
  presentada_en?: string;
}

export const impuestosApi = {
  formato607: (periodo: string) => request<Formato607>(`/impuestos/607?periodo=${periodo}`),
  /** TXT listo para la Oficina Virtual (nombre DGII_F_607_<RNC>_<AAAAMM>.TXT). 422 si hay errores bloqueantes. */
  descargar607: (periodo: string) => descargar(`/impuestos/607/txt?periodo=${periodo}`),

  formato608: (periodo: string) => request<Formato608>(`/impuestos/608?periodo=${periodo}`),
  tiposAnulacion: () => request<TipoAnulacion[]>("/impuestos/608/tipos-anulacion"),
  agregarAnulacion608: (payload: Anulacion608Payload) =>
    request<Fila608>("/impuestos/608", { method: "POST", body: JSON.stringify(payload) }),
  eliminarAnulacion608: (id: string) => request<void>(`/impuestos/608/${id}`, { method: "DELETE" }),
  descargar608: (periodo: string) => descargar(`/impuestos/608/txt?periodo=${periodo}`),

  preCalculo: (formulario: FormularioPreCalculo, periodo: string) =>
    request<PreCalculo>(`/impuestos/precalculo/${formulario}?periodo=${periodo}`),
  /**
   * Guarda los valores manuales del período y devuelve el pre-cálculo recalculado. Solo se envían
   * las claves que cambian; "" borra el valor manual y vuelve al sugerido por el sistema.
   */
  guardarAjustes: (formulario: FormularioPreCalculo, periodo: string, valores: Record<string, string>) =>
    request<PreCalculo>(`/impuestos/precalculo/${formulario}/ajustes?periodo=${periodo}`, {
      method: "PUT",
      body: JSON.stringify({ valores }),
    }),

  /** Calendario de declaraciones del año con su estado. */
  listarDeclaraciones: (anio: string) => request<Declaracion[]>(`/impuestos/declaraciones?anio=${anio}`),
  marcarPresentada: (formulario: FormularioDeclaracion, periodo: string, payload: MarcarPresentadaPayload) =>
    request<Declaracion>(`/impuestos/declaraciones/${formulario}/${periodo}/presentada`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  desmarcarPresentada: (formulario: FormularioDeclaracion, periodo: string) =>
    request<Declaracion>(`/impuestos/declaraciones/${formulario}/${periodo}/presentada`, { method: "DELETE" }),
};
