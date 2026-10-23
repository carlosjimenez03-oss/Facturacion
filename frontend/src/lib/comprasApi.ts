// Cliente de los endpoints de Compras de `erp-api` (fase 3): bandeja de
// entrada de e-CF recibidos (aprobación comercial), facturas de compra y
// cuentas por pagar, pagos a proveedores con retenciones, comprobantes E41 /
// E43 y retenciones del mes con su certificado. CONTRATO entre
// services/erp_api (app/compras.py, schemas.py) y las pantallas de Compras:
// rutas, campos y tipos deben coincidir 1:1 con el backend. Mismas
// convenciones que erpApi.ts: montos como string decimal con 2 decimales,
// fechas "YYYY-MM-DD", marcas de tiempo ISO 8601, periodo mensual "AAAAMM".
//
// Modelo: una factura de compra ES un renglón del Formato 606
// (`registro606_id`). Llega sola de un e-CF recibido (bandeja) o de un E41
// emitido en /facturas/nueva?tipo=41, o se registra con `crearFactura`
// cuando el proveedor tiene NCF/e-NCF propio. Pagarla con retenciones
// (`registrarPago`) actualiza el 606 — fecha de pago, ITBIS retenido, tipo y
// monto de retención ISR, forma de pago — y con eso el IT-1 y el IR-17 del
// mes del pago. Anular el pago lo revierte.
import { ERP_API_URL, ErpApiError } from "./erpApi";

/**
 * `detail` de un error de FastAPI como texto legible: un string tal cual; la lista de errores de validación
 * (422, `[{loc, msg}]`) como "campo: mensaje · campo: mensaje" (sin "body" ni el "Value error, " de pydantic).
 */
export function textoDetalle(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const textos = detail.map((e) => {
      if (typeof e === "string") return e;
      const error = (e ?? {}) as { loc?: (string | number)[]; msg?: string };
      const campo = (error.loc ?? []).filter((x) => x !== "body").join(".");
      const msg = (error.msg ?? "").replace(/^Value error, /, "");
      return campo ? `${campo}: ${msg}` : msg;
    });
    if (textos.length) return textos.join(" · ");
  }
  return JSON.stringify(detail);
}

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

// --- Bandeja de entrada (e-CF recibidos de proveedores) -------------------

/** Aprobación comercial (ACECF) que decide el usuario. El acuse de recibo (ARECF) ya se envió solo al recibirlo. */
export type EstadoAprobacion = "pendiente" | "aprobado" | "rechazado";

export interface EcfRecibidoResumen {
  id: string;
  rnc_emisor: string;
  razon_social_emisor: string | null;
  rnc_comprador: string | null;
  encf: string;
  /** 31, 33, 34, 44, 45… (del TipoeCF o del e-NCF). */
  tipo_ecf: number | null;
  fecha_emision: string | null;
  fecha_limite_pago: string | null;
  monto_total: string;
  itbis_total: string;
  recibido_en: string;
  estado_aprobacion: EstadoAprobacion;
  /** Motivo enviado al proveedor (solo rechazados). */
  motivo_rechazo: string | null;
  /** Fecha de la decisión (aprobación o rechazo). */
  aprobado_en: string | null;
  /** Último intento fallido de enviar la aprobación/rechazo a la DGII; el e-CF sigue pendiente. */
  error_aprobacion: string | null;
  /** Renglón del Formato 606 (= la factura de compra); se crea solo al recibir el e-CF. */
  compra606_id: string | null;
  /** Compra de inventario (recepción de mercancía) vinculada. */
  compra_inventario_id: string | null;
  /**
   * Firma digital del XML verificada al recibirlo: true = íntegra y el SN del certificado es el RNC emisor;
   * false = sin firma, alterada o de otro certificado (ver `firma_detalle`); null = no verificada.
   */
  firma_valida: boolean | null;
  firma_detalle: string | null;
  /** La aprobación/rechazo se está enviando a la DGII (estado_aprobacion sigue "pendiente"); los botones deben esperar. */
  envio_en_curso: boolean;
  /** El ACECF aceptado por la DGII se entregó también al proveedor (best-effort; no afecta la aprobación). */
  enviado_al_emisor: boolean;
  /** Por qué no se pudo entregar al proveedor. */
  error_envio_emisor: string | null;
}

