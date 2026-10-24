// Almacenes (grupo Inventario de Modulo.dc.html): bodegas y sucursales con su valor, existencias por
// almacén y transferencias de mercancía entre ellos (se aplican en el acto).
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { IconAlerta } from "../../layout/icons";
import { inventarioApi } from "../../lib/inventarioApi";
import { formatoMonto } from "../../lib/marca";
import type { Almacen, ExistenciaAlmacen, Lote, Producto, Transferencia, Valuacion } from "../../lib/types";
import { AvisoError, Buscador, Cargando, Estadistica, Modal, SinResultados, Vacio } from "../ingresos/IngresosShell";
import { useAviso } from "../ingresos/useAviso";
import { descargarCsv, fechaLegible, hoyIso } from "../ingresos/ingresosUtil";
import { AvisoOperacion, InventarioEncabezado, Interruptor } from "./InventarioShell";
import { decimalTexto, etiquetaProducto, formatoCantidad, mensajeInventario, soloBienes } from "./inventarioUtil";

interface FormAlmacen {
  nombre: string;
  direccion: string;
  permite_negativo: boolean;
  activo: boolean;
  es_principal: boolean;
}

const FORM_VACIO: FormAlmacen = { nombre: "", direccion: "", permite_negativo: false, activo: true, es_principal: false };

interface LineaTransferencia {
  producto_id: string;
  cantidad: string;
  lote_id: string;
}

const lineaVacia = (): LineaTransferencia => ({ producto_id: "", cantidad: "", lote_id: "" });

