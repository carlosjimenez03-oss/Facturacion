// Encabezado común de las pantallas de Compras (grupo "Compras" de Modulo.dc.html del prototipo):
// migas, título, acciones y pestañas del grupo. El resto de piezas (buscador, vacíos, modales…)
// se comparten con Ingresos.
import type { ReactNode } from "react";
import { NavLink } from "react-router-dom";
import "../ingresos/ingresos.css";
import "./compras.css";

const PESTANAS = [
  { to: "/compras/facturas", label: "Facturas de compra" },
  { to: "/compras/e41", label: "Comprobantes de compras E41" },
  { to: "/compras/e43", label: "Gastos menores E43" },
  { to: "/compras/pagos", label: "Pagos a proveedores" },
  { to: "/compras/retenciones", label: "Retenciones" },
];

export function ComprasEncabezado({ titulo, sub, acciones }: { titulo: string; sub: string; acciones?: ReactNode }) {
  return (
    <>
      <div className="ph no-imprimir">
        <div>
          <div className="crumb">
            <span>Compras</span>
            <span>/</span>
            <span>{titulo}</span>
          </div>
          <h1>{titulo}</h1>
          <p>{sub}</p>
        </div>
        {acciones && <div className="row">{acciones}</div>}
      </div>
      <nav className="tabs no-imprimir" aria-label="Compras">
        {PESTANAS.map((p) => (
          <NavLink key={p.to} to={p.to} end={p.to !== "/compras/facturas"} className={({ isActive }) => (isActive ? "tab on" : "tab")}>
            {p.label}
          </NavLink>
        ))}
      </nav>
    </>
  );
}

export function IconoActualizar() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M20.5 13a8.5 8.5 0 0 1-15 4.5L3 15.5M3.5 11a8.5 8.5 0 0 1 15-4.5L21 8.5" />
      <path d="M21 3.5v5h-5M3 20.5v-5h5" />
    </svg>
  );
}

export function IconoOk({ size }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.2} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M20 6 9 17l-5-5" />
    </svg>
  );
}

export function IconoInfo() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" aria-hidden="true">
      <circle cx="12" cy="12" r="9" />
      <path d="M12 11v5M12 8h.01" />
    </svg>
  );
}

export function IconoDocumento() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z" />
      <path d="M14 3v6h6" />
    </svg>
  );
}

export function IconoEscudo() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M12 3 4 6v6c0 5 3.5 8 8 9 4.5-1 8-4 8-9V6z" />
    </svg>
  );
}

export function IconoCalendario() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect x="3" y="4" width="18" height="17" rx="2" />
      <path d="M16 2v4M8 2v4M3 10h18" />
    </svg>
  );
}

/** Botón ⓘ con ayuda emergente, como en el prototipo. */
export function Ayuda({ texto, etiqueta, derecha }: { texto: string; etiqueta: string; derecha?: boolean }) {
  return (
    <span className={derecha ? "tip r" : "tip"} data-tip={texto}>
      <button type="button" className="info" aria-label={etiqueta}>
        <IconoInfo />
      </button>
    </span>
  );
}
