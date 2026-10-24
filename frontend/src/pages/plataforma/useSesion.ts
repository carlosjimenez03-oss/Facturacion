import { useSyncExternalStore } from "react";
import { sesionActual, suscribirSesion, type Sesion } from "../../lib/sesion";

/** Sesión actual de Supabase Auth (se actualiza al entrar, renovar o salir, también desde otra pestaña). */
export function useSesion(): Sesion | null {
  return useSyncExternalStore(suscribirSesion, sesionActual, sesionActual);
}
