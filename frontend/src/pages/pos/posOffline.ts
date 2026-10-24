// POS sin conexión con el servidor (internet caído en la tienda): reglas sin
// React ni navegador -- la cola de ventas guardadas en el navegador, su
// sincronización en orden al reconectar, los totales de caja con lo
// pendiente y la existencia descontada. El almacenamiento (IndexedDB) está
// en posAlmacen.ts y la pantalla en Pos.tsx. Ver docs/dgii/CONTINGENCIA.md,
// "POS sin conexión".
//
// Cada venta lleva desde el cobro una clave UUID (`clave`) que viaja como
// `clave_idempotencia` al crear la factura en facturas-api: si la
// sincronización se corta y se repite, el servidor devuelve la factura ya
// creada en vez de otra, y la caja de erp-api no registra dos veces la misma
// factura. El e-NCF NUNCA se inventa en el navegador: lo asigna el servidor
// (secuencia bloqueada) al sincronizar; hasta entonces el ticket es
// provisional.
//
// Multiempresa: cada venta guarda la empresa en que se cobró (`empresaId`) y
// la cola vive en la base de IndexedDB de esa empresa (posAlmacen.ts); además
// `sincronizarCola` salta las de otra empresa, para que cambiar de empresa
// nunca mande una venta a la que no es.
//
// Solo importa tipos: lo prueban las pruebas de frontend/pruebas/ con Node
// sin compilar (ver package.json, "test").
import type { DatosEmisor } from "../../lib/preferencias";
import type { MetodoPos, PagoPos } from "../../lib/solucionesApi";
import type { FacturaDatos, ItemFactura, ItemInventarioLink } from "../../lib/types";

/**
 * pendiente: guardada, por sincronizar (o se cortó la conexión a mitad: el
 * progreso queda en facturaId/enviada/registradaEnCaja).
 * error: el servidor la rechazó (p. ej. secuencia agotada); se reintenta a mano.
 * sincronizada: e-CF emitido y registrado en la caja; queda para reimprimir el ticket.
 */
export type EstadoVentaOffline = "pendiente" | "error" | "sincronizada";

export interface VentaOffline {
  /** UUID generado al cobrar: `clave_idempotencia` de la factura. */
  clave: string;
  /** Empresa en que se cobró (las de antes de la multiempresa no lo traen: la 1). */
  empresaId?: number;
  /** ISO 8601: orden de sincronización (la más antigua primero). */
  creadaEn: string;
  sesionId: string;
  caja: string;
  cajero: string | null;
  tipoEcf: "31" | "32";
  /** El e-CF tal como se cobró (precios, ITBIS, fecha), sin e-NCF: lo pone el servidor. */
  datos: FacturaDatos;
  itemsInventario: ItemInventarioLink[];
  /** Para el ticket. */
  items: ItemFactura[];
  emisor: DatosEmisor;
  cliente: { rnc: string; nombre: string };
  pagos: PagoPos[];
  recibido: number;
  cambio: number;
  total: number;
  estado: EstadoVentaOffline;
  intentos: number;
  error: string | null;
  erroresXml: string[];
  facturaId: string | null;
  encf: string | null;
  /** El e-CF ya llegó a la DGII (trackId) o quedó emitido en contingencia. */
  enviada: boolean;
  estadoDgii: string;
  contingencia: boolean;
  registradaEnCaja: boolean;
  sincronizadaEn: string | null;
}

export type NuevaVentaOffline = Omit<
  VentaOffline,
  | "estado"
  | "intentos"
  | "error"
  | "erroresXml"
  | "facturaId"
  | "encf"
  | "enviada"
  | "estadoDgii"
  | "contingencia"
  | "registradaEnCaja"
  | "sincronizadaEn"
>;

export function nuevaVentaOffline(v: NuevaVentaOffline): VentaOffline {
  return {
    ...v,
    estado: "pendiente",
    intentos: 0,
    error: null,
    erroresXml: [],
    facturaId: null,
    encf: null,
    enviada: false,
    estadoDgii: "",
    contingencia: false,
    registradaEnCaja: false,
    sincronizadaEn: null,
  };
}

/** UUID v4 (crypto.randomUUID solo existe en contextos seguros: https o localhost). */
export function nuevaClave(): string {
  const c = globalThis.crypto;
  if (typeof c?.randomUUID === "function") return c.randomUUID();
  const b = new Uint8Array(16);
  c.getRandomValues(b);
  b[6] = (b[6] & 0x0f) | 0x40;
  b[8] = (b[8] & 0x3f) | 0x80;
  const h = [...b].map((x) => x.toString(16).padStart(2, "0")).join("");
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}

/** Referencia corta de la venta para el ticket provisional (los 8 primeros de la clave). */
export const referenciaVenta = (clave: string) => clave.slice(0, 8).toUpperCase();

export function ordenarCola(ventas: VentaOffline[]): VentaOffline[] {
  return [...ventas].sort((a, b) => a.creadaEn.localeCompare(b.creadaEn) || a.clave.localeCompare(b.clave));
}

