// Mis tareas (Tareas.dc.html del prototipo): pendientes fiscales y de gestión. Las automáticas las calcula
// erp-api de los datos de cada módulo (declaraciones, e-CF rechazados, bandeja, cobros, pagos, inventario…)
// y desaparecen solas al resolverse su motivo; las manuales se agregan arriba. Ambas se completan o se posponen.
import { useCallback, useEffect, useMemo, useState } from "react";
import type { FormEvent } from "react";
import { Link } from "react-router-dom";
import { tareasApi } from "../../lib/operacionApi";
import type { CategoriaTarea, EstadoTarea, PrioridadTarea, Tarea } from "../../lib/operacionApi";
import { AvisoError, Cargando, Modal } from "../ingresos/IngresosShell";
import { useAviso } from "../ingresos/useAviso";
import { fechaLegible, hoyIso, mensajeError, sumarDias } from "../ingresos/ingresosUtil";
import "../ingresos/ingresos.css";
import "./tareas.css";
import { CATEGORIA, metaTarea } from "./tareasUtil";

type Pestana = EstadoTarea | "todas";

const PESTANAS: { valor: Pestana; label: string }[] = [
  { valor: "pendiente", label: "Pendientes" },
  { valor: "pospuesta", label: "Pospuestas" },
  { valor: "completada", label: "Completadas" },
  { valor: "todas", label: "Todas" },
];

const PRIORIDADES: { value: PrioridadTarea; label: string }[] = [
  { value: "alta", label: "Alta" },
  { value: "media", label: "Media" },
  { value: "baja", label: "Baja" },
];

function IconoBasura() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" aria-hidden="true">
      <path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3" />
    </svg>
  );
}

function IconoReloj() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5l3 2" />
    </svg>
  );
}

function IconoLapiz() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M4 20h4L19 9l-4-4L4 16z" />
      <path d="m14 6 4 4" />
    </svg>
  );
}

