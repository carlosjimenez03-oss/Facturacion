// Reportes (Reportes.dc.html del prototipo): catálogo de informes comerciales, fiscales y de inventario; el
// elegido se muestra debajo con sus filtros, la tabla con totales, exportación a CSV (Excel) e impresión (PDF).
// Todo se agrega en erp-api; el período y los filtros viven en la URL (/reportes/:reporte?desde=…&hasta=…).
import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { inventarioApi } from "../../lib/inventarioApi";
import { formatoMonto, formatoRnc } from "../../lib/marca";
import { useDatosEmpresa } from "../../lib/configuracionApi";
import { AvisoError, Cargando, Vacio } from "../ingresos/IngresosShell";
import { useAviso } from "../ingresos/useAviso";
import { descargarCsv, fechaLegible, hoyIso, mensajeError } from "../ingresos/ingresosUtil";
import { REPORTES, REPORTE_POR_ID } from "./reportesDef";
import type { Celda, Columna, DefReporte, ResultadoReporte } from "./reportesDef";
import "../ingresos/ingresos.css";
import "./reportes.css";

type Preset = "mes" | "mes_anterior" | "trimestre" | "anio" | "anio_anterior" | "personalizado";

const PRESETS: { value: Preset; label: string }[] = [
  { value: "mes", label: "Este mes" },
  { value: "mes_anterior", label: "Mes anterior" },
  { value: "trimestre", label: "Este trimestre" },
  { value: "anio", label: "Este año" },
  { value: "anio_anterior", label: "Año anterior" },
  { value: "personalizado", label: "Personalizado" },
];

const iso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

function rangoPreset(preset: Preset, hoy = new Date(`${hoyIso()}T12:00:00`)): { desde: string; hasta: string } {
  const a = hoy.getFullYear();
  const m = hoy.getMonth();
  switch (preset) {
    case "mes_anterior":
      return { desde: iso(new Date(a, m - 1, 1)), hasta: iso(new Date(a, m, 0)) };
    case "trimestre": {
      const t = Math.floor(m / 3) * 3;
      return { desde: iso(new Date(a, t, 1)), hasta: iso(new Date(a, t + 3, 0)) };
    }
    case "anio":
      return { desde: `${a}-01-01`, hasta: `${a}-12-31` };
    case "anio_anterior":
      return { desde: `${a - 1}-01-01`, hasta: `${a - 1}-12-31` };
    default:
      return { desde: iso(new Date(a, m, 1)), hasta: iso(new Date(a, m + 1, 0)) };
  }
}

function presetDe(desde: string, hasta: string): Preset {
  return PRESETS.find((p) => p.value !== "personalizado" && rangoPreset(p.value).desde === desde && rangoPreset(p.value).hasta === hasta)?.value ?? "personalizado";
}

const FAVORITOS = "kontara.reportes.favoritos";

function leerFavoritos(): string[] {
  try {
    const v = JSON.parse(window.localStorage.getItem(FAVORITOS) ?? "[]");
    return Array.isArray(v) ? v.filter((x) => typeof x === "string") : [];
  } catch {
    return [];
  }
}

function guardarFavoritos(lista: string[]) {
  try {
    window.localStorage.setItem(FAVORITOS, JSON.stringify(lista));
  } catch {
    // sin almacenamiento (modo privado): los favoritos duran lo que la pantalla
  }
}

/** Celda para la pantalla e impresión: montos con separadores, negativos entre paréntesis. */
function mostrar(v: Celda, col: Columna): string {
  if (v === null || v === "") return col.formato && col.formato !== "texto" && col.formato !== "mono" ? "" : "—";
  if (typeof v === "string") return v;
  switch (col.formato) {
    case "monto":
      return v < 0 ? `(${formatoMonto(-v)})` : formatoMonto(v);
    case "cantidad":
      return v.toLocaleString("en-US", { maximumFractionDigits: 2 });
    case "pct":
      return `${v.toLocaleString("en-US", { maximumFractionDigits: 2 })} %`;
    case "veces":
      return `${v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}×`;
    default:
      return String(v);
  }
}

