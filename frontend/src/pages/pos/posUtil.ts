// Reglas del POS sin React: carrito -> FormState del e-CF (con los helpers de
// buildDatos.ts, los mismos de Nueva factura) y pagos de contado (efectivo con
// cambio, tarjeta, transferencia o mixto), iguales a las de erp-api
// (soluciones_calculos.validar_pagos).
import { estadoInicial, INDICADOR_POR_TASA, nuevoItem } from "../../lib/buildDatos";
import type { FormState, ItemFormulario } from "../../lib/buildDatos";
import { decimal2 } from "../../lib/format";
import type { DatosEmisor } from "../../lib/preferencias";
import type { MetodoPos, PagoPos } from "../../lib/solucionesApi";
import type { Producto } from "../../lib/types";

export interface LineaCarrito {
  producto: Producto;
  cantidad: number;
}

/** Consumo (E32) para consumidor final; crédito fiscal (E31) al indicar RNC/cédula. */
export function tipoParaCliente(rnc: string): "31" | "32" {
  const d = rnc.replace(/\D/g, "");
  return d.length === 9 || d.length === 11 ? "31" : "32";
}

/** Sobre este total la DGII exige identificar al comprador en un E32. */
export const TOPE_CONSUMO_SIN_IDENTIFICAR = 250000;

export function itemsCarrito(lineas: LineaCarrito[]): ItemFormulario[] {
  return lineas.map((l, idx) => ({
    ...nuevoItem(idx + 1),
    NombreItem: l.producto.nombre,
    IndicadorBienoServicio: l.producto.tipo === "servicio" ? "2" : "1",
    IndicadorFacturacion: INDICADOR_POR_TASA[l.producto.tasa_itbis] ?? "1",
    CantidadItem: String(l.cantidad),
    PrecioUnitarioItem: l.producto.precio_venta,
    productoId: l.producto.id,
  }));
}

/** Código FormaPago de la DGII por método del POS. */
export const FORMA_PAGO_DGII: Record<MetodoPos, string> = { efectivo: "1", transferencia: "2", tarjeta: "3" };

export interface ResultadoPagos {
  recibido: number;
  cambio: number;
  error: string | null;
}

const cent = (n: number) => Math.round((n + Number.EPSILON) * 100) / 100;

export function validarPagos(total: number, pagos: PagoPos[]): ResultadoPagos {
  const efectivo = cent(pagos.filter((p) => p.metodo === "efectivo").reduce((s, p) => s + (Number(p.monto) || 0), 0));
  const otros = cent(pagos.filter((p) => p.metodo !== "efectivo").reduce((s, p) => s + (Number(p.monto) || 0), 0));
  const recibido = cent(efectivo + otros);
  const t = cent(total);
  if (pagos.length === 0 || pagos.some((p) => !(Number(p.monto) > 0))) return { recibido, cambio: 0, error: "Indique el monto de cada pago." };
  if (otros > t) return { recibido, cambio: 0, error: "Tarjeta y transferencia no pueden pasar del total: el cambio solo se da en efectivo." };
  if (recibido < t) return { recibido, cambio: 0, error: `Faltan RD$ ${(t - recibido).toFixed(2)} para completar el pago.` };
  return { recibido, cambio: cent(recibido - t), error: null };
}

/** TablaFormasPago del e-CF: lo aplicado por método (el efectivo, sin el cambio). */
export function formasPagoEcf(pagos: PagoPos[], cambio: number) {
  const porMetodo = new Map<MetodoPos, number>();
  for (const p of pagos) porMetodo.set(p.metodo, (porMetodo.get(p.metodo) ?? 0) + (Number(p.monto) || 0));
  if (porMetodo.has("efectivo")) porMetodo.set("efectivo", (porMetodo.get("efectivo") ?? 0) - cambio);
  return [...porMetodo.entries()]
    .filter(([, monto]) => cent(monto) > 0)
    .map(([metodo, monto]) => ({ FormaPago: FORMA_PAGO_DGII[metodo], MontoPago: decimal2(cent(monto)) }));
}

export interface DatosVenta {
  tipoEcf: "31" | "32";
  encf: string;
  vencimientoSecuencia: string;
  emisor: DatosEmisor;
  cliente: { rnc: string; nombre: string };
  lineas: LineaCarrito[];
  pagos: PagoPos[];
  cambio: number;
  almacenId: string;
}

/** FormState de contado (TipoPago 1) listo para construirDatos/construirItemsInventario. */
export function formularioVenta(v: DatosVenta): FormState {
  const rnc = v.cliente.rnc.replace(/\D/g, "");
  return {
    ...estadoInicial(v.tipoEcf),
    encf: v.encf,
    fechaVencimientoSecuencia: v.vencimientoSecuencia,
    tipoPago: "1",
    formasPago: formasPagoEcf(v.pagos, v.cambio),
    rncEmisor: v.emisor.rnc.replace(/\D/g, ""),
    razonSocialEmisor: v.emisor.razonSocial,
    direccionEmisor: v.emisor.direccion,
    correoEmisor: v.emisor.correo,
    compradorRnc: rnc,
    razonSocialComprador: rnc ? v.cliente.nombre : "",
    items: itemsCarrito(v.lineas),
    almacenId: v.almacenId,
  };
}
