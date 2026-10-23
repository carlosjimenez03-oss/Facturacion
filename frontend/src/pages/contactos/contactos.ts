// Piezas compartidas por el listado y el formulario de contactos.
import { ErpApiError } from "../../lib/erpApi";
import type { Cliente, ClientePayload, TipoContacto, TipoIdentificacion } from "../../lib/erpApi";

export const TIPO_CONTACTO_LABEL: Record<TipoContacto, string> = {
  cliente: "Cliente",
  proveedor: "Proveedor",
  ambos: "Cliente y proveedor",
};

export const TIPO_IDENTIFICACION_LABEL: Record<TipoIdentificacion, string> = {
  rnc: "RNC",
  cedula: "Cédula",
  pasaporte: "Pasaporte",
};

/** Tipo de identificación que corresponde a la cantidad de dígitos (9 = RNC, 11 = cédula). */
export function tipoPorDigitos(valor: string): TipoIdentificacion | null {
  const d = valor.replace(/\D/g, "");
  if (d.length === 9) return "rnc";
  if (d.length === 11) return "cedula";
  return null;
}

/** Mensaje de error de validación de un RNC/cédula, o null si es válido. */
export function errorIdentificacion(tipo: TipoIdentificacion | "", valor: string): string | null {
  const d = valor.replace(/\D/g, "");
  if (tipo === "pasaporte") return valor.trim() ? null : "Escriba el número de pasaporte.";
  if (tipo === "rnc" && d.length !== 9) return "El RNC debe tener 9 dígitos.";
  if (tipo === "cedula" && d.length !== 11) return "La cédula debe tener 11 dígitos.";
  if (!tipo && valor.trim() && !tipoPorDigitos(valor)) return "El RNC debe tener 9 dígitos y la cédula 11 dígitos.";
  return null;
}

export function correoValido(correo: string): boolean {
  return !correo.trim() || /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(correo.trim());
}

/** Texto legible de un error de erp-api. Los 422 de FastAPI traen una lista de errores por campo. */
export function mensajeErp(err: unknown): string {
  if (err instanceof ErpApiError) {
    if (err.message.startsWith("[")) {
      try {
        const lista = JSON.parse(err.message) as { msg?: string; loc?: unknown[] }[];
        const textos = lista.map((e) => e.msg).filter(Boolean);
        if (textos.length) return textos.join(" · ");
      } catch {
        // no era JSON: se muestra tal cual
      }
    }
    return err.message;
  }
  return "No se pudo conectar con el servicio de contactos. Verifique su conexión e intente de nuevo.";
}

/** Payload completo para actualizar un contacto existente (PUT reemplaza todos los campos). */
export function payloadDesde(c: Cliente, cambios: Partial<ClientePayload> = {}): ClientePayload {
  return {
    tipo: c.tipo,
    tipo_identificacion: c.tipo_identificacion,
    rnc: c.rnc,
    nombre: c.nombre,
    correo: c.correo,
    telefono: c.telefono,
    direccion: c.direccion,
    condicion_pago_dias: c.condicion_pago_dias,
    limite_credito: c.limite_credito,
    notas: c.notas,
    activo: c.activo,
    ...cambios,
  };
}
