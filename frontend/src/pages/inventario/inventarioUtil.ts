// Utilidades compartidas por las pantallas de Inventario (productos, almacenes, lotes, ajustes y Kardex).
import { InventarioApiError } from "../../lib/inventarioApi";
import type { Ajuste, EstadoAjuste, EstadoLote, EstadoStock, MotivoAjuste, Producto, TasaItbis } from "../../lib/types";
import { mensajeError } from "../ingresos/ingresosUtil";

/**
 * Mensaje legible de un error de inventario-api. Los 422 de validación de FastAPI llegan como una
 * lista de objetos ({loc, msg}); se muestran como frases.
 */
export function mensajeInventario(err: unknown): string {
  if (err instanceof InventarioApiError && err.message.startsWith("[")) {
    try {
      const lista = JSON.parse(err.message) as { loc?: (string | number)[]; msg?: string }[];
      const textos = lista.map((e) => {
        const campo = (e.loc ?? []).filter((x) => x !== "body").join(".");
        const msg = (e.msg ?? "").replace(/^Value error, /, "");
        return campo ? `${campo}: ${msg}` : msg;
      });
      if (textos.length) return textos.join(" · ");
    } catch {
      // no era JSON: se muestra tal cual
    }
  }
  return mensajeError(err);
}

/** Cantidades sin ceros sobrantes: "1250.0000" -> "1,250"; "2.5000" -> "2.5". */
export function formatoCantidad(valor: string | number | null | undefined): string {
  const n = typeof valor === "number" ? valor : Number(valor ?? 0);
  return (Number.isFinite(n) ? n : 0).toLocaleString("en-US", { maximumFractionDigits: 4 });
}

/** "18 sacos" / "18 uds" según la unidad de medida del producto. */
export function cantidadConUnidad(valor: string | number | null | undefined, unidad?: string | null): string {
  return `${formatoCantidad(valor)} ${unidad?.trim() || "uds"}`;
}

/** Número de un campo de texto para enviar como string decimal ("" -> "0"). */
export function decimalTexto(valor: string, decimales = 4): string {
  const n = Number(valor);
  return (Number.isFinite(n) ? n : 0).toFixed(decimales);
}

export const TASAS_ITBIS: { value: TasaItbis; label: string }[] = [
  { value: "18", label: "18%" },
  { value: "16", label: "16%" },
  { value: "0", label: "0%" },
  { value: "exento", label: "Exento" },
];

export const TASA_LABEL = Object.fromEntries(TASAS_ITBIS.map((t) => [t.value, t.label])) as Record<TasaItbis, string>;

export const AYUDA_ITBIS =
  "18% tasa general · 16% tasa reducida (café, yogur, mantequilla, azúcar, entre otros) · Exento según el Código Tributario.";

export const ESTADO_STOCK: Record<EstadoStock, { label: string; badge: string }> = {
  disponible: { label: "Disponible", badge: "bd b-acep" },
  bajo_minimo: { label: "Bajo stock", badge: "bd b-pend" },
  sin_existencia: { label: "Sin existencia", badge: "bd b-borr" },
  no_aplica: { label: "Servicio", badge: "bd b-teal" },
};

/** Estado que se muestra en la lista de productos (un producto inactivo lo dice primero). */
export function estadoProducto(p: Producto): { label: string; badge: string } {
  if (!p.activo) return { label: "Inactivo", badge: "bd b-borr" };
  return ESTADO_STOCK[p.estado_stock] ?? { label: p.estado_stock, badge: "bd b-borr" };
}

export const ESTADO_LOTE: Record<EstadoLote, { label: string; badge: string }> = {
  vigente: { label: "Vigente", badge: "bd b-acep" },
  por_vencer: { label: "Por vencer", badge: "bd b-proc" },
  vencido: { label: "Vencido", badge: "bd b-rech" },
  bloqueado: { label: "Bloqueado", badge: "bd b-rech" },
};

export const ESTADO_AJUSTE: Record<EstadoAjuste, { label: string; badge: string }> = {
  borrador: { label: "Borrador", badge: "bd b-borr" },
  aplicado: { label: "Aplicado", badge: "bd b-acep" },
  anulado: { label: "Revertido", badge: "bd b-rech" },
};

export const MOTIVOS_AJUSTE: { value: MotivoAjuste; label: string }[] = [
  { value: "conteo_fisico", label: "Conteo físico" },
  { value: "merma", label: "Merma" },
  { value: "dano", label: "Producto dañado" },
  { value: "vencimiento", label: "Vencimiento" },
  { value: "otro", label: "Otro" },
];

export const AYUDA_REVERTIR =
  "Parte de la mercancía que entró con este ajuste ya salió del almacén (venta, transferencia u otro ajuste), por eso no se puede revertir. Registre un ajuste de salida por lo que quede.";

export const MOTIVO_LABEL = Object.fromEntries(MOTIVOS_AJUSTE.map((m) => [m.value, m.label])) as Record<MotivoAjuste, string>;

/** "Vence en 12 días" / "Venció hace 3 días" / "Vence hoy". */
export function textoVencimiento(dias: number | null): string {
  if (dias == null) return "Sin vencimiento";
  if (dias === 0) return "Vence hoy";
  if (dias > 0) return `Vence en ${dias} ${dias === 1 ? "día" : "días"}`;
  return `Venció hace ${-dias} ${dias === -1 ? "día" : "días"}`;
}

/** Productos que llevan existencia (los servicios no entran en almacenes, lotes ni ajustes). */
export function soloBienes(productos: Producto[]): Producto[] {
  return productos.filter((p) => p.tipo !== "servicio");
}

/** Etiqueta de un producto en un selector: "CEM-425 · Cemento gris Portland 42.5 kg". */
export function etiquetaProducto(p: { sku: string; nombre: string }): string {
  return `${p.sku} · ${p.nombre}`;
}

/** Nombre de archivo sin acentos ni espacios. */
export function nombreArchivo(texto: string): string {
  return texto
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[^\w-]+/g, "_");
}

/** Valor del ajuste: entradas a su costo y salidas al costo promedio vigente del producto (estimado). */
export function valorAjuste(a: Pick<Ajuste, "items">, costoPromedio: (productoId: string) => number): number {
  return a.items.reduce((s, it) => {
    const costo = it.costo_unitario != null ? Number(it.costo_unitario) : costoPromedio(it.producto_id);
    return s + (it.tipo === "entrada" ? 1 : -1) * (Number(it.cantidad) || 0) * costo;
  }, 0);
}

/** Guarda un texto ya armado (p. ej. el CSV del Kardex que genera el backend) como archivo. */
export function descargarTexto(nombre: string, texto: string, tipo = "text/csv;charset=utf-8") {
  const url = URL.createObjectURL(new Blob([texto], { type: tipo }));
  const a = document.createElement("a");
  a.href = url;
  a.download = nombre;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 8000);
}
