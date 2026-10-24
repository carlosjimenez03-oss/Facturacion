// Productos (grupo Inventario de Modulo.dc.html): catálogo con existencias, alertas de stock y valuación.
import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { IconMas } from "../layout/icons";
import { inventarioApi } from "../lib/inventarioApi";
import { formatoMonto } from "../lib/marca";
import type { Almacen, EstadoStock, Producto, TipoProducto, Valuacion } from "../lib/types";
import { AvisoError, Buscador, Cargando, Estadistica, SinResultados, Vacio } from "../pages/ingresos/IngresosShell";
import { useAviso } from "../pages/ingresos/useAviso";
import { descargarCsv, hoyIso } from "../pages/ingresos/ingresosUtil";
import { InventarioEncabezado } from "../pages/inventario/InventarioShell";
import { cantidadConUnidad, estadoProducto, formatoCantidad, mensajeInventario, TASA_LABEL } from "../pages/inventario/inventarioUtil";

type FiltroStock = "" | "alerta" | EstadoStock;

export function ProductoList() {
  const navigate = useNavigate();
  const [productos, setProductos] = useState<Producto[]>([]);
  const [valuacion, setValuacion] = useState<Valuacion | null>(null);
  const [categorias, setCategorias] = useState<string[]>([]);
  const [almacenes, setAlmacenes] = useState<Almacen[]>([]);
  /** Existencia por producto en el almacén filtrado (null = todos los almacenes). */
  const [enAlmacen, setEnAlmacen] = useState<{ almacenId: string; cantidades: Map<string, string> } | null>(null);
  const [cargando, setCargando] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [categoria, setCategoria] = useState("");
  const [tipo, setTipo] = useState<"" | TipoProducto>("");
  const [stock, setStock] = useState<FiltroStock>("");
  const [almacenId, setAlmacenId] = useState("");
  const [procesando, setProcesando] = useState<string | null>(null);
  const [toast, mostrarAviso] = useAviso();

  const cargar = useCallback(async () => {
    setCargando(true);
    setError(null);
    const [lista, val, existencias] = await Promise.allSettled([
      inventarioApi.listarProductos({ q: q.trim() || undefined, categoria: categoria || undefined, tipo: tipo || undefined }),
      inventarioApi.valuacion(),
      almacenId ? inventarioApi.existenciasAlmacen(almacenId, { incluir_cero: true }) : Promise.resolve(null),
    ]);
    if (lista.status === "fulfilled") setProductos(lista.value);
    else {
      setProductos([]);
      setError(mensajeInventario(lista.reason));
    }
    setValuacion(val.status === "fulfilled" ? val.value : null);
    if (existencias.status === "fulfilled") {
      setEnAlmacen(existencias.value ? { almacenId, cantidades: new Map(existencias.value.map((e) => [e.producto_id, e.cantidad])) } : null);
    } else {
      setEnAlmacen(null);
      setError(mensajeInventario(existencias.reason));
    }
    setCargando(false);
  }, [q, categoria, tipo, almacenId]);

  useEffect(() => {
    const t = window.setTimeout(() => void cargar(), q ? 250 : 0);
    return () => window.clearTimeout(t);
  }, [cargar, q]);

  useEffect(() => {
    inventarioApi.listarCategorias().then(setCategorias).catch(() => setCategorias([]));
    inventarioApi.listarAlmacenes().then(setAlmacenes).catch(() => setAlmacenes([]));
  }, []);

  const filas = useMemo(
    () =>
      productos
        .filter((p) => !enAlmacen || enAlmacen.cantidades.has(p.id))
        .filter((p) => {
          if (!stock) return true;
          if (stock === "alerta") return p.estado_stock === "bajo_minimo" || p.estado_stock === "sin_existencia";
          return p.estado_stock === stock;
        }),
    [productos, enAlmacen, stock],
  );

  const hayFiltros = Boolean(q || categoria || tipo || stock || almacenId);
  const almacenSel = enAlmacen ? almacenes.find((a) => a.id === enAlmacen.almacenId) : undefined;
  const alertas = valuacion ? valuacion.bajo_minimo + valuacion.sin_existencia : null;

  function existencia(p: Producto): string {
    return enAlmacen ? (enAlmacen.cantidades.get(p.id) ?? "0") : p.stock_actual;
  }

  function limpiar() {
    setQ("");
    setCategoria("");
    setTipo("");
    setStock("");
    setAlmacenId("");
  }

  async function cambiarActivo(p: Producto) {
    setProcesando(p.id);
    try {
      await inventarioApi.actualizarProducto(p.id, {
        sku: p.sku,
        nombre: p.nombre,
        descripcion: p.descripcion,
        unidad_medida: p.unidad_medida,
        precio_venta: p.precio_venta,
        activo: !p.activo,
      });
      mostrarAviso(p.activo ? "Producto inactivado" : "Producto activado", {
        detalle: p.activo ? `${p.nombre} ya no aparecerá al facturar.` : `${p.nombre} vuelve a estar disponible para facturar.`,
      });
      void cargar();
    } catch (err) {
      mostrarAviso("No se pudo cambiar el estado del producto", { detalle: mensajeInventario(err), error: true });
    } finally {
      setProcesando(null);
    }
  }

  function exportar() {
    descargarCsv(`Productos_${hoyIso()}.csv`, [
      ["Código", "Producto", "Tipo", "Categoría", "Código de barras", "ITBIS", "Precio (RD$)", "Costo promedio (RD$)", almacenSel ? `Existencia · ${almacenSel.nombre}` : "Existencia", "Stock mínimo", "Estado"],
      ...filas.map((p) => [
        p.sku,
        p.nombre,
        p.tipo === "servicio" ? "Servicio" : "Bien",
        p.categoria,
        p.codigo_barras,
        TASA_LABEL[p.tasa_itbis] ?? p.tasa_itbis,
        Number(p.precio_venta).toFixed(2),
        Number(p.costo_promedio).toFixed(2),
        p.tipo === "servicio" ? "" : Number(existencia(p)),
        p.tipo === "servicio" ? "" : Number(p.stock_minimo),
        estadoProducto(p).label,
      ]),
    ]);
  }

  return (
    <div className="content">
      <InventarioEncabezado
        titulo="Productos"
        sub="Catálogo de productos y servicios con su tasa de ITBIS"
        acciones={
          <>
            <button type="button" className="btn btn-o" onClick={exportar} disabled={filas.length === 0}>
              Exportar
            </button>
            <Link className="btn btn-p" to="/inventario/productos/nuevo">
              <IconMas /> Nuevo producto
            </Link>
          </>
        }
      />

      {error && <AvisoError titulo="No se pudieron cargar los productos." mensaje={error} onReintentar={() => void cargar()} />}

      <div className="grid-stats">
        <Estadistica
          k="Productos"
          v={cargando ? "—" : String(valuacion?.productos ?? productos.filter((p) => p.tipo !== "servicio" && p.activo).length)}
          s={valuacion ? `${valuacion.con_existencia} con existencia · ${productos.filter((p) => p.tipo === "servicio").length} servicios` : undefined}
        />
        <Estadistica
          k="Valor del inventario"
          v={valuacion ? `RD$ ${formatoMonto(valuacion.valor_total)}` : "—"}
          s="Existencia × costo promedio"
        />
        <Estadistica
          k="Alertas de stock"
          v={alertas == null ? "—" : String(alertas)}
          s={valuacion ? `${valuacion.bajo_minimo} bajo el mínimo · ${valuacion.sin_existencia} sin existencia` : undefined}
        />
      </div>

      <section className="card">
        <div className="filters inv-filtros">
          <Buscador valor={q} onCambiar={setQ} placeholder="Buscar en Productos" />
          <select className="inp" value={categoria} onChange={(e) => setCategoria(e.target.value)} aria-label="Categoría">
            <option value="">Todas las categorías</option>
            {categorias.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
          <select className="inp" value={tipo} onChange={(e) => setTipo(e.target.value as "" | TipoProducto)} aria-label="Tipo">
            <option value="">Bienes y servicios</option>
            <option value="bien">Solo bienes</option>
            <option value="servicio">Solo servicios</option>
          </select>
          <select className="inp" value={stock} onChange={(e) => setStock(e.target.value as FiltroStock)} aria-label="Estado del stock">
            <option value="">Todo el stock</option>
            <option value="alerta">Con alerta de stock</option>
            <option value="bajo_minimo">Bajo stock</option>
            <option value="sin_existencia">Sin existencia</option>
            <option value="disponible">Disponible</option>
          </select>
          <select className="inp" value={almacenId} onChange={(e) => setAlmacenId(e.target.value)} aria-label="Almacén">
            <option value="">Todos los almacenes</option>
            {almacenes.map((a) => (
              <option key={a.id} value={a.id}>
                {a.nombre}
              </option>
            ))}
          </select>
        </div>

        {cargando && <Cargando />}
        {!cargando && !error && productos.length === 0 && !hayFiltros && (
          <Vacio titulo="No hay productos en el catálogo todavía" texto="Registre los productos y servicios que vende: el código se usa en facturas y en el Kardex.">
            <Link className="btn btn-p btn-sm" to="/inventario/productos/nuevo">
              Crear el primero
            </Link>
          </Vacio>
        )}
        {!cargando && !error && filas.length === 0 && (productos.length > 0 || hayFiltros) && <SinResultados q={q} onLimpiar={limpiar} />}
        {!cargando && filas.length > 0 && (
          <div className="tw">
            <table className="tbl" style={{ minWidth: 960 }}>
              <thead>
                <tr>
                  <th>Código</th>
                  <th>Producto</th>
                  <th>ITBIS</th>
                  <th className="num">Precio (RD$)</th>
                  <th className="num">{almacenSel ? `Existencia · ${almacenSel.nombre}` : "Existencia"}</th>
                  <th>Estado</th>
                  <th style={{ textAlign: "right" }}>Acciones</th>
                </tr>
              </thead>
              <tbody>
                {filas.map((p) => {
                  const est = estadoProducto(p);
                  const servicio = p.tipo === "servicio";
                  const alerta = p.estado_stock === "bajo_minimo" || p.estado_stock === "sin_existencia";
                  return (
                    <tr key={p.id} className="fila-link" onClick={() => navigate(`/inventario/productos/${p.id}/editar`)}>
                      <td className="nowrap">
                        <Link to={`/inventario/productos/${p.id}/editar`} className="link mono" onClick={(e) => e.stopPropagation()}>
                          {p.sku}
                        </Link>
                        {p.codigo_barras && <div className="hint mono">{p.codigo_barras}</div>}
                      </td>
                      <td>
                        <div className="strong">{p.nombre}</div>
                        <div className="hint">
                          {[servicio ? "Servicio" : p.categoria || "Sin categoría", p.maneja_lotes ? "Con lotes" : null].filter(Boolean).join(" · ")}
                        </div>
                      </td>
                      <td>{TASA_LABEL[p.tasa_itbis] ?? p.tasa_itbis}</td>
                      <td className="num">{formatoMonto(p.precio_venta)}</td>
                      <td className="num">
                        {servicio ? (
                          <span className="mut">—</span>
                        ) : (
                          <>
                            <span style={{ fontWeight: 600, color: alerta ? "#B91C1C" : "#111827" }}>{formatoCantidad(existencia(p))}</span>
                            {Number(p.stock_minimo) > 0 && <div className="hint">Mínimo {cantidadConUnidad(p.stock_minimo, p.unidad_medida)}</div>}
                          </>
                        )}
                      </td>
                      <td>
                        <span className={est.badge}>{est.label}</span>
                      </td>
                      <td onClick={(e) => e.stopPropagation()}>
                        <div className="fila-acc inv-acc">
                          {!servicio && (
                            <Link to={`/inventario/kardex?producto=${p.id}`} className="btn btn-g btn-sm">
                              Kardex
                            </Link>
                          )}
                          <button
                            type="button"
                            className="btn btn-g btn-sm"
                            style={{ color: p.activo ? "#B91C1C" : "#374151" }}
                            onClick={() => cambiarActivo(p)}
                            disabled={procesando === p.id}
                          >
                            {p.activo ? "Inactivar" : "Activar"}
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