export function TareasPage() {
  const [tareas, setTareas] = useState<Tarea[]>([]);
  const [cargando, setCargando] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [pestana, setPestana] = useState<Pestana>("pendiente");
  const [categoria, setCategoria] = useState<CategoriaTarea | "">("");
  const [titulo, setTitulo] = useState("");
  const [vence, setVence] = useState(sumarDias(hoyIso(), 7));
  const [prioridad, setPrioridad] = useState<PrioridadTarea>("media");
  const [guardando, setGuardando] = useState(false);
  const [ocupada, setOcupada] = useState<string | null>(null);
  const [posponer, setPosponer] = useState<Tarea | null>(null);
  const [editar, setEditar] = useState<Tarea | null>(null);
  const [toast, mostrarToast] = useAviso();

  const cargar = useCallback(() => {
    setCargando(true);
    setError(null);
    tareasApi
      .listar()
      .then(setTareas)
      .catch((err) => setError(mensajeError(err)))
      .finally(() => setCargando(false));
  }, []);

  useEffect(() => {
    cargar();
  }, [cargar]);

  const reemplazar = (t: Tarea) => setTareas((prev) => prev.map((x) => (x.id === t.id ? t : x)));

  async function agregar(e: FormEvent) {
    e.preventDefault();
    if (!titulo.trim()) return;
    setGuardando(true);
    try {
      const nueva = await tareasApi.crear({ titulo: titulo.trim(), vence: vence || null, prioridad });
      setTareas((prev) => [nueva, ...prev]);
      setTitulo("");
      setPestana("pendiente");
      mostrarToast("Tarea agregada", { detalle: nueva.titulo });
    } catch (err) {
      mostrarToast("No se pudo agregar la tarea", { detalle: mensajeError(err), error: true });
    } finally {
      setGuardando(false);
    }
  }

  async function alternar(t: Tarea) {
    setOcupada(t.id);
    try {
      const nueva = t.estado === "completada" ? await tareasApi.reabrir(t.id) : await tareasApi.completar(t.id);
      reemplazar(nueva);
      mostrarToast(nueva.estado === "completada" ? "Tarea completada" : "Tarea reabierta", { detalle: nueva.titulo });
    } catch (err) {
      mostrarToast("No se pudo actualizar la tarea", { detalle: mensajeError(err), error: true });
      cargar();
    } finally {
      setOcupada(null);
    }
  }

  async function eliminar(t: Tarea) {
    if (!window.confirm(`¿Eliminar la tarea «${t.titulo}»?`)) return;
    setOcupada(t.id);
    try {
      await tareasApi.eliminar(t.id);
      setTareas((prev) => prev.filter((x) => x.id !== t.id));
      mostrarToast("Tarea eliminada", { detalle: t.titulo });
    } catch (err) {
      mostrarToast("No se pudo eliminar la tarea", { detalle: mensajeError(err), error: true });
    } finally {
      setOcupada(null);
    }
  }

  const deCategoria = useMemo(() => tareas.filter((t) => !categoria || t.categoria === categoria), [tareas, categoria]);
  const visibles = deCategoria.filter((t) => pestana === "todas" || t.estado === pestana);
  const conteo = (p: Pestana) => deCategoria.filter((t) => p === "todas" || t.estado === p).length;
  const pendientes = tareas.filter((t) => t.estado === "pendiente");
  const vencidas = pendientes.filter((t) => t.vencida).length;
  const categorias = [...new Set(tareas.map((t) => t.categoria))];

  return (
    <div className="content ta-content">
      <div className="ph">
        <div>
          <h1>Mis tareas</h1>
          <p>Pendientes fiscales y de gestión. Las obligaciones de la DGII y los pendientes de cada módulo se agregan automáticamente.</p>
        </div>
        {!cargando && !error && (
          <div className="row ta-res">
            <span className="chip">{pendientes.length} pendientes</span>
            {vencidas > 0 && <span className="bd b-rech nodot">{vencidas} vencidas</span>}
          </div>
        )}
      </div>

      <form className="card card-b ta-nueva" onSubmit={agregar}>
        <div className="fld ta-nt">
          <label className="lbl" htmlFor="nt">
            Nueva tarea
          </label>
          <input id="nt" className="inp" placeholder="Ej.: Llamar a La Cosecha por factura vencida" value={titulo} onChange={(e) => setTitulo(e.target.value)} maxLength={300} />
        </div>
        <div className="fld ta-nf">
          <label className="lbl" htmlFor="nf">
            Vence
          </label>
          <input id="nf" className="inp" type="date" value={vence} onChange={(e) => setVence(e.target.value)} />
        </div>
        <div className="fld ta-np">
          <label className="lbl" htmlFor="np">
            Prioridad
          </label>
          <select id="np" className="inp" value={prioridad} onChange={(e) => setPrioridad(e.target.value as PrioridadTarea)}>
            {PRIORIDADES.map((p) => (
              <option key={p.value} value={p.value}>
                {p.label}
              </option>
            ))}
          </select>
        </div>
        <button type="submit" className="btn btn-p" disabled={!titulo.trim() || guardando}>
          {guardando ? "Agregando…" : "Agregar"}
        </button>
      </form>

      {error && <AvisoError titulo="No se pudieron cargar las tareas." mensaje={error} onReintentar={cargar} />}

      <section className="card">
        <div className="ta-barra">
          <div className="tabs" role="tablist" aria-label="Estado de las tareas">
            {PESTANAS.map((p) => (
              <button key={p.valor} type="button" role="tab" aria-selected={pestana === p.valor} className={pestana === p.valor ? "tab on" : "tab"} onClick={() => setPestana(p.valor)}>
                {p.label} <span className="chip ta-n">{conteo(p.valor)}</span>
              </button>
            ))}
          </div>
          {categorias.length > 1 && (
            <select className="inp inp-sm ta-cat" aria-label="Filtrar por tipo" value={categoria} onChange={(e) => setCategoria(e.target.value as CategoriaTarea | "")}>
              <option value="">Todos los tipos</option>
              {(Object.keys(CATEGORIA) as CategoriaTarea[])
                .filter((c) => categorias.includes(c))
                .map((c) => (
                  <option key={c} value={c}>
                    {CATEGORIA[c].label}
                  </option>
                ))}
            </select>
          )}
        </div>

        {cargando && <Cargando />}
        {!cargando && !error && visibles.length === 0 && (
          <div className="empty">
            <span className="ei">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M20 6 9 17l-5-5" />
              </svg>
            </span>
            <h3>{pestana === "completada" ? "Aún no completas tareas" : pestana === "pospuesta" ? "Sin tareas pospuestas" : "¡Todo al día!"}</h3>
            <p>
              {pestana === "completada"
                ? "Marca una tarea como hecha y aparecerá aquí."
                : pestana === "pospuesta"
                  ? "Las tareas que pospongas vuelven a Pendientes en la fecha que elijas."
                  : "No tienes tareas pendientes. Agrega una arriba."}
            </p>
          </div>
        )}
        {!cargando &&
          visibles.map((t) => {
            const hecha = t.estado === "completada";
            const cat = CATEGORIA[t.categoria] ?? CATEGORIA.gestion;
            return (
              <div key={t.id} className={`ta-fila${hecha ? " hecha" : ""}${t.vencida ? " vencida" : ""}`}>
                <input type="checkbox" className="ta-check" checked={hecha} disabled={ocupada === t.id} onChange={() => alternar(t)} aria-label={`${hecha ? "Reabrir" : "Completar"} ${t.titulo}`} />
                <div className="ta-txt">
                  <div className="ta-t">{t.titulo}</div>
                  <div className="hint">{metaTarea(t)}</div>
                  {t.descripcion && !hecha && <div className="hint ta-d">{t.descripcion}</div>}
                </div>
                <div className="ta-tags">
                  {t.prioridad === "alta" && !hecha && <span className="bd b-rech nodot">Urgente</span>}
                  <span className={cat.badge}>{cat.label}</span>
                </div>
                <div className="ta-acc">
                  {t.enlace && (
                    <Link to={t.enlace} className="btn btn-o btn-sm">
                      Abrir
                    </Link>
                  )}
                  {!hecha && (
                    <button type="button" className="ib" onClick={() => setPosponer(t)} disabled={ocupada === t.id} aria-label={`Posponer ${t.titulo}`} title="Posponer">
                      <IconoReloj />
                    </button>
                  )}
                  {t.origen === "manual" && (
                    <>
                      <button type="button" className="ib" onClick={() => setEditar(t)} disabled={ocupada === t.id} aria-label={`Editar ${t.titulo}`} title="Editar">
                        <IconoLapiz />
                      </button>
                      <button type="button" className="ib" onClick={() => eliminar(t)} disabled={ocupada === t.id} aria-label="Eliminar tarea" title="Eliminar">
                        <IconoBasura />
                      </button>
                    </>
                  )}
                </div>
              </div>
            );
          })}
        {!cargando && visibles.some((t) => t.origen === "automatica") && (
          <p className="hint ta-pie">Las tareas automáticas desaparecen solas cuando se resuelve su motivo (se presenta la declaración, se cobra la factura…).</p>
        )}
      </section>

      {posponer && (
        <ModalPosponer
          tarea={posponer}
          onCerrar={() => setPosponer(null)}
          onListo={(t) => {
            reemplazar(t);
            setPosponer(null);
            mostrarToast("Tarea pospuesta", { detalle: `${t.titulo} · hasta el ${fechaLegible(t.pospuesta_hasta)}` });
          }}
        />
      )}
      {editar && (
        <ModalEditar
          tarea={editar}
          onCerrar={() => setEditar(null)}
          onListo={(t) => {
            reemplazar(t);
            setEditar(null);
            mostrarToast("Tarea actualizada", { detalle: t.titulo });
          }}
        />
      )}
      {toast}
    </div>
  );
}

