// Nuevo / editar producto: datos, precio e ITBIS, control de inventario y, al editar, existencias
// por almacén y valuación.
import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { IconAlerta } from "../layout/icons";
import { inventarioApi } from "../lib/inventarioApi";
import { formatoMonto } from "../lib/marca";
import type { Almacen, ExistenciasProducto, Producto, TasaItbis, TipoProducto } from "../lib/types";
import { Ayuda } from "../pages/compras/ComprasShell";
import { AvisoError } from "../pages/ingresos/IngresosShell";
import { fechaLegible } from "../pages/ingresos/ingresosUtil";
import { Interruptor } from "../pages/inventario/InventarioShell";
import {
  AYUDA_ITBIS,
  cantidadConUnidad,
  decimalTexto,
  ESTADO_LOTE,
  estadoProducto,
  formatoCantidad,
  mensajeInventario,
  TASAS_ITBIS,
} from "../pages/inventario/inventarioUtil";
import "../pages/ingresos/ingresos.css";
import "../pages/inventario/inventario.css";

interface FormProducto {
  tipo: TipoProducto;
  sku: string;
  nombre: string;
  codigo_barras: string;
  categoria: string;
  unidad_medida: string;
  descripcion: string;
  precio_venta: string;
  tasa_itbis: TasaItbis;
  stock_minimo: string;
  maneja_lotes: boolean;
  stock_inicial: string;
  costo_unitario_inicial: string;
  almacen_id: string;
  activo: boolean;
}

const FORM_VACIO: FormProducto = {
  tipo: "bien",
  sku: "",
  nombre: "",
  codigo_barras: "",
  categoria: "",
  unidad_medida: "",
  descripcion: "",
  precio_venta: "",
  tasa_itbis: "18",
  stock_minimo: "",
  maneja_lotes: false,
  stock_inicial: "",
  costo_unitario_inicial: "",
  almacen_id: "",
  activo: true,
};

/** "0.0000" -> "" y "50.0000" -> "50", para que los campos numéricos se vean limpios. */
function numeroLimpio(v: string | null | undefined): string {
  const n = Number(v);
  return v == null || v === "" || !Number.isFinite(n) || n === 0 ? "" : String(n);
}

function deProducto(p: Producto): FormProducto {
  return {
    ...FORM_VACIO,
    tipo: p.tipo,
    sku: p.sku,
    nombre: p.nombre,
    codigo_barras: p.codigo_barras ?? "",
    categoria: p.categoria ?? "",
    unidad_medida: p.unidad_medida ?? "",
    descripcion: p.descripcion ?? "",
    precio_venta: String(Number(p.precio_venta)),
    tasa_itbis: p.tasa_itbis,
    stock_minimo: numeroLimpio(p.stock_minimo),
    maneja_lotes: p.maneja_lotes,
    activo: p.activo,
  };
}

export function ProductoForm() {
  const { id } = useParams<{ id: string }>();
  // Una instancia por producto: al pasar de uno a otro (o a «Nuevo») el formulario empieza limpio.
  return <FormularioProducto key={id ?? "nuevo"} id={id} />;
}

