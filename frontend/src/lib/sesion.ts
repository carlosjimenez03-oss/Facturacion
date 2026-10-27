// Sesión de los usuarios con Supabase Auth (GoTrue), por su API REST:
// POST /auth/v1/token?grant_type=password|refresh_token, /auth/v1/logout,
// /auth/v1/recover y GET/PUT /auth/v1/user, siempre con `apikey` = ANON_KEY.
//
// Todos los clientes de API (api.ts, inventarioApi.ts, erpApi.ts, ...) usan
// `fetchConSesion` en vez de `fetch`: agrega `Authorization: Bearer
// <access_token>`, renueva el token antes de que venza y, si una API
// responde 401, lo renueva una vez y reintenta; si aun así falla, cierra la
// sesión (la guarda de rutas lleva a /login). La cookie `kontara_sesion`
// lleva el mismo token para los enlaces que abre el navegador sin fetch
// (PDF de la factura, XML de la bandeja): nginx la acepta SOLO para esas
// descargas (GET); todo lo demás exige la cabecera Authorization, que un
// sitio ajeno no puede poner (CSRF).
//
// El token vive en localStorage (no en una cookie HttpOnly): un XSS en esta
// aplicación podría leerlo hasta que venza (1 h, o ~1 min después de cerrar
// la sesión si erp-api ve auth.sessions). Es el mismo modelo de supabase-js
// y a cambio no hay CSRF (el token no viaja solo). React escapa todo el
// contenido; no inserte HTML sin sanear. Ver docs/AUTH.md.
//
// Sin VITE_SUPABASE_URL / VITE_SUPABASE_ANON_KEY en el build, el inicio de
// sesión queda apagado (AUTH_CONFIGURADA = false) y todo funciona como antes.
// Ver docs/AUTH.md.
//
// Multiempresa: `fetchConSesion` manda también la empresa de la pestaña
// (`X-Kontara-Empresa`, ver empresaActual.ts). Si el servidor responde que el
// usuario no es miembro de esa empresa (403 sin_empresa), se olvida la
// elección y se recarga: el servidor da la que le corresponde.
import { cabeceraEmpresa, empresaSeleccionada, fijarUsuarioEmpresa, seleccionarEmpresa } from "./empresaActual";

const URL_CRUDA = ((import.meta.env.VITE_SUPABASE_URL as string | undefined) ?? "").trim();
export const SUPABASE_ANON_KEY = ((import.meta.env.VITE_SUPABASE_ANON_KEY as string | undefined) ?? "").trim();
/** Base de GoTrue: "/" = mismo dominio (nginx reenvía /auth/v1/ al gateway de Supabase). */
export const SUPABASE_URL = URL_CRUDA.replace(/\/+$/, "");
export const AUTH_CONFIGURADA = URL_CRUDA !== "" && SUPABASE_ANON_KEY !== "";

const CLAVE = "kontara.sesion";
const COOKIE = "kontara_sesion";
/** Renovar cuando falten menos de 60 s para que venza el access_token. */
const MARGEN_MS = 60_000;

export interface UsuarioSesion {
  id: string;
  email: string;
  nombre: string;
  telefono: string;
}

export interface Sesion {
  access_token: string;
  refresh_token: string;
  /** Epoch en milisegundos. */
  expira: number;
  usuario: UsuarioSesion;
  /** Entró por el enlace de "¿Olvidó su contraseña?": debe elegir una nueva. */
  recuperacion?: boolean;
}

export class AuthError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

// --- estado + suscriptores (useSyncExternalStore) ---

let sesion: Sesion | null = leerGuardada();
const oyentes = new Set<() => void>();
let temporizador: number | undefined;
fijarUsuarioEmpresa(sesion?.usuario.id ?? null);

function leerGuardada(): Sesion | null {
  if (!AUTH_CONFIGURADA) return null;
  try {
    const crudo = localStorage.getItem(CLAVE);
    return crudo ? (JSON.parse(crudo) as Sesion) : null;
  } catch {
    return null;
  }
}

function escribirCookie(token: string | null, expira?: number) {
  const segura = typeof location !== "undefined" && location.protocol === "https:" ? "; Secure" : "";
  if (!token) {
    document.cookie = `${COOKIE}=; Path=/; Max-Age=0; SameSite=Strict${segura}`;
    return;
  }
  const maxAge = Math.max(0, Math.floor(((expira ?? Date.now()) - Date.now()) / 1000));
  document.cookie = `${COOKIE}=${token}; Path=/; Max-Age=${maxAge}; SameSite=Strict${segura}`;
}

