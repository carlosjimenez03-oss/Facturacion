// Encabezado común de las pantallas de Inventario (grupo "Inventario" de Modulo.dc.html del prototipo):
// migas, título, acciones y pestañas Productos · Lotes · Ajustes · Almacenes. El resto de piezas
// (buscador, vacíos, modales…) se comparten con Ingresos.
import type { ReactNode } from "react";
import { NavLink } from "react-router-dom";
import { IconAlerta } from "../../layout/icons";
import type { FaltanteStock } from "../../lib/inventarioApi";
import { InventarioApiError } from "../../lib/inventarioApi";
import "../ingresos/ingresos.css";
import "./inventario.css";
import { formatoCantidad, mensajeInventario } from "./inventarioUtil";

const PESTANAS = [
  { to: "/inventario/productos", label: "Productos" },
  { to: "/inventario/lotes", label: "Lotes" },
  { to: "/inventario/ajustes", label: "Ajustes" },
  { to: "/inventario/almacenes", label: "Almacenes" },
];

export function InventarioEncabezado({ titulo, sub, acciones, pestanas = true }: { titulo: string; sub: string; acciones?: ReactNode; pestanas?: boolean }) {
  return (
    <>
      <div className="ph no-imprimir">
        <div>
          <div className="crumb">
            <span>Inventario</span>
            <span>/</span>
            <span>{titulo}</span>
          </div>
          <h1>{titulo}</h1>
          <p>{sub}</p>
        </div>
        {acciones && <div className="row">{acciones}</div>}
      </div>
      {pestanas && (
        <nav className="tabs no-imprimir" aria-label="Inventario">
          {PESTANAS.map((p) => (
            <NavLink key={p.to} to={p.to} className={({ isActive }) => (isActive ? "tab on" : "tab")}>
              {p.label}
            </NavLink>
          ))}
        </nav>
      )}
    </>
  );
}

/** Interruptor del prototipo (.sw) con su etiqueta y una ayuda debajo. */
export function Interruptor({
  id,
  etiqueta,
  ayuda,
  valor,
  onCambiar,
  disabled,
}: {
  id: string;
  etiqueta: string;
  ayuda?: ReactNode;
  valor: boolean;
  onCambiar: (v: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <div className="inv-sw">
      <button
        id={id}
        type="button"
        role="switch"
        aria-checked={valor}
        className={valor ? "sw on" : "sw"}
        onClick={() => onCambiar(!valor)}
        disabled={disabled}
      />
      <label htmlFor={id}>
        <b>{etiqueta}</b>
        {ayuda && <span className="hint">{ayuda}</span>}
      </label>
    </div>
  );
}

/**
 * Error de una operación de inventario. Un 409 por falta de existencia (transferencias, ajustes y
 * reversiones) trae `faltante`: se muestra qué producto, en qué almacén/lote, cuánto se pidió y cuánto hay.
 */
export function AvisoOperacion({
  error,
  titulo,
  almacenNombre,
  loteNumero,
  ayuda,
}: {
  error: unknown;
  titulo: string;
  /** Qué hacer ante el faltante; por defecto, ajustar la cantidad o registrar la entrada. */
  ayuda?: string;
  almacenNombre?: (id: string) => string | undefined;
  loteNumero?: (id: string) => string | undefined;
}) {
  const faltante: FaltanteStock | undefined = error instanceof InventarioApiError ? error.faltante : undefined;
  if (!faltante) {
    return (
      <div className="al al-d" role="alert">
        <IconAlerta />
        <span>
          <b>{titulo} </b>
          {mensajeInventario(error)}
        </span>
      </div>
    );
  }
  const almacen = almacenNombre?.(faltante.almacen_id);
  const lote = faltante.lote_id ? (loteNumero?.(faltante.lote_id) ?? "indicado") : null;
  const falta = Number(faltante.solicitado) - Number(faltante.disponible);
  return (
    <div className="al al-d" role="alert">
      <IconAlerta />
      <div className="inv-faltante">
        <b>Existencia insuficiente. {titulo}</b>
        <span>
          <b>{faltante.nombre}</b>
          {lote ? ` · lote ${lote}` : ""}
          {almacen ? ` · almacén ${almacen}` : ""}
        </span>
        <dl>
          <dt>Solicitado</dt>
          <dd>{formatoCantidad(faltante.solicitado)}</dd>
          <dt>Disponible</dt>
          <dd>{formatoCantidad(faltante.disponible)}</dd>
          <dt>Faltan</dt>
          <dd>{formatoCantidad(falta > 0 ? falta : faltante.solicitado)}</dd>
        </dl>
        <span>{ayuda ?? `Ajuste la cantidad, elija otro ${lote ? "lote" : "almacén"} o registre primero la entrada de la mercancía.`}</span>
      </div>
    </div>
  );
}
