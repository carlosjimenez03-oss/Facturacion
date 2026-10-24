// Utilidades compartidas por las pantallas de Contabilidad (resumen, catálogo, asientos, activos fijos y reportes).
import { useCallback } from "react";
import { useSearchParams } from "react-router-dom";
import type {
  AsientoResumen,
  CategoriaDgii,
  CuentaContable,
  DocumentoNoContabilizado,
  EstadoAsiento,
  GrupoConcepto,
  MetodoDepreciacion,
  OrigenAsiento,
  TipoCuentaContable,
} from "../../lib/contabilidadApi";
import { hoyIso } from "../ingresos/ingresosUtil";
import { periodoValido } from "../impuestos/impuestosUtil";

export const TIPO_CUENTA_CONTABLE: Record<TipoCuentaContable, string> = {
  activo: "Activo",
  pasivo: "Pasivo",
  patrimonio: "Patrimonio",
  ingreso: "Ingreso",
  costo: "Costo",
  gasto: "Gasto",
};

export const ESTADO_ASIENTO: Record<EstadoAsiento, { label: string; badge: string }> = {
  borrador: { label: "Borrador", badge: "bd b-borr" },
  contabilizado: { label: "Contabilizado", badge: "bd b-acep" },
  anulado: { label: "Anulado", badge: "bd b-rech" },
};

export const ORIGENES_ASIENTO: { value: OrigenAsiento; label: string }[] = [
  { value: "manual", label: "Manual" },
  { value: "venta", label: "Factura de venta" },
  { value: "nota_credito", label: "Nota de crédito" },
  { value: "nota_debito", label: "Nota de débito" },
  { value: "cobro", label: "Cobro" },
  { value: "compra", label: "Compra (606)" },
  { value: "pago", label: "Pago a proveedor" },
  { value: "movimiento", label: "Movimiento bancario" },
  { value: "transferencia", label: "Transferencia" },
  { value: "proporcionalidad", label: "Proporcionalidad ITBIS" },
  { value: "depreciacion", label: "Depreciación" },
  { value: "factura_papel", label: "Factura en papel" },
];

export const ORIGEN_LABEL = Object.fromEntries(ORIGENES_ASIENTO.map((o) => [o.value, o.label])) as Record<OrigenAsiento, string>;

export const GRUPO_CONCEPTO: Record<GrupoConcepto, string> = {
  ventas: "Ventas y cobros",
  compras: "Compras y pagos",
  bancos: "Bancos y caja",
  activos: "Activos fijos",
};

export const CATEGORIAS_DGII: { value: CategoriaDgii; label: string; tasa: number }[] = [
  { value: 1, label: "Categoría 1 · edificaciones (5 %)", tasa: 5 },
  { value: 2, label: "Categoría 2 · vehículos livianos, mobiliario, equipos de oficina y cómputo (25 %)", tasa: 25 },
  { value: 3, label: "Categoría 3 · otros bienes depreciables (15 %)", tasa: 15 },
];

export const METODO_DEPRECIACION: Record<MetodoDepreciacion, string> = {
  dgii: "Fiscal DGII (Art. 287)",
  linea_recta: "Línea recta",
};

/** Etiqueta de una cuenta en un selector: "1.1.01.03 · Bancos". */
export function etiquetaCuentaContable(c: Pick<CuentaContable, "codigo" | "nombre">): string {
  return `${c.codigo} · ${c.nombre}`;
}

/** "202610" -> { desde: "2026-10-01", hasta: "2026-10-31" }. */
export function rangoDePeriodo(periodo: string): { desde: string; hasta: string } {
  const a = Number(periodo.slice(0, 4));
  const m = Number(periodo.slice(4, 6));
  const fin = new Date(a, m, 0).getDate();
  return { desde: `${periodo.slice(0, 4)}-${periodo.slice(4, 6)}-01`, hasta: `${periodo.slice(0, 4)}-${periodo.slice(4, 6)}-${String(fin).padStart(2, "0")}` };
}

export function periodoDeHoy(): string {
  return hoyIso().slice(0, 7).replace("-", "");
}

/** Período mensual (?periodo=AAAAMM) en la URL; por defecto, el mes en curso (los asientos se generan al día). */
export function usePeriodoContable(): [string, (p: string) => void] {
  const [params, setParams] = useSearchParams();
  const valor = params.get("periodo");
  const periodo = periodoValido(valor) ? valor : periodoDeHoy();
  const cambiar = useCallback(
    (p: string) =>
      setParams(
        (prev) => {
          const n = new URLSearchParams(prev);
          n.set("periodo", p);
          return n;
        },
        { replace: true },
      ),
    [setParams],
  );
  return [periodo, cambiar];
}

/** Dónde se corrige un documento que no se pudo contabilizar. */
export function enlaceCorreccion(d: Pick<DocumentoNoContabilizado, "origen" | "origen_id" | "motivo">): { to: string; label: string } {
  if (/cuenta por defecto/i.test(d.motivo)) return { to: "/contabilidad/catalogo#por-defecto", label: "Asignar la cuenta" };
  switch (d.origen) {
    case "venta":
    case "nota_credito":
    case "nota_debito":
      return { to: `/facturas/${d.origen_id}`, label: "Abrir el e-CF" };
    case "cobro":
      return { to: "/cobros", label: "Ver cobros" };
    case "compra":
      return { to: `/compras/facturas/${d.origen_id}`, label: "Clasificar la compra" };
    case "pago":
      return { to: "/compras/pagos", label: "Ver pagos" };
    case "movimiento":
      return { to: "/bancos/cuentas", label: "Ver movimientos" };
    case "transferencia":
      return { to: "/bancos/transferencias", label: "Ver transferencias" };
    case "depreciacion":
      return { to: "/contabilidad/activos", label: "Ver activos fijos" };
    case "proporcionalidad":
      return { to: `/impuestos/precalculo/it1?periodo=${d.origen_id}`, label: "Ver el IT-1" };
    case "factura_papel":
      return { to: "/sincronizacion", label: "Ver facturas en papel" };
    default:
      return { to: "/contabilidad/asientos", label: "Ver asientos" };
  }
}

/** Número de un campo de texto como decimal con 2 cifras ("" o inválido -> "0.00"). */
export function dec2(valor: string | number): string {
  const n = Number(valor);
  return (Number.isFinite(n) ? n : 0).toFixed(2);
}

/** Se pueden anular a mano los manuales contabilizados y las depreciaciones (para recalcular el mes). */
export function sePuedeAnular(a: Pick<AsientoResumen, "estado" | "origen">): boolean {
  return a.estado === "contabilizado" && (a.origen === "manual" || a.origen === "depreciacion");
}