function guardar(nueva: Sesion | null) {
  sesion = nueva;
  fijarUsuarioEmpresa(nueva?.usuario.id ?? null);
  try {
    if (nueva) localStorage.setItem(CLAVE, JSON.stringify(nueva));
    else localStorage.removeItem(CLAVE);
  } catch {
    // almacenamiento bloqueado: la sesión vive solo en memoria
  }
  escribirCookie(nueva?.access_token ?? null, nueva?.expira);
  programarRenovacion();
  oyentes.forEach((o) => o());
}

export function sesionActual(): Sesion | null {
  return sesion;
}

export function suscribirSesion(oyente: () => void): () => void {
  oyentes.add(oyente);
  return () => oyentes.delete(oyente);
}

// Otra pestaña inició o cerró sesión.
if (typeof window !== "undefined" && AUTH_CONFIGURADA) {
  window.addEventListener("storage", (e) => {
    if (e.key !== CLAVE) return;
    sesion = leerGuardada();
    fijarUsuarioEmpresa(sesion?.usuario.id ?? null);
    programarRenovacion();
    oyentes.forEach((o) => o());
  });
}

// --- GoTrue REST ---

interface RespuestaToken {
  access_token: string;
  refresh_token: string;
  expires_in: number;
  expires_at?: number;
  user: GoTrueUser;
}

interface GoTrueUser {
  id: string;
  email?: string;
  phone?: string;
  user_metadata?: Record<string, unknown>;
}

function usuarioDe(u: GoTrueUser): UsuarioSesion {
  const meta = u.user_metadata ?? {};
  return {
    id: u.id,
    email: u.email ?? "",
    nombre: String(meta.full_name ?? meta.nombre ?? ""),
    telefono: String(meta.telefono ?? meta.phone ?? u.phone ?? ""),
  };
}

const MENSAJES: Record<string, string> = {
  invalid_credentials: "Correo o contraseña incorrectos.",
  "Invalid login credentials": "Correo o contraseña incorrectos.",
  email_not_confirmed: "Confirme su correo antes de entrar (revise su bandeja de entrada).",
  "Email not confirmed": "Confirme su correo antes de entrar (revise su bandeja de entrada).",
  user_banned: "Este usuario está desactivado. Pida acceso a un administrador.",
  over_request_rate_limit: "Demasiados intentos. Espere unos minutos y vuelva a intentarlo.",
  over_email_send_rate_limit: "Ya se envió un correo hace poco. Espere unos minutos.",
  weak_password: "La contraseña es muy débil: use al menos 8 caracteres.",
  same_password: "La nueva contraseña debe ser distinta de la actual.",
  refresh_token_not_found: "Su sesión expiró. Inicie sesión de nuevo.",
};

async function gotrue<T>(ruta: string, init: RequestInit & { token?: string } = {}): Promise<T> {
  const { token, ...resto } = init;
  let respuesta: Response;
  try {
    respuesta = await fetch(`${SUPABASE_URL}/auth/v1${ruta}`, {
      ...resto,
      headers: {
        "Content-Type": "application/json",
        apikey: SUPABASE_ANON_KEY,
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(resto.headers as Record<string, string> | undefined),
      },
    });
  } catch {
    throw new AuthError(0, "No se pudo conectar con el servicio de inicio de sesión.");
  }
  if (!respuesta.ok) {
    let mensaje = `Error ${respuesta.status} del servicio de inicio de sesión.`;
    try {
      const cuerpo = await respuesta.json();
      const clave = cuerpo.error_code ?? cuerpo.msg ?? cuerpo.error_description ?? cuerpo.error;
      mensaje = MENSAJES[clave] ?? MENSAJES[cuerpo.msg] ?? MENSAJES[cuerpo.error_description] ?? cuerpo.msg ?? cuerpo.error_description ?? mensaje;
    } catch {
      // sin cuerpo JSON
    }
    throw new AuthError(respuesta.status, mensaje);
  }
  if (respuesta.status === 204) return undefined as T;
  const texto = await respuesta.text();
  return (texto ? JSON.parse(texto) : undefined) as T;
}

function sesionDe(r: RespuestaToken, recuperacion = false): Sesion {
  const expira = r.expires_at ? r.expires_at * 1000 : Date.now() + r.expires_in * 1000;
  return { access_token: r.access_token, refresh_token: r.refresh_token, expira, usuario: usuarioDe(r.user), recuperacion };
}

// erp-api por el proxy de nginx (mismo valor que erpApi.ts, sin importarlo:
// erpApi.ts importa este módulo).
const ERP_API_URL = ((import.meta.env.VITE_ERP_API_URL as string | undefined) ?? "http://localhost:8019").replace(/\/+$/, "");

/** null si erp-api acepta el token; si no, el motivo (p. ej. usuario sin acceso a Kontara). */
async function motivoRechazo(token: string): Promise<string | null> {
  try {
    const r = await fetch(`${ERP_API_URL}/auth/verificar`, { headers: { Authorization: `Bearer ${token}` }, credentials: "omit" });
    if (r.status !== 401) return null;
    const cuerpo = await r.json().catch(() => ({}));
    return typeof cuerpo.detail === "string" ? cuerpo.detail : "Su usuario no tiene acceso a Kontara.";
  } catch {
    return null; // sin red: lo dirán las APIs
  }
}

