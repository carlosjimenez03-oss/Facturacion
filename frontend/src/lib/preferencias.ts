// Datos que el formulario de factura recuerda entre usos, en el navegador:
// el emisor (para no escribirlo en cada factura), la fecha de vencimiento de
// cada secuencia autorizada y los clientes ya facturados (para "Verificar"
// el RNC). Es solo una comodidad local hasta que existan los servicios de
// configuración y de clientes; si el navegador no deja guardar, todo sigue
// funcionando con los valores por defecto.
import { EMPRESA_NOMBRE, EMPRESA_RNC } from "./marca";

function leer<T>(clave: string, porDefecto: T): T {
  try {
    const valor = localStorage.getItem(clave);
    return valor ? { ...porDefecto, ...JSON.parse(valor) } : porDefecto;
  } catch {
    return porDefecto;
  }
}

function escribir(clave: string, valor: unknown) {
  try {
    localStorage.setItem(clave, JSON.stringify(valor));
  } catch {
    // almacenamiento bloqueado o lleno: se ignora
  }
}

export interface DatosEmisor {
  rnc: string;
  razonSocial: string;
  direccion: string;
  correo: string;
}

const CLAVE_EMISOR = "kontara.emisor";
const CLAVE_SECUENCIAS = "kontara.secuencias";
const CLAVE_CLIENTES = "kontara.clientes";

export function leerEmisor(): DatosEmisor {
  const emisor = leer<DatosEmisor>(CLAVE_EMISOR, {
    rnc: EMPRESA_RNC,
    razonSocial: EMPRESA_NOMBRE === "Mi empresa" ? "" : EMPRESA_NOMBRE,
    direccion: "",
    correo: "",
  });
  return emisor;
}

export function guardarEmisor(emisor: DatosEmisor) {
  escribir(CLAVE_EMISOR, emisor);
}

/** Fecha (YYYY-MM-DD) hasta la que está autorizada la secuencia de cada tipo de e-CF. */
export function leerVencimientoSecuencia(tipoEcf: string): string {
  return leer<Record<string, string>>(CLAVE_SECUENCIAS, {})[tipoEcf] ?? "";
}

export function guardarVencimientoSecuencia(tipoEcf: string, fecha: string) {
  if (!fecha) return;
  escribir(CLAVE_SECUENCIAS, { ...leer<Record<string, string>>(CLAVE_SECUENCIAS, {}), [tipoEcf]: fecha });
}

export interface ClienteGuardado {
  razonSocial: string;
  correo: string;
  direccion: string;
}

export function buscarCliente(rnc: string): ClienteGuardado | null {
  const limpio = rnc.replace(/\D/g, "");
  if (!limpio) return null;
  return leer<Record<string, ClienteGuardado>>(CLAVE_CLIENTES, {})[limpio] ?? null;
}

export function guardarCliente(rnc: string, cliente: ClienteGuardado) {
  const limpio = rnc.replace(/\D/g, "");
  if (!limpio || !cliente.razonSocial) return;
  escribir(CLAVE_CLIENTES, { ...leer<Record<string, ClienteGuardado>>(CLAVE_CLIENTES, {}), [limpio]: cliente });
}
