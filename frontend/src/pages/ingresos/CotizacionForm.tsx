import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import { erpApi } from "../../lib/erpApi";
import type { Cliente, Cotizacion, CotizacionPayload, TasaItbis } from "../../lib/erpApi";
import { inventarioApi } from "../../lib/inventarioApi";
import { formatoMonto, formatoRnc } from "../../lib/marca";
import { useDatosEmpresa } from "../../lib/configuracionApi";
import type { Producto } from "../../lib/types";
import { IconAlerta } from "../../layout/icons";
import { EnviarCotizacion } from "./EnviarCotizacion";
import { AvisoError } from "./IngresosShell";
import { useAviso } from "./useAviso";
import { ESTADO_COTIZACION, fechaLegible, hoyIso, importeLinea, mensajeError, puedeFacturar, sumarDias, TASAS_ITBIS, totalesCotizacion } from "./ingresosUtil";

/** Días de validez por defecto de una cotización. */
const VALIDEZ_DIAS = 15;

interface Linea {
  producto_id: string | null;
  descripcion: string;
  cantidad: string;
  precio_unitario: string;
  descuento_pct: string;
  itbis: TasaItbis;
}

interface ClienteForm {
  id: string | null;
  nombre: string;
  rnc: string;
}

function lineaVacia(): Linea {
  return { producto_id: null, descripcion: "", cantidad: "1", precio_unitario: "", descuento_pct: "", itbis: "18" };
}

/** "0.00" -> "" y "10.50" -> "10.5", para que los campos numéricos se vean limpios. */
function numeroLimpio(v: string | null | undefined): string {
  const n = Number(v);
  return v == null || v === "" || !Number.isFinite(n) || n === 0 ? "" : String(n);
}

export function CotizacionForm() {
  const { id } = useParams<{ id: string }>();
  // Una instancia por cotización: al pasar de una a otra (o a «Nueva») el formulario empieza limpio.
  return <FormularioCotizacion key={id ?? "nueva"} id={id} />;
}

