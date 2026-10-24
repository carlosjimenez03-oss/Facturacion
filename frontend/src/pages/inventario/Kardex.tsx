// Kardex valorizado (Kardex.dc.html): movimientos y costo del inventario de un producto, con el saldo
// acumulado fila a fila, vista por promedio ponderado o PEPS, exportación a Excel e impresión.
import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { inventarioApi } from "../../lib/inventarioApi";
import type { KardexParams } from "../../lib/inventarioApi";
import { EMPRESA_NOMBRE, formatoMonto } from "../../lib/marca";
import type { Almacen, Kardex, MetodoValuacion, Producto } from "../../lib/types";
import { Ayuda } from "../compras/ComprasShell";
import { AvisoError, Cargando, Vacio } from "../ingresos/IngresosShell";
import { useAviso } from "../ingresos/useAviso";
import { fechaLegible, hoyIso } from "../ingresos/ingresosUtil";
import { InventarioEncabezado } from "./InventarioShell";
import { descargarTexto, etiquetaProducto, formatoCantidad, mensajeInventario, nombreArchivo, soloBienes } from "./inventarioUtil";

const BOM = String.fromCharCode(0xfeff);

/** Color del tipo de movimiento, como en el prototipo. */
function claseConcepto(concepto: string, entrada: boolean): string {
  if (concepto.startsWith("Compra")) return "bd b-acep nodot";
  if (concepto.startsWith("Venta")) return "bd b-env nodot";
  if (concepto.startsWith("Devolución") || concepto.startsWith("Transferencia")) return "bd b-teal nodot";
  if (concepto.startsWith("Ajuste") || concepto.startsWith("Anulación")) return "bd b-pend nodot";
  if (concepto.startsWith("Stock inicial") || concepto.startsWith("Saldo")) return "bd b-borr nodot";
  return entrada ? "bd b-acep nodot" : "bd b-env nodot";
}

function conSigno(n: number): string {
  return (n > 0 ? "+" : n < 0 ? "−" : "") + formatoCantidad(Math.abs(n));
}

function primeroDelMes(): string {
  return `${hoyIso().slice(0, 7)}-01`;
}

