import { INDICADORES_FACTURACION } from "./catalogos";
import type { ItemFactura } from "./types";

/** Convierte un <input type="date"> (YYYY-MM-DD) al formato que exige la DGII (DD-MM-YYYY). */
export function fechaADgii(fechaIso: string): string {
  const [anio, mes, dia] = fechaIso.split("-");
  return `${dia}-${mes}-${anio}`;
}

/** Inverso de fechaADgii: DD-MM-YYYY -> YYYY-MM-DD (para precargar <input type="date">). */
export function fechaDeDgii(fechaDgii: string): string {
  const [dia, mes, anio] = fechaDgii.split("-");
  return `${anio}-${mes}-${dia}`;
}

export function decimal2(valor: number): string {
  return valor.toFixed(2);
}

export interface TotalesCalculados {
  montoGravadoTotal: number;
  totalITBIS: number;
  montoExento: number;
  montoTotal: number;
}

/** Calcula los totales del e-CF a partir de los items, aplicando la tasa de
 * ITBIS de cada `IndicadorFacturacion` (18% / 16% / 0% / exento). */
export function calcularTotales(items: ItemFactura[]): TotalesCalculados {
  let montoGravadoTotal = 0;
  let totalITBIS = 0;
  let montoExento = 0;

  for (const item of items) {
    const cantidad = Number(item.CantidadItem) || 0;
    const precio = Number(item.PrecioUnitarioItem) || 0;
    const descuento = Number(item.DescuentoMonto) || 0;
    const montoItem = cantidad * precio - descuento;

    const indicador = INDICADORES_FACTURACION.find((i) => i.value === item.IndicadorFacturacion);
    if (item.IndicadorFacturacion === "4" || item.IndicadorFacturacion === "0") {
      montoExento += montoItem;
    } else {
      montoGravadoTotal += montoItem;
      totalITBIS += montoItem * (indicador?.tasa ?? 0);
    }
  }

  const montoTotal = montoGravadoTotal + totalITBIS + montoExento;
  return { montoGravadoTotal, totalITBIS, montoExento, montoTotal };
}

export function calcularMontoItem(item: Pick<ItemFactura, "CantidadItem" | "PrecioUnitarioItem" | "DescuentoMonto">): number {
  const cantidad = Number(item.CantidadItem) || 0;
  const precio = Number(item.PrecioUnitarioItem) || 0;
  const descuento = Number(item.DescuentoMonto) || 0;
  return cantidad * precio - descuento;
}