/** Celda para el CSV: números sin separadores (Excel los suma). */
function csv(v: Celda, col: Columna): string | number {
  if (v === null) return "";
  if (typeof v === "number") return col.formato === "monto" ? v.toFixed(2) : v;
  return v;
}

const esNumero = (c: Columna) => !!c.formato && !["texto", "mono"].includes(c.formato);

export function ReportesPage() {
  const miEmpresa = useDatosEmpresa();
  const { reporte: idParam } = useParams();
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const def: DefReporte = REPORTE_POR_ID[idParam ?? ""] ?? REPORTES[0];
  const porDefecto = rangoPreset("mes");
  const desde = params.get("desde") || porDefecto.desde;
  const hasta = params.get("hasta") || porDefecto.hasta;
  const [preset, setPreset] = useState<Preset>(() => presetDe(desde, hasta));
  const filtros = useMemo(() => Object.fromEntries(def.filtros.map((f) => [f.clave, params.get(f.clave) ?? ""])), [def, params]);
  const [resultado, setResultado] = useState<ResultadoReporte | null>(null);
  const [cargando, setCargando] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [favoritos, setFavoritos] = useState<string[]>(leerFavoritos);
  const [opciones, setOpciones] = useState<{ almacenes: { value: string; label: string }[]; categorias: { value: string; label: string }[] }>({ almacenes: [], categorias: [] });
  const [borrador, setBorrador] = useState<Record<string, string>>(filtros);
  const [toast, mostrarToast] = useAviso();

  useEffect(() => setBorrador(filtros), [filtros]);

  const cargar = useCallback(() => {
    setCargando(true);
    setError(null);
    def
      .cargar({ desde, hasta }, filtros)
      .then(setResultado)
      .catch((err) => {
        setResultado(null);
        setError(mensajeError(err));
      })
      .finally(() => setCargando(false));
  }, [def, desde, hasta, filtros]);

  useEffect(() => {
    cargar();
  }, [cargar]);

  // Opciones de los filtros de inventario (best-effort: si inventario-api no responde, el filtro queda vacío).
  const dinamicos = def.filtros.map((f) => f.dinamico).filter(Boolean).join(",");
  useEffect(() => {
    if (!dinamicos) return;
    let vigente = true;
    void Promise.allSettled([inventarioApi.listarAlmacenes(), inventarioApi.listarCategorias()]).then(([a, c]) => {
      if (!vigente) return;
      setOpciones({
        almacenes: a.status === "fulfilled" ? a.value.map((x) => ({ value: x.id, label: x.nombre })) : [],
        categorias: c.status === "fulfilled" ? c.value.map((x) => ({ value: x, label: x })) : [],
      });
    });
    return () => {
      vigente = false;
    };
  }, [dinamicos]);

  function cambiarParams(cambios: Record<string, string>) {
    const nuevos = new URLSearchParams(params);
    for (const [k, v] of Object.entries(cambios)) {
      if (v) nuevos.set(k, v);
      else nuevos.delete(k);
    }
    setParams(nuevos, { replace: true });
  }

  function elegirPreset(p: Preset) {
    setPreset(p);
    if (p !== "personalizado") cambiarParams(rangoPreset(p));
  }

  function elegirReporte(id: string) {
    const conservar = new URLSearchParams();
    if (params.get("desde")) conservar.set("desde", desde);
    if (params.get("hasta")) conservar.set("hasta", hasta);
    navigate(`/reportes/${id}${conservar.toString() ? `?${conservar}` : ""}`);
  }

  function alternarFavorito() {
    const lista = favoritos.includes(def.id) ? favoritos.filter((x) => x !== def.id) : [...favoritos, def.id];
    setFavoritos(lista);
    guardarFavoritos(lista);
    mostrarToast(lista.includes(def.id) ? "Agregado a favoritos" : "Quitado de favoritos", { detalle: def.t });
  }

  const periodoTexto = def.usaPeriodo ? `${fechaLegible(desde)} – ${fechaLegible(hasta)}` : "";
  const subtitulo = resultado?.subtitulo ?? periodoTexto;

  function exportar() {
    if (!resultado) return;
    const filas: (string | number)[][] = [[def.t], [subtitulo]];
    for (const tabla of resultado.tablas) {
      filas.push([]);
      if (tabla.titulo) filas.push([tabla.titulo]);
      filas.push(tabla.columnas.map((c) => c.t));
      for (const f of tabla.filas) filas.push(f.map((v, i) => csv(v, tabla.columnas[i])));
      if (tabla.total) filas.push(tabla.total.map((v, i) => csv(v, tabla.columnas[i])));
    }
    const nombre = def.t.normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/[^\w]+/g, "_");
    descargarCsv(`${nombre}_${def.usaPeriodo ? `${desde}_${hasta}` : hoyIso()}.csv`, filas);
    mostrarToast("Archivo exportado", { detalle: `${nombre}.csv · ábralo con Excel` });
  }

  const ordenados = [...REPORTES].sort((a, b) => Number(favoritos.includes(b.id)) - Number(favoritos.includes(a.id)));
  const vacio = resultado && resultado.tablas.every((t) => t.filas.length === 0);
  const filtrosCambiados = def.filtros.some((f) => f.tipo !== "select" && (borrador[f.clave] ?? "") !== (filtros[f.clave] ?? ""));

  return (
    <div className="content">
      <div className="ph no-imprimir">
        <div>
          <h1>Reportes</h1>
          <p>Informes comerciales, fiscales y de inventario de tu empresa</p>
        </div>
        <div className="row rp-periodo">
          <select className="inp" aria-label="Período" value={preset} onChange={(e) => elegirPreset(e.target.value as Preset)} disabled={!def.usaPeriodo}>
            {PRESETS.map((p) => (
              <option key={p.value} value={p.value}>
                {p.label}
              </option>
            ))}
          </select>
          {preset === "personalizado" && def.usaPeriodo && (
            <>
              <input className="inp" type="date" aria-label="Desde" value={desde} max={hasta} onChange={(e) => e.target.value && cambiarParams({ desde: e.target.value })} />
              <input className="inp" type="date" aria-label="Hasta" value={hasta} min={desde} onChange={(e) => e.target.value && cambiarParams({ hasta: e.target.value })} />
            </>
          )}
        </div>
      </div>

      <div className="rp-grid no-imprimir">
        {ordenados.map((r) => {
          const on = r.id === def.id;
          return (
            <button key={r.id} type="button" className={on ? "rp-card on" : "rp-card"} aria-pressed={on} onClick={() => elegirReporte(r.id)}>
              <span className="chip">{r.cat}</span>
              <b>
                {favoritos.includes(r.id) ? "★ " : ""}
                {r.t}
              </b>
              <span className="rp-d">{r.d}</span>
            </button>
          );
        })}
      </div>

      <section className="card rp-reporte">
        <div className="rp-imp">
          <h1>{def.t}</h1>
          <p>
            {miEmpresa.razonSocial}
            {miEmpresa.rnc ? ` · RNC ${formatoRnc(miEmpresa.rnc)}` : ""} · {subtitulo} · Generado el {new Date().toLocaleString("es-DO")}
          </p>
        </div>
        <div className="card-h no-imprimir">
          <div>
            <h2>{def.t}</h2>
            <span className="hint">
              {def.d}
              {subtitulo ? ` · ${subtitulo}` : ""}
            </span>
          </div>
          <div className="row">
            <button type="button" className="btn btn-o btn-sm" onClick={exportar} disabled={!resultado || !!vacio}>
              Exportar Excel
            </button>
            <button type="button" className="btn btn-o btn-sm" onClick={() => window.print()} disabled={!resultado}>
              PDF
            </button>
            <button type="button" className="btn btn-p btn-sm" onClick={alternarFavorito}>
              {favoritos.includes(def.id) ? "Quitar de favoritos" : "Agregar a favoritos"}
            </button>
          </div>
        </div>

        {def.filtros.length > 0 && (
          <form
            className="rp-filtros no-imprimir"
            onSubmit={(e) => {
              e.preventDefault();
              cambiarParams(borrador);
            }}
          >
            {def.filtros.map((f) => {
              const lista = f.dinamico ? [{ value: "", label: f.dinamico === "almacenes" ? "Todos los almacenes" : "Todas las categorías" }, ...opciones[f.dinamico]] : f.opciones;
              return (
                <div key={f.clave} className="fld">
                  <label className="lbl" htmlFor={`rf-${f.clave}`}>
                    {f.label}
                  </label>
                  {f.tipo === "select" ? (
                    <select id={`rf-${f.clave}`} className="inp inp-sm" value={borrador[f.clave] ?? ""} onChange={(e) => cambiarParams({ [f.clave]: e.target.value })}>
                      {(lista ?? []).map((o) => (
                        <option key={o.value} value={o.value}>
                          {o.label}
                        </option>
                      ))}
                    </select>
                  ) : (
                    <input id={`rf-${f.clave}`} className="inp inp-sm" placeholder={f.placeholder} value={borrador[f.clave] ?? ""} onChange={(e) => setBorrador((b) => ({ ...b, [f.clave]: e.target.value }))} />
                  )}
                </div>
              );
            })}
            {def.filtros.some((f) => f.tipo !== "select") && (
              <button type="submit" className="btn btn-o btn-sm" disabled={!filtrosCambiados}>
                Aplicar
              </button>
            )}
          </form>
        )}

        {error && (
          <div className="pad">
            <AvisoError titulo="No se pudo generar el reporte." mensaje={error} onReintentar={cargar} />
          </div>
        )}
        {resultado?.advertencias.map((a) => (
          <div key={a} className="al al-w rp-adv" role="status">
            <span>{a}</span>
          </div>
        ))}
        {cargando && <Cargando />}
        {!cargando && vacio && <Vacio titulo="Sin datos en el período" texto={def.usaPeriodo ? "No hay movimientos en las fechas elegidas. Pruebe con otro período." : "No hay saldos ni existencias que mostrar."} />}
        {!cargando &&
          !vacio &&
          resultado?.tablas.map((tabla, ti) => (
            <div key={ti} className="rp-tabla">
              {tabla.titulo && <h3 className="rp-tt">{tabla.titulo}</h3>}
              <div className="tw">
                <table className="tbl" style={{ minWidth: Math.max(640, tabla.columnas.length * 95) }}>
                  <thead>
                    <tr>
                      {tabla.columnas.map((c) => (
                        <th key={c.t} className={esNumero(c) ? "num" : undefined}>
                          {c.t}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {tabla.filas.length === 0 && (
                      <tr>
                        <td colSpan={tabla.columnas.length} className="mut">
                          Sin registros
                        </td>
                      </tr>
                    )}
                    {tabla.filas.map((f, i) => (
                      <tr key={i}>
                        {f.map((v, j) => (
                          <td key={j} className={[esNumero(tabla.columnas[j]) ? "num" : "", tabla.columnas[j].formato === "mono" ? "mono" : "", j === 0 ? "rp-c0" : ""].join(" ").trim() || undefined}>
                            {mostrar(v, tabla.columnas[j])}
                          </td>
                        ))}
                      </tr>
                    ))}
                    {tabla.total && tabla.filas.length > 0 && (
                      <tr className="rp-total">
                        {tabla.total.map((v, j) => (
                          <td key={j} className={esNumero(tabla.columnas[j]) ? "num" : undefined}>
                            {mostrar(v, tabla.columnas[j]) === "—" ? "" : mostrar(v, tabla.columnas[j])}
                          </td>
                        ))}
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          ))}
      </section>
      {toast}
    </div>
  );
}