function ModalPosponer({ tarea, onCerrar, onListo }: { tarea: Tarea; onCerrar: () => void; onListo: (t: Tarea) => void }) {
  const [hasta, setHasta] = useState(sumarDias(hoyIso(), 1));
  const [error, setError] = useState<string | null>(null);
  const [guardando, setGuardando] = useState(false);
  const opciones = [
    { label: "Mañana", dias: 1 },
    { label: "En 3 días", dias: 3 },
    { label: "En una semana", dias: 7 },
  ];

  async function guardar() {
    setGuardando(true);
    setError(null);
    try {
      onListo(await tareasApi.posponer(tarea.id, hasta));
    } catch (err) {
      setError(mensajeError(err));
      setGuardando(false);
    }
  }

  return (
    <Modal
      titulo="Posponer tarea"
      onCerrar={onCerrar}
      ancho={460}
      pie={
        <>
          <button type="button" className="btn btn-o" onClick={onCerrar}>
            Cancelar
          </button>
          <button type="button" className="btn btn-p" onClick={guardar} disabled={!hasta || guardando}>
            {guardando ? "Guardando…" : "Posponer"}
          </button>
        </>
      }
    >
      <p className="ta-modal-t">{tarea.titulo}</p>
      <div className="row" style={{ flexWrap: "wrap" }}>
        {opciones.map((o) => {
          const fecha = sumarDias(hoyIso(), o.dias);
          return (
            <button key={o.dias} type="button" className={hasta === fecha ? "btn btn-sm btn-p" : "btn btn-sm btn-o"} onClick={() => setHasta(fecha)}>
              {o.label}
            </button>
          );
        })}
      </div>
      <div className="fld">
        <label className="lbl" htmlFor="ph">
          Hasta el
        </label>
        <input id="ph" className="inp" type="date" min={hoyIso()} value={hasta} onChange={(e) => setHasta(e.target.value)} />
        <span className="hint">Vuelve a Pendientes ese día{tarea.origen === "automatica" ? " si su motivo sigue vigente" : ""}.</span>
      </div>
      {error && <AvisoError mensaje={error} />}
    </Modal>
  );
}

