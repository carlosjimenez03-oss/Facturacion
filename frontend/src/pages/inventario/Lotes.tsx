// Lotes (grupo Inventario de Modulo.dc.html): control de lotes y fechas de vencimiento, con alertas
// de lotes por vencer y vencidos.
import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { IconAlerta } from "../../layout/icons";
import { inventarioApi } from "../../lib/inventarioApi";
import type { AlertasLotes, Almacen, EstadoLote, Lote, Producto } from "../../lib/types";
import { AvisoError, Buscador, Cargando, Estadistica, SinResultados, Vacio } from "../ingresos/IngresosShell";
import { useAviso } from "../ingresos/useAviso";
import { descargarCsv, fechaLegible, hoyIso } from "../ingresos/ingresosUtil";
import { InventarioEncabezado, Interruptor } from "./InventarioShell";
import { decimalTexto, ESTADO_LOTE, etiquetaProducto, formatoCantidad, mensajeInventario, soloBienes, textoVencimiento } from "./inventarioUtil";

interface FormLote {
  producto_id: string;
  numero: string;
  fecha_vencimiento: string;
  cantidad_inicial: string;
  almacen_id: string;
  costo_unitario: string;
  notas: string;
  bloqueado: boolean;
}

const FORM_VACIO: FormLote = { producto_id: "", numero: "", fecha_vencimiento: "", cantidad_inicial: "", almacen_id: "", costo_unitario: "", notas: "", bloqueado: false };

function almacenesDe(l: Lote): string {
  const con = l.existencias.filter((e) => Number(e.cantidad) !== 0);
  return con.length ? con.map((e) => e.almacen_nombre).join(", ") : "—";
}

/** Lista corta de lotes para las tarjetas de alerta: "L-2609-04 (Café molido 1 lb, 20/12/2026)". */
function resumenLotes(lotes: Lote[]): string {
  const primeros = lotes.slice(0, 3).map((l) => `${l.numero} (${l.producto_nombre}, ${fechaLegible(l.fecha_vencimiento)})`);
  return primeros.join(" · ") + (lotes.length > 3 ? ` y ${lotes.length - 3} más` : "");
}

