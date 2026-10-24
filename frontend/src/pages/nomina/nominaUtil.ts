// Utilidades de las pantallas de Kontara Nómina.
import { createContext, useCallback, useEffect, useState } from "react";
import { nominaApi, type AvisoNomina, type Empleado, type EmpleadoPayload, type EmpresaNomina, type EmpresaPayload, type EstadoPeriodo } from "../../lib/nominaApi";

/** true cuando el layout global ya dibuja <NominaSidebar /> en lugar del menú principal. */
export const NominaSidebarGlobal = createContext(false);

export const esRutaNomina = (pathname: string) => pathname === "/nomina" || pathname.startsWith("/nomina/");

export const MESES = [
  "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
];

export const capitalizar = (s: string) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : s);

/** "0.0287" -> "2.87%" */
export function porcentaje(tasa: string | number | null | undefined, decimales = 2): string {
  const n = Number(tasa ?? 0) * 100;
  return `${n.toFixed(decimales)}%`;
}

/** "2.87" (lo que escribe el usuario en %) -> "0.0287" */
export function aFraccion(pct: string): string {
  const n = Number(pct.replace(",", "."));
  return Number.isFinite(n) ? String(Math.round(n * 1e5) / 1e7) : "";
}

/** "0.0287" -> "2.87" para un campo en % */
export function aPorcentaje(tasa: string | null | undefined): string {
  const n = Number(tasa ?? 0) * 100;
  return String(Math.round(n * 1e5) / 1e5);
}

export const ESTADO_PERIODO: Record<EstadoPeriodo, { label: string; badge: string }> = {
  borrador: { label: "Sin calcular", badge: "bd b-borr" },
  calculada: { label: "Pendiente de aprobación", badge: "bd b-proc" },
  aprobada: { label: "Programada", badge: "bd b-env" },
  pagada: { label: "Pagada", badge: "bd b-acep" },
  cerrada: { label: "Cerrada", badge: "bd b-acep" },
};

/** 40223456781 -> 402-2345678-1 */
export function formatoCedula(cedula: string | null | undefined): string {
  const d = (cedula ?? "").replace(/\D/g, "");
  return d.length === 11 ? `${d.slice(0, 3)}-${d.slice(3, 10)}-${d.slice(10)}` : (cedula ?? "");
}

export function saludo(fecha = new Date()): string {
  const h = fecha.getHours();
  return h < 12 ? "Buenos días" : h < 19 ? "Buenas tardes" : "Buenas noches";
}

export function descargarBlob(nombre: string, blob: Blob) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = nombre;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 8000);
}

// --- Bandeja: avisos leídos (solo en este navegador) ---------------------

const CLAVE_LEIDOS = "kontara.nomina.avisos-leidos";
const EVENTO = "kontara-nomina-avisos";

function leerLeidos(): Set<string> {
  try {
    return new Set(JSON.parse(localStorage.getItem(CLAVE_LEIDOS) ?? "[]") as string[]);
  } catch {
    return new Set();
  }
}

function guardarLeidos(ids: Set<string>) {
  try {
    localStorage.setItem(CLAVE_LEIDOS, JSON.stringify([...ids]));
  } catch {
    // sin almacenamiento: solo dura mientras la página esté abierta
  }
  window.dispatchEvent(new Event(EVENTO));
}

/** Avisos de la bandeja de Nómina y cuáles ya se leyeron. Se sincroniza entre el menú y la bandeja. */
export function useAvisosNomina() {
  const [avisos, setAvisos] = useState<AvisoNomina[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [leidos, setLeidos] = useState<Set<string>>(leerLeidos);

  const cargar = useCallback(() => {
    nominaApi
      .avisos()
      .then((a) => {
        setAvisos(a);
        setError(null);
      })
      .catch((e) => {
        setAvisos([]);
        setError(e);
      });
  }, []);

  useEffect(() => {
    cargar();
    const alCambiar = () => {
      setLeidos(leerLeidos());
      cargar();
    };
    window.addEventListener(EVENTO, alCambiar);
    return () => window.removeEventListener(EVENTO, alCambiar);
  }, [cargar]);

  const marcar = useCallback((id: string, leido: boolean) => {
    const s = leerLeidos();
    if (leido) s.add(id);
    else s.delete(id);
    guardarLeidos(s);
  }, []);

  const marcarTodos = useCallback((ids: string[]) => {
    const s = leerLeidos();
    ids.forEach((id) => s.add(id));
    guardarLeidos(s);
  }, []);

  const noLeidos = (avisos ?? []).filter((a) => !leidos.has(a.id)).length;
  return { avisos, error, leidos, noLeidos, marcar, marcarTodos, recargar: cargar };
}

/** Avisa al menú de Nómina que cambió algo que afecta la bandeja (p. ej. se aprobó una nómina). */
export function refrescarAvisos() {
  window.dispatchEvent(new Event(EVENTO));
}

export const CATEGORIAS = [
  "Comercio al por mayor", "Comercio al por menor", "Servicios", "Manufactura", "Construcción", "Hoteles y restaurantes",
  "Transporte y almacenamiento", "Agropecuaria", "Zona franca", "Salud", "Educación", "Tecnología", "Financiero",
];

export const MUNICIPIOS = [
  "Santo Domingo de Guzmán", "Santo Domingo Este", "Santo Domingo Norte", "Santo Domingo Oeste", "Los Alcarrizos",
  "Boca Chica", "Santiago de los Caballeros", "La Vega", "San Cristóbal", "San Pedro de Macorís", "La Romana",
  "Higüey", "Puerto Plata", "San Francisco de Macorís", "Moca", "Baní", "Bonao", "Azua",
];

export function payloadEmpresa(e: EmpresaNomina): EmpresaPayload {
  return {
    rnc: e.rnc, razon_social: e.razon_social, categoria: e.categoria, municipio: e.municipio, contacto_nombre: e.contacto_nombre,
    tasa_srl: e.tasa_srl, frecuencia_pago: e.frecuencia_pago, dia_pago: e.dia_pago, banco: e.banco, cuenta_origen: e.cuenta_origen,
  };
}

export function payloadEmpleado(e: Empleado): EmpleadoPayload {
  return {
    cedula: e.cedula, nombre: e.nombre, cargo: e.cargo, departamento: e.departamento, fecha_ingreso: e.fecha_ingreso,
    salario_mensual: e.salario_mensual, tipo_pago: e.tipo_pago, banco: e.banco, cuenta_bancaria: e.cuenta_bancaria,
    tipo_cuenta: e.tipo_cuenta, afp: e.afp, ars: e.ars, correo: e.correo, estado: e.estado, fecha_salida: e.fecha_salida,
  };
}