function FormularioProducto({ id }: { id?: string }) {
  const navigate = useNavigate();
  const editando = Boolean(id);
  const [producto, setProducto] = useState<Producto | null>(null);
  const [existencias, setExistencias] = useState<ExistenciasProducto | null>(null);
  const [errorExistencias, setErrorExistencias] = useState<string | null>(null);
  const [form, setForm] = useState<FormProducto>(FORM_VACIO);
  const [categorias, setCategorias] = useState<string[]>([]);
  const [almacenes, setAlmacenes] = useState<Almacen[]>([]);
  const [cargando, setCargando] = useState(editando);
  const [errorCarga, setErrorCarga] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [guardando, setGuardando] = useState(false);

  function cargar() {
    if (!id) return;
    setCargando(true);
    setErrorCarga(null);
    inventarioApi
      .obtenerProducto(id)
      .then((p) => {
        setProducto(p);
        setForm(deProducto(p));
      })
      .catch((err) => setErrorCarga(mensajeInventario(err)))
      .finally(() => setCargando(false));
    inventarioApi
      .existenciasProducto(id)
      .then((e) => {
        setExistencias(e);
        setErrorExistencias(null);
      })
      .catch((err) => setErrorExistencias(mensajeInventario(err)));
  }

  useEffect(cargar, [id]);

  useEffect(() => {
    inventarioApi.listarCategorias().then(setCategorias).catch(() => setCategorias([]));
    inventarioApi
      .listarAlmacenes({ activo: true })
      .then(setAlmacenes)
      .catch(() => setAlmacenes([]));
  }, []);

  function set<K extends keyof FormProducto>(campo: K, valor: FormProducto[K]) {
    setForm((prev) => ({ ...prev, [campo]: valor }));
    setError(null);
  }

  const servicio = form.tipo === "servicio";
  const conExistencia = producto != null && Number(producto.stock_actual) !== 0;
  const stockInicial = Number(form.stock_inicial) || 0;

  function validar(): string | null {
    if (!form.sku.trim()) return "Escriba el código del producto.";
    if (!form.nombre.trim()) return "Escriba el nombre del producto.";
    if (form.precio_venta === "" || !(Number(form.precio_venta) >= 0)) return "Indique el precio de venta (puede ser 0).";
    if (!servicio && Number(form.stock_minimo) < 0) return "El stock mínimo no puede ser negativo.";
    if (!editando && !servicio && stockInicial < 0) return "El stock inicial no puede ser negativo.";
    if (editando && servicio && conExistencia) return "Para convertirlo en servicio, primero deje su existencia en cero con un ajuste de salida.";
    return null;
  }

  async function guardar() {
    const problema = validar();
    if (problema) return setError(problema);
    setGuardando(true);
    setError(null);
    const comunes = {
      sku: form.sku.trim(),
      nombre: form.nombre.trim(),
      descripcion: form.descripcion.trim() || null,
      unidad_medida: form.unidad_medida.trim() || null,
      precio_venta: decimalTexto(form.precio_venta, 2),
      tipo: form.tipo,
      tasa_itbis: form.tasa_itbis,
      codigo_barras: form.codigo_barras.trim() || null,
      categoria: form.categoria.trim() || null,
      stock_minimo: servicio ? "0" : decimalTexto(form.stock_minimo),
      maneja_lotes: servicio ? false : form.maneja_lotes,
    };
    try {
      if (editando && id) {
        await inventarioApi.actualizarProducto(id, { ...comunes, activo: form.activo });
      } else {
        await inventarioApi.crearProducto({
          ...comunes,
          stock_inicial: servicio ? "0" : decimalTexto(form.stock_inicial),
          ...(!servicio && stockInicial > 0
            ? { costo_unitario_inicial: form.costo_unitario_inicial === "" ? null : decimalTexto(form.costo_unitario_inicial), almacen_id: form.almacen_id || null }
            : {}),
        });
      }
      navigate("/inventario/productos");
    } catch (err) {
      setError(mensajeInventario(err));
    } finally {
      setGuardando(false);
    }
  }

  const migas = (
    <div className="crumb">
      <span>Inventario</span>
      <span>/</span>
      <Link to="/inventario/productos">Productos</Link>
      <span>/</span>
      <span>{editando ? (producto?.sku ?? "Editar") : "Nuevo"}</span>
    </div>
  );

  if (cargando) return <div className="content"><p className="mut">Cargando…</p></div>;
  if (errorCarga)
    return (
      <div className="content">
        <div className="ph">
          <div>
            {migas}
            <h1>Producto</h1>
          </div>
        </div>
        <AvisoError titulo="No se pudo abrir el producto." mensaje={errorCarga} onReintentar={cargar} />
        <div>
          <Link to="/inventario/productos" className="btn btn-o">
            Volver a productos
          </Link>
        </div>
      </div>
    );

  const estado = producto ? estadoProducto(producto) : null;
  const unidad = form.unidad_medida || producto?.unidad_medida;
  const valor = existencias ? Math.max(Number(existencias.stock_actual), 0) * Number(existencias.costo_promedio) : 0;

  return (
    <div className="content">
      <div className="ph">
        <div>
          {migas}
          <h1 className="row" style={{ gap: 12 }}>
            {producto ? producto.nombre : "Nuevo producto"}
            {estado && <span className={estado.badge}>{estado.label}</span>}
          </h1>
          <p>{editando ? "Los cambios aplican a las próximas facturas y movimientos" : "El código se usa en facturas y en el Kardex"}</p>
        </div>
        {producto && producto.tipo === "bien" && (
          <div className="row">
            <Link to={`/inventario/kardex?producto=${producto.id}`} className="btn btn-o">
              Ver Kardex
            </Link>
          </div>
        )}
      </div>

      <div className="inv-split">
        <div className="inv-main">
          <section className="card">
            <div className="card-h">
              <h2>Datos del producto</h2>
              <div className="seg" role="group" aria-label="Tipo de producto">
                <button type="button" className={servicio ? "" : "on"} onClick={() => set("tipo", "bien")} aria-pressed={!servicio}>
                  Bien
                </button>
                <button type="button" className={servicio ? "on" : ""} onClick={() => set("tipo", "servicio")} aria-pressed={servicio} disabled={conExistencia}>
                  Servicio
                </button>
              </div>
            </div>
            <div className="card-b inv-grid">
              {conExistencia && (
                <span className="hint inv-full">
                  Tiene existencia ({cantidadConUnidad(producto?.stock_actual, unidad)}): no se puede convertir en servicio mientras no quede en cero.
                </span>
              )}
              <div className="fld">
                <label className="lbl" htmlFor="pr-sku">
                  Código <span className="req">*</span>
                </label>
                <input id="pr-sku" className="inp mono" value={form.sku} onChange={(e) => set("sku", e.target.value.toUpperCase())} placeholder="ARE-001" />
              </div>
              <div className="fld" style={{ gridColumn: "span 2" }}>
                <label className="lbl" htmlFor="pr-nombre">
                  Nombre <span className="req">*</span>
                </label>
                <input id="pr-nombre" className="inp" value={form.nombre} onChange={(e) => set("nombre", e.target.value)} placeholder="Arena lavada (m³)" />
              </div>
              <div className="fld">
                <label className="lbl" htmlFor="pr-barras">Código de barras</label>
                <input id="pr-barras" className="inp mono" value={form.codigo_barras} onChange={(e) => set("codigo_barras", e.target.value)} placeholder="opcional" inputMode="numeric" />
              </div>
              <div className="fld">
                <label className="lbl" htmlFor="pr-cat">Categoría</label>
                <input id="pr-cat" className="inp" list="pr-categorias" value={form.categoria} onChange={(e) => set("categoria", e.target.value)} placeholder="Materiales de construcción" />
                <datalist id="pr-categorias">
                  {categorias.map((c) => (
                    <option key={c} value={c} />
                  ))}
                </datalist>
              </div>
              <div className="fld">
                <label className="lbl" htmlFor="pr-um">Unidad de medida</label>
                <input id="pr-um" className="inp" value={form.unidad_medida} onChange={(e) => set("unidad_medida", e.target.value)} placeholder={servicio ? "Servicio, hora…" : "Saco, galón, unidad…"} />
              </div>
              <div className="fld inv-full">
                <label className="lbl" htmlFor="pr-desc">Descripción</label>
                <textarea id="pr-desc" className="inp" rows={2} value={form.descripcion} onChange={(e) => set("descripcion", e.target.value)} placeholder="opcional" />
              </div>
            </div>
          </section>

          <section className="card">
            <div className="card-h">
              <h2>Precio e impuestos</h2>
            </div>
            <div className="card-b inv-grid">
              <div className="fld">
                <label className="lbl" htmlFor="pr-precio">
                  Precio (RD$) <span className="req">*</span>
                </label>
                <input
                  id="pr-precio"
                  type="number"
                  min="0"
                  step="0.01"
                  className="inp"
                  style={{ textAlign: "right" }}
                  value={form.precio_venta}
                  onChange={(e) => set("precio_venta", e.target.value)}
                  placeholder="0.00"
                />
              </div>
              <div className="fld">
                <span className="lbl">
                  <label htmlFor="pr-itbis">ITBIS</label>
                  <Ayuda texto={AYUDA_ITBIS} etiqueta="Tasas de ITBIS" />
                </span>
                <select id="pr-itbis" className="inp" value={form.tasa_itbis} onChange={(e) => set("tasa_itbis", e.target.value as TasaItbis)}>
                  {TASAS_ITBIS.map((t) => (
                    <option key={t.value} value={t.value}>
                      {t.label}
                    </option>
                  ))}
                </select>
              </div>
              {producto && (
                <div className="fld">
                  <span className="lbl">Costo promedio</span>
                  <input className="inp" style={{ textAlign: "right" }} value={`RD$ ${formatoMonto(producto.costo_promedio)}`} disabled aria-label="Costo promedio" />
                </div>
              )}
            </div>
          </section>

          {!servicio && (
            <section className="card">
              <div className="card-h">
                <h2>Control de inventario</h2>
                <span className="hint">Existencia, alertas y lotes</span>
              </div>
              <div className="card-b inv-grid">
                <div className="fld">
                  <label className="lbl" htmlFor="pr-min">Stock mínimo</label>
                  <input
                    id="pr-min"
                    type="number"
                    min="0"
                    step="any"
                    className="inp"
                    style={{ textAlign: "right" }}
                    value={form.stock_minimo}
                    onChange={(e) => set("stock_minimo", e.target.value)}
                    placeholder="0"
                  />
                  <span className="hint">Se marca «Bajo stock» cuando la existencia llega a este nivel.</span>
                </div>
                {!editando && (
                  <>
                    <div className="fld">
                      <label className="lbl" htmlFor="pr-ini">Stock inicial</label>
                      <input
                        id="pr-ini"
                        type="number"
                        min="0"
                        step="any"
                        className="inp"
                        style={{ textAlign: "right" }}
                        value={form.stock_inicial}
                        onChange={(e) => set("stock_inicial", e.target.value)}
                        placeholder="0"
                      />
                    </div>
                    <div className="fld">
                      <label className="lbl" htmlFor="pr-costo">Costo unitario inicial (RD$)</label>
                      <input
                        id="pr-costo"
                        type="number"
                        min="0"
                        step="0.01"
                        className="inp"
                        style={{ textAlign: "right" }}
                        value={form.costo_unitario_inicial}
                        onChange={(e) => set("costo_unitario_inicial", e.target.value)}
                        placeholder="0.00"
                        disabled={stockInicial <= 0}
                      />
                    </div>
                    <div className="fld">
                      <label className="lbl" htmlFor="pr-alm">Almacén del stock inicial</label>
                      <select id="pr-alm" className="inp" value={form.almacen_id} onChange={(e) => set("almacen_id", e.target.value)} disabled={stockInicial <= 0}>
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
                  </>
                )}
                <div className="inv-full">
                  <Interruptor
                    id="pr-lotes"
                    etiqueta="Maneja lotes y vencimientos"
                    ayuda="Las ventas sin lote indicado salen del lote que vence primero (FEFO). Registrar un lote lo activa automáticamente."
                    valor={form.maneja_lotes}
                    onCambiar={(v) => set("maneja_lotes", v)}
                  />
                </div>
                {!editando && form.maneja_lotes && stockInicial > 0 && (
                  <span className="hint inv-full">El stock inicial entra sin lote. Para controlarlo por lote, regístrelo luego en Lotes con su cantidad.</span>
                )}
              </div>
            </section>
          )}

          {editando && (
            <section className="card">
              <div className="card-b">
                <Interruptor
                  id="pr-activo"
                  etiqueta="Producto activo"
                  ayuda="Un producto inactivo no aparece al facturar ni en los selectores de inventario."
                  valor={form.activo}
                  onCambiar={(v) => set("activo", v)}
                />
              </div>
            </section>
          )}

          {error && (
            <div className="al al-d" role="alert">
              <IconAlerta />
              <span>
                <b>No se pudo guardar el producto.</b> {error}
              </span>
            </div>
          )}

          <div className="row" style={{ justifyContent: "flex-end" }}>
            <Link to="/inventario/productos" className="btn btn-o">
              Cancelar
            </Link>
            <button type="button" className="btn btn-p" onClick={guardar} disabled={guardando}>
              {guardando ? "Guardando…" : "Guardar producto"}
            </button>
          </div>
        </div>

        {producto && producto.tipo === "bien" && (
          <div className="inv-side">
            <section className="card">
              <div className="card-h">
                <h2>Valuación</h2>
                <span className="hint">Promedio ponderado</span>
              </div>
              <dl className="kv card-b inv-tot">
                <dt>Existencia total</dt>
                <dd className="num">{cantidadConUnidad(producto.stock_actual, unidad)}</dd>
                <dt>Stock mínimo</dt>
                <dd className="num">{Number(producto.stock_minimo) > 0 ? cantidadConUnidad(producto.stock_minimo, unidad) : "—"}</dd>
                <dt>Costo promedio</dt>
                <dd className="num">RD$ {formatoMonto(producto.costo_promedio)}</dd>
                <dt className="t">Valor del inventario</dt>
                <dd className="num t">RD$ {formatoMonto(existencias ? valor : Math.max(Number(producto.stock_actual), 0) * Number(producto.costo_promedio))}</dd>
              </dl>
              {producto.estado_stock === "bajo_minimo" && (
                <div style={{ padding: "0 20px 16px" }}>
                  <div className="al al-w">
                    <IconAlerta />
                    <span>
                      <b>Bajo el mínimo.</b> Quedan {cantidadConUnidad(producto.stock_actual, unidad)}; el mínimo es {cantidadConUnidad(producto.stock_minimo, unidad)}.
                    </span>
                  </div>
                </div>
              )}
              {producto.estado_stock === "sin_existencia" && (
                <div style={{ padding: "0 20px 16px" }}>
                  <div className="al al-d">
                    <IconAlerta />
                    <span>
                      <b>Sin existencia.</b> Registre una entrada de mercancía o un ajuste de entrada.
                    </span>
                  </div>
                </div>
              )}
            </section>

            <section className="card">
              <div className="card-h">
                <h2>Existencias por almacén</h2>
              </div>
              {errorExistencias && (
                <div className="card-b">
                  <AvisoError titulo="No se pudieron cargar las existencias." mensaje={errorExistencias} onReintentar={cargar} />
                </div>
              )}
              {!errorExistencias && !existencias && <p className="pad mut">Cargando…</p>}
              {existencias && existencias.almacenes.length === 0 && <p className="pad mut">Sin existencia en ningún almacén.</p>}
              {existencias && existencias.almacenes.length > 0 && (
                <div className="tw">
                  <table className="tbl">
                    <thead>
                      <tr>
                        <th>Almacén</th>
                        <th className="num">Cantidad</th>
                        <th className="num">Valor (RD$)</th>
                      </tr>
                    </thead>
                    <tbody>
                      {existencias.almacenes.map((a) => (
                        <tr key={a.almacen_id}>
                          <td>
                            <div className="strong">{a.almacen_nombre}</div>
                            {!a.activo && <div className="hint">Inactivo</div>}
                            {a.lotes.length > 0 && (
                              <div className="inv-lotes">
                                {a.lotes.map((l) => (
                                  <span key={l.lote_id} className={ESTADO_LOTE[l.estado]?.badge.replace("bd ", "bd nodot ") ?? "chip"} title={`Vence ${fechaLegible(l.fecha_vencimiento)}`}>
                                    {l.numero} · {formatoCantidad(l.cantidad)}
                                  </span>
                                ))}
                                {Number(a.sin_lote) !== 0 && <span className="chip">Sin lote · {formatoCantidad(a.sin_lote)}</span>}
                              </div>
                            )}
                          </td>
                          <td className="num" style={{ fontWeight: 600, color: Number(a.cantidad) < 0 ? "#B91C1C" : "#111827" }}>
                            {formatoCantidad(a.cantidad)}
                          </td>
                          <td className="num">{formatoMonto(a.valor)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>
          </div>
        )}
      </div>
    </div>
  );
}