export function LotesPage() {
  const [lotes, setLotes] = useState<Lote[]>([]);
  const [alertas, setAlertas] = useState<AlertasLotes | null>(null);
  const [productos, setProductos] = useState<Producto[]>([]);
  const [almacenes, setAlmacenes] = useState<Almacen[]>([]);
  const [cargando, setCargando] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [productoId, setProductoId] = useState("");
  const [almacenId, setAlmacenId] = useState("");
  const [estado, setEstado] = useState<"" | EstadoLote>("");
  const [toast, mostrarAviso] = useAviso();

  const [formAbierto, setFormAbierto] = useState<"nuevo" | Lote | null>(null);
  const [form, setForm] = useState<FormLote>(FORM_VACIO);
  const [errorForm, setErrorForm] = useState<string | null>(null);
  const [guardando, setGuardando] = useState(false);
  const [procesando, setProcesando] = useState<string | null>(null);
  const formRef = useRef<HTMLElement>(null);

  const cargar = useCallback(async () => {
    setCargando(true);
    const [lista, al] = await Promise.allSettled([
      inventarioApi.listarLotes({ q: q.trim() || undefined, producto_id: productoId || undefined, almacen_id: almacenId || undefined, estado: estado || undefined }),
      inventarioApi.alertasLotes(),
    ]);
    if (lista.status === "fulfilled") {
      setLotes(lista.value);
      setError(null);
    } else {
      setLotes([]);
      setError(mensajeInventario(lista.reason));
    }
    setAlertas(al.status === "fulfilled" ? al.value : null);
    setCargando(false);
  }, [q, productoId, almacenId, estado]);

  useEffect(() => {
    const t = window.setTimeout(() => void cargar(), q ? 250 : 0);
    return () => window.clearTimeout(t);
  }, [cargar, q]);

  useEffect(() => {
    inventarioApi
      .listarProductos({ activo: true, tipo: "bien" })
      .then((ps) => setProductos(soloBienes(ps)))
      .catch(() => setProductos([]));
    inventarioApi
      .listarAlmacenes({ activo: true })
      .then(setAlmacenes)
      .catch(() => setAlmacenes([]));
  }, []);

  const hayFiltros = Boolean(q || productoId || almacenId || estado);
  const totalUnidades = lotes.reduce((s, l) => s + (Number(l.cantidad) || 0), 0);
  const editando = formAbierto && formAbierto !== "nuevo" ? formAbierto : null;
  const productoForm = productos.find((p) => p.id === form.producto_id);
  const cantidadInicial = Number(form.cantidad_inicial) || 0;

  function abrirForm(l?: Lote) {
    setErrorForm(null);
    setForm(
      l
        ? { ...FORM_VACIO, producto_id: l.producto_id, numero: l.numero, fecha_vencimiento: l.fecha_vencimiento ?? "", notas: l.notas ?? "", bloqueado: l.bloqueado }
        : { ...FORM_VACIO, producto_id: productoId },
    );
    setFormAbierto(l ?? "nuevo");
    window.setTimeout(() => formRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 0);
  }

  function setCampo<K extends keyof FormLote>(campo: K, valor: FormLote[K]) {
    setForm((prev) => ({ ...prev, [campo]: valor }));
    setErrorForm(null);
  }

  async function guardar() {
    if (!editando && !form.producto_id) return setErrorForm("Elija el producto del lote.");
    if (!form.numero.trim()) return setErrorForm("Escriba el número de lote.");
    if (!editando && cantidadInicial < 0) return setErrorForm("La cantidad no puede ser negativa.");
    setGuardando(true);
    try {
      if (editando) {
        const l = await inventarioApi.actualizarLote(editando.id, {
          numero: form.numero.trim(),
          fecha_vencimiento: form.fecha_vencimiento || null,
          notas: form.notas.trim() || null,
          bloqueado: form.bloqueado,
        });
        mostrarAviso("Lote actualizado", { detalle: `${l.numero} · ${l.producto_nombre}` });
      } else {
        const l = await inventarioApi.crearLote({
          producto_id: form.producto_id,
          numero: form.numero.trim(),
          fecha_vencimiento: form.fecha_vencimiento || null,
          notas: form.notas.trim() || null,
          ...(cantidadInicial > 0
            ? {
                cantidad_inicial: decimalTexto(form.cantidad_inicial),
                almacen_id: form.almacen_id || null,
                costo_unitario: form.costo_unitario === "" ? null : decimalTexto(form.costo_unitario),
              }
            : {}),
        });
        mostrarAviso("Lote registrado", {
          detalle: cantidadInicial > 0 ? `${l.numero}: entrada de ${formatoCantidad(cantidadInicial)} registrada en el Kardex.` : `${l.numero} · ${l.producto_nombre}`,
        });
      }
      setFormAbierto(null);
      void cargar();
    } catch (err) {
      setErrorForm(mensajeInventario(err));
    } finally {
      setGuardando(false);
    }
  }

  async function cambiarBloqueo(l: Lote) {
    setProcesando(l.id);
    try {
      await inventarioApi.actualizarLote(l.id, { numero: l.numero, fecha_vencimiento: l.fecha_vencimiento, notas: l.notas, bloqueado: !l.bloqueado });
      mostrarAviso(l.bloqueado ? "Lote desbloqueado" : "Lote bloqueado", {
        detalle: l.bloqueado ? `${l.numero} vuelve a usarse en las ventas (FEFO).` : `${l.numero} ya no se tomará en las ventas ni en las sugerencias FEFO.`,
      });
      void cargar();
    } catch (err) {
      mostrarAviso("No se pudo cambiar el lote", { detalle: mensajeInventario(err), error: true });
    } finally {
      setProcesando(null);
    }
  }

  function exportar() {
    descargarCsv(`Lotes_${hoyIso()}.csv`, [
      ["Lote", "Código", "Producto", "Vence", "Cantidad", "Almacén", "Estado"],
      ...lotes.map((l) => [l.numero, l.producto_sku, l.producto_nombre, fechaLegible(l.fecha_vencimiento), Number(l.cantidad), almacenesDe(l), ESTADO_LOTE[l.estado]?.label ?? l.estado]),
    ]);
  }

  const porVencer = alertas?.por_vencer ?? [];
  const vencidos = alertas?.vencidos ?? [];

  return (
    <div className="content">
      <InventarioEncabezado
        titulo="Lotes"
        sub="Control de lotes y fechas de vencimiento"
        acciones={
          <>
            <button type="button" className="btn btn-o" onClick={exportar} disabled={lotes.length === 0}>
              Exportar
            </button>
            <button type="button" className="btn btn-p" onClick={() => (formAbierto ? setFormAbierto(null) : abrirForm())}>
              {formAbierto ? "Cerrar formulario" : "+ Nuevo lote"}
            </button>
          </>
        }
      />

      {formAbierto && (
        <section className="card" ref={formRef}>
          <div className="card-h">
            <h2>{editando ? `Editar lote ${editando.numero}` : "Nuevo lote"}</h2>
            <span className="hint">
              {editando ? editando.producto_nombre : cantidadInicial > 0 ? "Se registrará una entrada de inventario a este lote" : "Se asociará a la próxima entrada de inventario"}
            </span>
          </div>
          <div className="card-b form-grid">
            <div className="fld">
              <label className="lbl" htmlFor="lo-prod">
                Producto <span className="req">*</span>
              </label>
              {editando ? (
                <input id="lo-prod" className="inp" value={etiquetaProducto({ sku: editando.producto_sku, nombre: editando.producto_nombre })} disabled />
              ) : (
                <select id="lo-prod" className="inp" value={form.producto_id} onChange={(e) => setCampo("producto_id", e.target.value)}>
                  <option value="">Elija un producto…</option>
                  {productos.map((p) => (
                    <option key={p.id} value={p.id}>
                      {etiquetaProducto(p)}
                    </option>
                  ))}
                </select>
              )}
            </div>
            <div className="fld">
              <label className="lbl" htmlFor="lo-num">
                Número de lote <span className="req">*</span>
              </label>
              <input id="lo-num" className="inp mono" value={form.numero} onChange={(e) => setCampo("numero", e.target.value.toUpperCase())} placeholder="L-2610-05" />
            </div>
            <div className="fld">
              <label className="lbl" htmlFor="lo-vence">Vence</label>
              <input id="lo-vence" type="date" className="inp" value={form.fecha_vencimiento} onChange={(e) => setCampo("fecha_vencimiento", e.target.value)} />
            </div>
            {!editando && (
              <>
                <div className="fld">
                  <label className="lbl" htmlFor="lo-cant">Cantidad</label>
                  <input
                    id="lo-cant"
                    type="number"
                    min="0"
                    step="any"
                    className="inp"
                    style={{ textAlign: "right" }}
                    value={form.cantidad_inicial}
                    onChange={(e) => setCampo("cantidad_inicial", e.target.value)}
                    placeholder="0"
                  />
                </div>
                <div className="fld">
                  <label className="lbl" htmlFor="lo-alm">Almacén</label>
                  <select id="lo-alm" className="inp" value={form.almacen_id} onChange={(e) => setCampo("almacen_id", e.target.value)} disabled={cantidadInicial <= 0}>
                    <option value="">{almacenes.find((a) => a.es_principal)?.nombre ?? "Principal"} (principal)</option>
                    {almacenes
                      .filter((a) => !a.es_principal)
                      .map((a) => (
                        <option key={a.id} value={a.id}>
                          {a.nombre}
                        </option>
                      ))}
                  </select>
                </div>
                <div className="fld">
                  <label className="lbl" htmlFor="lo-costo">Costo unitario (RD$)</label>
                  <input
                    id="lo-costo"
                    type="number"
                    min="0"
                    step="0.01"
                    className="inp"
                    style={{ textAlign: "right" }}
                    value={form.costo_unitario}
                    onChange={(e) => setCampo("costo_unitario", e.target.value)}
                    placeholder={productoForm ? Number(productoForm.costo_promedio).toFixed(2) : "Costo promedio"}
                    disabled={cantidadInicial <= 0}
                  />
                </div>
              </>
            )}
            <div className="fld" style={{ gridColumn: "span 2" }}>
              <label className="lbl" htmlFor="lo-notas">Notas</label>
              <input id="lo-notas" className="inp" value={form.notas} onChange={(e) => setCampo("notas", e.target.value)} placeholder="Proveedor, factura de compra…" />
            </div>
            {editando && (
              <div className="fld-full">
                <Interruptor
                  id="lo-bloq"
                  etiqueta="Bloqueado"
                  ayuda="Un lote bloqueado no se vende ni aparece en las sugerencias FEFO; solo sale con un ajuste que lo indique."
                  valor={form.bloqueado}
                  onCambiar={(v) => setCampo("bloqueado", v)}
                />
              </div>
            )}
            {!editando && productoForm && !productoForm.maneja_lotes && (
              <span className="hint fld-full">Al registrar este lote, {productoForm.nombre} pasará a manejar lotes: sus ventas saldrán primero del lote que vence antes.</span>
            )}
            {errorForm && (
              <div className="al al-d fld-full" role="alert">
                <IconAlerta />
                <span>
                  <b>No se pudo guardar el lote.</b> {errorForm}
                </span>
              </div>
            )}
            <div className="row fld-full" style={{ justifyContent: "flex-end" }}>
              <button type="button" className="btn btn-o" onClick={() => setFormAbierto(null)}>
                Cancelar
              </button>
              <button type="button" className="btn btn-p" onClick={guardar} disabled={guardando}>
                {guardando ? "Guardando…" : editando ? "Guardar cambios" : "Registrar lote"}
              </button>
            </div>
          </div>
        </section>
      )}

      {vencidos.length > 0 && (
        <div className="al al-d">
          <IconAlerta />
          <span>
            <b>
              {vencidos.length === 1 ? "1 lote vencido" : `${vencidos.length} lotes vencidos`} con existencia.
            </b>{" "}
            {resumenLotes(vencidos)}. Sáquelos con un ajuste por vencimiento o bloquéelos para que no se vendan.
          </span>
        </div>
      )}
      {porVencer.length > 0 && (
        <div className="al al-w">
          <IconAlerta />
          <span>
            <b>
              {porVencer.length === 1 ? "1 lote vence" : `${porVencer.length} lotes vencen`} en los próximos {alertas?.dias ?? 30} días.
            </b>{" "}
            {resumenLotes(porVencer)}.
          </span>
        </div>
      )}

      {error && <AvisoError titulo="No se pudieron cargar los lotes." mensaje={error} onReintentar={() => void cargar()} />}

      <div className="grid-stats">
        <Estadistica k="Lotes" v={cargando ? "—" : String(lotes.length)} s={cargando ? undefined : `${formatoCantidad(totalUnidades)} unidades en total`} />
        <Estadistica k="Por vencer" v={alertas ? String(porVencer.length) : "—"} s={`Vencen en ${alertas?.dias ?? 30} días o menos`} />
        <Estadistica k="Vencidos" v={alertas ? String(vencidos.length) : "—"} s="Con existencia, sin bloquear" />
      </div>

      <section className="card">
        <div className="filters inv-filtros">
          <Buscador valor={q} onCambiar={setQ} placeholder="Buscar en Lotes" />
          <select className="inp" value={productoId} onChange={(e) => setProductoId(e.target.value)} aria-label="Producto">
            <option value="">Todos los productos</option>
            {productos.map((p) => (
              <option key={p.id} value={p.id}>
                {etiquetaProducto(p)}
              </option>
            ))}
          </select>
          <select className="inp" value={almacenId} onChange={(e) => setAlmacenId(e.target.value)} aria-label="Almacén">
            <option value="">Todos los almacenes</option>
            {almacenes.map((a) => (
              <option key={a.id} value={a.id}>
                {a.nombre}
              </option>
            ))}
          </select>
          <select className="inp" value={estado} onChange={(e) => setEstado(e.target.value as "" | EstadoLote)} aria-label="Estado">
            <option value="">Todos los estados</option>
            <option value="vigente">Vigente</option>
            <option value="por_vencer">Por vencer</option>
            <option value="vencido">Vencido</option>
            <option value="bloqueado">Bloqueado</option>
          </select>
        </div>

        {cargando && <Cargando />}
        {!cargando && !error && lotes.length === 0 && !hayFiltros && (
          <Vacio titulo="No hay lotes registrados" texto="Registre los lotes de los productos con vencimiento (alimentos, pinturas, químicos) para controlar qué se vende primero.">
            <button type="button" className="btn btn-p btn-sm" onClick={() => abrirForm()}>
              Nuevo lote
            </button>
          </Vacio>
        )}
        {!cargando && !error && lotes.length === 0 && hayFiltros && (
          <SinResultados
            q={q}
            onLimpiar={() => {
              setQ("");
              setProductoId("");
              setAlmacenId("");
              setEstado("");
            }}
          />
        )}
        {!cargando && lotes.length > 0 && (
          <div className="tw">
            <table className="tbl" style={{ minWidth: 960 }}>
              <thead>
                <tr>
                  <th>Lote</th>
                  <th>Producto</th>
                  <th>Vence</th>
                  <th className="num">Cantidad</th>
                  <th>Almacén</th>
                  <th>Estado</th>
                  <th style={{ textAlign: "right" }}>Acciones</th>
                </tr>
              </thead>
              <tbody>
                {lotes.map((l) => {
                  const est = ESTADO_LOTE[l.estado] ?? { label: l.estado, badge: "bd b-borr" };
                  return (
                    <tr key={l.id}>
                      <td className="mono nowrap">{l.numero}</td>
                      <td>
                        <div className="strong">{l.producto_nombre}</div>
                        <div className="hint mono">{l.producto_sku}</div>
                      </td>
                      <td className="nowrap">
                        {fechaLegible(l.fecha_vencimiento)}
                        {l.fecha_vencimiento && (
                          <div className={l.estado === "vencido" ? "err-t" : "hint"} style={{ fontSize: 12 }}>
                            {textoVencimiento(l.dias_para_vencer)}
                          </div>
                        )}
                      </td>
                      <td className="num" style={{ fontWeight: 600, color: "#111827" }}>
                        {formatoCantidad(l.cantidad)}
                      </td>
                      <td>{almacenesDe(l)}</td>
                      <td>
                        <span className={est.badge}>{est.label}</span>
                      </td>
                      <td>
                        <div className="fila-acc inv-acc">
                          <Link to={`/inventario/kardex?producto=${l.producto_id}`} className="btn btn-g btn-sm">
                            Kardex
                          </Link>
                          <button type="button" className="btn btn-g btn-sm" onClick={() => abrirForm(l)}>
                            Editar
                          </button>
                          <button
                            type="button"
                            className="btn btn-g btn-sm"
                            style={{ color: l.bloqueado ? "#374151" : "#B91C1C" }}
                            onClick={() => cambiarBloqueo(l)}
                            disabled={procesando === l.id}
                          >
                            {l.bloqueado ? "Desbloquear" : "Bloquear"}
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {toast}
    </div>
  );
}