function FormularioCotizacion({ id }: { id?: string }) {
  const navigate = useNavigate();
  const location = useLocation();
  const [cot, setCot] = useState<Cotizacion | null>(null);
  const [cargando, setCargando] = useState(Boolean(id));
  const [errorCarga, setErrorCarga] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [cliente, setCliente] = useState<ClienteForm>({ id: null, nombre: "", rnc: "" });
  const [sugerencias, setSugerencias] = useState<Cliente[]>([]);
  const [buscando, setBuscando] = useState(false);
  const [pickerAbierto, setPickerAbierto] = useState(false);
  const [fecha, setFecha] = useState(hoyIso);
  const [vence, setVence] = useState(() => sumarDias(hoyIso(), VALIDEZ_DIAS));
  const [lineas, setLineas] = useState<Linea[]>([lineaVacia()]);
  const [notas, setNotas] = useState("");
  const [productos, setProductos] = useState<Producto[]>([]);
  const [procesando, setProcesando] = useState<"" | "guardar" | "facturar" | "estado">("");
  const [sucio, setSucio] = useState(false);
  const [enviar, setEnviar] = useState(false);
  const [aviso, mostrarAviso] = useAviso();
  const busquedaId = useRef(0);

  // "vencida" se guarda como abierta: se puede editar (p. ej. extender `vence`) o eliminar.
  const soloLectura = cot != null && cot.estado !== "abierta" && cot.estado !== "vencida";
  const totales = useMemo(() => totalesCotizacion(lineas), [lineas]);

  useEffect(() => {
    inventarioApi
      .listarProductos({ activo: true })
      .then(setProductos)
      .catch(() => setProductos([]));
  }, []);

  function aplicar(c: Cotizacion) {
    setCot(c);
    setCliente({ id: c.cliente_id, nombre: c.cliente_nombre, rnc: c.cliente_rnc ?? "" });
    setFecha(c.fecha);
    setVence(c.vence);
    setNotas(c.notas ?? "");
    setLineas(
      c.items.length
        ? c.items.map((i) => ({
            producto_id: i.producto_id,
            descripcion: i.descripcion,
            cantidad: numeroLimpio(i.cantidad) || "0",
            precio_unitario: String(Number(i.precio_unitario)),
            descuento_pct: numeroLimpio(i.descuento_pct),
            itbis: i.itbis,
          }))
        : [lineaVacia()],
    );
    setSucio(false);
  }

  useEffect(() => {
    const guardada = (location.state as { guardada?: { texto: string; detalle: string } } | null)?.guardada;
    if (!guardada) return;
    mostrarAviso(guardada.texto, { detalle: guardada.detalle });
    navigate(location.pathname, { replace: true, state: null });
  }, [location.state, location.pathname, mostrarAviso, navigate]);

  useEffect(() => {
    if (!id) return;
    setCargando(true);
    setErrorCarga(null);
    erpApi
      .obtenerCotizacion(id)
      .then(aplicar)
      .catch((err) => setErrorCarga(mensajeError(err)))
      .finally(() => setCargando(false));
  }, [id]);

  // Busca clientes registrados mientras se escribe (con una pequeña espera).
  useEffect(() => {
    const texto = cliente.nombre.trim();
    if (!pickerAbierto || cliente.id || texto.length < 2) {
      setSugerencias([]);
      return;
    }
    const n = ++busquedaId.current;
    setBuscando(true);
    const t = window.setTimeout(() => {
      erpApi
        .listarClientes({ q: texto, activo: true })
        .then((r) => n === busquedaId.current && setSugerencias(r.filter((c) => c.tipo !== "proveedor").slice(0, 8)))
        .catch(() => n === busquedaId.current && setSugerencias([]))
        .finally(() => n === busquedaId.current && setBuscando(false));
    }, 250);
    return () => window.clearTimeout(t);
  }, [cliente.nombre, cliente.id, pickerAbierto]);

  function cambiar() {
    setSucio(true);
    setError(null);
  }

  function elegirCliente(c: Cliente) {
    setCliente({ id: c.id, nombre: c.nombre, rnc: c.rnc ?? "" });
    setPickerAbierto(false);
    cambiar();
  }

  function cambiarFecha(nueva: string) {
    // la validez se mantiene: si vence era fecha + 15, se mueve con la fecha
    if (vence === sumarDias(fecha, VALIDEZ_DIAS) && nueva) setVence(sumarDias(nueva, VALIDEZ_DIAS));
    setFecha(nueva);
    cambiar();
  }

  function actualizarLinea(index: number, cambios: Partial<Linea>) {
    setLineas((prev) => prev.map((l, i) => (i === index ? { ...l, ...cambios } : l)));
    cambiar();
  }

  function elegirProducto(index: number, productoId: string) {
    const p = productos.find((x) => x.id === productoId);
    actualizarLinea(index, p ? { producto_id: p.id, descripcion: p.nombre, precio_unitario: String(Number(p.precio_venta)) } : { producto_id: null, descripcion: "" });
  }

  function agregarLinea() {
    setLineas((prev) => [...prev, lineaVacia()]);
    cambiar();
  }

  function quitarLinea(index: number) {
    setLineas((prev) => (prev.length <= 1 ? prev : prev.filter((_, i) => i !== index)));
    cambiar();
  }

  function validar(): string | null {
    if (!cliente.nombre.trim()) return "Indique el cliente: búsquelo entre sus contactos o escriba su nombre.";
    const rnc = cliente.rnc.replace(/\D/g, "");
    if (!cliente.id && rnc && rnc.length !== 9 && rnc.length !== 11) return "El RNC debe tener 9 dígitos y la cédula 11.";
    if (!fecha || !vence) return "Indique la fecha y hasta cuándo es válida la cotización.";
    if (vence < fecha) return "La fecha de vencimiento no puede ser anterior a la fecha de la cotización.";
    if (lineas.some((l) => !l.descripcion.trim())) return "Cada línea debe tener un producto o una descripción.";
    if (lineas.some((l) => !(Number(l.cantidad) > 0))) return "Las cantidades deben ser mayores que cero.";
    if (lineas.some((l) => l.precio_unitario === "" || Number(l.precio_unitario) < 0)) return "Indique el precio de cada línea.";
    if (lineas.some((l) => Number(l.descuento_pct) < 0 || Number(l.descuento_pct) > 100)) return "El descuento debe estar entre 0 y 100 %.";
    return null;
  }

  function payload(): CotizacionPayload {
    const rnc = cliente.rnc.replace(/\D/g, "");
    return {
      ...(cliente.id ? { cliente_id: cliente.id } : { cliente_id: null, cliente_nombre: cliente.nombre.trim(), cliente_rnc: rnc || null }),
      fecha,
      vence,
      items: lineas.map((l) => ({
        producto_id: l.producto_id,
        descripcion: l.descripcion.trim(),
        cantidad: String(Number(l.cantidad)),
        precio_unitario: (Number(l.precio_unitario) || 0).toFixed(4),
        descuento_pct: String(Number(l.descuento_pct) || 0),
        itbis: l.itbis,
      })),
      notas: notas.trim() || null,
    };
  }

  /** Crea o actualiza la cotización y devuelve la guardada. */
  async function guardar(): Promise<Cotizacion> {
    const guardada = cot ? await erpApi.actualizarCotizacion(cot.id, payload()) : await erpApi.crearCotizacion(payload());
    aplicar(guardada);
    return guardada;
  }

  async function onGuardar() {
    const problema = validar();
    if (problema) return setError(problema);
    setProcesando("guardar");
    try {
      const nueva = !cot;
      const c = await guardar();
      const msg = { texto: `Cotización ${c.numero} guardada`, detalle: `Total RD$ ${formatoMonto(c.total)} · válida hasta el ${fechaLegible(c.vence)}` };
      if (nueva) navigate(`/cotizaciones/${c.id}`, { replace: true, state: { guardada: msg } });
      else mostrarAviso(msg.texto, { detalle: msg.detalle });
    } catch (err) {
      setError(mensajeError(err));
    } finally {
      setProcesando("");
    }
  }

  async function onFacturar() {
    if (cot && !puedeFacturar(cot)) return;
    if (!soloLectura) {
      const problema = validar();
      if (problema) return setError(problema);
    }
    setProcesando("facturar");
    try {
      const c = !cot || (sucio && !soloLectura) ? await guardar() : cot;
      navigate(`/facturas/nueva?cotizacion=${c.id}`);
    } catch (err) {
      setError(mensajeError(err));
      setProcesando("");
    }
  }

  async function cambiarEstado(estado: "abierta" | "aceptada" | "rechazada") {
    if (!cot) return;
    setProcesando("estado");
    try {
      const c = await erpApi.cambiarEstadoCotizacion(cot.id, estado);
      aplicar(c);
      mostrarAviso(`${c.numero} marcada como ${ESTADO_COTIZACION[c.estado]?.label.toLowerCase() ?? c.estado}`);
    } catch (err) {
      mostrarAviso("No se pudo cambiar el estado", { detalle: mensajeError(err), error: true });
    } finally {
      setProcesando("");
    }
  }

  if (cargando) return <div className="content"><p className="mut">Cargando…</p></div>;
  if (errorCarga)
    return (
      <div className="content">
        <div className="ph">
          <div>
            <div className="crumb">
              <Link to="/cotizaciones">Cotizaciones</Link>
              <span>/</span>
              <span>Cotización</span>
            </div>
            <h1>Cotización</h1>
          </div>
        </div>
        <AvisoError titulo="No se pudo abrir la cotización." mensaje={errorCarga} />
        <div>
          <Link to="/cotizaciones" className="btn btn-o">
            Volver a cotizaciones
          </Link>
        </div>
      </div>
    );

  const estado = cot ? ESTADO_COTIZACION[cot.estado] ?? { label: cot.estado, badge: "bd b-borr" } : null;
  const ocupado = procesando !== "";

  return (
    <div className="content">
      <div className="ph no-imprimir">
        <div>
          <div className="crumb">
            <Link to="/cotizaciones">Cotizaciones</Link>
            <span>/</span>
            <span>{cot ? cot.numero : "Nueva"}</span>
          </div>
          <h1 className="row" style={{ gap: 12 }}>
            {cot ? `Cotización ${cot.numero}` : "Nueva cotización"}
            {estado && <span className={estado.badge}>{estado.label}</span>}
          </h1>
          <p>
            {cot
              ? `${cot.cliente_nombre} · válida hasta el ${fechaLegible(cot.vence)}`
              : `Propuesta comercial sin valor fiscal · válida por ${VALIDEZ_DIAS} días`}
          </p>
        </div>
        {cot && (
          <div className="row">
            {cot.estado === "abierta" && (
              <>
                <button type="button" className="btn btn-g" onClick={() => cambiarEstado("rechazada")} disabled={ocupado}>
                  Marcar rechazada
                </button>
                <button type="button" className="btn btn-o" onClick={() => cambiarEstado("aceptada")} disabled={ocupado}>
                  Marcar aceptada
                </button>
              </>
            )}
            {(cot.estado === "aceptada" || cot.estado === "rechazada") && (
              <button type="button" className="btn btn-o" onClick={() => cambiarEstado("abierta")} disabled={ocupado}>
                Reabrir
              </button>
            )}
            {cot.estado !== "facturada" && cot.estado !== "vencida" && (
              <button type="button" className="btn btn-o" onClick={() => setEnviar(true)}>
                Enviar
              </button>
            )}
          </div>
        )}
      </div>

      {cot?.estado === "vencida" && (
        <div className="al al-w no-imprimir">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" aria-hidden="true">
            <circle cx="12" cy="12" r="9" />
            <path d="M12 7v5l3 2" />
          </svg>
          <span>
            <b>Esta cotización venció el {fechaLegible(cot.vence)}.</b> Revise los precios y extienda la fecha de vencimiento para
            retomarla, o elimínela.
          </span>
        </div>
      )}

      {soloLectura && cot && (
        <div className="al al-i no-imprimir">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" aria-hidden="true">
            <circle cx="12" cy="12" r="9" />
            <path d="M12 11v5M12 8h.01" />
          </svg>
          <span>
            {cot.estado === "facturada" ? (
              <>
                <b>Esta cotización ya fue facturada.</b> No se puede modificar.{" "}
                {cot.factura_id && (
                  <Link to={`/facturas/${cot.factura_id}`} className="link">
                    Ver la factura
                  </Link>
                )}
              </>
            ) : (
              <>
                <b>Esta cotización está {estado?.label.toLowerCase()}.</b> No se puede modificar
                {cot.estado === "aceptada" ? "; puede facturarla o imprimirla." : ". Reábrala si necesita cambiarla."}
              </>
            )}
          </span>
        </div>
      )}

      <div className="no-imprimir" style={{ display: "flex", flexWrap: "wrap", gap: 16, alignItems: "flex-start" }}>
        <div style={{ flex: "2 1 620px", minWidth: 0, display: "flex", flexDirection: "column", gap: 16 }}>
          <section className="card">
            <div className="card-h">
              <h2>Cliente</h2>
              <span className="chip">{cliente.id ? "Cliente registrado" : cliente.nombre ? "Cliente ocasional" : "Busque o escriba el cliente"}</span>
            </div>
            <div className="card-b" style={{ display: "flex", flexDirection: "column", gap: 16 }}>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))", gap: 16 }}>
                <div className="fld">
                  <label className="lbl" htmlFor="cli">
                    Cliente <span className="req">*</span>
                  </label>
                  {cliente.id ? (
                    <div className="picker-sel">
                      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#15803D" strokeWidth={2.4} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                        <path d="M20 6 9 17l-5-5" />
                      </svg>
                      <b style={{ flex: 1, minWidth: 0 }}>{cliente.nombre}</b>
                      {!soloLectura && (
                        <button
                          type="button"
                          className="btn btn-g btn-sm"
                          onClick={() => {
                            setCliente({ id: null, nombre: "", rnc: "" });
                            setPickerAbierto(true);
                            cambiar();
                          }}
                        >
                          Cambiar
                        </button>
                      )}
                    </div>
                  ) : (
                    <div className="picker">
                      <input
                        id="cli"
                        className="inp"
                        value={cliente.nombre}
                        autoComplete="off"
                        disabled={soloLectura}
                        placeholder="Buscar por nombre o RNC, o escribir uno nuevo"
                        onFocus={() => setPickerAbierto(true)}
                        onBlur={() => window.setTimeout(() => setPickerAbierto(false), 150)}
                        onChange={(e) => {
                          setCliente({ ...cliente, nombre: e.target.value });
                          setPickerAbierto(true);
                          cambiar();
                        }}
                        aria-autocomplete="list"
                        aria-expanded={pickerAbierto && sugerencias.length > 0}
                      />
                      {pickerAbierto && cliente.nombre.trim().length >= 2 && (sugerencias.length > 0 || !buscando) && (
                        <div className="picker-list" role="listbox">
                          {sugerencias.map((c) => (
                            <button key={c.id} type="button" role="option" aria-selected={false} className="picker-i" onMouseDown={(e) => e.preventDefault()} onClick={() => elegirCliente(c)}>
                              <b>{c.nombre}</b>
                              <span className="hint mono">{c.rnc ? formatoRnc(c.rnc) : "Sin RNC"}</span>
                            </button>
                          ))}
                          {sugerencias.length === 0 && <div className="picker-i hint">Sin coincidencias en Contactos: se usará como cliente ocasional.</div>}
                        </div>
                      )}
                    </div>
                  )}
                </div>
                <div className="fld">
                  <label className="lbl" htmlFor="cli-rnc">RNC o cédula</label>
                  <input
                    id="cli-rnc"
                    className="inp mono"
                    value={cliente.id ? formatoRnc(cliente.rnc) || "—" : cliente.rnc}
                    disabled={soloLectura || Boolean(cliente.id)}
                    onChange={(e) => {
                      setCliente({ ...cliente, rnc: e.target.value.replace(/[^\d-]/g, "") });
                      cambiar();
                    }}
                    placeholder={soloLectura ? "—" : "opcional"}
                    inputMode="numeric"
                  />
                  {!cliente.id && cliente.nombre && !soloLectura && <span className="hint">Cliente ocasional: no se agrega a Contactos.</span>}
                </div>
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: 16 }}>
                <div className="fld">
                  <label className="lbl" htmlFor="cfecha">Fecha</label>
                  <input id="cfecha" type="date" className="inp" value={fecha} disabled={soloLectura} onChange={(e) => cambiarFecha(e.target.value)} />
                </div>
                <div className="fld">
                  <label className="lbl" htmlFor="cvence">Válida hasta</label>
                  <input
                    id="cvence"
                    type="date"
                    className="inp"
                    value={vence}
                    min={fecha}
                    disabled={soloLectura}
                    onChange={(e) => {
                      setVence(e.target.value);
                      cambiar();
                    }}
                  />
                </div>
                <div className="fld">
                  <label className="lbl" htmlFor="cnum">Número</label>
                  <input id="cnum" className="inp mono" value={cot?.numero ?? "Automático"} disabled />
                </div>
              </div>
            </div>
          </section>

          <section className="card">
            <div className="card-h">
              <h2>Productos y servicios</h2>
              {!soloLectura && (
                <button type="button" className="btn btn-o btn-sm" onClick={agregarLinea}>
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" aria-hidden="true">
                    <path d="M12 5v14M5 12h14" />
                  </svg>
                  Agregar línea
                </button>
              )}
            </div>
            <div className="tw">
              <table className="tbl tbl-lineas" style={{ minWidth: 680, tableLayout: "fixed" }}>
                <thead>
                  <tr>
                    <th>Producto</th>
                    <th style={{ width: 76 }}>Cantidad</th>
                    <th className="num" style={{ width: 100 }}>Precio</th>
                    <th className="num" style={{ width: 70 }}>Desc. %</th>
                    <th style={{ width: 96 }}>
                      <span className="tip" data-tip="18% tasa general · 16% tasa reducida (café, yogur, mantequilla, azúcar, entre otros) · Exento según el Código Tributario.">
                        ITBIS ⓘ
                      </span>
                    </th>
                    <th className="num" style={{ width: 112 }}>Importe (RD$)</th>
                    {!soloLectura && <th style={{ width: 46 }} aria-label="Quitar" />}
                  </tr>
                </thead>
                <tbody>
                  {lineas.map((l, idx) => {
                    const producto = productos.find((p) => p.id === l.producto_id);
                    return (
                      <tr key={idx}>
                        <td>
                          {soloLectura ? (
                            <>
                              <div className="strong">{l.descripcion}</div>
                              {producto && <div className="hint mono">{producto.sku}</div>}
                            </>
                          ) : (
                            <>
                              <select
                                className="inp inp-sm"
                                value={l.producto_id ?? ""}
                                onChange={(e) => elegirProducto(idx, e.target.value)}
                                aria-label={`Producto de la línea ${idx + 1}`}
                                style={{ fontWeight: 500 }}
                              >
                                <option value="">Servicio u otro (descripción libre)</option>
                                {l.producto_id && !producto && <option value={l.producto_id}>{l.descripcion}</option>}
                                {productos.map((p) => (
                                  <option key={p.id} value={p.id}>
                                    {p.nombre}
                                  </option>
                                ))}
                              </select>
                              {producto ? (
                                <div className="hint mono" style={{ marginTop: 4 }}>{producto.sku}</div>
                              ) : (
                                !l.producto_id && (
                                  <input
                                    className="inp inp-sm"
                                    style={{ marginTop: 6 }}
                                    value={l.descripcion}
                                    onChange={(e) => actualizarLinea(idx, { descripcion: e.target.value })}
                                    placeholder="Descripción del servicio o producto"
                                    aria-label={`Descripción de la línea ${idx + 1}`}
                                  />
                                )
                              )}
                            </>
                          )}
                        </td>
                        <td>
                          <input
                            type="number"
                            min="0"
                            step="any"
                            className="inp inp-sm"
                            value={l.cantidad}
                            disabled={soloLectura}
                            onChange={(e) => actualizarLinea(idx, { cantidad: e.target.value })}
                            aria-label={`Cantidad de la línea ${idx + 1}`}
                          />
                        </td>
                        <td className="num">
                          <input
                            type="number"
                            min="0"
                            step="any"
                            className="inp inp-sm"
                            style={{ textAlign: "right" }}
                            value={l.precio_unitario}
                            disabled={soloLectura}
                            onChange={(e) => actualizarLinea(idx, { precio_unitario: e.target.value })}
                            placeholder="0.00"
                            aria-label={`Precio de la línea ${idx + 1}`}
                          />
                        </td>
                        <td className="num">
                          <input
                            type="number"
                            min="0"
                            max="100"
                            step="any"
                            className="inp inp-sm"
                            style={{ textAlign: "right" }}
                            value={l.descuento_pct}
                            disabled={soloLectura}
                            onChange={(e) => actualizarLinea(idx, { descuento_pct: e.target.value })}
                            placeholder="—"
                            aria-label={`Descuento de la línea ${idx + 1}`}
                          />
                        </td>
                        <td>
                          <select
                            className="inp inp-sm"
                            value={l.itbis}
                            disabled={soloLectura}
                            onChange={(e) => actualizarLinea(idx, { itbis: e.target.value as TasaItbis })}
                            aria-label={`ITBIS de la línea ${idx + 1}`}
                          >
                            {TASAS_ITBIS.map((t) => (
                              <option key={t.value} value={t.value}>
                                {t.label}
                              </option>
                            ))}
                          </select>
                        </td>
                        <td className="num" style={{ fontWeight: 600, color: "#111827" }}>
                          {formatoMonto(importeLinea(l))}
                        </td>
                        {!soloLectura && (
                          <td>
                            <button
                              type="button"
                              className="ib"
                              style={{ width: 30, height: 30 }}
                              onClick={() => quitarLinea(idx)}
                              disabled={lineas.length <= 1}
                              aria-label={`Quitar línea ${idx + 1}`}
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
            {productos.length === 0 && !soloLectura && (
              <div style={{ padding: "0 20px 16px" }}>
                <span className="hint">
                  No hay productos en el catálogo. Puede cotizar servicios con descripción libre o{" "}
                  <Link to="/inventario/productos/nuevo">crear un producto</Link>.
                </span>
              </div>
            )}
          </section>

          <section className="card">
            <div className="card-h">
              <h2>Notas y condiciones</h2>
              <span className="hint">Aparecen en la cotización impresa</span>
            </div>
            <div className="card-b">
              <textarea
                className="inp"
                rows={3}
                value={notas}
                disabled={soloLectura}
                onChange={(e) => {
                  setNotas(e.target.value);
                  cambiar();
                }}
                placeholder="Ej.: Precios sujetos a disponibilidad. Entrega en 5 días laborables. Pago 50% por adelantado."
                aria-label="Notas y condiciones"
              />
            </div>
          </section>
        </div>

        <div style={{ flex: "1 1 320px", minWidth: 0, display: "flex", flexDirection: "column", gap: 16 }}>
          <section className="card">
            <div className="card-h">
              <h2>Totales</h2>
              <span className="chip mono">{cot?.numero ?? "Nueva"}</span>
            </div>
            <dl className="kv card-b" style={{ gridTemplateColumns: "1fr auto" }}>
              <dt>Subtotal</dt>
              <dd className="num">{formatoMonto(totales.subtotal)}</dd>
              <dt>Descuentos</dt>
              <dd className="num">-{formatoMonto(totales.descuentos)}</dd>
              <dt>ITBIS 18%</dt>
              <dd className="num">{formatoMonto(totales.i18)}</dd>
              <dt>ITBIS 16%</dt>
              <dd className="num">{formatoMonto(totales.i16)}</dd>
              <dt style={{ fontSize: 15, fontWeight: 650, color: "#111827", borderTop: "1px solid #E5E7EB", paddingTop: 12 }}>Total</dt>
              <dd className="num" style={{ fontSize: 20, fontWeight: 700, borderTop: "1px solid #E5E7EB", paddingTop: 10 }}>
                RD$ {formatoMonto(totales.total)}
              </dd>
            </dl>
          </section>

          {error && (
            <div className="al al-d" role="alert">
              <IconAlerta />
              <span>
                <b>{error}</b>
              </span>
            </div>
          )}

          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {!soloLectura && (
              <button type="button" className="btn btn-p" onClick={onGuardar} disabled={ocupado} style={{ height: 46 }}>
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z" />
                  <path d="M17 21v-8H7v8M7 3v5h8" />
                </svg>
                {procesando === "guardar" ? "Guardando…" : "Guardar cotización"}
              </button>
            )}
            {(!cot || puedeFacturar(cot)) && (
              <button type="button" className={soloLectura ? "btn btn-p" : "btn btn-o"} onClick={onFacturar} disabled={ocupado} style={soloLectura ? { height: 46 } : undefined}>
                {procesando === "facturar" ? "Preparando factura…" : "Facturar"}
              </button>
            )}
            <button type="button" className="btn btn-g" onClick={() => window.print()}>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M6 9V3h12v6M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2" />
                <path d="M6 14h12v7H6z" />
              </svg>
              Imprimir
            </button>
            <span className="hint" style={{ textAlign: "center" }}>
              Las cotizaciones no tienen valor fiscal ni se envían a la DGII.
            </span>
          </div>
        </div>
      </div>

      <CotizacionImpresa cot={cot} cliente={cliente} fecha={fecha} vence={vence} lineas={lineas} notas={notas} totales={totales} />

      {enviar && cot && <EnviarCotizacion cotizacion={cot} onCerrar={() => setEnviar(false)} />}
      {aviso}
    </div>
  );
}

/** Versión para imprimir o guardar como PDF (oculta en pantalla). */
function CotizacionImpresa({
  cot,
  cliente,
  fecha,
  vence,
  lineas,
  notas,
  totales,
}: {
  cot: Cotizacion | null;
  cliente: ClienteForm;
  fecha: string;
  vence: string;
  lineas: Linea[];
  notas: string;
  totales: ReturnType<typeof totalesCotizacion>;
}) {
  const miEmpresa = useDatosEmpresa();
  return (
    <div className="solo-impresion">
      <div className="imp-h">
        <div>
          <h1>{miEmpresa.nombre}</h1>
          {miEmpresa.rnc && <div>RNC {formatoRnc(miEmpresa.rnc)}</div>}
        </div>
        <div style={{ textAlign: "right" }}>
          <h1>Cotización</h1>
          <div className="mono">{cot?.numero ?? "Borrador"}</div>
        </div>
      </div>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 24 }}>
        <div>
          <div style={{ color: "#6B7280", fontSize: 11.5 }}>Cliente</div>
          <b>{cliente.nombre || "—"}</b>
          {cliente.rnc && <div>RNC/Cédula {formatoRnc(cliente.rnc)}</div>}
        </div>
        <div style={{ textAlign: "right" }}>
          <div>Fecha: {fechaLegible(fecha)}</div>
          <div>Válida hasta: {fechaLegible(vence)}</div>
        </div>
      </div>
      <table className="imp-tbl">
        <thead>
          <tr>
            <th>Descripción</th>
            <th style={{ textAlign: "right" }}>Cantidad</th>
            <th style={{ textAlign: "right" }}>Precio</th>
            <th style={{ textAlign: "right" }}>Desc.</th>
            <th>ITBIS</th>
            <th style={{ textAlign: "right" }}>Importe (RD$)</th>
          </tr>
        </thead>
        <tbody>
          {lineas.map((l, i) => (
            <tr key={i}>
              <td>{l.descripcion}</td>
              <td style={{ textAlign: "right" }}>{l.cantidad}</td>
              <td style={{ textAlign: "right" }}>{formatoMonto(l.precio_unitario)}</td>
              <td style={{ textAlign: "right" }}>{Number(l.descuento_pct) ? `${l.descuento_pct}%` : "—"}</td>
              <td>{TASAS_ITBIS.find((t) => t.value === l.itbis)?.label}</td>
              <td style={{ textAlign: "right" }}>{formatoMonto(importeLinea(l))}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="imp-tot">
        <span>Subtotal</span>
        <span>{formatoMonto(totales.subtotal)}</span>
        <span>Descuentos</span>
        <span>-{formatoMonto(totales.descuentos)}</span>
        <span>ITBIS 18%</span>
        <span>{formatoMonto(totales.i18)}</span>
        <span>ITBIS 16%</span>
        <span>{formatoMonto(totales.i16)}</span>
        <span className="t">Total</span>
        <span className="t">RD$ {formatoMonto(totales.total)}</span>
      </div>
      {notas && <p style={{ whiteSpace: "pre-wrap", marginTop: 20 }}>{notas}</p>}
      <p style={{ marginTop: 28, color: "#6B7280", fontSize: 11.5 }}>Documento sin valor fiscal. Los precios incluyen ITBIS según se indica en cada línea.</p>
    </div>
  );
}
