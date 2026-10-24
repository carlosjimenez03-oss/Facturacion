// Utilidades compartidas por las pantallas de Bancos (cuentas y movimientos, conciliación y transferencias).
import type { CuentaBancaria, Moneda, TipoCuentaBancaria, TipoMovimiento, TipoMovimientoManual } from "../../lib/bancosApi";
import { formatoMonto } from "../../lib/marca";
import { fechaLegible, hoyIso } from "../ingresos/ingresosUtil";

export const TIPOS_CUENTA: { value: TipoCuentaBancaria; label: string }[] = [
  { value: "corriente", label: "Corriente" },
  { value: "ahorro", label: "Ahorros" },
  { value: "caja_chica", label: "Caja chica" },
  { value: "tarjeta", label: "Tarjeta de crédito" },
];

export const TIPO_CUENTA_LABEL = Object.fromEntries(TIPOS_CUENTA.map((t) => [t.value, t.label])) as Record<TipoCuentaBancaria, string>;

/** Bancos y asociaciones más comunes en RD (el campo admite cualquier otro). */
export const BANCOS_RD = [
  "Banco Popular Dominicano",
  "Banreservas",
  "Banco BHD",
  "Scotiabank",
  "Banco Santa Cruz",
  "Banco Promerica",
  "Banesco",
  "Banco Caribe",
  "Banco López de Haro",
  "Banco BDI",
  "Banco Vimenca",
  "Banco Ademi",
  "Citibank",
  "Asociación Popular de Ahorros y Préstamos",
  "Asociación Cibao de Ahorros y Préstamos",
];

export const TIPOS_MOVIMIENTO_MANUAL: { value: TipoMovimientoManual; label: string; entra: boolean }[] = [
  { value: "deposito", label: "Depósito", entra: true },
  { value: "retiro", label: "Retiro", entra: false },
  { value: "cargo_bancario", label: "Cargo bancario", entra: false },
  { value: "interes", label: "Interés ganado", entra: true },
  { value: "comision", label: "Comisión bancaria", entra: false },
];

export const TIPO_MOVIMIENTO_LABEL: Record<TipoMovimiento, string> = {
  deposito: "Depósito",
  retiro: "Retiro",
  cargo_bancario: "Cargo bancario",
  interes: "Interés",
  comision: "Comisión",
  transferencia_entrada: "Transferencia recibida",
  transferencia_salida: "Transferencia enviada",
  cobro: "Cobro",
  pago: "Pago a proveedor",
};

export function simboloMoneda(moneda: Moneda | string | null | undefined): string {
  return moneda === "USD" ? "US$" : "RD$";
}

/** "RD$ 1,250.00" / "US$ 300.00"; los negativos con el signo delante del símbolo. */
export function montoMoneda(valor: string | number | null | undefined, moneda: Moneda | string = "DOP"): string {
  const n = Number(valor ?? 0);
  return `${n < 0 ? "−" : ""}${simboloMoneda(moneda)} ${formatoMonto(Math.abs(n))}`;
}

/** "Banco Popular · ****4471" (como en los selectores del prototipo). */
export function etiquetaCuenta(c: Pick<CuentaBancaria, "nombre" | "numero_enmascarado">): string {
  return c.numero_enmascarado ? `${c.nombre} · ${c.numero_enmascarado}` : c.nombre;
}

/** Línea de estado de la tarjeta: "3 transacciones sin conciliar", "Conciliada al 30/09"… */
export function estadoTarjeta(c: CuentaBancaria): string {
  if (!c.activa) return "Inactiva";
  const pendientes = c.movimientos_sin_conciliar;
  if (pendientes > 0) return `${pendientes} ${pendientes === 1 ? "transacción" : "transacciones"} sin conciliar`;
  if (c.conciliada_hasta) return `Conciliada al ${fechaLegible(c.conciliada_hasta).slice(0, 5)}`;
  if (!c.ultimo_movimiento) return "Cuenta nueva";
  return "Al día";
}

export function primeroDelMes(fecha = hoyIso()): string {
  return `${fecha.slice(0, 7)}-01`;
}

/** Último día del mes de una fecha YYYY-MM-DD. */
export function finDeMes(fecha: string): string {
  const [a, m] = fecha.split("-").map(Number);
  const d = new Date(a, m, 0);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

/** "octubre 2026" de una fecha YYYY-MM-DD. */
export function mesDeFecha(fecha: string): string {
  const s = new Date(`${fecha.slice(0, 10)}T12:00:00`).toLocaleDateString("es-DO", { month: "long", year: "numeric" });
  return s.replace(/ de /, " ");
}
