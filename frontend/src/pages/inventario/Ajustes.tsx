// Ajustes de inventario (grupo Inventario de Modulo.dc.html): entradas y salidas por conteo físico,
// mermas o daños. Se crean en borrador, se aplican (generan movimientos) y se pueden revertir.
import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { IconMas } from "../../layout/icons";
import { inventarioApi } from "../../lib/inventarioApi";
import { formatoMonto } from "../../lib/marca";
import type { Ajuste, Almacen, EstadoAjuste, Producto } from "../../lib/types";
import { AvisoError, Buscador, Cargando, Estadistica, Modal, SinResultados, Vacio } from "../ingresos/IngresosShell";
import { useAviso } from "../ingresos/useAviso";
import { descargarCsv, fechaIso, fechaLegible, hoyIso, mesActualLegible } from "../ingresos/ingresosUtil";
import { AvisoOperacion, InventarioEncabezado } from "./InventarioShell";
import { AYUDA_REVERTIR, ESTADO_AJUSTE, formatoCantidad, mensajeInventario, MOTIVO_LABEL, valorAjuste } from "./inventarioUtil";

function tipoAjuste(a: Ajuste): string {
  const tipos = new Set(a.items.map((i) => i.tipo));
  if (tipos.size > 1) return "Entrada y salida";
  return tipos.has("entrada") ? "Entrada" : "Salida";
}

/** Cantidad con signo del ajuste de un solo producto (como la columna del prototipo). */
function cantidadNeta(a: Ajuste): number {
  return a.items.reduce((s, it) => s + (it.tipo === "entrada" ? 1 : -1) * (Number(it.cantidad) || 0), 0);
}

