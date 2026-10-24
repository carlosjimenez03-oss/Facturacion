import { useState, type ReactNode } from "react";
import { AUTH_CONFIGURADA } from "../lib/sesion";
import "../pages/plataforma/plataforma.css";
import { Sidebar } from "./Sidebar";
import { Topbar } from "./Topbar";

const CLAVE_AVISO = "kontara.aviso-auth-oculto";

function leerOculto(): boolean {
  try {
    return sessionStorage.getItem(CLAVE_AVISO) === "1";
  } catch {
    return false;
  }
}

/** Mientras no se configure Supabase Auth, la aplicación sigue abierta pero lo avisa. */
function AvisoSinInicioSesion() {
  const [oculto, setOculto] = useState(leerOculto);
  if (AUTH_CONFIGURADA || oculto) return null;
  return (
    <div className="aviso-auth" role="status">
      <span>
        <b>Inicio de sesión no configurado.</b> Cualquiera que abra esta dirección puede usar la aplicación. Configure{" "}
        <code>VITE_SUPABASE_URL</code>, <code>VITE_SUPABASE_ANON_KEY</code> y <code>SUPABASE_JWT_SECRET</code> en Dokploy (ver{" "}
        <code>docs/AUTH.md</code>).
      </span>
      <button
        type="button"
        className="btn btn-g btn-sm"
        onClick={() => {
          setOculto(true);
          try {
            sessionStorage.setItem(CLAVE_AVISO, "1");
          } catch {
            // sin almacenamiento: se oculta hasta recargar
          }
        }}
      >
        Ocultar
      </button>
    </div>
  );
}

/** Estructura general: menú lateral fijo + barra superior + contenido. */
export function AppShell({ children }: { children: ReactNode }) {
  return (
    <div className="shell">
      <Sidebar />
      <div className="main">
        <AvisoSinInicioSesion />
        <Topbar />
        <main className="page">{children}</main>
      </div>
    </div>
  );
}