/** Por sincronizar (pendientes o con error): bloquean el cierre de su caja. */
export const sinSincronizar = (ventas: VentaOffline[], sesionId?: string) =>
  ventas.filter((v) => v.estado !== "sincronizada" && (!sesionId || v.sesionId === sesionId));

// --- Errores -----------------------------------------------------------------------

/** Estados HTTP con que nginx o un proxy dicen que el servidor de atrás no responde. */
const HTTP_SIN_SERVIDOR = new Set([0, 502, 503, 504]);

function estadoHttp(err: unknown): number | null {
  if (err && typeof err === "object" && "status" in err && typeof (err as { status: unknown }).status === "number")
    return (err as { status: number }).status;
  return null;
}

/**
 * "red": no se llegó al servidor (fetch falló o el proxy no encontró el
 * servicio) -- la venta sigue pendiente, sin marcarla con error.
 * "sesion": la sesión venció y no se pudo renovar -- hay que iniciar sesión.
 * "rechazo": el servidor respondió que no (409 secuencia agotada, 422...).
 */
export function clasificarError(err: unknown): "red" | "sesion" | "rechazo" {
  if (err instanceof TypeError) return "red";
  const status = estadoHttp(err);
  if (status !== null && HTTP_SIN_SERVIDOR.has(status)) return "red";
  if (status === 401) return "sesion";
  return "rechazo";
}

export const esErrorDeRed = (err: unknown) => clasificarError(err) === "red";

/** Error propio de una venta (XML que no pasa el XSD, envío no recibido). */
export class ErrorVentaOffline extends Error {
  erroresXml: string[];

  constructor(mensaje: string, erroresXml: string[] = []) {
    super(mensaje);
    this.erroresXml = erroresXml;
  }
}

/** Mensaje para la fila de la venta con error (el del servidor ya es claro: "La secuencia E32 está agotada…"). */
export function mensajeSincronizacion(err: unknown): string {
  const texto = err instanceof Error ? err.message : String(err);
  const status = estadoHttp(err);
  if (status === 409 && /secuencia/i.test(texto)) return `No se pudo asignar el e-NCF: ${texto}`;
  if (status === 409 && /caja/i.test(texto)) return `No se pudo registrar en la caja: ${texto}`;
  return texto || "Error desconocido al sincronizar.";
}

// --- Totales de caja y existencia ----------------------------------------------------

const cent = (n: number) => Math.round((n + Number.EPSILON) * 100) / 100;

/** Lo que entra a la caja por método (el efectivo sin el cambio), igual que el esperado de erp-api. */
export function aplicadoPorMetodo(pagos: PagoPos[], cambio: number): Record<MetodoPos, number> {
  const r: Record<MetodoPos, number> = { efectivo: 0, tarjeta: 0, transferencia: 0 };
  for (const p of pagos) r[p.metodo] = cent(r[p.metodo] + (Number(p.monto) || 0));
  r.efectivo = cent(r.efectivo - cambio);
  return r;
}

export interface ResumenPendiente {
  cantidad: number;
  total: number;
  porMetodo: Record<MetodoPos, number>;
}

/**
 * Ventas de esa caja que la sesión de erp-api todavía no tiene: se suman a
 * sus totales en pantalla. Al registrarse en la caja pasan a contar en el
 * servidor (registradaEnCaja) y dejan de sumarse aquí.
 */
export function resumenPendiente(ventas: VentaOffline[], sesionId: string): ResumenPendiente {
  const r: ResumenPendiente = { cantidad: 0, total: 0, porMetodo: { efectivo: 0, tarjeta: 0, transferencia: 0 } };
  for (const v of ventas) {
    if (v.sesionId !== sesionId || v.registradaEnCaja) continue;
    r.cantidad += 1;
    r.total = cent(r.total + v.total);
    const a = aplicadoPorMetodo(v.pagos, v.cambio);
    for (const m of Object.keys(a) as MetodoPos[]) r.porMetodo[m] = cent(r.porMetodo[m] + a[m]);
  }
  return r;
}

/**
 * Existencia del almacén descontando lo vendido sin conexión que aún no
 * llegó a la DGII (inventario-api registra la salida al enviar el e-CF).
 */
export function stockConPendientes(stock: Record<string, number>, ventas: VentaOffline[], almacenId: string): Record<string, number> {
  const r = { ...stock };
  for (const v of ventas) {
    if (v.enviada) continue;
    for (const i of v.itemsInventario) {
      if ((i.almacen_id || almacenId) !== almacenId || !(i.producto_id in r)) continue;
      r[i.producto_id] = r[i.producto_id] - (Number(i.cantidad) || 0);
    }
  }
  return r;
}

// --- Sincronización ---------------------------------------------------------------

const ESTADOS_YA_ENVIADA = new Set(["enviado", "aceptado", "aceptado_condicional", "rechazado", "contingencia"]);

export interface FacturaCreada {
  id: string;
  encf: string | null;
  estado: string;
  track_id?: string | null;
  emitida_en_contingencia?: boolean;
}