export function AjustesPage() {
  const navigate = useNavigate();
  const [ajustes, setAjustes] = useState<Ajuste[]>([]);
  const [productos, setProductos] = useState<Producto[]>([]);
  const [almacenes, setAlmacenes] = useState<Almacen[]>([]);
  const [cargando, setCargando] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [estado, setEstado] = useState<"" | EstadoAjuste>("");
  const [almacenId, setAlmacenId] = useState("");
  const [confirmar, setConfirmar] = useState<{ accion: "aplicar" | "anular" | "eliminar"; ajuste: Ajuste } | null>(null);
  const [errorAccion, setErrorAccion] = useState<unknown>(null);
  const [procesando, setProcesando] = useState(false);
  const [toast, mostrarAviso] = useAviso();

  const cargar = useCallback(async () => {
    setCargando(true);
    const [lista, prods, alms] = await Promise.allSettled([
      inventarioApi.listarAjustes({ estado: estado || undefined, almacen_id: almacenId || undefined }),
      inventarioApi.listarProductos({ tipo: "bien" }),
      inventarioApi.listarAlmacenes(),
    ]);
    if (lista.status === "fulfilled") {
      setAjustes(lista.value);
      setError(null);
    } else {
      setAjustes([]);
      setError(mensajeInventario(lista.reason));
    }
    if (prods.status === "fulfilled") setProductos(prods.value);
    if (alms.status === "fulfilled") setAlmacenes(alms.value);
    setCargando(false);
  }, [estado, almacenId]);

  useEffect(() => {
    void cargar();
  }, [cargar]);

  const costoPromedio = useCallback((id: string) => Number(productos.find((p) => p.id === id)?.costo_promedio ?? 0), [productos]);

  const filas = useMemo(() => {
    const texto = q.trim().toLowerCase();
    return ajustes.filter(
      (a) =>
        !texto ||
        [a.numero, a.almacen_nombre, MOTIVO_LABEL[a.motivo], a.notas, ESTADO_AJUSTE[a.estado]?.label, fechaLegible(a.fecha), ...a.items.map((i) => `${i.producto_sku} ${i.producto_nombre} ${i.lote_numero ?? ""}`)]
          .join(" ")
          .toLowerCase()
          .includes(texto),
    );
  }, [ajustes, q]);

  const mes = hoyIso().slice(0, 7);
  const aplicadosMes = ajustes.filter((a) => a.estado === "aplicado" && fechaIso(a.fecha).startsWith(mes));
  const borradores = ajustes.filter((a) => a.estado === "borrador");
  const hayFiltros = Boolean(q || estado || almacenId);

  async function ejecutar() {
    if (!confirmar) return;
    const { accion, ajuste } = confirmar;
    setProcesando(true);
    setErrorAccion(null);
    try {
      if (accion === "aplicar") {
        await inventarioApi.aplicarAjuste(ajuste.id);
        mostrarAviso(`Ajuste ${ajuste.numero} aplicado`, { detalle: "Los movimientos ya están en el Kardex." });
      } else if (accion === "anular") {
        await inventarioApi.anularAjuste(ajuste.id);
        mostrarAviso(`Ajuste ${ajuste.numero} revertido`, { detalle: "Se registraron los movimientos inversos en el Kardex." });
      } else {
        await inventarioApi.eliminarAjuste(ajuste.id);
        mostrarAviso(`Borrador ${ajuste.numero} eliminado`);
      }
      setConfirmar(null);
      void cargar();
    } catch (err) {
      setErrorAccion(err);
    } finally {
      setProcesando(false);
    }
  }

  function exportar() {
    descargarCsv(`Ajustes_de_inventario_${hoyIso()}.csv`, [
      ["N.º", "Fecha", "Almacén", "Producto", "Tipo", "Cantidad", "Lote", "Costo unitario", "Motivo", "Estado"],
      ...filas.flatMap((a) =>
        a.items.map((it) => [
          a.numero,
          fechaLegible(a.fecha),
          a.almacen_nombre,
          `${it.producto_sku} · ${it.producto_nombre}`,
          it.tipo === "entrada" ? "Entrada" : "Salida",
          (it.tipo === "entrada" ? 1 : -1) * Number(it.cantidad),
          it.lote_numero,
          it.costo_unitario != null ? Number(it.costo_unitario).toFixed(2) : "",
          MOTIVO_LABEL[a.motivo] ?? a.motivo,
          ESTADO_AJUSTE[a.estado]?.label ?? a.estado,
        ]),
      ),
    ]);
  }

  const textos = {
    aplicar: { titulo: "¿Aplicar el ajuste?", boton: "Sí, aplicar", clase: "btn btn-p" },
    anular: { titulo: "¿Revertir el ajuste?", boton: "Sí, revertir", clase: "btn btn-d" },
    eliminar: { titulo: "¿Eliminar el borrador?", boton: "Sí, eliminar", clase: "btn btn-d" },
  };

  return (
    <div className="content">
      <InventarioEncabezado
        titulo="Ajustes de inventario"
        sub="Entradas y salidas por conteo físico, mermas o daños"
        acciones={
          <>
            <button type="button" className="btn btn-o" onClick={exportar} disabled={filas.length === 0}>
              Exportar
            </button>
            <Link className="btn btn-p" to="/inventario/ajustes/nuevo">
              <IconMas /> Nuevo ajuste
            </Link>
          </>
        }
      />

      {error && <AvisoError titulo="No se pudieron cargar los ajustes." mensaje={error} onReintentar={() => void cargar()} />}

      <div className="grid-stats">
        <Estadistica k="Registros" v={cargando ? "—" : String(ajustes.length)} s={borradores.length ? `${borradores.length} en borrador` : undefined} />
        <Estadistica
          k="Valor ajustado (RD$) · aplicados"
          v={cargando ? "—" : `RD$ ${formatoMonto(ajustes.filter((a) => a.estado === "aplicado").reduce((s, a) => s + valorAjuste(a, costoPromedio), 0))}`}
          s="Entradas menos salidas"
        />
        <Estadistica k={`Período · ${mesActualLegible()}`} v={cargando ? "—" : String(aplicadosMes.length)} s={aplicadosMes.length === 1 ? "ajuste aplicado este mes" : "ajustes aplicados este mes"} />
      </div>

      <section className="card">
        <div className="filters inv-filtros">
          <Buscador valor={q} onCambiar={setQ} placeholder="Buscar en Ajustes de inventario" />
          <select className="inp" value={estado} onChange={(e) => setEstado(e.target.value as "" | EstadoAjuste)} aria-label="Estado">
            <option value="">Todos los estados</option>
            <option value="borrador">Borrador</option>
            <option value="aplicado">Aplicado</option>
            <option value="anulado">Revertido</option>
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
        {!cargando && !error && ajustes.length === 0 && !hayFiltros && (
          <Vacio titulo="No hay ajustes de inventario" texto="Registre aquí las diferencias del conteo físico, las mermas y los productos dañados o vencidos.">
            <Link className="btn btn-p btn-sm" to="/inventario/ajustes/nuevo">
              Nuevo ajuste
            </Link>
          </Vacio>
        )}
        {!cargando && !error && filas.length === 0 && (ajustes.length > 0 || hayFiltros) && (
          <SinResultados
            q={q}
            onLimpiar={() => {
              setQ("");
              setEstado("");
              setAlmacenId("");
            }}
          />
        )}
        {!cargando && filas.length > 0 && (
          <div className="tw">
            <table className="tbl" style={{ minWidth: 1080 }}>
              <thead>
                <tr>
                  <th>N.º</th>
                  <th>Fecha</th>
                  <th>Producto</th>
                  <th>Tipo</th>
                  <th className="num">Cantidad</th>
                  <th>Almacén</th>
                  <th>Motivo</th>
                  <th className="num">Valor (RD$)</th>
                  <th>Estado</th>
                  <th style={{ textAlign: "right" }}>Acciones</th>
                </tr>
              </thead>
              <tbody>
                {filas.map((a) => {
                  const est = ESTADO_AJUSTE[a.estado] ?? { label: a.estado, badge: "bd b-borr" };
                  const primero = a.items[0];
                  const unProducto = new Set(a.items.map((i) => i.producto_id)).size === 1;
                  const neta = cantidadNeta(a);
                  return (
                    <tr key={a.id} className="fila-link" onClick={() => navigate(`/inventario/ajustes/${a.id}`)}>
                      <td className="nowrap">
                        <Link to={`/inventario/ajustes/${a.id}`} className="link mono" onClick={(e) => e.stopPropagation()}>
                          {a.numero}
                        </Link>
                      </td>
                      <td className="nowrap">{fechaLegible(a.fecha)}</td>
                      <td>
                        <div className="strong">{primero?.producto_nombre ?? "—"}</div>
                        {a.items.length > 1 && (
                          <div className="hint">
                            {a.items.length} líneas{unProducto ? "" : ` · ${new Set(a.items.map((i) => i.producto_id)).size} productos`}
                          </div>
                        )}
                      </td>
                      <td>{tipoAjuste(a)}</td>
                      <td className="num">
                        {unProducto ? <span className={neta < 0 ? "inv-salida" : "inv-entrada"}>{(neta > 0 ? "+" : neta < 0 ? "−" : "") + formatoCantidad(Math.abs(neta))}</span> : <span className="mut">Varias</span>}
                      </td>
                      <td>{a.almacen_nombre}</td>
                      <td>
                        {MOTIVO_LABEL[a.motivo] ?? a.motivo}
                        {a.notas && <div className="hint">{a.notas}</div>}
                      </td>
                      <td className="num">{formatoMonto(valorAjuste(a, costoPromedio))}</td>
                      <td>
                        <span className={est.badge}>{est.label}</span>
                      </td>
                      <td onClick={(e) => e.stopPropagation()}>
                        <div className="fila-acc inv-acc">
                          {a.estado === "borrador" ? (
                            <>
                              <Link to={`/inventario/ajustes/${a.id}`} className="btn btn-g btn-sm">
                                Editar
                              </Link>
                              <button type="button" className="btn btn-g btn-sm" onClick={() => { setErrorAccion(null); setConfirmar({ accion: "aplicar", ajuste: a }); }}>
                                Aplicar
                              </button>
                              <button type="button" className="btn btn-g btn-sm" style={{ color: "#B91C1C" }} onClick={() => { setErrorAccion(null); setConfirmar({ accion: "eliminar", ajuste: a }); }}>
                                Eliminar
                              </button>
                            </>
                          ) : (
                            <>
                              {unProducto && primero && (
                                <Link to={`/inventario/kardex?producto=${primero.producto_id}&almacen=${a.almacen_id}`} className="btn btn-g btn-sm">
                                  Kardex
                                </Link>
                              )}
                              {a.estado === "aplicado" && (
                                <button type="button" className="btn btn-g btn-sm" style={{ color: "#B91C1C" }} onClick={() => { setErrorAccion(null); setConfirmar({ accion: "anular", ajuste: a }); }}>
                                  Revertir
                                </button>
                              )}
                            </>
                          )}
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

      {confirmar && (
        <Modal
          titulo={textos[confirmar.accion].titulo}
          onCerrar={() => setConfirmar(null)}
          ancho={560}
          pie={
            <>
              <button type="button" className="btn btn-o" onClick={() => setConfirmar(null)}>
                {errorAccion ? "Cerrar" : "Cancelar"}
              </button>
              {!errorAccion && (
                <button type="button" className={textos[confirmar.accion].clase} onClick={ejecutar} disabled={procesando}>
                  {procesando ? "Procesando…" : textos[confirmar.accion].boton}
                </button>
              )}
            </>
          }
        >
          <span>
            {confirmar.accion === "aplicar" && (
              <>
                El ajuste <b className="mono" style={{ color: "#111827" }}>{confirmar.ajuste.numero}</b> moverá la existencia de {confirmar.ajuste.almacen_nombre}: las salidas salen al costo
                promedio y las entradas lo recalculan. Ya no se podrá editar.
              </>
            )}
            {confirmar.accion === "anular" && (
              <>
                Se registrarán los movimientos inversos de <b className="mono" style={{ color: "#111827" }}>{confirmar.ajuste.numero}</b> en el mismo almacén y lote. Si la mercancía que
                entró ya salió, no se podrá revertir.
              </>
            )}
            {confirmar.accion === "eliminar" && (
              <>
                El borrador <b className="mono" style={{ color: "#111827" }}>{confirmar.ajuste.numero}</b> no afectó el inventario; se eliminará por completo.
              </>
            )}
          </span>
          {errorAccion != null && (
            <AvisoOperacion
              error={errorAccion}
              titulo={confirmar.accion === "anular" ? "No se pudo revertir el ajuste." : confirmar.accion === "aplicar" ? "No se pudo aplicar el ajuste." : "No se pudo eliminar el borrador."}
              ayuda={confirmar.accion === "anular" ? AYUDA_REVERTIR : undefined}
              almacenNombre={(id) => almacenes.find((x) => x.id === id)?.nombre}
              loteNumero={(id) => confirmar.ajuste.items.find((i) => i.lote_id === id)?.lote_numero ?? undefined}
            />
          )}
        </Modal>
      )}

      {toast}
    </div>
  );
}
