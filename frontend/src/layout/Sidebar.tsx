import { useEffect, useState } from "react";
import { Link, NavLink, useLocation } from "react-router-dom";
import { MARCA } from "../lib/marca";
import { IconChevron, IconMenu, IconPanel, Logo } from "./icons";
import { MENU_PRINCIPAL, MENU_SOLUCIONES, type MenuItem } from "./menu";

/** ¿La ruta actual pertenece a este destino? "/" solo coincide exacto. */
function coincide(pathname: string, to: string): boolean {
  if (to === "/") return pathname === "/";
  return pathname === to || pathname.startsWith(`${to}/`);
}

function grupoActivo(pathname: string): string | null {
  for (const item of MENU_PRINCIPAL) {
    if (item !== "separador" && item.hijos?.some((h) => coincide(pathname, h.to))) return item.id;
  }
  return null;
}

export function Sidebar() {
  const { pathname } = useLocation();
  const [contraido, setContraido] = useState(false);
  const [movilAbierto, setMovilAbierto] = useState(false);
  const [abiertos, setAbiertos] = useState<Record<string, boolean>>(() => {
    const g = grupoActivo(pathname);
    return g ? { [g]: true } : {};
  });

  // Al navegar: cerrar el cajón móvil y desplegar el grupo de la ruta actual.
  useEffect(() => {
    setMovilAbierto(false);
    const g = grupoActivo(pathname);
    if (g) setAbiertos((a) => (a[g] ? a : { ...a, [g]: true }));
  }, [pathname]);

  const clases = ["sb", contraido ? "collapsed" : "", movilAbierto ? "open" : ""].filter(Boolean).join(" ");

  const renderItem = (item: MenuItem) => {
    if (item.hijos) {
      const abierto = !!abiertos[item.id];
      const contieneActivo = item.hijos.some((h) => coincide(pathname, h.to));
      return (
        <div key={item.id}>
          <button
            type="button"
            className={`nv${contieneActivo ? " has" : ""}`}
            aria-expanded={abierto}
            title={item.label}
            onClick={() => setAbiertos((a) => ({ ...a, [item.id]: !a[item.id] }))}
          >
            {item.icon}
            <span className="sb-lbl">{item.label}</span>
            <IconChevron className={`chev${abierto ? " up" : ""}`} />
          </button>
          <div className={abierto ? "sub" : "sub sub-closed"}>
            {item.hijos.map((h) => (
              <Link key={h.to} to={h.to} className={coincide(pathname, h.to) ? "sv on" : "sv"}>
                {h.label}
              </Link>
            ))}
          </div>
        </div>
      );
    }
    return (
      <NavLink
        key={item.id}
        to={item.to ?? "/"}
        end={item.to === "/"}
        title={item.label}
        className={({ isActive }) => (isActive ? "nv on" : "nv")}
      >
        {item.icon}
        <span className="sb-lbl">{item.label}</span>
      </NavLink>
    );
  };

  return (
    <div className="sb-slot">
      <aside className={clases} aria-label="Menú principal">
        <div className="sb-head">
          <Link to="/" className="sb-logo" aria-label={`${MARCA} · Inicio`}>
            <Logo />
            <span className="sb-word">{MARCA}</span>
          </Link>
          <button type="button" className="ib sb-col" onClick={() => setContraido((c) => !c)} aria-label="Contraer o expandir menú">
            <IconPanel />
          </button>
          <button
            type="button"
            className="ib sb-burger"
            onClick={() => setMovilAbierto((m) => !m)}
            aria-label="Abrir menú"
            aria-expanded={movilAbierto}
          >
            <IconMenu />
          </button>
        </div>
        <button type="button" className="sb-scrim" onClick={() => setMovilAbierto(false)} aria-label="Cerrar menú" />
        <nav className="sb-nav">
          {MENU_PRINCIPAL.map((item, i) => (item === "separador" ? <div key={`sep-${i}`} className="sb-sep" /> : renderItem(item)))}
          <div className="sb-cap">Descubre más soluciones</div>
          {MENU_SOLUCIONES.map(renderItem)}
        </nav>
      </aside>
    </div>
  );
}
