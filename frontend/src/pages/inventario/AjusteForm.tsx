// Nuevo / ver ajuste de inventario: almacén, motivo y líneas de entrada o salida (con costo para las
// entradas y lote opcional). Borrador → aplicado → revertido (anulado).
import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import { IconAlerta } from "../../layout/icons";
import { inventarioApi } from "../../lib/inventarioApi";
import type { AjustePayload } from "../../lib/inventarioApi";
import { formatoMonto } from "../../lib/marca";
import type { Ajuste, Almacen, Lote, MotivoAjuste, Producto } from "../../lib/types";
import { AvisoError, Modal } from "../ingresos/IngresosShell";
import { useAviso } from "../ingresos/useAviso";
import { fechaLegible, hoyIso } from "../ingresos/ingresosUtil";
import { AvisoOperacion } from "./InventarioShell";
import "../ingresos/ingresos.css";
import "./inventario.css";
import { AYUDA_REVERTIR, decimalTexto, ESTADO_AJUSTE, etiquetaProducto, formatoCantidad, mensajeInventario, MOTIVOS_AJUSTE, soloBienes, valorAjuste } from "./inventarioUtil";

interface Linea {
  producto_id: string;
  tipo: "entrada" | "salida";
  cantidad: string;
  costo_unitario: string;
  lote_id: string;
}

const lineaVacia = (): Linea => ({ producto_id: "", tipo: "salida", cantidad: "", costo_unitario: "", lote_id: "" });

type Accion = "aplicar" | "anular" | "eliminar";

export function AjusteForm() {
  const { id } = useParams<{ id: string }>();
  // Una instancia por ajuste: al pasar de uno a otro (o a «Nuevo») el formulario empieza limpio.
  return <FormularioAjuste key={id ?? "nuevo"} id={id} />;
}