export async function iniciarSesion(email: string, password: string): Promise<Sesion> {
  const r = await gotrue<RespuestaToken>("/token?grant_type=password", {
    method: "POST",
    body: JSON.stringify({ email: email.trim().toLowerCase(), password }),
  });
  // Un usuario del mismo Supabase Auth sin app_metadata.kontara no entra:
  // se le dice por qué en vez de volver a /login sin explicación.
  const rechazo = await motivoRechazo(r.access_token);
  if (rechazo) {
    await gotrue<void>("/logout?scope=local", { method: "POST", token: r.access_token }).catch(() => undefined);
    throw new AuthError(403, rechazo);
  }
  const nueva = sesionDe(r);
  guardar(nueva);
  return nueva;
}

/**
 * Con el inicio de sesión configurado, las APIs NO deben responder sin
 * token: pide /api-erp/health sin sesión y espera el 401 de nginx (erp-api
 * deja /health abierto, así que solo nginx puede negarlo). Si llega otra
 * cosa (200 = nginx abierto, 503 = sin SUPABASE_JWT_SECRET en nginx o en
 * erp-api) la aplicación muestra un aviso rojo. null = no se sabe: sin red,
 * o 502/504 (erp-api reiniciándose tras un despliegue), que no es una falla
 * de protección y se vuelve a comprobar en la próxima carga.
 */
let comprobacion: Promise<boolean | null> | null = null;

export function apisProtegidas(): Promise<boolean | null> {
  if (!AUTH_CONFIGURADA) return Promise.resolve(true);
  // Una sola vez por carga de la página (la piden el login y el AppShell).
  comprobacion ??= fetch(`${ERP_API_URL}/health`, { credentials: "omit", cache: "no-store" })
    .then((r) => {
      if (r.status === 502 || r.status === 504) {
        comprobacion = null;
        return null;
      }
      return r.status === 401;
    })
    .catch(() => {
      comprobacion = null; // sin red: se vuelve a intentar la próxima vez
      return null;
    });
  return comprobacion;
}

let renovando: Promise<Sesion | null> | null = null;

/** Renueva el access_token con el refresh_token (una sola petición a la vez). */
export function renovarSesion(): Promise<Sesion | null> {
  if (!sesion) return Promise.resolve(null);
  if (renovando) return renovando;
  const refresh = sesion.refresh_token;
  renovando = gotrue<RespuestaToken>("/token?grant_type=refresh_token", {
    method: "POST",
    body: JSON.stringify({ refresh_token: refresh }),
  })
    .then((r) => {
      const nueva = sesionDe(r, sesion?.recuperacion);
      guardar(nueva);
      return nueva;
    })
    .catch((err: unknown) => {
      // 4xx = refresh token inválido/revocado: fuera. Sin red: se conserva.
      if (err instanceof AuthError && err.status >= 400 && err.status < 500) guardar(null);
      return null;
    })
    .finally(() => {
      renovando = null;
    });
  return renovando;
}

function programarRenovacion() {
  if (typeof window === "undefined") return;
  window.clearTimeout(temporizador);
  if (!sesion) return;
  const espera = Math.max(5_000, sesion.expira - Date.now() - MARGEN_MS);
  temporizador = window.setTimeout(() => void renovarSesion(), espera);
}
programarRenovacion();

export async function cerrarSesion(alcance: "local" | "global" | "others" = "local"): Promise<void> {
  const actual = sesion;
  if (alcance !== "others") guardar(null);
  if (actual) {
    try {
      await gotrue<void>(`/logout?scope=${alcance}`, { method: "POST", token: actual.access_token });
    } catch {
      // el token ya no sirve o no hay red: la sesión local igual se cerró
    }
  }
}

/** "¿Olvidó su contraseña?": GoTrue envía el enlace de recuperación por correo. */
export async function recuperarContrasena(email: string): Promise<void> {
  const vuelta = `${window.location.origin}/login`;
  await gotrue<void>(`/recover?redirect_to=${encodeURIComponent(vuelta)}`, {
    method: "POST",
    body: JSON.stringify({ email: email.trim().toLowerCase() }),
  });
}

async function actualizarUsuario(cambios: Record<string, unknown>): Promise<UsuarioSesion> {
  const actual = await tokenVigente();
  if (!actual) throw new AuthError(401, "Su sesión expiró. Inicie sesión de nuevo.");
  const u = await gotrue<GoTrueUser>("/user", { method: "PUT", token: actual, body: JSON.stringify(cambios) });
  const usuario = usuarioDe(u);
  if (sesion) guardar({ ...sesion, usuario, recuperacion: cambios.password ? false : sesion.recuperacion });
  return usuario;
}

