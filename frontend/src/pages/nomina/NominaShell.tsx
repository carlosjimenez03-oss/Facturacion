// Marco de Kontara Nómina (Nomina.dc.html del prototipo): Nómina tiene su PROPIO menú lateral
// (logo "kontara Nómina", Inicio, Bandeja de entrada, Empleados, Nómina, Pagos, Suscripciones,
// Configuración, "← Volver a Facturación" y la tarjeta de prueba gratuita).
//
// En las rutas /nomina* AppShell dibuja NominaSidebar en el lugar del menú principal y avisa con
// NominaSidebarGlobal (./nominaUtil), así que NominaShell ya no repite el menú como columna interna.
// Fuera de AppShell (sin ese contexto) cada pantalla de Nómina dibuja su menú a la izquierda.
import { useContext, type ReactNode } from "react";
import { Link, NavLink } from "react-router-dom";
import "../ingresos/ingresos.css";
import "./nomina.css";
import { tarjetaPrueba, useSuscripcion } from "../../lib/solucionesApi";
import { NominaSidebarGlobal, useAvisosNomina } from "./nominaUtil";

const MENU = [
  { to: "/nomina", label: "Inicio", end: true },
  { to: "/nomina/bandeja", label: "Bandeja de entrada", bandeja: true },
  { to: "/nomina/empleados", label: "Empleados" },
  { to: "/nomina/periodos", label: "Nómina" },
  { to: "/nomina/pagos", label: "Pagos" },
  { to: "/nomina/suscripciones", label: "Suscripciones" },
  { to: "/nomina/configuracion", label: "Configuración" },
];

export function LogoNomina({ size = 32 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="#14B8A6" aria-hidden="true">
      <circle cx="6" cy="7" r="2.6" />
      <circle cx="12" cy="5.5" r="2.6" />
      <circle cx="18" cy="7" r="2.6" />
      <path d="M1.5 14a4.5 4.5 0 0 1 9 0v1h-9zM7.5 13a4.5 4.5 0 0 1 9 0v1h-9zM13.5 14a4.5 4.5 0 0 1 9 0v1h-9z" />
    </svg>
  );
}

function IconoVolver() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect x="3" y="4" width="18" height="16" rx="2" />
      <path d="M9 4v16" />
    </svg>
  );
}

function MenuNomina({ clase }: { clase: string }) {
  const { noLeidos } = useAvisosNomina();
  const prueba = tarjetaPrueba(useSuscripcion());
  return (
    <aside className={clase} aria-label="Menú de Nómina">
      <div className={clase === "sb" ? "sb-head" : "nm-head"}>
        <Link to="/nomina" className={clase === "sb" ? "sb-logo" : "nm-logo"} aria-label="Nómina · Inicio">
          <LogoNomina />
          <span className="nm-word">
            <small>kontara</small>
            <b>Nómina</b>
          </span>
        </Link>
        <Link to="/" className="ib" aria-label="Volver a Facturación" title="Volver a Facturación">
          <IconoVolver />
        </Link>
      </div>
      <nav className={clase === "sb" ? "sb-nav nm-nav" : "nm-nav"}>
        {MENU.map((m) => (
          <NavLink key={m.to} to={m.to} end={m.end} className={({ isActive }) => (isActive ? "nv on" : "nv")}>
            <span className="sb-lbl">{m.label}</span>
            {m.bandeja && noLeidos > 0 && (
              <span className="chip nm-chip" aria-label={`${noLeidos} sin leer`}>
                {noLeidos}
              </span>
            )}
          </NavLink>
        ))}
        <div className="sb-sep" />
        <Link to="/" className="nv">
          <span className="sb-lbl">← Volver a Facturación</span>
        </Link>
      </nav>
      {prueba && (
        <div className="nm-trial">
          <b>{prueba.titulo}</b>
          <div className="pb">
            <span style={{ width: `${prueba.avance}%` }} />
          </div>
          <Link to="/planes" className="btn btn-sm nm-cta">
            {prueba.boton}
          </Link>
        </div>
      )}
    </aside>
  );
}

/** Menú de Nómina para el lugar del menú principal (ver el comentario del archivo). */
export function NominaSidebar() {
  return (
    <div className="sb-slot">
      <MenuNomina clase="sb" />
    </div>
  );
}

/** Pantalla de Nómina: menú propio (si el layout no lo dibuja) + contenido. */
export function NominaShell({ children }: { children: ReactNode }) {
  const global = useContext(NominaSidebarGlobal);
  if (global) return <div className="content nm-main">{children}</div>;
  return (
    <div className="nm">
      <div className="no-imprimir">
        <MenuNomina clase="nm-sb" />
      </div>
      <div className="content nm-main">{children}</div>
    </div>
  );
}

export function Encabezado({ titulo, sub, acciones }: { titulo: string; sub?: ReactNode; acciones?: ReactNode }) {
  return (
    <div className="ph no-imprimir">
      <div>
        <h1>{titulo}</h1>
        {sub && <p>{sub}</p>}
      </div>
      {acciones && <div className="row">{acciones}</div>}
    </div>
  );
}

export function PestanasNomina() {
  return (
    <nav className="tabs no-imprimir" aria-label="Nómina">
      <NavLink to="/nomina/periodos" className={({ isActive }) => (isActive ? "tab on" : "tab")}>
        Períodos
      </NavLink>
      <NavLink to="/nomina/reportes" className={({ isActive }) => (isActive ? "tab on" : "tab")}>
        Reportes TSS y DGII
      </NavLink>
    </nav>
  );
}