export function KardexPage() {
  const [params, setParams] = useSearchParams();
  const [productos, setProductos] = useState<Producto[]>([]);
  const [almacenes, setAlmacenes] = useState<Almacen[]>([]);
  const [errorCatalogo, setErrorCatalogo] = useState<string | null>(null);
  const [cargandoCatalogo, setCargandoCatalogo] = useState(true);
  const [kardex, setKardex] = useState<Kardex | null>(null);
  const [cargando, setCargando] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [exportando, setExportando] = useState(false);
  const [toast, mostrarAviso] = useAviso();

  const productoId = params.get("producto") ?? "";
  const almacenId = params.get("almacen") ?? "";
  const desde = params.get("desde") ?? primeroDelMes();
  const hasta = params.get("hasta") ?? hoyIso();
  const tipo = (params.get("tipo") ?? "") as "" | "entrada" | "salida";
  const metodo: MetodoValuacion = params.get("metodo") === "peps" ? "peps" : "promedio";
  const peps = metodo === "peps";

  function cambiar(clave: string, valor: string) {
    setParams(
      (prev) => {
        const p = new URLSearchParams(prev);
        if (valor) p.set(clave, valor);
        else p.delete(clave);
        return p;
      },
      { replace: true },
    );
  }

  const cargarCatalogo = useCallback(async () => {
    setCargandoCatalogo(true);
    const [prods, alms] = await Promise.allSettled([inventarioApi.listarProductos({ tipo: "bien" }), inventarioApi.listarAlmacenes()]);
    if (prods.status === "fulfilled") {
      setProductos(soloBienes(prods.value));
      setErrorCatalogo(null);
    } else {
      setProductos([]);
      setErrorCatalogo(mensajeInventario(prods.reason));
    }
    setAlmacenes(alms.status === "fulfilled" ? alms.value : []);
    setCargandoCatalogo(false);
  }, []);

  useEffect(() => {
    void cargarCatalogo();
  }, [cargarCatalogo]);

  // Sin producto en la URL: el primero del catálogo (activo de preferencia).
  useEffect(() => {
    if (productoId || productos.length === 0) return;
    const primero = productos.find((p) => p.activo) ?? productos[0];
    cambiar("producto", primero.id);
  }, [productoId, productos]);

  const consulta: KardexParams | null = useMemo(
    () =>
      productoId
        ? { producto_id: productoId, almacen_id: almacenId || undefined, desde: desde || undefined, hasta: hasta || undefined, metodo, tipo: tipo || undefined }
        : null,
    [productoId, almacenId, desde, hasta, metodo, tipo],
  );

  const cargar = useCallback(() => {
    if (!consulta) return;
    setCargando(true);
    setError(null);
    inventarioApi
      .kardex(consulta)
      .then(setKardex)
      .catch((err) => {
        setKardex(null);
        setError(mensajeInventario(err));
      })
      .finally(() => setCargando(false));
  }, [consulta]);

  useEffect(cargar, [cargar]);

  const producto = productos.find((p) => p.id === productoId);
  const almacen = almacenes.find((a) => a.id === almacenId);
  const unidad = kardex?.unidad_medida?.trim() || producto?.unidad_medida?.trim() || "uds";
  const metodoTexto = peps ? "PEPS" : "Promedio ponderado";
  const finalCantidad = Number(kardex?.saldo_final.cantidad ?? 0);
  const minimo = Number(kardex?.stock_minimo ?? 0);
  const bajoMinimo = minimo > 0 && finalCantidad <= minimo;

  async function exportar() {
    if (!consulta || !kardex) return;
    setExportando(true);
    try {
      const csv = await inventarioApi.descargarKardexCsv(consulta);
      descargarTexto(nombreArchivo(`kardex_${kardex.producto_sku}_${peps ? "PEPS" : "promedio"}_${desde || "inicio"}_${hasta || hoyIso()}`) + ".csv", csv.startsWith(BOM) ? csv : BOM + csv);
      mostrarAviso("Kardex exportado", { detalle: `${kardex.producto_sku} · ${metodoTexto} · listo para abrir en Excel.` });
    } catch (err) {
      mostrarAviso("No se pudo exportar el Kardex", { detalle: mensajeInventario(err), error: true });
    } finally {
      setExportando(false);
    }
  }

  const periodo = `${desde ? fechaLegible(desde) : "Inicio"} – ${fechaLegible(hasta || hoyIso())}`;
  const sub = `Movimientos y costo del inventario por producto · ${almacen ? `almacén ${almacen.nombre}` : "todos los almacenes"}`;

  return (
    <div className="content inv-kardex">
      <InventarioEncabezado
        titulo="Kardex valorizado"
        sub={sub}
        pestanas={false}
        acciones={
          <>
            <button type="button" className="btn btn-o" onClick={() => window.print()} disabled={!kardex}>
              Imprimir
            </button>
            <button type="button" className="btn btn-o" onClick={exportar} disabled={!kardex || exportando}>
              {exportando ? "Exportando…" : "Exportar a Excel"}
            </button>
          </>
        }
      />

      <div className="inv-imp">
        <h1>Kardex valorizado · {kardex ? etiquetaProducto({ sku: kardex.producto_sku, nombre: kardex.producto_nombre }) : ""}</h1>
        <p>
          {EMPRESA_NOMBRE} · {almacen ? `Almacén ${almacen.nombre}` : "Todos los almacenes"} · {periodo} · {metodoTexto} · Montos en RD$
        </p>
      </div>

      {errorCatalogo && <AvisoError titulo="No se pudieron cargar los productos." mensaje={errorCatalogo} onReintentar={() => void cargarCatalogo()} />}

      <section className="card no-imprimir" style={{ padding: "16px 20px", display: "flex", flexWrap: "wrap", gap: 16, alignItems: "flex-end" }}>
        <div className="fld" style={{ flex: "2 1 320px" }}>
          <label className="lbl" htmlFor="kx-prod">Producto</label>
          <select id="kx-prod" className="inp" value={productoId} onChange={(e) => cambiar("producto", e.target.value)} disabled={productos.length === 0}>
            {productos.length === 0 && <option value="">{cargandoCatalogo ? "Cargando…" : "Sin productos"}</option>}
            {productos.map((p) => (
              <option key={p.id} value={p.id}>
                {etiquetaProducto(p)}
                {p.unidad_medida ? ` (${p.unidad_medida})` : ""}
                {p.activo ? "" : " · inactivo"}
              </option>
            ))}
          </select>
        </div>
        <div className="fld" style={{ flex: "1 1 180px" }}>
          <label className="lbl" htmlFor="kx-alm">Almacén</label>
          <select id="kx-alm" className="inp" value={almacenId} onChange={(e) => cambiar("almacen", e.target.value)}>
            <option value="">Todos los almacenes</option>
            {almacenes.map((a) => (
              <option key={a.id} value={a.id}>
                {a.nombre}
              </option>
            ))}
          </select>
        </div>
        <div className="fld" style={{ flex: "1 1 150px" }}>
          <label className="lbl" htmlFor="kx-desde">Desde</label>
          <input id="kx-desde" type="date" className="inp" value={desde} max={hasta || undefined} onChange={(e) => cambiar("desde", e.target.value)} />
        </div>
        <div className="fld" style={{ flex: "1 1 150px" }}>
          <label className="lbl" htmlFor="kx-hasta">Hasta</label>
          <input id="kx-hasta" type="date" className="inp" value={hasta} min={desde || undefined} onChange={(e) => cambiar("hasta", e.target.value)} />
        </div>
        <div className="fld" style={{ flex: "1 1 150px" }}>
          <label className="lbl" htmlFor="kx-tipo">Movimientos</label>
          <select id="kx-tipo" className="inp" value={tipo} onChange={(e) => cambiar("tipo", e.target.value)}>
            <option value="">Entradas y salidas</option>
            <option value="entrada">Solo entradas</option>
            <option value="salida">Solo salidas</option>
          </select>
        </div>
        <div className="fld">
          <span className="lbl">
            Método de valuación{" "}
            <Ayuda
              derecha
              etiqueta="Diferencia entre métodos"
              texto="Promedio ponderado: recalcula el costo con cada entrada. PEPS (primeras entradas, primeras salidas): las salidas se valoran con el costo de los lotes más antiguos."
            />
          </span>
          <div className="seg" role="group" aria-label="Método de valuación">
            <button type="button" className={peps ? "" : "on"} onClick={() => cambiar("metodo", "")} aria-pressed={!peps}>
              Promedio ponderado
            </button>
            <button type="button" className={peps ? "on" : ""} onClick={() => cambiar("metodo", "peps")} aria-pressed={peps}>
              PEPS
            </button>
          </div>
        </div>
      </section>

      {!cargandoCatalogo && !errorCatalogo && productos.length === 0 && (
        <section className="card">
          <Vacio titulo="No hay productos con inventario" texto="El Kardex muestra los movimientos de los bienes del catálogo (los servicios no llevan existencia).">
            <Link className="btn btn-p btn-sm" to="/inventario/productos/nuevo">
              Crear un producto
            </Link>
          </Vacio>
        </section>
      )}

      {error && <AvisoError titulo="No se pudo cargar el Kardex." mensaje={error} onReintentar={cargar} />}

      {(kardex || cargando) && (
        <div className="grid-stats">
          <div className="card stat">
            <span className="k">Existencia actual</span>
            <span className="v">{kardex ? `${formatoCantidad(finalCantidad)} ${unidad}` : "—"}</span>
            <span className="s">
              {bajoMinimo ? (
                <>
                  <span className="down">Bajo el mínimo</span> · mínimo {formatoCantidad(minimo)} {unidad}
                </>
              ) : minimo > 0 ? (
                `Mínimo ${formatoCantidad(minimo)} ${unidad}`
              ) : (
                `Al ${fechaLegible(hasta || hoyIso())}`
              )}
            </span>
          </div>
          <div className="card stat">
            <span className="k">{peps ? "Costo unitario (capas PEPS)" : "Costo promedio"}</span>
            <span className="v">{kardex ? `RD$ ${formatoMonto(kardex.saldo_final.costo_promedio)}` : "—"}</span>
            <span className="s">
              Por {unidad === "uds" ? "unidad" : unidad.replace(/s$/, "")} · {metodoTexto}
            </span>
          </div>
          <div className="card stat">
            <span className="k">Valor del inventario</span>
            <span className="v">{kardex ? `RD$ ${formatoMonto(kardex.saldo_final.valor)}` : "—"}</span>
            <span className="s">Existencia × costo</span>
          </div>
          <div className="card stat">
            <span className="k">Movimientos del período</span>
            <span className="v">{kardex ? `${formatoCantidad(kardex.entradas)} / ${formatoCantidad(kardex.salidas)}` : "—"}</span>
            <span className="s">Entradas / salidas ({unidad})</span>
          </div>
        </div>
      )}

      {(kardex || cargando) && (
        <section className="card">
          <div className="card-h">
            <h2>Movimientos · {metodoTexto}</h2>
            <span className="hint">
              {periodo} · Montos en RD$
              {!almacenId && " · sin transferencias entre almacenes"}
            </span>
          </div>
          {cargando && !kardex && <Cargando />}
          {kardex && (
            <div className="tw" style={{ opacity: cargando ? 0.6 : 1 }}>
              <table className="tbl" style={{ minWidth: 1040 }}>
                <thead>
                  <tr>
                    <th>Fecha</th>
                    <th>Documento</th>
                    <th>Tipo de movimiento</th>
                    <th className="num">Cantidad</th>
                    <th className="num">Costo unitario</th>
                    <th className="num">Costo del movimiento</th>
                    <th className="num">Costo promedio</th>
                    <th className="num">Existencia</th>
                    <th className="num">Valor total</th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <td className="mut nowrap">{desde ? fechaLegible(desde) : "—"}</td>
                    <td className="mono">—</td>
                    <td>
                      <span className="bd b-borr nodot">Saldo inicial</span>
                    </td>
                    <td className="num" style={{ fontWeight: 600 }}>
                      {formatoCantidad(kardex.saldo_inicial.cantidad)}
                    </td>
                    <td className="num">{formatoMonto(kardex.saldo_inicial.costo_promedio)}</td>
                    <td className="num">{formatoMonto(kardex.saldo_inicial.valor)}</td>
                    <td className="num">{formatoMonto(kardex.saldo_inicial.costo_promedio)}</td>
                    <td className="num" style={{ fontWeight: 600, color: "#111827" }}>
                      {formatoCantidad(kardex.saldo_inicial.cantidad)}
                    </td>
                    <td className="num" style={{ fontWeight: 600, color: "#111827" }}>
                      {formatoMonto(kardex.saldo_inicial.valor)}
                    </td>
                  </tr>
                  {kardex.filas.map((f) => {
                    const cant = Number(f.cantidad);
                    const detalle = [!almacenId && f.almacen_nombre, f.lote_numero && `Lote ${f.lote_numero}`].filter(Boolean).join(" · ");
                    return (
                      <tr key={f.movimiento_id}>
                        <td className="mut nowrap">{fechaLegible(f.fecha)}</td>
                        <td className="mono">{f.documento || "—"}</td>
                        <td>
                          <span className={claseConcepto(f.concepto, f.tipo === "entrada")}>{f.concepto}</span>
                          {detalle && <div className="hint">{detalle}</div>}
                        </td>
                        <td className="num" style={{ fontWeight: 600, color: cant < 0 ? "#B91C1C" : "#15803D" }}>
                          {conSigno(cant)}
                        </td>
                        <td className="num">{formatoMonto(f.costo_unitario)}</td>
                        <td className="num">{formatoMonto(Math.abs(Number(f.costo_total)))}</td>
                        <td className="num">{formatoMonto(f.costo_promedio)}</td>
                        <td className="num" style={{ fontWeight: 600, color: Number(f.saldo_cantidad) < 0 ? "#B91C1C" : "#111827" }}>
                          {formatoCantidad(f.saldo_cantidad)}
                        </td>
                        <td className="num" style={{ fontWeight: 600, color: "#111827" }}>
                          {formatoMonto(f.saldo_valor)}
                        </td>
                      </tr>
                    );
                  })}
                  {kardex.filas.length === 0 && (
                    <tr>
                      <td colSpan={9} className="mut" style={{ textAlign: "center", padding: "28px 14px" }}>
                        Sin movimientos {tipo === "entrada" ? "de entrada " : tipo === "salida" ? "de salida " : ""}en el período.
                      </td>
                    </tr>
                  )}
                </tbody>
                <tfoot>
                  <tr>
                    <td colSpan={7}>Saldo final al {fechaLegible(hasta || hoyIso())}</td>
                    <td className="num">{formatoCantidad(kardex.saldo_final.cantidad)}</td>
                    <td className="num">{formatoMonto(kardex.saldo_final.valor)}</td>
                  </tr>
                </tfoot>
              </table>
            </div>
          )}
        </section>
      )}

      {toast}
    </div>
  );
}
