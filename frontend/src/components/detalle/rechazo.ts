// Lectura defensiva de la respuesta de la DGII guardada en la factura
// (`dgii_respuesta`, el JSON crudo de recepción / consulta de resultado) y
// sugerencias en lenguaje claro para los rechazos más comunes.
import type { FacturaDetalle } from "../../lib/types";

export interface MensajeDgii {
  codigo: string | null;
  valor: string;
}

function texto(v: unknown): string | null {
  if (v == null) return null;
  if (typeof v === "string") return v.trim() || null;
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  return null;
}

/** Busca una clave sin distinguir mayúsculas (la DGII alterna `mensajes` / `Mensajes`). */
function campo(obj: Record<string, unknown>, ...claves: string[]): unknown {
  const mapa = new Map(Object.keys(obj).map((k) => [k.toLowerCase(), k]));
  for (const c of claves) {
    const real = mapa.get(c.toLowerCase());
    if (real !== undefined && obj[real] != null) return obj[real];
  }
  return undefined;
}

function aMensaje(item: unknown): MensajeDgii | null {
  if (typeof item === "string") return item.trim() ? { codigo: null, valor: item.trim() } : null;
  if (item && typeof item === "object") {
    const o = item as Record<string, unknown>;
    const valor = texto(campo(o, "valor", "mensaje", "message", "descripcion", "detalle", "detail"));
    const codigo = texto(campo(o, "codigo", "code", "cod"));
    if (valor) return { codigo, valor };
  }
  return null;
}

/** Mensajes de la DGII (código + texto) de una factura rechazada o con error. Nunca vacío si hay algo que mostrar. */
export function mensajesDgii(f: FacturaDetalle): MensajeDgii[] {
  const salida: MensajeDgii[] = [];
  const r = f.dgii_respuesta;
  if (r && typeof r === "object") {
    const lista = campo(r, "mensajes", "messages", "errores", "errors");
    if (Array.isArray(lista)) {
      for (const item of lista) {
        const m = aMensaje(item);
        if (m) salida.push(m);
      }
    } else {
      const m = aMensaje(lista);
      if (m) salida.push(m);
    }
    if (salida.length === 0) {
      const suelto = texto(campo(r, "mensaje", "message", "error", "detalle"));
      if (suelto) salida.push({ codigo: texto(campo(r, "codigo", "code")), valor: suelto });
    }
  }
  if (salida.length === 0 && f.error) salida.push({ codigo: f.dgii_codigo, valor: f.error });
  if (salida.length === 0 && (f.dgii_codigo || f.dgii_estado)) {
    salida.push({ codigo: f.dgii_codigo, valor: f.dgii_estado ?? "La DGII no devolvió un motivo." });
  }
  // Los mensajes de un rechazo sin código propio heredan el código general de la respuesta.
  if (salida.length === 1 && !salida[0].codigo && f.dgii_codigo && f.estado === "rechazado") {
    salida[0] = { ...salida[0], codigo: f.dgii_codigo };
  }
  return salida;
}

/** true / false si la DGII lo informó; null si la respuesta no lo dice. */
export function secuenciaUtilizada(f: FacturaDetalle): boolean | null {
  const r = f.dgii_respuesta;
  if (!r || typeof r !== "object") return null;
  const v = campo(r, "secuenciaUtilizada", "secuencia_utilizada");
  if (typeof v === "boolean") return v;
  if (v === "true" || v === "false") return v === "true";
  return null;
}

const SUGERENCIAS: { patron: RegExp; consejo: string }[] = [
  {
    patron: /stock insuficiente|inventario/i,
    consejo: "Ajuste las cantidades a la existencia disponible o registre primero la compra de la mercancía, y vuelva a emitir.",
  },
  {
    patron: /no se pudo contactar|recepci[oó]n|timeout|tiempo de espera|conexi[oó]n|connect|502|503|504/i,
    consejo: "No hubo comunicación con la DGII. El comprobante no llegó a registrarse: reintente el envío en unos minutos con la misma secuencia.",
  },
  {
    patron: /token|autenticaci[oó]n|semilla|unauthori[sz]ed|401/i,
    consejo: "Falló la autenticación con la DGII. Revise que el certificado digital esté vigente y configurado en el servicio, y reintente.",
  },
  {
    patron: /firma|signature|certificado/i,
    consejo: "La firma digital no es válida. Verifique que el certificado (.p12) esté vigente y pertenezca al RNC emisor, y vuelva a emitir.",
  },
  {
    patron: /rnc ?comprador|comprador|c[eé]dula|contribuyente/i,
    consejo: "Revise el RNC o la cédula del cliente. Una factura de Crédito fiscal (E31) exige un comprador inscrito y activo; si no lo está, emita una factura de Consumo (E32).",
  },
  {
    patron: /rnc ?emisor|emisor/i,
    consejo: "Revise el RNC y la razón social del emisor: deben coincidir con los registrados en la DGII y con el certificado digital.",
  },
  {
    patron: /vencimiento.*secuencia|secuencia.*venc|FechaVencimientoSecuencia/i,
    consejo: "La secuencia autorizada venció o la fecha indicada no coincide con la autorizada. Solicite una nueva secuencia en la Oficina Virtual o corrija la fecha.",
  },
  {
    patron: /e-?ncf|secuencia|duplicad|ya existe|utilizad/i,
    consejo: "Ese e-NCF ya fue usado o no pertenece a una secuencia autorizada. Emita la factura con el siguiente e-NCF disponible.",
  },
  {
    patron: /ncf ?modificado|referencia|nota de cr[eé]dito|nota de d[eé]bito/i,
    consejo: "Revise el e-NCF que modifica la nota y su fecha: deben corresponder a un comprobante aceptado del mismo cliente.",
  },
  {
    patron: /monto|total|itbis|suma|redonde|descuento/i,
    consejo: "Los totales no cuadran con las líneas. Revise cantidades, precios, descuentos y la tasa de ITBIS de cada línea.",
  },
  {
    patron: /fecha/i,
    consejo: "Revise la fecha de emisión: no puede ser futura ni posterior al vencimiento de la secuencia.",
  },
  {
    patron: /xsd|estructura|schema|esquema|elemento|element|formato/i,
    consejo: "El XML no cumple la estructura del e-CF. Revise los campos señalados en el mensaje, corrija el comprobante y vuelva a emitir.",
  },
];

/** Sugerencia "Cómo corregirlo" para un mensaje de la DGII, o null si no reconocemos el caso. */
export function comoCorregir(mensaje: string): string | null {
  return SUGERENCIAS.find((s) => s.patron.test(mensaje))?.consejo ?? null;
}