function FormularioAjuste({ id }: { id?: string }) {
  const navigate = useNavigate();
  const location = useLocation();
  const [ajuste, setAjuste] = useState<Ajuste | null>(null);
  const [almacenes, setAlmacenes] = useState<Almacen[]>([]);
  const [productos, setProductos] = useState<Producto[]>([]);
  const [cargando, setCargando] = useState(true);
  const [errorCarga, setErrorCarga] = useState<string | null>(null);

  const [almacenId, setAlmacenId] = useState("");
  const [fecha, setFecha] = useState(hoyIso);
  const [motivo, setMotivo] = useState<MotivoAjuste>("conteo_fisico");
  const [notas, setNotas] = useState("");
  const [lineas, setLineas] = useState<Linea[]>([lineaVacia()]);

  const [existencias, setExistencias] = useState<Map<string, string>>(new Map());
  const [lotes, setLotes] = useState<Record<string, Lote[]>>({});
  const [error, setError] = useState<{ err: unknown; titulo: string; ayuda?: string } | null>(null);
  const [confirmar, setConfirmar] = useState<Accion | null>(null);
  const [procesando, setProcesando] = useState<"" | "borrador" | Accion>("");
  const [toast, mostrarAviso] = useAviso();

  const cargar = useCallback(async () => {
    setCargando(true);
    setErrorCarga(null);
    const [alms, prods, aj] = await Promise.allSettled([
      inventarioApi.listarAlmacenes(),
      inventarioApi.listarProductos({ tipo: "bien" }),
      id ? inventarioApi.obtenerAjuste(id) : Promise.resolve(null),
    ]);
    const listaAlmacenes = alms.status === "fulfilled" ? alms.value : [];
    setAlmacenes(listaAlmacenes);
    setProductos(prods.status === "fulfilled" ? soloBienes(prods.value) : []);
    if (aj.status === "rejected") {
      setErrorCarga(mensajeInventario(aj.reason));
    } else if (aj.value) {
      const a = aj.value;
      setAjuste(a);
      setAlmacenId(a.almacen_id);
      setFecha(a.fecha);
      setMotivo(a.motivo);
      setNotas(a.notas ?? "");
      setLineas(
        a.items.map((it) => ({
          producto_id: it.producto_id,
          tipo: it.tipo,
          cantidad: String(Number(it.cantidad)),
          costo_unitario: it.costo_unitario != null ? String(Number(it.costo_unitario)) : "",
          lote_id: it.lote_id ?? "",
        })),
      );
    } else {
      setAlmacenId(listaAlmacenes.find((x) => x.es_principal)?.id ?? listaAlmacenes[0]?.id ?? "");
    }
    if (alms.status === "rejected" && !id) setErrorCarga(mensajeInventario(alms.reason));
    else if (prods.status === "rejected" && !id) setErrorCarga(mensajeInventario(prods.reason));
    setCargando(false);
  }, [id]);

  useEffect(() => {
    void cargar();
  }, [cargar]);

  useEffect(() => {
    const guardada = (location.state as { guardada?: { texto: string; detalle: string } } | null)?.guardada;
    if (!guardada) return;
    mostrarAviso(guardada.texto, { detalle: guardada.detalle });
    navigate(location.pathname, { replace: true, state: null });
  }, [location.state, location.pathname, mostrarAviso, navigate]);

  // Existencia de cada producto en el almacén elegido.
  const cargarExistencias = useCallback(() => {
    if (!almacenId) return;
    inventarioApi
      .existenciasAlmacen(almacenId, { incluir_cero: true })
      .then((ex) => setExistencias(new Map(ex.map((e) => [e.producto_id, e.cantidad]))))
      .catch(() => setExistencias(new Map()));
  }, [almacenId]);

  useEffect(cargarExistencias, [cargarExistencias]);

  // Lotes de los productos con lotes que aparecen en las líneas.
  useEffect(() => {
    const faltan = [...new Set(lineas.map((l) => l.producto_id))].filter((pid) => pid && productos.find((p) => p.id === pid)?.maneja_lotes && !lotes[pid]);
    if (faltan.length === 0) return;
    setLotes((prev) => ({ ...prev, ...Object.fromEntries(faltan.map((pid) => [pid, []])) }));
    for (const pid of faltan) {
      inventarioApi
        .listarLotes({ producto_id: pid })
        .then((ls) => setLotes((prev) => ({ ...prev, [pid]: ls })))
        .catch(() => undefined);
    }
  }, [lineas, productos, lotes]);

  const soloLectura = ajuste != null && ajuste.estado !== "borrador";
  const almacen = almacenes.find((a) => a.id === almacenId);
  const producto = useCallback((pid: string) => productos.find((p) => p.id === pid), [productos]);
  const costoPromedio = useCallback((pid: string) => Number(producto(pid)?.costo_promedio ?? 0), [producto]);
  const enLote = (l: Lote) => Number(l.existencias.find((e) => e.almacen_id === almacenId)?.cantidad ?? 0);

  function setLinea(i: number, cambios: Partial<Linea>) {
    setLineas((prev) => prev.map((l, j) => (j === i ? { ...l, ...cambios } : l)));
    setError(null);
  }

  const usadas = lineas.filter((l) => l.producto_id);
  const resumen = useMemo(() => {
    let entradas = 0;
    let salidas = 0;
    let valorEntradas = 0;
    let valorSalidas = 0;
    for (const l of usadas) {
      const cant = Number(l.cantidad) || 0;
      const costo = l.tipo === "entrada" && l.costo_unitario !== "" ? Number(l.costo_unitario) : costoPromedio(l.producto_id);
      if (l.tipo === "entrada") {
        entradas += cant;
        valorEntradas += cant * costo;
      } else {
        salidas += cant;
        valorSalidas += cant * costo;
      }
    }
    return { entradas, salidas, valorEntradas, valorSalidas };
  }, [usadas, costoPromedio]);

  function validar(): string | null {
    if (!almacenId) return "Elija el almacén del ajuste.";
    if (usadas.length === 0) return "Agregue al menos un producto al ajuste.";
    if (lineas.some((l) => !l.producto_id && (l.cantidad || l.costo_unitario))) return "Elija el producto de cada línea.";
    if (usadas.some((l) => !(Number(l.cantidad) > 0))) return "Las cantidades deben ser mayores que cero.";
    if (usadas.some((l) => l.tipo === "entrada" && l.costo_unitario !== "" && Number(l.costo_unitario) < 0)) return "El costo unitario no puede ser negativo.";
    return null;
  }

  function payload(): AjustePayload {
    return {
      almacen_id: almacenId || null,
      motivo,
      fecha: fecha || null,
      notas: notas.trim() || null,
      items: usadas.map((l) => ({
        producto_id: l.producto_id,
        tipo: l.tipo,
        cantidad: decimalTexto(l.cantidad),
        costo_unitario: l.tipo === "entrada" && l.costo_unitario !== "" ? decimalTexto(l.costo_unitario) : null,
        lote_id: l.lote_id || null,
      })),
    };
  }

  function pedir(accion: Accion) {
    setError(null);
    if (accion === "aplicar") {
      const problema = validar();
      if (problema) return setError({ err: new Error(problema), titulo: "Revise el ajuste." });
    }
    setConfirmar(accion);
  }

  async function guardarBorrador() {
    const problema = validar();
    if (problema) return setError({ err: new Error(problema), titulo: "Revise el ajuste." });
    setProcesando("borrador");
    setError(null);
    try {
      if (ajuste) {
        const a = await inventarioApi.actualizarAjuste(ajuste.id, payload());
        setAjuste(a);
        mostrarAviso("Borrador guardado", { detalle: `${a.numero} no afecta el inventario hasta que lo aplique.` });
      } else {
        const a = await inventarioApi.crearAjuste(payload());
        navigate(`/inventario/ajustes/${a.id}`, { replace: true, state: { guardada: { texto: "Borrador guardado", detalle: `${a.numero} no afecta el inventario hasta que lo aplique.` } } });
      }
    } catch (err) {
      setError({ err, titulo: "No se pudo guardar el borrador." });
    } finally {
      setProcesando("");
    }
  }

  async function ejecutar() {
    const accion = confirmar;
    if (!accion) return;
    setConfirmar(null);
    setProcesando(accion);
    setError(null);
    try {
      if (accion === "aplicar") {
        let a: Ajuste;
        if (ajuste) {
          await inventarioApi.actualizarAjuste(ajuste.id, payload());
          a = await inventarioApi.aplicarAjuste(ajuste.id);
        } else {
          a = await inventarioApi.crearAjuste(payload(), { aplicar: true });
        }
        const msg = { texto: `Ajuste ${a.numero} aplicado`, detalle: "Los movimientos ya están en el Kardex." };
        if (ajuste) {
          setAjuste(a);
          mostrarAviso(msg.texto, { detalle: msg.detalle });
          cargarExistencias();
        } else navigate(`/inventario/ajustes/${a.id}`, { replace: true, state: { guardada: msg } });
      } else if (accion === "anular" && ajuste) {
        const a = await inventarioApi.anularAjuste(ajuste.id);
        setAjuste(a);
        mostrarAviso(`Ajuste ${a.numero} revertido`, { detalle: "Se registraron los movimientos inversos en el Kardex." });
        cargarExistencias();
      } else if (accion === "eliminar" && ajuste) {
        await inventarioApi.eliminarAjuste(ajuste.id);
        navigate("/inventario/ajustes");
      }
    } catch (err) {
      const titulos = { aplicar: "No se pudo aplicar el ajuste.", anular: "No se pudo revertir el ajuste.", eliminar: "No se pudo eliminar el borrador." };
      setError({ err, titulo: titulos[accion], ayuda: accion === "anular" ? AYUDA_REVERTIR : undefined });
    } finally {
      setProcesando("");
    }
  }

  const loteNumero = (lid: string) => Object.values(lotes).flat().find((l) => l.id === lid)?.numero ?? ajuste?.items.find((i) => i.lote_id === lid)?.lote_numero ?? undefined;
  const nombreAlmacen = (aid: string) => almacenes.find((a) => a.id === aid)?.nombre;

  const migas = (
    <div className="crumb">
      <span>Inventario</span>
      <span>/</span>
      <Link to="/inventario/ajustes">Ajustes</Link>
      <span>/</span>
      <span>{ajuste ? ajuste.numero : id ? "Ajuste" : "Nuevo"}</span>
    </div>
  );

  if (cargando) return <div className="content"><p className="mut">Cargando…</p></div>;
  if (errorCarga)
    return (
      <div className="content">
        <div className="ph">
          <div>
            {migas}
            <h1>Ajuste de inventario</h1>
          </div>
        </div>
        <AvisoError titulo={id ? "No se pudo abrir el ajuste." : "No se pudieron cargar los almacenes y productos."} mensaje={errorCarga} onReintentar={() => void cargar()} />
        <div>
          <Link to="/inventario/ajustes" className="btn btn-o">
            Volver a ajustes
          </Link>
        </div>
      </div>
    );

  const estado = ajuste ? ESTADO_AJUSTE[ajuste.estado] : null;
  const ocupado = procesando !== "";
  const neto = resumen.valorEntradas - resumen.valorSalidas;
  const valorGuardado = ajuste && soloLectura ? valorAjuste(ajuste, costoPromedio) : null;

  return (
    <div className="content">
      <div className="ph">
        <div>
          {migas}
          <h1 className="row" style={{ gap: 12 }}>
            {ajuste ? `Ajuste ${ajuste.numero}` : "Nuevo ajuste de inventario"}
            {estado && <span className={estado.badge}>{estado.label}</span>}
          </h1>
          <p>
            {ajuste?.estado === "aplicado" && ajuste.aplicado_en
              ? `Aplicado el ${fechaLegible(ajuste.aplicado_en)} · afecta el Kardex y el costo del inventario`
              : ajuste?.estado === "anulado" && ajuste.anulado_en
                ? `Revertido el ${fechaLegible(ajuste.anulado_en)} con movimientos inversos`
                : "Afecta el Kardex y el costo del inventario al aplicarlo"}
          </p>
        </div>
        <div className="row">
          {ajuste?.estado === "borrador" && (
            <button type="button" className="btn btn-do" onClick={() => pedir("eliminar")} disabled={ocupado}>
              Eliminar borrador
            </button>
          )}
          {ajuste?.estado === "aplicado" && (
            <button type="button" className="btn btn-do" onClick={() => pedir("anular")} disabled={ocupado}>
              {procesando === "anular" ? "Revirtiendo…" : "Revertir"}
            </button>
          )}
        </div>
      </div>

      {soloLectura && (
        <div className="al al-i">
          <IconAlerta />
          <span>
            <b>Este ajuste está {estado?.label.toLowerCase()}.</b> No se puede modificar
            {ajuste?.estado === "aplicado" ? "; si fue un error, reviértalo y registre uno nuevo." : "."}
          </span>
        </div>
      )}

      <div className="inv-split">
        <div className="inv-main">
          <section className="card">
            <div className="card-h">
              <h2>Datos del ajuste</h2>
              <span className="chip mono">{ajuste?.numero ?? "AJ- automático"}</span>
            </div>
            <div className="card-b inv-grid">
              <div className="fld">
                <label className="lbl" htmlFor="aj-alm">
                  Almacén <span className="req">*</span>
                </label>
                <select
                  id="aj-alm"
                  className="inp"
                  value={almacenId}
                  disabled={soloLectura}
                  onChange={(e) => {
                    setAlmacenId(e.target.value);
                    setError(null);
                  }}
                >
                  {almacenes
                    .filter((a) => a.activo || a.id === almacenId)
                    .map((a) => (
                      <option key={a.id} value={a.id}>
                        {a.nombre}
                        {a.es_principal ? " (principal)" : ""}
                      </option>
                    ))}
                </select>
              </div>
              <div className="fld">
                <label className="lbl" htmlFor="aj-fecha">Fecha</label>
                <input id="aj-fecha" type="date" className="inp" value={fecha} max={hoyIso()} disabled={soloLectura} onChange={(e) => setFecha(e.target.value)} />
              </div>
              <div className="fld">
                <label className="lbl" htmlFor="aj-motivo">
                  Motivo <span className="req">*</span>
                </label>
                <select id="aj-motivo" className="inp" value={motivo} disabled={soloLectura} onChange={(e) => setMotivo(e.target.value as MotivoAjuste)}>
                  {MOTIVOS_AJUSTE.map((m) => (
                    <option key={m.value} value={m.value}>
                      {m.label}
                    </option>
                  ))}
                </select>
              </div>
              <div className="fld inv-full">
                <label className="lbl" htmlFor="aj-notas">Notas</label>
                <input id="aj-notas" className="inp" value={notas} disabled={soloLectura} onChange={(e) => setNotas(e.target.value)} placeholder={soloLectura ? "" : "Merma (sacos rotos), diferencia del conteo de octubre…"} />
              </div>
            </div>
          </section>
        </div>

        <div className="inv-side">
          <section className="card">
            <div className="card-h">
              <h2>Resumen</h2>
              <span className="hint">{almacen?.nombre}</span>
            </div>
            <dl className="kv card-b inv-tot">
              <dt>Entradas</dt>
              <dd className="num inv-entrada">+{formatoCantidad(resumen.entradas)}</dd>
              <dt>Valor de las entradas</dt>
              <dd className="num">RD$ {formatoMonto(resumen.valorEntradas)}</dd>
              <dt>Salidas</dt>
              <dd className="num inv-salida">−{formatoCantidad(resumen.salidas)}</dd>
              <dt>Valor de las salidas</dt>
              <dd className="num">RD$ {formatoMonto(resumen.valorSalidas)}</dd>
              <dt className="t">Efecto en el inventario</dt>
              <dd className="num t">RD$ {formatoMonto(valorGuardado ?? neto)}</dd>
            </dl>
            <div style={{ padding: "0 20px 16px" }}>
              <span className="hint">Las salidas se valoran al costo promedio vigente; las entradas sin costo, también.</span>
            </div>
          </section>
        </div>
      </div>

      <section className="card">
        <div className="card-h">
          <h2>Productos</h2>
          {!soloLectura && (
            <button type="button" className="btn btn-o btn-sm" onClick={() => setLineas((prev) => [...prev, lineaVacia()])}>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" aria-hidden="true">
                <path d="M12 5v14M5 12h14" />
              </svg>
              Agregar línea
            </button>
          )}
        </div>
        <div className="tw">
          <table className="tbl inv-lineas" style={{ minWidth: 900 }}>
            <thead>
              <tr>
                <th style={{ width: "30%" }}>Producto</th>
                <th className="num">Existencia</th>
                <th style={{ width: 120 }}>Tipo</th>
                <th style={{ width: 100 }}>Cantidad</th>
                <th style={{ width: 120 }}>Costo unitario</th>
                <th style={{ width: "20%" }}>Lote</th>
                <th className="num">Valor (RD$)</th>
                {!soloLectura && <th style={{ width: 40 }} aria-label="Quitar" />}
              </tr>
            </thead>
            <tbody>
              {lineas.map((l, i) => {
                const p = producto(l.producto_id);
                const item = soloLectura ? ajuste?.items[i] : undefined;
                const lotesP = lotes[l.producto_id] ?? [];
                const loteSel = lotesP.find((x) => x.id === l.lote_id);
                const disp = l.producto_id ? (loteSel ? enLote(loteSel) : Number(existencias.get(l.producto_id) ?? 0)) : null;
                const cant = Number(l.cantidad) || 0;
                const falta = !soloLectura && l.tipo === "salida" && disp != null && cant > disp && !almacen?.permite_negativo;
                const costo = l.tipo === "entrada" && l.costo_unitario !== "" ? Number(l.costo_unitario) : costoPromedio(l.producto_id);
                const opcionesLote = l.tipo === "salida" ? lotesP.filter((x) => enLote(x) > 0 || x.id === l.lote_id) : lotesP;
                return (
                  <tr key={i}>
                    <td>
                      {soloLectura ? (
                        <>
                          <div className="strong" style={{ lineHeight: "34px" }}>{item?.producto_nombre ?? p?.nombre}</div>
                          <div className="hint mono">{item?.producto_sku ?? p?.sku}</div>
                        </>
                      ) : (
                        <select className="inp inp-sm" value={l.producto_id} onChange={(e) => setLinea(i, { producto_id: e.target.value, lote_id: "" })} aria-label={`Producto de la línea ${i + 1}`}>
                          <option value="">Elija un producto…</option>
                          {productos
                            .filter((x) => x.activo || x.id === l.producto_id)
                            .map((x) => (
                              <option key={x.id} value={x.id}>
                                {etiquetaProducto(x)}
                              </option>
                            ))}
                        </select>
                      )}
                    </td>
                    <td className="num">
                      <span className={falta ? "inv-stock bad" : "inv-stock"}>{disp == null ? "—" : formatoCantidad(disp)}</span>
                      {falta && <div className="err-t" style={{ fontSize: 12, justifyContent: "flex-end" }}>Faltan {formatoCantidad(cant - (disp ?? 0))}</div>}
                    </td>
                    <td>
                      <select
                        className="inp inp-sm"
                        value={l.tipo}
                        disabled={soloLectura}
                        onChange={(e) => setLinea(i, { tipo: e.target.value as Linea["tipo"], lote_id: "" })}
                        aria-label={`Tipo de la línea ${i + 1}`}
                      >
                        <option value="salida">Salida</option>
                        <option value="entrada">Entrada</option>
                      </select>
                    </td>
                    <td>
                      <input
                        type="number"
                        min="0"
                        step="any"
                        className="inp inp-sm"
                        style={{ textAlign: "right", borderColor: falta ? "#F87171" : undefined }}
                        value={l.cantidad}
                        disabled={soloLectura}
                        onChange={(e) => setLinea(i, { cantidad: e.target.value })}
                        aria-label={`Cantidad de la línea ${i + 1}`}
                      />
                    </td>
                    <td>
                      <input
                        type="number"
                        min="0"
                        step="0.01"
                        className="inp inp-sm"
                        style={{ textAlign: "right" }}
                        value={l.tipo === "entrada" ? l.costo_unitario : ""}
                        disabled={soloLectura || l.tipo === "salida"}
                        onChange={(e) => setLinea(i, { costo_unitario: e.target.value })}
                        placeholder={p ? Number(p.costo_promedio).toFixed(2) : "—"}
                        title={l.tipo === "salida" ? "Las salidas salen al costo promedio vigente" : "Vacío = al costo promedio vigente"}
                        aria-label={`Costo unitario de la línea ${i + 1}`}
                      />
                    </td>
                    <td>
                      {soloLectura ? (
                        <span className="inv-stock">{item?.lote_numero ?? "—"}</span>
                      ) : p?.maneja_lotes ? (
                        <select className="inp inp-sm" value={l.lote_id} onChange={(e) => setLinea(i, { lote_id: e.target.value })} aria-label={`Lote de la línea ${i + 1}`}>
                          <option value="">{l.tipo === "salida" ? "Automático (FEFO)" : "Sin lote"}</option>
                          {opcionesLote.map((x) => (
                            <option key={x.id} value={x.id}>
                              {x.numero}
                              {l.tipo === "salida" ? ` · ${formatoCantidad(enLote(x))} disp.` : ""}
                              {x.fecha_vencimiento ? ` · vence ${fechaLegible(x.fecha_vencimiento)}` : ""}
                            </option>
                          ))}
                        </select>
                      ) : (
                        <span className="mut inv-stock">—</span>
                      )}
                    </td>
                    <td className="num" style={{ fontWeight: 600, color: "#111827", lineHeight: "34px" }}>
                      {l.producto_id ? <span className={l.tipo === "salida" ? "inv-salida" : "inv-entrada"}>{(l.tipo === "salida" ? "−" : "+") + formatoMonto(cant * costo)}</span> : "—"}
                    </td>
                    {!soloLectura && (
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
                    )}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        {!soloLectura && productos.length === 0 && (
          <div style={{ padding: "0 20px 16px" }}>
            <span className="hint">
              No hay productos con existencia en el catálogo. <Link to="/inventario/productos/nuevo">Cree un producto</Link> primero.
            </span>
          </div>
        )}
      </section>

      {error && (
        <AvisoOperacion
          error={error.err}
          titulo={error.titulo}
          ayuda={error.ayuda}
          almacenNombre={nombreAlmacen}
          loteNumero={loteNumero}
        />
      )}

      {!soloLectura && (
        <div className="row" style={{ justifyContent: "flex-end" }}>
          <Link to="/inventario/ajustes" className="btn btn-g">
            Cancelar
          </Link>
          <button type="button" className="btn btn-o" onClick={guardarBorrador} disabled={ocupado}>
            {procesando === "borrador" ? "Guardando…" : "Guardar borrador"}
          </button>
          <button type="button" className="btn btn-p" onClick={() => pedir("aplicar")} disabled={ocupado}>
            {procesando === "aplicar" ? "Aplicando…" : "Aplicar ajuste"}
          </button>
        </div>
      )}
      {soloLectura && ajuste && (
        <div className="row" style={{ justifyContent: "flex-end" }}>
          {[...new Set(ajuste.items.map((i) => i.producto_id))].slice(0, 1).map((pid) => (
            <Link key={pid} to={`/inventario/kardex?producto=${pid}&almacen=${ajuste.almacen_id}`} className="btn btn-o">
              Ver Kardex
            </Link>
          ))}
          <Link to="/inventario/ajustes" className="btn btn-p">
            Volver a ajustes
          </Link>
        </div>
      )}

      {confirmar && (
        <Modal
          titulo={confirmar === "aplicar" ? "¿Aplicar el ajuste?" : confirmar === "anular" ? "¿Revertir el ajuste?" : "¿Eliminar el borrador?"}
          onCerrar={() => setConfirmar(null)}
          pie={
            <>
              <button type="button" className="btn btn-o" onClick={() => setConfirmar(null)}>
                Cancelar
              </button>
              <button type="button" className={confirmar === "aplicar" ? "btn btn-p" : "btn btn-d"} onClick={ejecutar}>
                {confirmar === "aplicar" ? "Sí, aplicar" : confirmar === "anular" ? "Sí, revertir" : "Sí, eliminar"}
              </button>
            </>
          }
        >
          {confirmar === "aplicar" && (
            <span>
              Se moverá la existencia de <b style={{ color: "#111827" }}>{almacen?.nombre}</b>: {formatoCantidad(resumen.entradas)} de entrada y {formatoCantidad(resumen.salidas)} de salida
              (RD$ {formatoMonto(neto)}). Las salidas salen al costo promedio y las entradas lo recalculan. Después no se podrá editar.
            </span>
          )}
          {confirmar === "anular" && (
            <span>
              Se registrarán los movimientos inversos de <b className="mono" style={{ color: "#111827" }}>{ajuste?.numero}</b> en el mismo almacén y lote. Si la mercancía que entró
              ya salió, no se podrá revertir.
            </span>
          )}
          {confirmar === "eliminar" && (
            <span>
              El borrador <b className="mono" style={{ color: "#111827" }}>{ajuste?.numero}</b> no afectó el inventario; se eliminará por completo.
            </span>
          )}
        </Modal>
      )}

      {toast}
    </div>
  );
}