function ModalEditar({ tarea, onCerrar, onListo }: { tarea: Tarea; onCerrar: () => void; onListo: (t: Tarea) => void }) {
  const [titulo, setTitulo] = useState(tarea.titulo);
  const [descripcion, setDescripcion] = useState(tarea.descripcion ?? "");
  const [vence, setVence] = useState(tarea.vence ?? "");
  const [prioridad, setPrioridad] = useState<PrioridadTarea>(tarea.prioridad);
  const [error, setError] = useState<string | null>(null);
  const [guardando, setGuardando] = useState(false);

  async function guardar() {
    setGuardando(true);
    setError(null);
    try {
      onListo(await tareasApi.actualizar(tarea.id, { titulo: titulo.trim(), descripcion: descripcion.trim() || null, vence: vence || null, prioridad }));
    } catch (err) {
      setError(mensajeError(err));
      setGuardando(false);
    }
  }

  return (
    <Modal
      titulo="Editar tarea"
      onCerrar={onCerrar}
      ancho={520}
      pie={
        <>
          <button type="button" className="btn btn-o" onClick={onCerrar}>
            Cancelar
          </button>
          <button type="button" className="btn btn-p" onClick={guardar} disabled={!titulo.trim() || guardando}>
            {guardando ? "Guardando…" : "Guardar"}
          </button>
        </>
      }
    >
      <div className="fld">
        <label className="lbl" htmlFor="et">
          Tarea <span className="req">*</span>
        </label>
        <input id="et" className="inp" value={titulo} onChange={(e) => setTitulo(e.target.value)} maxLength={300} />
      </div>
      <div className="fld">
        <label className="lbl" htmlFor="ed">
          Descripción
        </label>
        <textarea id="ed" className="inp" rows={3} value={descripcion} onChange={(e) => setDescripcion(e.target.value)} />
      </div>
      <div className="form-grid">
        <div className="fld">
          <label className="lbl" htmlFor="ev">
            Vence
          </label>
          <input id="ev" className="inp" type="date" value={vence} onChange={(e) => setVence(e.target.value)} />
        </div>
        <div className="fld">
          <label className="lbl" htmlFor="ep">
            Prioridad
          </label>
          <select id="ep" className="inp" value={prioridad} onChange={(e) => setPrioridad(e.target.value as PrioridadTarea)}>
            {PRIORIDADES.map((p) => (
              <option key={p.value} value={p.value}>
                {p.label}
              </option>
            ))}
          </select>
        </div>
      </div>
      {error && <AvisoError mensaje={error} />}
    </Modal>
  );
}