export interface ItemEcfRecibido {
  linea: number;
  descripcion: string;
  cantidad: string;
  precio_unitario: string;
  monto: string;
  bien_o_servicio: "bien" | "servicio";
  /** IndicadorFacturacion del e-CF: "1" = ITBIS 18 %, "2" = 16 %, "3" = 0 %, "4" = exento. */
  indicador_facturacion: string | null;
}

export interface EcfRecibido extends EcfRecibidoResumen {
  items: ItemEcfRecibido[];
  /** monto_total − itbis_total. */
  subtotal: string;
  /** El XML tal como lo envió el proveedor. */
  xml: string;
  /** Respuesta de la DGII al ACECF ({ mensaje, estado, codigo }). */
  respuesta_dgii: Record<string, unknown> | null;
}

export interface VincularPayload {
  /** Renglón del 606 existente del mismo proveedor; si se omite, se usa (o se crea desde el XML) el del e-CF. */
  registro606_id?: string | null;
  /** inventario.compras.id; null lo desvincula; omitido, no cambia. */
  compra_inventario_id?: string | null;
}

// --- Cuentas por pagar / facturas de compra ------------------------------

export type EstadoCuentaPagar = "pendiente" | "vencida" | "pagada";
export type MetodoPago = "efectivo" | "transferencia" | "tarjeta" | "cheque";

/**
 * Una factura de compra por pagar (renglón del 606 que no es nota de crédito, ni gasto menor E43, ni e-CF
 * rechazado). pendiente = total − notas de crédito del proveedor − retenciones − pagado.
 */
export interface CuentaPorPagar {
  /** Identificador de la factura de compra (id del renglón del 606). */
  registro606_id: string;
  ncf: string;
  tipo_ecf: number | null;
  /** AAAAMM del comprobante (periodo del 606). */
  periodo: string;
  /** "receptor_recepcion" (e-CF recibido) | "auto_emitido" (E41/E47) | "compras" | "manual" (606). */
  origen: string;
  proveedor_rnc: string;
  proveedor_nombre: string;
  contacto_id: string | null;
  compra_inventario_id: string | null;
  ecf_recibido_id: string | null;
  estado_aprobacion: EstadoAprobacion | null;
  /** facturacion.facturas.id del E41/E47 autoemitido. */
  factura_id: string | null;
  concepto: string | null;
  /** Tipo de bienes y servicios del 606 (1-11); null = sin clasificar. */
  tipo_bienes_servicios: number | null;
  fecha_comprobante: string;
  /** FechaLimitePago del e-CF, o la de la factura, o comprobante + días de crédito del contacto (30 por defecto). */
  vence: string;
  /** Sin ITBIS (servicios + bienes). */
  monto_facturado: string;
  itbis_facturado: string;
  total: string;
  notas_credito: string;
  tipo_retencion_isr: number | null;
  tipo_retencion_isr_label: string | null;
  retencion_isr: string;
  retencion_itbis: string;
  retenciones: string;
  pagado: string;
  pendiente: string;
  estado: EstadoCuentaPagar;
  dias_vencida: number;
  /** Fecha de pago del 606 (la del pago con retenciones o, si no hay, la del último pago). */
  fecha_pago: string | null;
  /** Forma de pago del 606 (1-7). */
  forma_pago: number | null;
  /** Retenciones que declara el propio e-CF (E41): para proponerlas al pagar. */
  retencion_isr_ecf: string;
  retencion_itbis_ecf: string;
  /**
   * Nota de crédito del proveedor (B04/E34), registrada con `crearFactura`: solo aparece en el detalle
   * (`obtenerFactura`), nunca en cuentas por pagar; pendiente "0.00" y estado "pagada" (no se paga). Su total
   * se descuenta de la factura que modifica (`notas_credito` de esa factura).
   */
  es_nota_credito: boolean;
  /** NCF de la factura que modifica (solo notas de crédito). */
  ncf_modificado: string | null;
  /** registro606_id de esa factura, si está registrada. */
  afecta_registro606_id: string | null;
}

export interface FacturaCompra extends CuentaPorPagar {
  pagos: PagoProveedor[];
}

