// Piezas comunes de las pantallas de Ingresos, según Modulo.dc.html del prototipo:
// encabezado con migas y pestañas del grupo, buscador, estados vacíos, avisos y modales.
import { useEffect } from "react";
import type { ReactNode } from "react";
import { NavLink } from "react-router-dom";
import { IconAlerta } from "../../layout/icons";
import "./ingresos.css";

const PESTANAS = [
  { to: "/notas-credito", label: "Notas de crédito" },
  { to: "/notas-debito", label: "Notas de débito" },
  { to: "/cotizaciones", label: "Cotizaciones" },
  { to: "/cobros", label: "Cobros" },
];

export function IngresosEncabezado({ titulo, sub, acciones }: { titulo: string; sub: string; acciones?: ReactNode }) {
  return (
    <>
      <div className="ph">
        <div>
          <div className="crumb">
            <span>Ingresos</span>
            <span>/</span>
            <span>{titulo}</span>
          </div>
          <h1>{titulo}</h1>
          <p>{sub}</p>
        </div>
        {acciones && <div className="row">{acciones}</div>}
      </div>
      <nav className="tabs" aria-label="Ingresos">
        {PESTANAS.map((p) => (
          <NavLink key={p.to} to={p.to} className={({ isActive }) => (isActive ? "tab on" : "tab")}>
            {p.label}
          </NavLink>
        ))}
      </nav>
    </>
  );
}

export function IconoBuscar() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" aria-hidden="true">
      <circle cx="11" cy="11" r="7" />
      <path d="m20 20-3.5-3.5" />
    </svg>
  );
}

export function Buscador({ valor, onCambiar, placeholder }: { valor: string; onCambiar: (v: string) => void; placeholder: string }) {
  return (
    <label className="tb-search">
      <IconoBuscar />
      <input className="inp" type="search" placeholder={placeholder} value={valor} onChange={(e) => onCambiar(e.target.value)} aria-label={placeholder} />
    </label>
  );
}

export function SinResultados({ q, onLimpiar }: { q: string; onLimpiar: () => void }) {
  return (
    <div className="empty">
      <span className="ei">
        <IconoBuscar />
      </span>
      <h3>Sin resultados</h3>
      <p>{q ? `No hay registros que coincidan con «${q}».` : "No hay registros con estos filtros."}</p>
      <button type="button" className="btn btn-o btn-sm" onClick={onLimpiar}>
        {q ? "Limpiar búsqueda" : "Ver todos"}
      </button>
    </div>
  );
}

export function Vacio({ titulo, texto, children }: { titulo: string; texto: string; children?: ReactNode }) {
  return (
    <div className="empty">
      <span className="ei">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" />
          <path d="M14 3v5h5M9 13h6M9 17h4" />
        </svg>
      </span>
      <h3>{titulo}</h3>
      <p>{texto}</p>
      {children}
    </div>
  );
}

export function Cargando() {
  return <p className="pad mut">Cargando…</p>;
}

export function AvisoError({ titulo, mensaje, onReintentar }: { titulo?: string; mensaje: string; onReintentar?: () => void }) {
  return (
    <div className="al al-d" role="alert">
      <IconAlerta />
      <span style={{ flex: 1 }}>
        {titulo && <b>{titulo} </b>}
        {mensaje}
      </span>
      {onReintentar && (
        <button type="button" className="btn btn-o btn-sm" onClick={onReintentar}>
          Reintentar
        </button>
      )}
    </div>
  );
}

export function Estadistica({ k, v, s }: { k: string; v: string; s?: string }) {
  return (
    <div className="card stat">
      <span className="k">{k}</span>
      <span className="v" style={{ fontSize: 21 }}>
        {v}
      </span>
      {s && <span className="s">{s}</span>}
    </div>
  );
}

export function Modal({
  titulo,
  children,
  pie,
  onCerrar,
  ancho,
}: {
  titulo: string;
  children: ReactNode;
  pie: ReactNode;
  onCerrar: () => void;
  ancho?: number;
}) {
  useEffect(() => {
    const tecla = (e: KeyboardEvent) => e.key === "Escape" && onCerrar();
    window.addEventListener("keydown", tecla);
    return () => window.removeEventListener("keydown", tecla);
  }, [onCerrar]);
  return (
    <div className="scrim" onClick={(e) => e.target === e.currentTarget && onCerrar()}>
      <div className="modal" role="dialog" aria-modal="true" aria-labelledby="modal-t" style={{ maxWidth: ancho }}>
        <div className="modal-h">
          <h3 id="modal-t">{titulo}</h3>
        </div>
        <div className="modal-b">{children}</div>
        <div className="modal-f">{pie}</div>
      </div>
    </div>
  );
}