export interface DependenciasSincronizacion {
  /** Renovar la sesión antes de empezar (ver lib/sesion.ts, prepararSesion). */
  prepararSesion(): Promise<"ok" | "sin_red" | "sin_sesion">;
  /** POST /facturas con `clave_idempotencia` = venta.clave (201 nueva, 200 la ya creada). */
  crearFactura(venta: VentaOffline): Promise<FacturaCreada>;
  generarXml(facturaId: string): Promise<{ errores: string[] }>;
  enviar(facturaId: string): Promise<{ estado: string; track_id: string | null; contingencia?: boolean; error?: string | null }>;
  /** erp-api: registrar en la caja (idempotente para la misma sesión). */
  registrarVenta(sesionId: string, facturaId: string, pagos: PagoPos[]): Promise<unknown>;
  /** Guarda el progreso de la venta (IndexedDB) tras cada paso. */
  guardar(venta: VentaOffline): Promise<void>;
  ahora?: () => Date;
  /** Empresa de la pestaña: las ventas de otra se dejan para cuando se vuelva a ella. */
  empresaId?: number;
}

/** La venta es de la empresa `empresaId` (sin empresa guardada: la 1). */
export const deLaEmpresa = (v: VentaOffline, empresaId: number | undefined) =>
  empresaId === undefined || (v.empresaId ?? 1) === empresaId;

export interface ResultadoSincronizacion {
  sincronizadas: VentaOffline[];
  conError: VentaOffline[];
  /** null = se recorrió toda la cola; si no, por qué se detuvo (lo pendiente sigue guardado). */
  detenida: null | "sin_red" | "sin_sesion";
}

/**
 * Una venta: crear (idempotente) -> generar-xml -> enviar -> registrar en
 * caja. `progreso.venta` queda con lo último guardado aunque un paso falle.
 */
async function sincronizarVenta(progreso: { venta: VentaOffline }, deps: DependenciasSincronizacion): Promise<VentaOffline> {
  const avanzar = async (v: VentaOffline) => {
    progreso.venta = v;
    await deps.guardar(v);
  };
  let v = progreso.venta;
  if (!v.enviada) {
    const f = await deps.crearFactura(v);
    v = { ...v, facturaId: f.id, encf: f.encf };
    await avanzar(v);
    // Un reintento puede encontrar la factura ya enviada (se cortó la
    // conexión después del envío): no se regenera ni se reenvía.
    if (ESTADOS_YA_ENVIADA.has(f.estado) || f.track_id) {
      v = { ...v, enviada: true, estadoDgii: f.estado, contingencia: Boolean(f.emitida_en_contingencia) };
    } else {
      const xml = await deps.generarXml(f.id);
      if (xml.errores.length > 0)
        throw new ErrorVentaOffline("La DGII no aceptaría este comprobante: revíselo en Facturas de venta.", xml.errores);
      const envio = await deps.enviar(f.id);
      if (!envio.track_id && !envio.contingencia)
        throw new ErrorVentaOffline(envio.error || "La DGII no recibió el comprobante. Reintente la sincronización.");
      v = { ...v, enviada: true, estadoDgii: envio.estado, contingencia: Boolean(envio.contingencia) };
    }
    await avanzar(v);
  }
  if (!v.registradaEnCaja) {
    await deps.registrarVenta(v.sesionId, v.facturaId as string, v.pagos);
    v = { ...v, registradaEnCaja: true };
    progreso.venta = v;
  }
  return v;
}

/**
 * Sincroniza la cola en orden (la venta más antigua primero, para que los
 * e-NCF sigan el orden de las ventas). Sin red o sin sesión se detiene y
 * deja lo que falta como estaba (pendiente, con el progreso guardado); una
 * venta que el servidor rechaza queda con su error y se sigue con la
 * siguiente.
 */
export async function sincronizarCola(
  ventas: VentaOffline[],
  deps: DependenciasSincronizacion,
): Promise<ResultadoSincronizacion> {
  const resultado: ResultadoSincronizacion = { sincronizadas: [], conError: [], detenida: null };
  const cola = ordenarCola(sinSincronizar(ventas)).filter((v) => deLaEmpresa(v, deps.empresaId));
  if (cola.length === 0) return resultado;
  const sesion = await deps.prepararSesion();
  if (sesion !== "ok") return { ...resultado, detenida: sesion };
  const ahora = deps.ahora ?? (() => new Date());
  for (const original of cola) {
    const progreso = { venta: { ...original, intentos: original.intentos + 1 } };
    try {
      let v = await sincronizarVenta(progreso, deps);
      v = { ...v, estado: "sincronizada", error: null, erroresXml: [], sincronizadaEn: ahora().toISOString() };
      await deps.guardar(v);
      resultado.sincronizadas.push(v);
    } catch (err) {
      const tipo = clasificarError(err);
      if (tipo !== "rechazo") {
        await deps.guardar({ ...progreso.venta, estado: "pendiente", error: null });
        return { ...resultado, detenida: tipo === "red" ? "sin_red" : "sin_sesion" };
      }
      const v: VentaOffline = {
        ...progreso.venta,
        estado: "error",
        error: mensajeSincronizacion(err),
        erroresXml: err instanceof ErrorVentaOffline ? err.erroresXml : [],
      };
      await deps.guardar(v);
      resultado.conError.push(v);
    }
  }
  return resultado;
}
