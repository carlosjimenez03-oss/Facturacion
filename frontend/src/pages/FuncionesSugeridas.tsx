import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { claveEmpresa } from "../lib/empresaActual";
import { inventarioApi } from "../lib/inventarioApi";
import { useFacturas } from "../lib/useFacturas";

interface Paso {
  id: string;
  label: string;
  titulo: string;
  descripcion: string;
  cta: string;
  to: string;
  /** null = no se puede saber con la API: el usuario la marca a mano. */
  hecho: boolean | null;
}

// Por empresa (multiempresa): cada empresa nueva tiene sus primeros pasos.
const CLAVE_MANUAL = claveEmpresa("kontara.pasosCompletados");
const CLAVE_OCULTO = claveEmpresa("kontara.funcionesSugeridasOcultas");

function leer<T>(clave: string, defecto: T): T {
  try {
    const v = localStorage.getItem(clave);
    return v ? (JSON.parse(v) as T) : defecto;
  } catch {
    return defecto;
  }
}

function guardar(clave: string, valor: unknown) {
  try {
    localStorage.setItem(clave, JSON.stringify(valor));
  } catch {
    // sin almacenamiento disponible: el estado dura solo esta sesión
  }
}

/** Lista guiada de primeros pasos. Se marcan solos según los datos reales de las APIs. */
export function FuncionesSugeridas() {
  const { facturas } = useFacturas();
  const [productos, setProductos] = useState<number | null>(null);
  const [proveedores, setProveedores] = useState<number | null>(null);
  const [manual, setManual] = useState<Record<string, boolean>>(() => leer(CLAVE_MANUAL, {}));
  const [oculto, setOculto] = useState<boolean>(() => leer(CLAVE_OCULTO, false));
  const [seleccion, setSeleccion] = useState<string | null>(null);

  useEffect(() => {
    inventarioApi.listarProductos().then((p) => setProductos(p.length)).catch(() => setProductos(null));
    inventarioApi.listarProveedores().then((p) => setProveedores(p.length)).catch(() => setProveedores(null));
  }, []);

  const pasos: Paso[] = [
    {
      id: "fe",
      label: "Facturación electrónica",
      titulo: "Activa tu facturación electrónica",
      descripcion: "Envía tu primer e-CF a la DGII para confirmar que el certificado y el ambiente están bien configurados.",
      cta: "Emitir y enviar",
      to: "/facturas/nueva",
      hecho: facturas.some((f) => !!f.track_id),
    },
    {
      id: "factura",
      label: "Crea tu primera factura",
      titulo: "Crea tu primera factura",
      descripcion: "Emite un e-CF de crédito fiscal o de consumo con tus productos del catálogo.",
      cta: "Crear factura",
      to: "/facturas/nueva",
      hecho: facturas.length > 0,
    },
    {
      id: "inventario",
      label: "Organiza tu inventario",
      titulo: "Organiza tu inventario",
      descripcion: "Registra tus productos con su precio, costo y existencia para facturar y llevar el kardex.",
      cta: "Agregar productos",
      to: "/inventario/productos/nuevo",
      hecho: productos === null ? null : productos > 0,
    },
    {
      id: "contactos",
      label: "Añade tus contactos",
      titulo: "Añade tus contactos",
      descripcion: "Registra tus proveedores con su RNC o cédula para tus compras y el Formato 606.",
      cta: "Agregar contactos",
      to: "/inventario/proveedores/nuevo",
      hecho: proveedores === null ? null : proveedores > 0,
    },
    {
      id: "usuarios",
      label: "Administra usuarios y roles",
      titulo: "Administra usuarios y roles",
      descripcion: "Invita a tu contador y a tu equipo con permisos por módulo: facturación, compras e impuestos.",
      cta: "Ir a configuración",
      to: "/modulo/configuracion",
      hecho: null,
    },
  ].map((p) => ({ ...p, hecho: p.hecho === true || !!manual[p.id] }));

  const completados = pasos.filter((p) => p.hecho).length;
  const actual = pasos.find((p) => p.id === seleccion) ?? pasos.find((p) => !p.hecho) ?? pasos[0];

  if (oculto) {
    return (
      <button
        type="button"
        className="btn btn-o btn-sm"
        style={{ alignSelf: "flex-start" }}
        onClick={() => {
          setOculto(false);
          guardar(CLAVE_OCULTO, false);
        }}
      >
        Mostrar funciones sugeridas ({completados} de {pasos.length})
      </button>
    );
  }

  return (
    <div className="stack-sm">
      <div>
        <h2 className="sec-title">Funciones sugeridas</h2>
        <p className="mut sec-sub">Basados en tus necesidades, hemos preparado estas funciones ideales para ti.</p>
      </div>
      <section className="card onb">
        <div className="onb-steps">
          {pasos.map((p) => (
            <button
              key={p.id}
              type="button"
              className={`onb-step${p.id === actual.id ? " on" : ""}${p.hecho ? " done" : ""}`}
              aria-pressed={p.id === actual.id}
              onClick={() => setSeleccion(p.id)}
            >
              {p.hecho ? (
                <svg viewBox="0 0 24 24" fill="none" stroke="#15803D" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <circle cx="12" cy="12" r="9" />
                  <path d="m8.5 12 2.5 2.5 4.5-5" />
                </svg>
              ) : (
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeDasharray="3 3" aria-hidden="true">
                  <circle cx="12" cy="12" r="9" />
                </svg>
              )}
              <span>{p.label}</span>
            </button>
          ))}
          <span className="hint onb-count">
            <b>{completados}</b> de {pasos.length} funcionalidades completadas
          </span>
        </div>
        <div className="onb-panel">
          <button
            type="button"
            className="onb-hide"
            onClick={() => {
              setOculto(true);
              guardar(CLAVE_OCULTO, true);
            }}
          >
            Ocultar
          </button>
          <svg className="onb-ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.6} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z" />
            <path d="M14 3v6h6M12 11v7M10 13h3a1 1 0 0 1 0 2h-2a1 1 0 0 0 0 2h3" />
          </svg>
          <h3>{actual.titulo}</h3>
          <p>{actual.descripcion}</p>
          <div className="row">
            <Link to={actual.to} className="btn onb-cta">
              {actual.cta} ↗
            </Link>
            {!actual.hecho && (
              <button
                type="button"
                className="btn btn-g"
                onClick={() => {
                  const m = { ...manual, [actual.id]: true };
                  setManual(m);
                  guardar(CLAVE_MANUAL, m);
                }}
              >
                Marcar como completada
              </button>
            )}
          </div>
        </div>
      </section>
    </div>
  );
}