export function AlmacenesPage() {
  const [almacenes, setAlmacenes] = useState<Almacen[]>([]);
  const [valuacion, setValuacion] = useState<Valuacion | null>(null);
  const [productos, setProductos] = useState<Producto[]>([]);
  const [transferencias, setTransferencias] = useState<Transferencia[]>([]);
  const [cargando, setCargando] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [toast, mostrarAviso] = useAviso();

  // Crear / editar almacén
  const [formAbierto, setFormAbierto] = useState<"nuevo" | Almacen | null>(null);
  const [form, setForm] = useState<FormAlmacen>(FORM_VACIO);
  const [errorForm, setErrorForm] = useState<string | null>(null);
  const [guardando, setGuardando] = useState(false);
  const formRef = useRef<HTMLElement>(null);

  // Existencias de un almacén
  const [verId, setVerId] = useState<string | null>(null);
  const [existencias, setExistencias] = useState<ExistenciaAlmacen[]>([]);
  const [cargandoEx, setCargandoEx] = useState(false);
  const [errorEx, setErrorEx] = useState<string | null>(null);
  const [qEx, setQEx] = useState("");
  const exRef = useRef<HTMLElement>(null);

  // Transferencia
  const [trAbierta, setTrAbierta] = useState(false);
  const [origen, setOrigen] = useState("");
  const [destino, setDestino] = useState("");
  const [notasTr, setNotasTr] = useState("");
  const [lineas, setLineas] = useState<LineaTransferencia[]>([lineaVacia()]);
  const [disponibles, setDisponibles] = useState<Map<string, string>>(new Map());
  const [lotes, setLotes] = useState<Record<string, Lote[]>>({});
  const [errorTr, setErrorTr] = useState<unknown>(null);
  const [confirmarTr, setConfirmarTr] = useState(false);
  const [transfiriendo, setTransfiriendo] = useState(false);
  const trRef = useRef<HTMLElement>(null);

  // Inactivar
  const [inactivar, setInactivar] = useState<Almacen | null>(null);

  const cargar = useCallback(async () => {
    setCargando(true);
    const [lista, val, prods, trs] = await Promise.allSettled([
      inventarioApi.listarAlmacenes(),
      inventarioApi.valuacion(),
      inventarioApi.listarProductos({ activo: true, tipo: "bien" }),
      inventarioApi.listarTransferencias({ limit: 10 }),
    ]);
    if (lista.status === "fulfilled") {
      setAlmacenes(lista.value);
      setError(null);
    } else {
      setAlmacenes([]);
      setError(mensajeInventario(lista.reason));
    }
    setValuacion(val.status === "fulfilled" ? val.value : null);
    setProductos(prods.status === "fulfilled" ? soloBienes(prods.value) : []);
    setTransferencias(trs.status === "fulfilled" ? trs.value : []);
    setCargando(false);
  }, []);

  useEffect(() => {
    void cargar();
  }, [cargar]);

  const nombreAlmacen = useCallback((id: string) => almacenes.find((a) => a.id === id)?.nombre, [almacenes]);
  const activos = almacenes.filter((a) => a.activo);

  const filas = useMemo(() => {
    const texto = q.trim().toLowerCase();
    return almacenes.filter((a) => !texto || [a.nombre, a.direccion].join(" ").toLowerCase().includes(texto));
  }, [almacenes, q]);

  const valorTotal = almacenes.reduce((s, a) => s + (Number(a.valor) || 0), 0);
  const alertas = valuacion ? valuacion.bajo_minimo + valuacion.sin_existencia : null;

  // ----- crear / editar
  function abrirForm(a?: Almacen) {
    setErrorForm(null);
    setForm(a ? { nombre: a.nombre, direccion: a.direccion ?? "", permite_negativo: a.permite_negativo, activo: a.activo, es_principal: a.es_principal } : FORM_VACIO);
    setFormAbierto(a ?? "nuevo");
    window.setTimeout(() => formRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 0);
  }

  function setCampo<K extends keyof FormAlmacen>(campo: K, valor: FormAlmacen[K]) {
    setForm((prev) => ({ ...prev, [campo]: valor }));
    setErrorForm(null);
  }

  async function guardarAlmacen() {
    if (!form.nombre.trim()) return setErrorForm("Escriba el nombre del almacén.");
    setGuardando(true);
    const base = { nombre: form.nombre.trim(), direccion: form.direccion.trim() || null, permite_negativo: form.permite_negativo };
    try {
      if (formAbierto && formAbierto !== "nuevo") {
        const a = await inventarioApi.actualizarAlmacen(formAbierto.id, {
          ...base,
          activo: form.activo,
          ...(form.es_principal && !formAbierto.es_principal ? { es_principal: true } : {}),
        });
        mostrarAviso("Almacén actualizado", { detalle: a.nombre });
      } else {
        const a = await inventarioApi.crearAlmacen(base);
        mostrarAviso("Almacén creado", { detalle: `${a.nombre}: ya puede transferirle mercancía.` });
      }
      setFormAbierto(null);
      void cargar();
    } catch (err) {
      setErrorForm(mensajeInventario(err));
    } finally {
      setGuardando(false);
    }
  }

  async function cambiarActivo(a: Almacen) {
    try {
      await inventarioApi.actualizarAlmacen(a.id, { nombre: a.nombre, direccion: a.direccion, permite_negativo: a.permite_negativo, activo: !a.activo });
      mostrarAviso(a.activo ? "Almacén inactivado" : "Almacén activado", { detalle: a.nombre });
      void cargar();
    } catch (err) {
      mostrarAviso(`No se pudo ${a.activo ? "inactivar" : "activar"} el almacén`, { detalle: mensajeInventario(err), error: true });
    } finally {
      setInactivar(null);
    }
  }

  // ----- existencias
  const almacenVer = almacenes.find((a) => a.id === verId);
  const cargarExistencias = useCallback(() => {
    if (!verId) return;
    setCargandoEx(true);
    setErrorEx(null);
    inventarioApi
      .existenciasAlmacen(verId)
      .then(setExistencias)
      .catch((err) => {
        setExistencias([]);
        setErrorEx(mensajeInventario(err));
      })
      .finally(() => setCargandoEx(false));
  }, [verId]);

  useEffect(cargarExistencias, [cargarExistencias]);

  function verExistencias(a: Almacen) {
    setQEx("");
    setVerId(a.id);
    window.setTimeout(() => exRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 0);
  }

  const filasEx = useMemo(() => {
    const texto = qEx.trim().toLowerCase();
    return existencias.filter((e) => !texto || [e.sku, e.nombre, e.categoria].join(" ").toLowerCase().includes(texto));
  }, [existencias, qEx]);

  // ----- transferencia
  function abrirTransferencia(desde?: Almacen) {
    const principal = activos.find((a) => a.es_principal) ?? activos[0];
    const o = desde?.id ?? principal?.id ?? "";
    setOrigen(o);
    setDestino(activos.find((a) => a.id !== o)?.id ?? "");
    setNotasTr("");
    setLineas([lineaVacia()]);
    setErrorTr(null);
    setTrAbierta(true);
    window.setTimeout(() => trRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 0);
  }

  useEffect(() => {
    if (!trAbierta || !origen) return;
    inventarioApi
      .existenciasAlmacen(origen)
      .then((ex) => setDisponibles(new Map(ex.map((e) => [e.producto_id, e.cantidad]))))
      .catch(() => setDisponibles(new Map()));
  }, [trAbierta, origen]);

  // Lotes con existencia en el origen de los productos con lotes elegidos en las líneas.
  useEffect(() => {
    if (!trAbierta || !origen) return;
    const faltan = [...new Set(lineas.map((l) => l.producto_id))].filter(
      (pid) => pid && productos.find((p) => p.id === pid)?.maneja_lotes && !lotes[`${pid}|${origen}`],
    );
    if (faltan.length === 0) return;
    setLotes((prev) => ({ ...prev, ...Object.fromEntries(faltan.map((pid) => [`${pid}|${origen}`, []])) }));
    for (const pid of faltan) {
      inventarioApi
        .listarLotes({ producto_id: pid, almacen_id: origen, con_existencia: true })
        .then((ls) => setLotes((prev) => ({ ...prev, [`${pid}|${origen}`]: ls })))
        .catch(() => setLotes((prev) => ({ ...prev, [`${pid}|${origen}`]: [] })));
    }
  }, [trAbierta, origen, lineas, productos, lotes]);

  function lotesDe(productoId: string): Lote[] {
    return lotes[`${productoId}|${origen}`] ?? [];
  }

  function disponibleEnLote(l: Lote): number {
    return Number(l.existencias.find((e) => e.almacen_id === origen)?.cantidad ?? 0);
  }

  function setLinea(i: number, cambios: Partial<LineaTransferencia>) {
    setLineas((prev) => prev.map((l, j) => (j === i ? { ...l, ...cambios } : l)));
    setErrorTr(null);
  }

  const loteNumero = useCallback(
    (id: string) => {
      for (const ls of Object.values(lotes)) {
        const l = ls.find((x) => x.id === id);
        if (l) return l.numero;
      }
      return undefined;
    },
    [lotes],
  );

  function validarTransferencia(): string | null {
    if (!origen || !destino) return "Elija el almacén de origen y el de destino.";
    if (origen === destino) return "El origen y el destino deben ser almacenes distintos.";
    const usadas = lineas.filter((l) => l.producto_id || l.cantidad);
    if (usadas.length === 0) return "Agregue al menos un producto a transferir.";
    if (usadas.some((l) => !l.producto_id)) return "Elija el producto de cada línea.";
    if (usadas.some((l) => !(Number(l.cantidad) > 0))) return "Las cantidades deben ser mayores que cero.";
    return null;
  }

  function pedirConfirmacion() {
    const problema = validarTransferencia();
    if (problema) return setErrorTr(new Error(problema));
    setErrorTr(null);
    setConfirmarTr(true);
  }

  async function transferir() {
    setTransfiriendo(true);
    try {
      const tr = await inventarioApi.crearTransferencia({
        almacen_origen_id: origen,
        almacen_destino_id: destino,
        notas: notasTr.trim() || null,
        items: lineas
          .filter((l) => l.producto_id)
          .map((l) => ({ producto_id: l.producto_id, cantidad: decimalTexto(l.cantidad), lote_id: l.lote_id || null })),
      });
      setTrAbierta(false);
      mostrarAviso(`Transferencia ${tr.numero} aplicada`, {
        detalle: `${tr.items.length} ${tr.items.length === 1 ? "producto" : "productos"} de ${nombreAlmacen(origen)} a ${nombreAlmacen(destino)}.`,
      });
      setLotes({});
      void cargar();
      if (verId) cargarExistencias();
    } catch (err) {
      setErrorTr(err);
    } finally {
      setTransfiriendo(false);
      setConfirmarTr(false);
    }
  }

  function exportar() {
    descargarCsv(`Almacenes_${hoyIso()}.csv`, [
      ["Almacén", "Dirección", "Principal", "Permite negativo", "Productos", "Cantidad", "Valor del inventario (RD$)", "Estado"],
      ...filas.map((a) => [a.nombre, a.direccion, a.es_principal ? "Sí" : "No", a.permite_negativo ? "Sí" : "No", a.productos, Number(a.cantidad), Number(a.valor).toFixed(2), a.activo ? "Activo" : "Inactivo"]),
    ]);
  }

  const editando = formAbierto && formAbierto !== "nuevo" ? formAbierto : null;
  const lineasConfirmar = lineas.filter((l) => l.producto_id);

  return (
    <div className="content">
      <InventarioEncabezado
        titulo="Almacenes"
        sub="Bodegas y sucursales donde se guarda el inventario"
        acciones={
          <>
            <button type="button" className="btn btn-o" onClick={exportar} disabled={filas.length === 0}>
              Exportar
            </button>
            <button type="button" className="btn btn-o" onClick={() => (trAbierta ? setTrAbierta(false) : abrirTransferencia())} disabled={activos.length < 2}>
              {trAbierta ? "Cerrar transferencia" : "Transferir mercancía"}
            </button>
            <button type="button" className="btn btn-p" onClick={() => (formAbierto ? setFormAbierto(null) : abrirForm())}>
              {formAbierto ? "Cerrar formulario" : "+ Nuevo almacén"}
            </button>
          </>
        }
      />

      {formAbierto && (
        <section className="card" ref={formRef}>
          <div className="card-h">
            <h2>{editando ? `Editar ${editando.nombre}` : "Nuevo almacén"}</h2>
            <span className="hint">{editando ? "Los cambios aplican a las próximas operaciones" : "Podrá transferir mercancía entre almacenes"}</span>
          </div>
          <div className="card-b form-grid">
            <div className="fld">
              <label className="lbl" htmlFor="al-nombre">
                Nombre <span className="req">*</span>
              </label>
              <input id="al-nombre" className="inp" value={form.nombre} onChange={(e) => setCampo("nombre", e.target.value)} placeholder="Sucursal La Romana" />
            </div>
            <div className="fld" style={{ gridColumn: "span 2" }}>
              <label className="lbl" htmlFor="al-dir">Dirección</label>
              <input id="al-dir" className="inp" value={form.direccion} onChange={(e) => setCampo("direccion", e.target.value)} placeholder="C/ Padre Abreu 12, La Romana" />
            </div>
            <div className="fld-full">
              <Interruptor
                id="al-neg"
                etiqueta="Permitir existencia negativa"
                ayuda="Transferencias y ajustes de salida podrán dejar este almacén en negativo (por ejemplo, si factura antes de registrar la compra)."
                valor={form.permite_negativo}
                onCambiar={(v) => setCampo("permite_negativo", v)}
              />
            </div>
            {editando && (
              <>
                <div className="fld-full">
                  <Interruptor
                    id="al-principal"
                    etiqueta="Almacén principal"
                    ayuda={
                      editando.es_principal
                        ? "Es el almacén que se usa cuando una factura, compra o ajuste no indica otro. Para cambiarlo, marque otro almacén como principal."
                        : "Pasará a ser el almacén por defecto de facturas, compras y ajustes (el actual deja de serlo)."
                    }
                    valor={form.es_principal}
                    onCambiar={(v) => setCampo("es_principal", v)}
                    disabled={editando.es_principal}
                  />
                </div>
                <div className="fld-full">
                  <Interruptor
                    id="al-activo"
                    etiqueta="Activo"
                    ayuda="No se puede inactivar el principal ni un almacén con existencia."
                    valor={form.activo}
                    onCambiar={(v) => setCampo("activo", v)}
                    disabled={editando.es_principal}
                  />
                </div>
              </>
            )}
            {errorForm && (
              <div className="al al-d fld-full" role="alert">
                <IconAlerta />
                <span>
                  <b>No se pudo guardar el almacén.</b> {errorForm}
                </span>
              </div>
            )}
            <div className="row fld-full" style={{ justifyContent: "flex-end" }}>
              <button type="button" className="btn btn-o" onClick={() => setFormAbierto(null)}>
                Cancelar
              </button>
              <button type="button" className="btn btn-p" onClick={guardarAlmacen} disabled={guardando}>
                {guardando ? "Guardando…" : editando ? "Guardar cambios" : "Crear almacén"}
              </button>
            </div>
          </div>
        </section>
      )}

      {trAbierta && (
        <section className="card" ref={trRef}>
          <div className="card-h">
            <h2>Transferir mercancía</h2>
            <span className="hint">Se aplica en el acto: sale del origen y entra al destino al costo promedio</span>
          </div>
          <div className="card-b stack">
            <div className="inv-grid">
              <div className="fld">
                <label className="lbl" htmlFor="tr-origen">
                  Origen <span className="req">*</span>
                </label>
                <select
                  id="tr-origen"
                  className="inp"
                  value={origen}
                  onChange={(e) => {
                    setOrigen(e.target.value);
                    setLineas((prev) => prev.map((l) => ({ ...l, lote_id: "" })));
                    setErrorTr(null);
                  }}
                >
                  {activos.map((a) => (
                    <option key={a.id} value={a.id}>
                      {a.nombre}
                    </option>
                  ))}
                </select>
              </div>
              <div className="fld">
                <label className="lbl" htmlFor="tr-destino">
                  Destino <span className="req">*</span>
                </label>
                <select
                  id="tr-destino"
                  className="inp"
                  value={destino}
                  onChange={(e) => {
                    setDestino(e.target.value);
                    setErrorTr(null);
                  }}
                >
                  <option value="">Elija el destino…</option>
                  {activos
                    .filter((a) => a.id !== origen)
                    .map((a) => (
                      <option key={a.id} value={a.id}>
                        {a.nombre}
                      </option>
                    ))}
                </select>
              </div>
              <div className="fld" style={{ gridColumn: "span 2" }}>
                <label className="lbl" htmlFor="tr-notas">Notas</label>
                <input id="tr-notas" className="inp" value={notasTr} onChange={(e) => setNotasTr(e.target.value)} placeholder="Reposición semanal de la sucursal" />
              </div>
            </div>
            <div className="tw" style={{ border: "1px solid #E5E7EB", borderRadius: 10 }}>
              <table className="tbl inv-lineas" style={{ minWidth: 720 }}>
                <thead>
                  <tr>
                    <th style={{ width: "40%" }}>Producto</th>
                    <th className="num">En origen</th>
                    <th style={{ width: 120 }}>Cantidad</th>
                    <th style={{ width: "26%" }}>Lote</th>
                    <th style={{ width: 40 }} aria-label="Quitar" />
                  </tr>
                </thead>
                <tbody>
                  {lineas.map((l, i) => {
                    const p = productos.find((x) => x.id === l.producto_id);
                    const lote = l.lote_id ? lotesDe(l.producto_id).find((x) => x.id === l.lote_id) : undefined;
                    const disp = p ? (lote ? disponibleEnLote(lote) : Number(disponibles.get(p.id) ?? 0)) : null;
                    const falta = disp != null && Number(l.cantidad) > disp;
                    return (
                      <tr key={i}>
                        <td>
                          <select className="inp inp-sm" value={l.producto_id} onChange={(e) => setLinea(i, { producto_id: e.target.value, lote_id: "" })} aria-label={`Producto de la línea ${i + 1}`}>
                            <option value="">Elija un producto…</option>
                            {productos.map((x) => (
                              <option key={x.id} value={x.id}>
                                {etiquetaProducto(x)}
                              </option>
                            ))}
                          </select>
                        </td>
                        <td className="num">
                          <span className={falta ? "inv-stock bad" : "inv-stock"}>{disp == null ? "—" : formatoCantidad(disp)}</span>
                        </td>
                        <td>
                          <input
                            type="number"
                            min="0"
                            step="any"
                            className="inp inp-sm"
                            style={{ textAlign: "right", borderColor: falta ? "#F87171" : undefined }}
                            value={l.cantidad}
                            onChange={(e) => setLinea(i, { cantidad: e.target.value })}
                            aria-label={`Cantidad de la línea ${i + 1}`}
                          />
                          {falta && <div className="err-t" style={{ fontSize: 12, marginTop: 2 }}>Faltan {formatoCantidad(Number(l.cantidad) - (disp ?? 0))}</div>}
                        </td>
                        <td>
                          {p?.maneja_lotes ? (
                            <select className="inp inp-sm" value={l.lote_id} onChange={(e) => setLinea(i, { lote_id: e.target.value })} aria-label={`Lote de la línea ${i + 1}`}>
                              <option value="">Automático (FEFO)</option>
                              {lotesDe(l.producto_id).map((x) => (
                                <option key={x.id} value={x.id}>
                                  {x.numero} · {formatoCantidad(disponibleEnLote(x))} · vence {fechaLegible(x.fecha_vencimiento)}
                                </option>
                              ))}
                            </select>
                          ) : (
                            <span className="mut inv-stock">—</span>
                          )}
                        </td>
                        <td>
                          <button
                            type="button"
                            className="ib"
                            style={{ width: 30, height: 30 }}
                            onClick={() => setLineas((prev) => (prev.length <= 1 ? [lineaVacia()] : prev.filter((_, j) => j !== i)))}
                            aria-label={`Quitar línea ${i + 1}`}
                          >
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" aria-hidden="true">
                              <path d="M18 6 6 18M6 6l12 12" />
                            </svg>
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <div>
              <button type="button" className="btn btn-o btn-sm" onClick={() => setLineas((prev) => [...prev, lineaVacia()])}>
                + Agregar producto
              </button>
            </div>
            {errorTr != null && (
              <AvisoOperacion error={errorTr} titulo="No se pudo transferir." almacenNombre={nombreAlmacen} loteNumero={loteNumero} />
            )}
            <div className="row" style={{ justifyContent: "flex-end" }}>
              <button type="button" className="btn btn-o" onClick={() => setTrAbierta(false)}>
                Cancelar
              </button>
              <button type="button" className="btn btn-p" onClick={pedirConfirmacion} disabled={transfiriendo}>
                Transferir
              </button>
            </div>
          </div>
        </section>
      )}

      {error && <AvisoError titulo="No se pudieron cargar los almacenes." mensaje={error} onReintentar={() => void cargar()} />}

      <div className="grid-stats">
        <Estadistica k="Almacenes" v={cargando ? "—" : String(activos.length)} s={almacenes.length > activos.length ? `${almacenes.length - activos.length} inactivos` : "Activos"} />
        <Estadistica k="Valor del inventario (RD$) · total" v={cargando ? "—" : `RD$ ${formatoMonto(valorTotal)}`} s="Existencia × costo promedio" />
        <Estadistica
          k="Alertas de stock"
          v={alertas == null ? "—" : String(alertas)}
          s={valuacion ? `${valuacion.bajo_minimo} bajo el mínimo · ${valuacion.sin_existencia} sin existencia` : undefined}
        />
      </div>

      <section className="card">
        <div className="filters">
          <Buscador valor={q} onCambiar={setQ} placeholder="Buscar en Almacenes" />
        </div>
        {cargando && <Cargando />}
        {!cargando && !error && almacenes.length === 0 && (
          <Vacio titulo="No hay almacenes" texto="Cree su almacén principal para empezar a controlar existencias por ubicación.">
            <button type="button" className="btn btn-p btn-sm" onClick={() => abrirForm()}>
              Nuevo almacén
            </button>
          </Vacio>
        )}
        {!cargando && almacenes.length > 0 && filas.length === 0 && <SinResultados q={q} onLimpiar={() => setQ("")} />}
        {!cargando && filas.length > 0 && (
          <div className="tw">
            <table className="tbl" style={{ minWidth: 960 }}>
              <thead>
                <tr>
                  <th>Almacén</th>
                  <th>Dirección</th>
                  <th className="num">Productos</th>
                  <th className="num">Valor del inventario (RD$)</th>
                  <th>Estado</th>
                  <th style={{ textAlign: "right" }}>Acciones</th>
                </tr>
              </thead>
              <tbody>
                {filas.map((a) => (
                  <tr key={a.id} className="fila-link" onClick={() => verExistencias(a)} title="Ver las existencias del almacén">
                    <td>
                      <div className="row" style={{ gap: 8 }}>
                        <span className="strong">{a.nombre}</span>
                        {a.es_principal && <span className="chip chip-sm">Principal</span>}
                      </div>
                      {a.permite_negativo && <div className="hint">Permite existencia negativa</div>}
                    </td>
                    <td>{a.direccion || <span className="mut">—</span>}</td>
                    <td className="num">{a.productos.toLocaleString("en-US")}</td>
                    <td className="num" style={{ fontWeight: 600, color: "#111827" }}>
                      {formatoMonto(a.valor)}
                    </td>
                    <td>
                      <span className={a.activo ? "bd b-acep" : "bd b-borr"}>{a.activo ? "Activo" : "Inactivo"}</span>
                    </td>
                    <td onClick={(e) => e.stopPropagation()}>
                      <div className="fila-acc inv-acc">
                        <Link to={`/inventario/kardex?almacen=${a.id}`} className="btn btn-g btn-sm">
                          Kardex
                        </Link>
                        {a.activo && activos.length > 1 && (
                          <button type="button" className="btn btn-g btn-sm" onClick={() => abrirTransferencia(a)}>
                            Transferir
                          </button>
                        )}
                        <button type="button" className="btn btn-g btn-sm" onClick={() => abrirForm(a)}>
                          Editar
                        </button>
                        {!a.es_principal && (
                          <button
                            type="button"
                            className="btn btn-g btn-sm"
                            style={{ color: a.activo ? "#B91C1C" : "#374151" }}
                            onClick={() => (a.activo ? setInactivar(a) : void cambiarActivo(a))}
                          >
                            {a.activo ? "Inactivar" : "Activar"}
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {almacenVer && (
        <section className="card" ref={exRef}>
          <div className="card-h">
            <h2>Existencias · {almacenVer.nombre}</h2>
            <div className="row">
              <span className="hint">
                {almacenVer.productos} {almacenVer.productos === 1 ? "producto" : "productos"} · RD$ {formatoMonto(almacenVer.valor)}
              </span>
              <button type="button" className="btn btn-g btn-sm" onClick={() => setVerId(null)}>
                Cerrar
              </button>
            </div>
          </div>
          <div className="filters">
            <Buscador valor={qEx} onCambiar={setQEx} placeholder={`Buscar en ${almacenVer.nombre}`} />
          </div>
          {cargandoEx && <Cargando />}
          {!cargandoEx && errorEx && (
            <div className="card-b">
              <AvisoError titulo="No se pudieron cargar las existencias del almacén." mensaje={errorEx} onReintentar={cargarExistencias} />
            </div>
          )}
          {!cargandoEx && !errorEx && existencias.length === 0 && <Vacio titulo="Almacén vacío" texto="Este almacén no tiene existencia de ningún producto." />}
          {!cargandoEx && existencias.length > 0 && filasEx.length === 0 && <SinResultados q={qEx} onLimpiar={() => setQEx("")} />}
          {!cargandoEx && filasEx.length > 0 && (
            <div className="tw">
              <table className="tbl" style={{ minWidth: 760 }}>
                <thead>
                  <tr>
                    <th>Código</th>
                    <th>Producto</th>
                    <th className="num">Existencia</th>
                    <th className="num">Costo promedio</th>
                    <th className="num">Valor (RD$)</th>
                    <th style={{ textAlign: "right" }}>Acciones</th>
                  </tr>
                </thead>
                <tbody>
                  {filasEx.map((e) => {
                    const bajo = Number(e.stock_minimo) > 0 && Number(e.cantidad) <= Number(e.stock_minimo);
                    return (
                      <tr key={e.producto_id}>
                        <td className="mono">{e.sku}</td>
                        <td>
                          <div className="strong">{e.nombre}</div>
                          {e.categoria && <div className="hint">{e.categoria}</div>}
                        </td>
                        <td className="num" style={{ fontWeight: 600, color: bajo || Number(e.cantidad) < 0 ? "#B91C1C" : "#111827" }}>
                          {formatoCantidad(e.cantidad)} {e.unidad_medida ?? ""}
                        </td>
                        <td className="num">{formatoMonto(e.costo_promedio)}</td>
                        <td className="num">{formatoMonto(e.valor)}</td>
                        <td>
                          <div className="fila-acc">
                            <Link to={`/inventario/kardex?producto=${e.producto_id}&almacen=${almacenVer.id}`} className="btn btn-g btn-sm">
                              Kardex
                            </Link>
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
      )}

      {transferencias.length > 0 && (
        <section className="card">
          <div className="card-h">
            <h2>Transferencias recientes</h2>
            <span className="hint">Últimas {transferencias.length}</span>
          </div>
          <div className="tw">
            <table className="tbl" style={{ minWidth: 760 }}>
              <thead>
                <tr>
                  <th>N.º</th>
                  <th>Fecha</th>
                  <th>Origen → destino</th>
                  <th>Productos</th>
                  <th>Notas</th>
                </tr>
              </thead>
              <tbody>
                {transferencias.map((t) => (
                  <tr key={t.id}>
                    <td className="mono">{t.numero}</td>
                    <td className="nowrap">{fechaLegible(t.creado_en)}</td>
                    <td>
                      {nombreAlmacen(t.almacen_origen_id) ?? "—"} → {nombreAlmacen(t.almacen_destino_id) ?? "—"}
                    </td>
                    <td>{t.items.map((it) => `${it.producto_nombre} (${formatoCantidad(it.cantidad)})`).join(", ")}</td>
                    <td>{t.notas || <span className="mut">—</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {confirmarTr && (
        <Modal
          titulo="¿Transferir la mercancía?"
          onCerrar={() => setConfirmarTr(false)}
          ancho={560}
          pie={
            <>
              <button type="button" className="btn btn-o" onClick={() => setConfirmarTr(false)}>
                Cancelar
              </button>
              <button type="button" className="btn btn-p" onClick={transferir} disabled={transfiriendo}>
                {transfiriendo ? "Transfiriendo…" : "Sí, transferir"}
              </button>
            </>
          }
        >
          <span>
            Saldrá de <b style={{ color: "#111827" }}>{nombreAlmacen(origen)}</b> y entrará a <b style={{ color: "#111827" }}>{nombreAlmacen(destino)}</b> de inmediato. La
            transferencia no se puede deshacer; para revertirla haga otra en sentido contrario.
          </span>
          <ul style={{ margin: 0, paddingLeft: 18 }}>
            {lineasConfirmar.map((l, i) => {
              const p = productos.find((x) => x.id === l.producto_id);
              const lote = l.lote_id ? loteNumero(l.lote_id) : null;
              return (
                <li key={i}>
                  {formatoCantidad(l.cantidad)} {p?.unidad_medida ?? "uds"} · {p?.nombre}
                  {lote ? ` · lote ${lote}` : p?.maneja_lotes ? " · lotes por FEFO" : ""}
                </li>
              );
            })}
          </ul>
        </Modal>
      )}

      {inactivar && (
        <Modal
          titulo={`¿Inactivar ${inactivar.nombre}?`}
          onCerrar={() => setInactivar(null)}
          pie={
            <>
              <button type="button" className="btn btn-o" onClick={() => setInactivar(null)}>
                Cancelar
              </button>
              <button type="button" className="btn btn-d" onClick={() => void cambiarActivo(inactivar)}>
                Sí, inactivar
              </button>
            </>
          }
        >
          <span>No se podrá usar en facturas, compras, ajustes ni transferencias. Solo se puede inactivar un almacén sin existencia.</span>
        </Modal>
      )}

      {toast}
    </div>
  );
}