/** Factura de un proveedor con NCF/e-NCF propio. Al proveedor informal se le emite un E41 desde /facturas/nueva?tipo=41. */
export interface FacturaCompraPayload {
  /** RNC (9) o cédula (11); se aceptan guiones. */
  proveedor_rnc: string;
  proveedor_nombre?: string | null;
  contacto_id?: string | null;
  compra_inventario_id?: string | null;
  ncf: string;
  ncf_modificado?: string | null;
  fecha_comprobante: string;
  vence?: string | null;
  /** 1-11 (catálogo del 606). */
  tipo_bienes_servicios: number;
  concepto?: string | null;
  monto_servicios?: string;
  monto_bienes?: string;
  itbis_facturado?: string;
  impuesto_selectivo_consumo?: string;
  otros_impuestos?: string;
  monto_propina_legal?: string;
  /** Forma de pago del 606 mientras no se pague (por defecto 4, compra a crédito). */
  forma_pago?: number | null;
}

/** Solo se cambian los campos enviados (null borra el valor). */
export interface FacturaCompraActualizar {
  proveedor_nombre?: string | null;
  contacto_id?: string | null;
  compra_inventario_id?: string | null;
  concepto?: string | null;
  vence?: string | null;
  /** Clasificación del 606 (1-11), p. ej. al registrar como compra un e-CF de la bandeja. */
  tipo_bienes_servicios?: number | null;
}

// --- Pagos a proveedores --------------------------------------------------

export interface PagoProveedor {
  id: string;
  registro606_id: string;
  ncf: string | null;
  proveedor_rnc: string | null;
  proveedor_nombre: string;
  fecha: string;
  /** Lo pagado al proveedor, neto de retenciones. */
  monto: string;
  metodo: MetodoPago;
  cuenta_origen: string | null;
  referencia: string | null;
  notas: string | null;
  tipo_retencion_isr: number | null;
  tipo_retencion_isr_label: string | null;
  retencion_isr: string;
  retencion_itbis: string;
  anulado: boolean;
  anulado_en: string | null;
  creado_en: string;
}

/**
 * Las retenciones se aplican una sola vez por factura (en un pago): 409 si ya hay otro pago con retenciones.
 * 422 si monto + retenciones supera lo pendiente, la fecha es anterior al comprobante, el ITBIS retenido supera el
 * facturado o falta el tipo de retención ISR. 409 si cambia un IT-1/IR-17 ya marcado como presentado.
 */
export interface PagoProveedorPayload {
  registro606_id: string;
  /** Por defecto hoy. Decide el mes del IT-1/IR-17 en que se declaran las retenciones. */
  fecha?: string;
  monto: string;
  metodo: MetodoPago;
  cuenta_origen?: string | null;
  referencia?: string | null;
  notas?: string | null;
  /** 1-9 (catálogo del 606); obligatorio si retencion_isr > 0. */
  tipo_retencion_isr?: number | null;
  retencion_isr?: string;
  retencion_itbis?: string;
}

// --- Comprobantes E41 / E43 (emitidos en /facturas/nueva?tipo=41|43) ------

export interface ComprobanteCompra {
  /** facturacion.facturas.id (para abrir el detalle / PDF / XML en facturas-api). */
  id: string;
  tipo_ecf: 41 | 43;
  encf: string | null;
  /** Estado en facturas-api: borrador | enviado | aceptado | rechazado | error… */
  estado: string;
  dgii_codigo: string | null;
  error: string | null;
  /** Solo E41: el proveedor informal (Comprador del e-CF). */
  proveedor_rnc: string | null;
  proveedor_nombre: string | null;
  /** Primera línea del comprobante. */
  concepto: string | null;
  lineas: number;
  fecha_emision: string | null;
  /** Antes de ITBIS. */
  monto: string;
  itbis: string;
  total: string;
  /** Según el e-CF (TotalITBISRetenido / TotalISRRetencion). */
  itbis_retenido: string;
  isr_retenido: string;
  /** Su renglón del 606 (se crea al enviarlo) y lo registrado allí al pagarlo. */
  registro606_id: string | null;
  fecha_pago_606: string | null;
  itbis_retenido_606: string | null;
  isr_retenido_606: string | null;
  creado_en: string;
}

// --- Retenciones ----------------------------------------------------------

export interface FilaRetencion {
  registro606_id: string;
  proveedor_rnc: string;
  proveedor_nombre: string;
  ncf: string;
  fecha_comprobante: string | null;
  fecha_pago: string | null;
  monto_facturado: string;
  itbis_facturado: string;
  tipo_retencion_isr: number | null;
  tipo_retencion_isr_label: string | null;
  isr_retenido: string;
  itbis_retenido: string;
  /** Dónde se declara: IR-17 (ISR) y/o IT-1 (ITBIS) del mes del pago. */
  declaraciones: ("ir17" | "it1")[];
  /** "declarada" si esas declaraciones del mes están marcadas como presentadas. */
  estado: "por_declarar" | "declarada";
}

