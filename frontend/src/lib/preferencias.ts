// Datos que el formulario de factura recuerda entre usos, en el navegador:
// el emisor y la fecha de vencimiento de cada secuencia autorizada. Desde la
// fase 6 la fuente es Configuración (erp-api: /configuracion/empresa y
// /configuracion/secuencias, ver configuracionApi.ts); esto queda SOLO como
// respaldo cuando erp-api no responde (la empresa leída se copia aquí).
// Los clientes viven en Contactos (erp-api); la copia local de los clientes
// ya facturados es solo el respaldo de "Verificar" el RNC cuando erp-api no
// responde. Si el navegador no deja guardar, todo sigue funcionando con los
// valores por defecto.
// Multiempresa: cada empresa con sus claves (la 1, las de siempre; ver
// empresaActual.ts claveEmpresa). Los valores por defecto VITE_EMPRESA_* son
// de la empresa 1.
import { claveEmpresa, empresaEnUso, EMPRESA_PREDETERMINADA } from "./empresaActual";
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

const CLAVE_EMISOR = () => claveEmpresa("kontara.emisor");
const CLAVE_SECUENCIAS = () => claveEmpresa("kontara.secuencias");
const CLAVE_CLIENTES = () => claveEmpresa("kontara.clientes");

export function leerEmisor(): DatosEmisor {
  const predeterminada = empresaEnUso() === EMPRESA_PREDETERMINADA;
  const emisor = leer<DatosEmisor>(CLAVE_EMISOR(), {
    rnc: predeterminada ? EMPRESA_RNC : "",
    razonSocial: !predeterminada || EMPRESA_NOMBRE === "Mi empresa" ? "" : EMPRESA_NOMBRE,
    direccion: "",
    correo: "",
  });
  return emisor;
}

export function guardarEmisor(emisor: DatosEmisor) {
  escribir(CLAVE_EMISOR(), emisor);
}

/** Fecha (YYYY-MM-DD) hasta la que está autorizada la secuencia de cada tipo de e-CF. */
export function leerVencimientoSecuencia(tipoEcf: string): string {
  return leer<Record<string, string>>(CLAVE_SECUENCIAS(), {})[tipoEcf] ?? "";
}

export function guardarVencimientoSecuencia(tipoEcf: string, fecha: string) {
  if (!fecha) return;
  escribir(CLAVE_SECUENCIAS(), { ...leer<Record<string, string>>(CLAVE_SECUENCIAS(), {}), [tipoEcf]: fecha });
}

export interface ClienteGuardado {
  razonSocial: string;
  correo: string;
  direccion: string;
}

export function buscarCliente(rnc: string): ClienteGuardado | null {
  const limpio = rnc.replace(/\D/g, "");
  if (!limpio) return null;
  return leer<Record<string, ClienteGuardado>>(CLAVE_CLIENTES(), {})[limpio] ?? null;
}

export function guardarCliente(rnc: string, cliente: ClienteGuardado) {
  const limpio = rnc.replace(/\D/g, "");
  if (!limpio || !cliente.razonSocial) return;
  escribir(CLAVE_CLIENTES(), { ...leer<Record<string, ClienteGuardado>>(CLAVE_CLIENTES(), {}), [limpio]: cliente });
}