export function cambiarContrasena(nueva: string): Promise<UsuarioSesion> {
  return actualizarUsuario({ password: nueva });
}

export function actualizarPerfil(perfil: { nombre: string; telefono: string }): Promise<UsuarioSesion> {
  return actualizarUsuario({ data: { full_name: perfil.nombre.trim(), telefono: perfil.telefono.trim() } });
}

/**
 * Enlace de recuperación (o invitación): GoTrue vuelve a /login con
 * #access_token=…&refresh_token=…&type=recovery|invite. Se guarda la sesión
 * y se pide la nueva contraseña. Devuelve true si había uno.
 */
export async function capturarSesionDeUrl(): Promise<boolean> {
  if (!AUTH_CONFIGURADA || !window.location.hash.includes("access_token=")) return false;
  const p = new URLSearchParams(window.location.hash.slice(1));
  const access = p.get("access_token");
  const refresh = p.get("refresh_token");
  if (!access || !refresh) return false;
  window.history.replaceState(null, "", window.location.pathname + window.location.search);
  const tipo = p.get("type");
  const user = await gotrue<GoTrueUser>("/user", { token: access });
  guardar({
    access_token: access,
    refresh_token: refresh,
    expira: Date.now() + Number(p.get("expires_in") ?? 3600) * 1000,
    usuario: usuarioDe(user),
    recuperacion: tipo === "recovery" || tipo === "invite",
  });
  return true;
}

/**
 * Antes de sincronizar las ventas que el POS guardó sin conexión: renueva el
 * token si venció o está por vencer (pudo pasar horas sin red). "ok" = hay
 * sesión vigente (o el inicio de sesión no está configurado); "sin_red" = no
 * se pudo renovar por falta de conexión (se reintenta luego); "sin_sesion" =
 * hay que iniciar sesión de nuevo. En los dos últimos casos el POS conserva
 * las ventas pendientes en el navegador.
 */
export async function prepararSesion(): Promise<"ok" | "sin_red" | "sin_sesion"> {
  if (!AUTH_CONFIGURADA) return "ok";
  if (!sesion) return "sin_sesion";
  if (sesion.expira - Date.now() >= MARGEN_MS) return "ok";
  if (await renovarSesion()) return "ok";
  return sesion ? "sin_red" : "sin_sesion";
}

// --- para los clientes de API ---

async function tokenVigente(): Promise<string | null> {
  if (!sesion) return null;
  if (sesion.expira - Date.now() < MARGEN_MS / 2) await renovarSesion();
  return sesion?.access_token ?? null;
}

/** Cabecera Authorization de la sesión actual ({} si no hay sesión o no está configurada). */
export function authHeaders(): Record<string, string> {
  return sesion ? { Authorization: `Bearer ${sesion.access_token}` } : {};
}

function conToken(init: RequestInit | undefined, token: string | null): RequestInit {
  const headers = new Headers(init?.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  for (const [k, v] of Object.entries(cabeceraEmpresa())) {
    if (!headers.has(k)) headers.set(k, v);
  }
  return { ...init, headers };
}

/** 403 de una empresa de la que el usuario no es miembro (o que ya no
 * existe): se olvida la elegida y se recarga con la que dé el servidor. */
async function sinEmpresa(respuesta: Response): Promise<boolean> {
  if (respuesta.status !== 403 || empresaSeleccionada() === null) return false;
  try {
    const cuerpo = await respuesta.clone().json();
    if (cuerpo?.codigo !== "sin_empresa" && cuerpo?.codigo !== "empresa_inexistente") return false;
  } catch {
    return false;
  }
  seleccionarEmpresa(null);
  if (typeof window !== "undefined") window.location.assign("/");
  return true;
}

/** `fetch` con la sesión: Bearer, renovación previa y un reintento tras 401;
 * y la empresa de la pestaña (X-Kontara-Empresa). */
export async function fetchConSesion(input: string, init?: RequestInit): Promise<Response> {
  if (!AUTH_CONFIGURADA) {
    const respuesta = await fetch(input, conToken(init, null));
    await sinEmpresa(respuesta);
    return respuesta;
  }
  const token = await tokenVigente();
  const respuesta = await fetch(input, conToken(init, token));
  if (respuesta.status !== 401 || !sesion) {
    await sinEmpresa(respuesta);
    return respuesta;
  }
  const renovada = await renovarSesion();
  if (!renovada) {
    guardar(null);
    return respuesta;
  }
  const segunda = await fetch(input, conToken(init, renovada.access_token));
  if (segunda.status === 401) guardar(null);
  else await sinEmpresa(segunda);
  return segunda;
}