export interface RetencionesProveedor {
  proveedor_rnc: string;
  proveedor_nombre: string;
  proveedor_correo: string | null;
  proveedor_telefono: string | null;
  comprobantes: number;
  isr_retenido: string;
  itbis_retenido: string;
}

/** Retenciones efectuadas en el mes (por fecha de pago del 606, igual que el IT-1 y el IR-17). */
export interface RetencionesMes {
  periodo: string;
  filas: FilaRetencion[];
  proveedores: RetencionesProveedor[];
  totales: { isr_retenido: string; itbis_retenido: string };
  it1_presentado: boolean;
  ir17_presentado: boolean;
}

/** Datos para imprimir/enviar el certificado de retención de un proveedor en un mes. */
export interface CertificadoRetencion {
  periodo: string;
  /** "octubre 2026". */
  periodo_nombre: string;
  fecha_emision: string;
  /** La empresa (agente de retención). `nombre` es null si erp-api no tiene EMISOR_RAZON_SOCIAL: use la de la configuración. */
  agente: { rnc: string; nombre: string | null };
  proveedor: { rnc: string; tipo_identificacion: "rnc" | "cedula"; nombre: string; correo: string | null };
  comprobantes: {
    registro606_id: string;
    ncf: string;
    fecha_comprobante: string | null;
    fecha_pago: string | null;
    monto_facturado: string;
    itbis_facturado: string;
    tipo_retencion_isr: number | null;
    tipo_retencion_isr_label: string | null;
    isr_retenido: string;
    itbis_retenido: string;
  }[];
  totales: {
    monto_facturado: string;
    itbis_facturado: string;
    isr_retenido: string;
    itbis_retenido: string;
    total_retenido: string;
  };
}

export type TipoPersona = "fisica" | "juridica";

/** Retenciones sugeridas para "Nueva factura de compra" (tasas vigentes al mes del pago). */
export interface SugerenciaRetencion {
  tipo_retencion_isr: number | null;
  tipo_retencion_isr_label: string | null;
  /** Porcentaje: "15.00". */
  isr_pct: string;
  retencion_isr: string;
  itbis_pct: string;
  retencion_itbis: string;
  total: string;
  neto_a_pagar: string;
  /** Texto de "Regla aplicada". */
  regla: string;
}

export interface SugerenciaRetencionParams {
  persona: TipoPersona;
  tipo_bienes_servicios: number;
  /** Monto antes de ITBIS. */
  monto: string;
  itbis?: string;
  /** false = proveedor informal con E41. */
  emisor_electronico?: boolean;
  fecha_pago?: string;
}

