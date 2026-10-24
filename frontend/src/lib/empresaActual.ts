// La empresa con que trabaja el usuario (multiempresa, docs/MULTIEMPRESA.md).
//
// Se recuerda en el navegador POR USUARIO (localStorage `kontara.empresa.<id>`)
// y viaja en cada request a las APIs en la cabecera `X-Kontara-Empresa`
// (fetchConSesion, sesion.ts). nginx no la reenvía tal cual: le pregunta a
// erp-api (/auth/verificar) si el usuario es miembro y reenvía la verificada.
// Los enlaces que el navegador abre sin fetch (PDF, XML) la llevan en la
// cookie `kontara_empresa`. Sin empresa elegida no se manda nada y el
// servidor usa la última que eligió el usuario (o la predeterminada).
//
// La de cada pestaña se fija al cargarla (`enUso`): si otra pestaña cambia
// de empresa, esta sigue mandando la suya hasta recargar -- nunca escribe en
// una empresa que no es la que muestra. Cambiar de empresa recarga la página.
//
// Lo que se guarda en el navegador por empresa (cola y catálogo del POS sin
// conexión, emisor y vencimientos de secuencias recordados, avisos leídos)
// usa `claveEmpresa`: la empresa 1 conserva las claves de siempre, así una
// instalación de una sola empresa no pierde nada.

export const CABECERA_EMPRESA = "X-Kontara-Empresa";
export const EMPRESA_PREDETERMINADA = 1;
const PREFIJO = "kontara.empresa.";
const COOKIE = "kontara_empresa";

let usuario = "sin-sesion";
let enUso: number | null = leerGuardada();
const oyentes = new Set<() => void>();

function leerGuardada(): number | null {
  try {
    const valor = Number(localStorage.getItem(PREFIJO + usuario));
    return Number.isInteger(valor) && valor >= 1 ? valor : null;
  } catch {
    return null;
  }
}

function escribirCookie(id: number | null) {
  if (typeof document === "undefined") return;
  const segura = typeof location !== "undefined" && location.protocol === "https:" ? "; Secure" : "";
  document.cookie = id
    ? `${COOKIE}=${id}; Path=/; SameSite=Strict${segura}`
    : `${COOKIE}=; Path=/; Max-Age=0; SameSite=Strict${segura}`;
}

/** sesion.ts lo llama al cargar y al iniciar o cerrar sesión: la elección es de cada usuario. */
export function fijarUsuarioEmpresa(id: string | null) {
  const nuevo = id || "sin-sesion";
  if (nuevo === usuario) return;
  usuario = nuevo;
  enUso = leerGuardada();
  escribirCookie(enUso);
  oyentes.forEach((o) => o());
}

/** La de esta pestaña (null = ninguna elegida todavía: la decide el servidor). */
export function empresaSeleccionada(): number | null {
  return enUso;
}

/** La que se está usando: la elegida o, sin elección, la predeterminada. */
export function empresaEnUso(): number {
  return enUso ?? EMPRESA_PREDETERMINADA;
}

/** Recuerda la empresa (para este usuario en este navegador). Quien cambia de
 * empresa recarga la página después (ver empresasApi.cambiarEmpresa). */
export function seleccionarEmpresa(id: number | null) {
  try {
    if (id) localStorage.setItem(PREFIJO + usuario, String(id));
    else localStorage.removeItem(PREFIJO + usuario);
  } catch {
    // almacenamiento bloqueado: la elección dura lo que la pestaña
  }
  enUso = id;
  escribirCookie(id);
  oyentes.forEach((o) => o());
}

export function suscribirEmpresa(oyente: () => void): () => void {
  oyentes.add(oyente);
  return () => oyentes.delete(oyente);
}

/** Cabecera para las APIs ({} sin empresa elegida). */
export function cabeceraEmpresa(): Record<string, string> {
  return enUso ? { [CABECERA_EMPRESA]: String(enUso) } : {};
}

/** Clave de almacenamiento local de la empresa en uso (la 1: la de siempre). */
export function claveEmpresa(base: string, empresa: number = empresaEnUso()): string {
  return empresa === EMPRESA_PREDETERMINADA ? base : `${base}.e${empresa}`;
}

// La cookie de las descargas es de todo el navegador: al volver a esta
// pestaña se pone otra vez la suya.
if (typeof window !== "undefined") {
  escribirCookie(enUso);
  window.addEventListener("focus", () => escribirCookie(enUso));
}
