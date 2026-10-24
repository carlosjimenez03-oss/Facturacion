import { useEffect, useState, type ReactNode } from "react";
import { useLocation } from "react-router-dom";
import { AUTH_CONFIGURADA } from "../lib/sesion";
import { NominaSidebar } from "../pages/nomina/NominaShell";
import { esRutaNomina, NominaSidebarGlobal } from "../pages/nomina/nominaUtil";
import "../pages/plataforma/plataforma.css";
import { AvisoProteccion } from "./AvisoProteccion";
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

/** Estructura general: menú lateral fijo + barra superior + contenido. Nómina
 * (/nomina*) tiene su propio menú lateral en el lugar del principal. */
export function AppShell({ children }: { children: ReactNode }) {
  const { pathname } = useLocation();
  const nomina = esRutaNomina(pathname);
  // En el teléfono las pestañas se desplazan de lado: deja visible la activa.
  useEffect(() => {
    const t = window.setTimeout(() => {
      document.querySelectorAll<HTMLElement>(".tabs .on, .cfg-nav .on").forEach((el) => {
        const tira = el.parentElement;
        if (tira && tira.scrollWidth > tira.clientWidth) {
          tira.scrollLeft = el.offsetLeft - (tira.clientWidth - el.offsetWidth) / 2;
        }
      });
    }, 150);
    return () => window.clearTimeout(t);
  }, [pathname]);
  return (
    <div className="shell">
      {nomina ? <NominaSidebar /> : <Sidebar />}
      <div className="main">
        <AvisoSinInicioSesion />
        <AvisoProteccion />
        <Topbar />
        <main className="page">
          <NominaSidebarGlobal.Provider value={nomina}>{children}</NominaSidebarGlobal.Provider>
        </main>
      </div>
    </div>
  );
}