export const comprasApi = {
  // Bandeja de entrada
  listarBandeja: (p?: { estado?: EstadoAprobacion; q?: string; periodo?: string }) =>
    request<EcfRecibidoResumen[]>(`/compras/bandeja${consulta({ ...p })}`),
  obtenerEcfRecibido: (id: string) => request<EcfRecibido>(`/compras/bandeja/${id}`),
  /** URL directa de descarga del XML recibido (RNC+e-NCF.xml). */
  urlXmlEcfRecibido: (id: string) => `${ERP_API_URL}/compras/bandeja/${id}/xml`,
  /**
   * Envía el ACECF (Estado 1) a la DGII y, aceptado, al proveedor (best-effort: `enviado_al_emisor`). Solo el
   * código "1" de la DGII cuenta como aceptado. 409 si ya se decidió, si se está enviando (`envio_en_curso`) o si la
   * DGII dice que no requiere aprobación comercial; 422 si el e-CF no tiene FechaEmision; 502 si
   * aprobacion_comercial falla o la DGII no lo acepta (sigue pendiente con `error_aprobacion`).
   */
  aprobar: (id: string) => request<EcfRecibido>(`/compras/bandeja/${id}/aprobar`, { method: "POST" }),
  /**
   * Envía el ACECF (Estado 2) con el motivo (máx. 250 caracteres). Mismos errores que `aprobar`, más 409 si tiene
   * pagos vigentes (anúlelos antes). Aceptado el rechazo, su renglón del 606 se quita (ya no es una compra válida:
   * deja de sumar ITBIS adelantado al IT-1); 409 si el 606/IT-1 de ese mes ya está presentado.
   */
  rechazar: (id: string, motivo: string) =>
    request<EcfRecibido>(`/compras/bandeja/${id}/rechazar`, { method: "POST", body: JSON.stringify({ motivo }) }),
  /**
   * Asegura su renglón del 606 y, opcionalmente, lo vincula a una compra de inventario. 409 si fue rechazado;
   * 422 si `registro606_id` es de otro proveedor o de otro NCF que el e-NCF del e-CF.
   */
  vincular: (id: string, payload: VincularPayload = {}) =>
    request<EcfRecibido>(`/compras/bandeja/${id}/vincular`, { method: "POST", body: JSON.stringify(payload) }),

  // Cuentas por pagar y facturas de compra
  /** Sin `estado`: lo que falta por pagar (pendientes + vencidas). `periodo` = mes del comprobante. */
  listarCuentasPorPagar: (p?: {
    estado?: EstadoCuentaPagar | "todas";
    proveedor_rnc?: string;
    q?: string;
    periodo?: string;
  }) => request<CuentaPorPagar[]>(`/compras/cuentas-por-pagar${consulta({ ...p })}`),
  obtenerFactura: (registro606Id: string) => request<FacturaCompra>(`/compras/facturas/${registro606Id}`),
  /**
   * 409 si el NCF de ese proveedor ya está en el 606 o si el 606 / IT-1 del mes del comprobante está presentado;
   * 422 si E41/E43/E47 (se emiten en Facturas). Una nota de crédito (B04/E34) exige `ncf_modificado` y se devuelve
   * con `es_nota_credito` (descuenta su total del pendiente de la factura que modifica).
   */
  crearFactura: (payload: FacturaCompraPayload) =>
    request<FacturaCompra>("/compras/facturas", { method: "POST", body: JSON.stringify(payload) }),
  actualizarFactura: (registro606Id: string, payload: FacturaCompraActualizar) =>
    request<FacturaCompra>(`/compras/facturas/${registro606Id}`, { method: "PUT", body: JSON.stringify(payload) }),
  /** Solo las creadas con crearFactura y sin pagos vigentes (409); 409 también si el 606 / IT-1 de su mes está presentado. */
  eliminarFactura: (registro606Id: string) =>
    request<void>(`/compras/facturas/${registro606Id}`, { method: "DELETE" }),

  // Pagos a proveedores
  listarPagos: (p?: { registro606_id?: string; periodo?: string }) =>
    request<PagoProveedor[]>(`/compras/pagos${consulta({ ...p })}`),
  /**
   * Actualiza el 606: mientras quede saldo la forma de pago sigue 4 (crédito). 422 si la fecha es futura (hora RD) o
   * supera lo pendiente; 409 si el 606 del mes del comprobante o el IT-1/IR-17 del mes del pago está presentado.
   */
  registrarPago: (payload: PagoProveedorPayload) =>
    request<PagoProveedor>("/compras/pagos", { method: "POST", body: JSON.stringify(payload) }),
  /** Recalcula el 606 con los pagos que quedan. 409 si ya estaba anulado o el 606 / IT-1 / IR-17 afectado está presentado. */
  anularPago: (id: string) => request<PagoProveedor>(`/compras/pagos/${id}/anular`, { method: "POST" }),

  // Comprobantes E41 / E43
  listarComprobantes: (p?: { tipo_ecf?: 41 | 43; estado?: string; q?: string; periodo?: string }) =>
    request<ComprobanteCompra[]>(`/compras/comprobantes${consulta({ ...p })}`),

  // Retenciones
  retencionesDelMes: (periodo: string) => request<RetencionesMes>(`/compras/retenciones?periodo=${periodo}`),
  /** 404 si no se le retuvo nada al proveedor en el mes. */
  certificadoRetencion: (periodo: string, rnc: string) =>
    request<CertificadoRetencion>(
      `/compras/retenciones/certificado${consulta({ periodo, rnc: rnc.replace(/[^0-9]/g, "") })}`,
    ),
  sugerirRetenciones: (p: SugerenciaRetencionParams) =>
    request<SugerenciaRetencion>(`/compras/retenciones/sugerencia${consulta({ ...p })}`),
};
