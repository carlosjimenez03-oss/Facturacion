import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { IconMas } from "../../layout/icons";
import { erpApi } from "../../lib/erpApi";
import type { CotizacionResumen, EstadoCotizacion } from "../../lib/erpApi";
import { formatoMonto, formatoRnc } from "../../lib/marca";
import { AvisoError, Buscador, Cargando, Estadistica, IngresosEncabezado, Modal, SinResultados, Vacio } from "./IngresosShell";
import { useAviso } from "./useAviso";
import { EnviarCotizacion } from "./EnviarCotizacion";
import { descargarCsv, ESTADO_COTIZACION, fechaLegible, hoyIso, mensajeError, puedeFacturar } from "./ingresosUtil";

type Filtro = "todas" | EstadoCotizacion;

const FILTROS: { id: Filtro; label: string }[] = [
  { id: "todas", label: "Todas" },
  { id: "abierta", label: "Abiertas" },
  { id: "aceptada", label: "Aceptadas" },
  { id: "vencida", label: "Vencidas" },
  { id: "facturada", label: "Facturadas" },
  { id: "rechazada", label: "Rechazadas" },
];

export function CotizacionList() {
  const navigate = useNavigate();
  const [cotizaciones, setCotizaciones] = useState<CotizacionResumen[]>([]);
  const [cargando, setCargando] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filtro, setFiltro] = useState<Filtro>("todas");
  const [q, setQ] = useState("");
  const [eliminar, setEliminar] = useState<CotizacionResumen | null>(null);
  const [enviar, setEnviar] = useState<CotizacionResumen | null>(null);
  const [ocupado, setOcupado] = useState<string | null>(null);
  const [aviso, mostrarAviso] = useAviso();

  const cargar = useCallback(() => {
    setCargando(true);
    setError(null);
    erpApi
      .listarCotizaciones()
      .then(setCotizaciones)
      .catch((err) => {
        setCotizaciones([]);
        setError(mensajeError(err));
      })
      .finally(() => setCargando(false));
  }, []);

  useEffect(() => cargar(), [cargar]);

  const filas = useMemo(() => {
    const texto = q.trim().toLowerCase();
    return [...cotizaciones]
      .sort((a, b) => b.numero.localeCompare(a.numero))
      .filter((c) => filtro === "todas" || c.estado === filtro)
      .filter(
        (c) =>
          !texto ||
          [c.numero, c.cliente_nombre, c.cliente_rnc, fechaLegible(c.fecha), ESTADO_COTIZACION[c.estado]?.label].join(" ").toLowerCase().includes(texto),
      );
  }, [cotizaciones, filtro, q]);

  const total = cotizaciones.reduce((s, c) => s + (Number(c.total) || 0), 0);
  const porFacturar = cotizaciones.filter(puedeFacturar);
  const montoPorFacturar = porFacturar.reduce((s, c) => s + (Number(c.total) || 0), 0);

  async function marcarAceptada(c: CotizacionResumen) {
    setOcupado(c.id);
    try {
      const nueva = await erpApi.cambiarEstadoCotizacion(c.id, "aceptada");
      setCotizaciones((prev) => prev.map((x) => (x.id === c.id ? { ...x, estado: nueva.estado } : x)));
      mostrarAviso(`${c.numero} marcada como aceptada`);
    } catch (err) {
      mostrarAviso("No se pudo cambiar el estado", { detalle: mensajeError(err), error: true });
    } finally {
      setOcupado(null);
    }
  }

  async function confirmarEliminar() {
    if (!eliminar) return;
    const c = eliminar;
    setOcupado(c.id);
    try {
      await erpApi.eliminarCotizacion(c.id);
      setCotizaciones((prev) => prev.filter((x) => x.id !== c.id));
      setEliminar(null);
      mostrarAviso(`${c.numero} eliminada`);
    } catch (err) {
      setEliminar(null);
      mostrarAviso("No se pudo eliminar la cotización", { detalle: mensajeError(err), error: true });
    } finally {
      setOcupado(null);
    }
  }

  function exportar() {
    descargarCsv(`Cotizaciones_${hoyIso()}.csv`, [
      ["N.º", "Cliente", "RNC", "Fecha", "Vence", "Total (RD$)", "Estado"],
      ...filas.map((c) => [c.numero, c.cliente_nombre, c.cliente_rnc, fechaLegible(c.fecha), fechaLegible(c.vence), Number(c.total).toFixed(2), ESTADO_COTIZACION[c.estado]?.label]),
    ]);
  }

  return (
    <div className="content">
      <IngresosEncabezado
        titulo="Cotizaciones"
        sub="Propuestas comerciales sin valor fiscal; conviértelas en factura con un clic"
        acciones={
          <>
            <button type="button" className="btn btn-o" onClick={exportar} disabled={filas.length === 0}>
              Exportar
            </button>
            <Link className="btn btn-p" to="/cotizaciones/nueva">
              <IconMas /> Nueva cotización
            </Link>
          </>
        }
      />

      {error && <AvisoError titulo="No se pudieron cargar las cotizaciones." mensaje={error} onReintentar={cargar} />}

      <div className="grid-stats">
        <Estadistica k="Registros" v={cargando ? "—" : String(cotizaciones.length)} />
        <Estadistica k="Total (RD$) · total" v={cargando ? "—" : `RD$ ${formatoMonto(total)}`} />
        <Estadistica
          k="Por facturar"
          v={cargando ? "—" : `RD$ ${formatoMonto(montoPorFacturar)}`}
          s={`${porFacturar.length} ${porFacturar.length === 1 ? "cotización abierta o aceptada" : "cotizaciones abiertas o aceptadas"}`}
        />
      </div>

      <section className="card">
        <div className="filters">
          <Buscador valor={q} onCambiar={setQ} placeholder="Buscar en Cotizaciones" />
          <div className="seg" role="group" aria-label="Filtrar por estado">
            {FILTROS.map((f) => (
              <button key={f.id} type="button" className={filtro === f.id ? "on" : ""} aria-pressed={filtro === f.id} onClick={() => setFiltro(f.id)}>
                {f.label}
              </button>
            ))}
          </div>
        </div>

        {cargando && <Cargando />}
        {!cargando && !error && cotizaciones.length === 0 && (
          <Vacio titulo="No hay cotizaciones todavía" texto="Prepare una propuesta para su cliente y conviértala en factura cuando la acepte.">
            <Link className="btn btn-p btn-sm" to="/cotizaciones/nueva">
              Crear la primera
            </Link>
          </Vacio>
        )}
        {!cargando && cotizaciones.length > 0 && filas.length === 0 && (
          <SinResultados
            q={q}
            onLimpiar={() => {
              setQ("");
              setFiltro("todas");
            }}
          />
        )}
        {!cargando && filas.length > 0 && (
          <div className="tw">
            <table className="tbl" style={{ minWidth: 960 }}>
              <thead>
                <tr>
                  <th>N.º</th>
                  <th>Cliente</th>
                  <th>Fecha</th>
                  <th>Vence</th>
                  <th className="num">Total (RD$)</th>
                  <th>Estado</th>
                  <th style={{ textAlign: "right" }}>Acciones</th>
                </tr>
              </thead>
              <tbody>
                {filas.map((c) => {
                  const est = ESTADO_COTIZACION[c.estado] ?? { label: c.estado, badge: "bd b-borr" };
                  return (
                    <tr key={c.id} className="fila-link" onClick={() => navigate(`/cotizaciones/${c.id}`)}>
                      <td className="nowrap">
                        <Link to={`/cotizaciones/${c.id}`} className="link mono" onClick={(e) => e.stopPropagation()}>
                          {c.numero}
                        </Link>
                      </td>
                      <td>
                        <div className="strong">{c.cliente_nombre}</div>
                        {c.cliente_rnc && <div className="hint mono">{formatoRnc(c.cliente_rnc)}</div>}
                      </td>
                      <td className="nowrap">{fechaLegible(c.fecha)}</td>
                      <td className="nowrap">{fechaLegible(c.vence)}</td>
                      <td className="num">{formatoMonto(c.total)}</td>
                      <td>
                        <span className={est.badge}>{est.label}</span>
                      </td>
                      <td onClick={(e) => e.stopPropagation()}>
                        <div className="fila-acc">
                          <Link to={`/cotizaciones/${c.id}`} className="btn btn-g btn-sm">
                            Abrir
                          </Link>
                          {puedeFacturar(c) && (
                            <Link to={`/facturas/nueva?cotizacion=${c.id}`} className="btn btn-g btn-sm">
                              Facturar
                            </Link>
                          )}
                          {c.factura_id && (
                            <Link to={`/facturas/${c.factura_id}`} className="btn btn-g btn-sm">
                              Ver factura
                            </Link>
                          )}
                          {c.estado === "abierta" && (
                            <button type="button" className="btn btn-g btn-sm" onClick={() => marcarAceptada(c)} disabled={ocupado === c.id}>
                              Marcar aceptada
                            </button>
                          )}
                          {c.estado !== "facturada" && c.estado !== "vencida" && (
                            <button type="button" className="btn btn-g btn-sm" onClick={() => setEnviar(c)}>
                              Enviar
                            </button>
                          )}
                          {(c.estado === "abierta" || c.estado === "vencida") && (
                            <button type="button" className="btn btn-g btn-sm" style={{ color: "#B91C1C" }} onClick={() => setEliminar(c)} disabled={ocupado === c.id}>
                              Eliminar
                            </button>
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

      {eliminar && (
        <Modal
          titulo={`¿Eliminar la cotización ${eliminar.numero}?`}
          onCerrar={() => setEliminar(null)}
          pie={
            <>
              <button type="button" className="btn btn-o" onClick={() => setEliminar(null)}>
                Cancelar
              </button>
              <button type="button" className="btn btn-d" onClick={confirmarEliminar} disabled={ocupado === eliminar.id}>
                {ocupado === eliminar.id ? "Eliminando…" : "Sí, eliminar"}
              </button>
            </>
          }
        >
          <span>
            Se eliminará la cotización de <b style={{ color: "#111827" }}>{eliminar.cliente_nombre}</b> por{" "}
            <b style={{ color: "#111827" }}>RD$ {formatoMonto(eliminar.total)}</b>. Esta acción no se puede deshacer.
          </span>
        </Modal>
      )}

      {enviar && <EnviarCotizacion cotizacion={enviar} onCerrar={() => setEnviar(null)} />}

      {aviso}
    </div>
  );
}
