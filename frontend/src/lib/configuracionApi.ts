// Cliente de Configuración y Plataforma de `erp-api` (fase 6): empresa,
// certificado digital, secuencias e-NCF, vendedores, usuarios (Supabase
// Auth), método de inventario, envío por correo/WhatsApp y "Estado de las
// soluciones". CONTRATO con services/erp_api/app/configuracion.py,
// configuracion_schemas.py y plataforma.py: nombres y tipos 1:1.

import { useEffect, useState } from "react";
import { ERP_API_URL, ErpApiError } from "./erpApi";
import { guardarEmisor } from "./preferencias";
import { fetchConSesion } from "./sesion";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetchConSesion(`${ERP_API_URL}${path}`, {
    headers: init?.body instanceof FormData ? undefined : { "Content-Type": "application/json" },
    ...init,
  });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = body.detail ?? JSON.stringify(body);
    } catch {
      // sin cuerpo JSON, se mantiene statusText
    }
    throw new ErpApiError(response.status, typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

const json = (method: string, body: unknown): RequestInit => ({ method, body: JSON.stringify(body) });

export interface Empresa {
  configurada: boolean;
  razon_social: string;
  nombre_comercial: string | null;
  rnc: string | null;
  actividad_economica: string | null;
  direccion: string | null;
  provincia: string | null;
  municipio: string | null;
  telefono: string | null;
  correo: string | null;
  logo: string | null;
  ambiente: string;
  ambiente_dgii: string;
  emisor_rnc_configurado: string | null;
  actualizado_en: string | null;
}

export type EmpresaPayload = Pick<
  Empresa,
  "razon_social" | "nombre_comercial" | "rnc" | "actividad_economica" | "direccion" | "provincia" | "municipio" | "telefono" | "correo" | "logo"
>;

export type EstadoSecuencia = "sin_configurar" | "inactiva" | "vencida" | "agotada" | "por_vencer" | "por_agotarse" | "vigente";

export interface Secuencia {
  tipo_ecf: number;
  nombre: string;
  prefijo: string;
  configurada: boolean;
  desde: number | null;
  hasta: number | null;
  proximo: number | null;
  fecha_vencimiento: string | null;
  activa: boolean;
  ultimo_usado: number | null;
  siguiente: number | null;
  siguiente_encf: string | null;
  usados: number;
  disponibles: number;
  consumo_pct: number;
  estado: EstadoSecuencia;
  avisos: string[];
  actualizado_en: string | null;
}

export interface SecuenciaPayload {
  desde: number;
  hasta: number;
  proximo: number | null;
  fecha_vencimiento: string | null;
  activa: boolean;
}

export interface Vendedor {
  id: string;
  nombre: string;
  correo: string | null;
  comision_pct: string;
  activo: boolean;
  creado_en: string;
  actualizado_en: string;
}

export interface VendedorPayload {
  nombre: string;
  correo: string | null;
  comision_pct: string;
  activo: boolean;
}

export interface Usuario {
  id: string;
  correo: string | null;
  creado_en: string | null;
  ultimo_acceso: string | null;
  confirmado: boolean;
  activo: boolean;
  invitado: boolean;
}

export interface Usuarios {
  configurado: boolean;
  usuarios: Usuario[];
  mensaje: string | null;
}

export interface Certificado {
  estado: "vigente" | "por_vencer" | "vencido" | "no_configurado" | "error";
  origen: "cargado" | "variables" | null;
  archivo: string | null;
  titular: string | null;
  firmante: string | null;
  documento_firmante: string | null;
  sujeto: string | null;
  emisor: string | null;
  rnc: string | null;
  numero_serie: string | null;
  valido_desde: string | null;
  vence: string | null;
  dias_restantes: number | null;
  dias_para_vencer: number | null;
  vigencia_consumida_pct: number | null;
  coincide_rnc_emisor: boolean | null;
  error: string | null;
  carga_disponible: boolean;
}

export interface CorreoConfig {
  host: string;
  puerto: number;
  seguridad: "starttls" | "ssl" | "ninguna";
  usuario: string;
  remitente: string;
  cco: string;
  adjuntar_xml: boolean;
  envio_automatico: boolean;
  contrasena_configurada: boolean;
  configurado: boolean;
}

export interface WhatsappConfig {
  phone_number_id: string;
  numero: string;
  plantilla: string;
  idioma: string;
  envio_automatico: boolean;
  token_configurado: boolean;
  conectado: boolean;
}

export interface EnvioConfig {
  correo: CorreoConfig;
  whatsapp: WhatsappConfig;
  enviados_mes: Record<string, number>;
  cifrado_disponible: boolean;
}

export type EstadoServicio = "ok" | "error" | "caido" | "no_configurado";

export interface ServicioEstado {
  id: string;
  nombre: string;
  grupo: string;
  estado: EstadoServicio;
  latencia_ms: number | null;
  detalle: string | null;
}

export interface EstadoServicios {
  ambiente: string;
  ambiente_dgii: string;
  verificado_en: string;
  servicios: ServicioEstado[];
}

export const configuracionApi = {
  empresa: () => request<Empresa>("/configuracion/empresa"),
  guardarEmpresa: (p: EmpresaPayload) => request<Empresa>("/configuracion/empresa", json("PUT", p)),

  certificado: () => request<Certificado>("/configuracion/certificado"),
  cargarCertificado: (archivo: File, contrasena: string) => {
    const datos = new FormData();
    datos.append("archivo", archivo);
    datos.append("contrasena", contrasena);
    return request<Certificado>("/configuracion/certificado", { method: "POST", body: datos });
  },

  secuencias: () => request<Secuencia[]>("/configuracion/secuencias"),
  guardarSecuencia: (tipo: number, p: SecuenciaPayload) => request<Secuencia>(`/configuracion/secuencias/${tipo}`, json("PUT", p)),
  eliminarSecuencia: (tipo: number) => request<void>(`/configuracion/secuencias/${tipo}`, { method: "DELETE" }),

  vendedores: (p?: { activo?: boolean }) =>
    request<Vendedor[]>(`/configuracion/vendedores${p?.activo === undefined ? "" : `?activo=${p.activo}`}`),
  crearVendedor: (p: VendedorPayload) => request<Vendedor>("/configuracion/vendedores", json("POST", p)),
  actualizarVendedor: (id: string, p: VendedorPayload) => request<Vendedor>(`/configuracion/vendedores/${id}`, json("PUT", p)),
  eliminarVendedor: (id: string) => request<void>(`/configuracion/vendedores/${id}`, { method: "DELETE" }),

  usuarios: () => request<Usuarios>("/configuracion/usuarios"),
  crearUsuario: (correo: string, contrasena: string | null) =>
    request<Usuario>("/configuracion/usuarios", json("POST", { correo, contrasena: contrasena || null })),
  desactivarUsuario: (id: string) => request<Usuario>(`/configuracion/usuarios/${id}/desactivar`, { method: "POST" }),
  activarUsuario: (id: string) => request<Usuario>(`/configuracion/usuarios/${id}/activar`, { method: "POST" }),

  inventario: () => request<{ metodo: "promedio" | "peps" }>("/configuracion/inventario"),
  guardarInventario: (metodo: "promedio" | "peps") => request<{ metodo: string }>("/configuracion/inventario", json("PUT", { metodo })),

  envio: () => request<EnvioConfig>("/configuracion/envio"),
  guardarCorreo: (p: Omit<CorreoConfig, "contrasena_configurada" | "configurado"> & { contrasena: string | null }) =>
    request<EnvioConfig>("/configuracion/envio/correo", json("PUT", p)),
  probarCorreo: (destino: string) => request<{ ok: boolean; mensaje: string }>("/configuracion/envio/correo/prueba", json("POST", { destino })),
  guardarWhatsapp: (p: Omit<WhatsappConfig, "token_configurado" | "conectado"> & { token: string | null }) =>
    request<EnvioConfig>("/configuracion/envio/whatsapp", json("PUT", p)),
  desconectarWhatsapp: () => request<EnvioConfig>("/configuracion/envio/whatsapp", { method: "DELETE" }),
  probarWhatsapp: (destino: string) => request<{ ok: boolean; mensaje: string }>("/configuracion/envio/whatsapp/prueba", json("POST", { destino })),

  estadoServicios: () => request<EstadoServicios>("/estado-servicios"),
};

// --- empresa compartida (barra superior, factura, configuración) ---

let empresaCache: Empresa | null = null;
let empresaPromesa: Promise<Empresa | null> | null = null;
const oyentesEmpresa = new Set<(e: Empresa | null) => void>();

/** Lee la empresa una vez por carga de página (y la recuerda en el navegador como respaldo). */
export function cargarEmpresa(forzar = false): Promise<Empresa | null> {
  if (empresaPromesa && !forzar) return empresaPromesa;
  empresaPromesa = configuracionApi
    .empresa()
    .then((e) => {
      publicarEmpresa(e);
      return e;
    })
    .catch(() => empresaCache);
  return empresaPromesa;
}

export function publicarEmpresa(e: Empresa) {
  empresaCache = e;
  if (e.configurada) {
    guardarEmisor({ rnc: e.rnc ?? "", razonSocial: e.razon_social, direccion: e.direccion ?? "", correo: e.correo ?? "" });
  }
  oyentesEmpresa.forEach((o) => o(e));
}

export function useEmpresa(): Empresa | null {
  const [empresa, setEmpresa] = useState<Empresa | null>(empresaCache);
  useEffect(() => {
    oyentesEmpresa.add(setEmpresa);
    void cargarEmpresa();
    return () => {
      oyentesEmpresa.delete(setEmpresa);
    };
  }, []);
  return empresa;
}

/** Nombre a mostrar: comercial, o razón social. */
export function nombreEmpresa(e: Empresa | null, porDefecto: string): string {
  return e?.nombre_comercial || e?.razon_social || porDefecto;
}
