// Mis empresas (multiempresa): contrato con services/erp_api/app/empresas.py.
// La empresa en uso de la pestaña vive en empresaActual.ts.
import { useEffect, useState } from "react";
import { empresaSeleccionada, seleccionarEmpresa } from "./empresaActual";
import { ERP_API_URL, ErpApiError } from "./erpApi";
import { fetchConSesion } from "./sesion";

export type RolEmpresa = "admin" | "usuario";

export interface EmpresaResumen {
  id: number;
  rnc: string | null;
  razon_social: string;
  nombre_comercial: string | null;
  estado: string;
  rol: RolEmpresa;
  predeterminada: boolean;
}

/** Invitación pendiente a otra empresa (la acepta o rechaza el usuario). */
export interface Invitacion {
  id: number;
  rnc: string | null;
  razon_social: string;
  nombre_comercial: string | null;
  rol: RolEmpresa;
  agregado_por: string | null;
}

export interface MisEmpresas {
  /** La que el servidor usó para este request (la de la pestaña o, sin elegir, la preferida). */
  actual: number;
  empresas: EmpresaResumen[];
  invitaciones: Invitacion[];
  /** Administrador de la instalación (no de una empresa) y la base aísla las empresas. */
  puede_agregar: boolean;
  /** La base puede aislar los datos de cada empresa (si no, solo hay una). */
  aislamiento: boolean;
  motivo: string | null;
}

export interface NuevaEmpresa {
  rnc: string;
  razon_social: string;
  nombre_comercial?: string | null;
  actividad_economica?: string | null;
  direccion?: string | null;
  provincia?: string | null;
  municipio?: string | null;
  telefono?: string | null;
  correo?: string | null;
}

export interface ConsultaRnc {
  rnc: string;
  registrada: boolean;
  consultado: boolean;
  nombre: string | null;
  emisor_electronico: boolean | null;
  detalle: string | null;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetchConSesion(`${ERP_API_URL}${path}`, { headers: { "Content-Type": "application/json" }, ...init });
  if (!r.ok) {
    let detalle = r.statusText;
    try {
      const cuerpo = await r.json();
      detalle = typeof cuerpo.detail === "string" ? cuerpo.detail : JSON.stringify(cuerpo.detail ?? cuerpo);
    } catch {
      // sin cuerpo JSON
    }
    throw new ErpApiError(r.status, detalle);
  }
  return r.json() as Promise<T>;
}

export const empresasApi = {
  listar: () => request<MisEmpresas>("/empresas"),
  agregar: (datos: NuevaEmpresa) => request<EmpresaResumen>("/empresas", { method: "POST", body: JSON.stringify(datos) }),
  elegir: (empresaId: number) =>
    request<{ empresa_id: number }>("/empresas/actual", { method: "PUT", body: JSON.stringify({ empresa_id: empresaId }) }),
  responderInvitacion: (empresaId: number, aceptar: boolean) =>
    request<MisEmpresas>(`/empresas/${empresaId}/invitacion`, { method: "POST", body: JSON.stringify({ aceptar }) }),
  consultarRnc: (rnc: string) => request<ConsultaRnc>(`/empresas/consulta-rnc/${encodeURIComponent(rnc.replace(/\D/g, ""))}`),
};

/**
 * Cambia la empresa de esta pestaña: la recuerda en el servidor (la próxima
 * vez que el usuario entre desde otro navegador) y en este, y recarga desde
 * el inicio, para que ninguna pantalla quede mostrando datos de la anterior.
 */
export async function cambiarEmpresa(id: number): Promise<void> {
  await empresasApi.elegir(id).catch(() => undefined);
  seleccionarEmpresa(id);
  window.location.assign("/");
}

// --- caché compartida (la barra superior y Configuración) ---

let cache: MisEmpresas | null = null;
let promesa: Promise<MisEmpresas | null> | null = null;
const oyentes = new Set<(m: MisEmpresas | null) => void>();

export function cargarMisEmpresas(forzar = false): Promise<MisEmpresas | null> {
  if (promesa && !forzar) return promesa;
  promesa = empresasApi
    .listar()
    .then((m) => {
      cache = m;
      // Sin elección en este navegador: la que usó el servidor queda elegida
      // (los datos ya en pantalla son de esa misma).
      if (empresaSeleccionada() === null && m.empresas.length > 1) seleccionarEmpresa(m.actual);
      oyentes.forEach((o) => o(m));
      return m;
    })
    .catch(() => cache);
  return promesa;
}

export function useMisEmpresas(): MisEmpresas | null {
  const [valor, setValor] = useState<MisEmpresas | null>(cache);
  useEffect(() => {
    oyentes.add(setValor);
    void cargarMisEmpresas();
    return () => {
      oyentes.delete(setValor);
    };
  }, []);
  return valor;
}
