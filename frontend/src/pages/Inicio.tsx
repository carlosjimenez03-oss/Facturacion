import { useMemo } from "react";
import { Link } from "react-router-dom";
import { IconAlerta, IconMas, IconRechazo } from "../layout/icons";
import { proximosVencimientos } from "../lib/calendarioFiscal";
import { ESTADO_BADGE, ESTADO_LABEL, ESTADOS_PENDIENTES, TIPO_ECF_LABEL } from "../lib/estados";
import { motivoRechazo, useFacturas } from "../lib/useFacturas";

const MESES = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"];

export function Inicio() {
  const { facturas, cargando, error } = useFacturas();
  const hoy = useMemo(() => new Date(), []);

  const resumen = useMemo(() => {
    const aceptadas = facturas.filter((f) => f.estado === "aceptado").length;
    const rechazadas = facturas.filter((f) => f.estado === "rechazado" || f.estado === "error");
    const pendientes = facturas.filter((f) => ESTADOS_PENDIENTES.includes(f.estado)).length;
    const meses = Array.from({ length: 6 }, (_, i) => {
      const d = new Date(hoy.getFullYear(), hoy.getMonth() - 5 + i, 1);
      const n = facturas.filter((f) => {
        const c = new Date(f.creado_en);
        return c.getFullYear() === d.getFullYear() && c.getMonth() === d.getMonth();
      }).length;
      return { nombre: MESES[d.getMonth()], n, actual: i === 5 };
    });
    const max = Math.max(1, ...meses.map((m) => m.n));
    const recientes = [...facturas].sort((a, b) => b.creado_en.localeCompare(a.creado_en)).slice(0, 6);
    return { aceptadas, rechazadas, pendientes, meses, max, recientes };
  }, [facturas, hoy]);

  const vencimientos = proximosVencimientos(hoy);

  return (
    <div className="content">
      <div className="ph">
        <div>
          <h1>Inicio</h1>
          <p>Resumen de facturación electrónica · {hoy.toLocaleDateString("es-DO", { weekday: "long", day: "numeric", month: "long", year: "numeric" })}</p>
        </div>
        <div className="row">
          <Link to="/inventario/compras/nueva" className="btn btn-o">
            Registrar compra
          </Link>
          <Link to="/facturas/nueva" className="btn btn-p">
            <IconMas /> Nueva factura
          </Link>
        </div>
      </div>

      {error && (
        <div className="al al-d">
          <IconAlerta />
          <span>
            <b>No se pudo consultar la API de facturación.</b> {error}
          </span>
        </div>
      )}

      {resumen.rechazadas.length > 0 && (
        <section className="card card-danger">
          <div className="card-b stack-sm">
            <div className="row danger-title">
              <IconRechazo />
              <b>
                {resumen.rechazadas.length === 1 ? "1 factura rechazada o con error" : `${resumen.rechazadas.length} facturas rechazadas o con error`}
              </b>
            </div>
            {resumen.rechazadas.slice(0, 4).map((f) => (
              <div key={f.id} className="danger-row">
                <span className="mono strong">{f.encf || "(sin e-NCF)"}</span>
                <span className="danger-msg">{motivoRechazo(f)}</span>
                <Link to={`/facturas/${f.id}`} className="btn btn-sm btn-d">
                  Ver y corregir
                </Link>
              </div>
            ))}
          </div>
        </section>
      )}

      <div className="grid-stats">
        <div className="card stat">
          <span className="k">Comprobantes emitidos</span>
          <span className="v">{cargando ? "…" : facturas.length}</span>
          <span className="s">Total registrado en el sistema</span>
        </div>
        <div className="card stat">
          <span className="k">Aceptados por la DGII</span>
          <span className="v">{cargando ? "…" : resumen.aceptadas}</span>
          <span className="s">Con validez fiscal</span>
        </div>
        <div className="card stat">
          <span className="k">Rechazados o con error</span>
          <span className={`v${resumen.rechazadas.length ? " down" : ""}`}>{cargando ? "…" : resumen.rechazadas.length}</span>
          <span className="s">Requieren corrección</span>
        </div>
        <div className="card stat">
          <span className="k">En proceso</span>
          <span className="v">{cargando ? "…" : resumen.pendientes}</span>
          <span className="s">Borradores, firmados o enviados</span>
        </div>
      </div>

      <div className="split">
        <section className="card grow-2">
          <div className="card-h">
            <div>
              <h2>Comprobantes de los últimos 6 meses</h2>
              <span className="hint">Cantidad de e-CF creados por mes</span>
            </div>
          </div>
          <div className="card-b">
            <div className="bars" role="img" aria-label="Comprobantes creados por mes">
              {resumen.meses.map((m) => (
                <div key={m.nombre} className="bar-col">
                  <span className="bar-v">{m.n}</span>
                  <div className={`bar${m.actual ? " now" : ""}`} style={{ height: `${Math.round((m.n / resumen.max) * 190) + 4}px` }} />
                </div>
              ))}
            </div>
            <div className="bars-x">
              {resumen.meses.map((m) => (
                <span key={m.nombre}>{m.nombre}</span>
              ))}
            </div>
          </div>
        </section>

        <section className="card grow-1">
          <div className="card-h">
            <h2>Fechas límite fiscales</h2>
          </div>
          <div className="card-b stack">
            {vencimientos.map((v) => (
              <div key={v.formulario} className="due">
                <div className="due-date">
                  <span>{MESES[v.fecha.getMonth()]}</span>
                  <b>{v.fecha.getDate()}</b>
                </div>
                <div className="due-txt">
                  <b>{v.formulario}</b>
                  <span className="hint">{v.descripcion}</span>
                </div>
                <span className={v.diasRestantes <= 3 ? "bd b-proc nodot" : "chip"}>
                  {v.diasRestantes === 0 ? "Hoy" : `En ${v.diasRestantes} días`}
                </span>
              </div>
            ))}
          </div>
        </section>
      </div>

      <section className="card">
        <div className="card-h">
          <h2>Últimas facturas</h2>
          <Link to="/facturas" className="link-strong">
            Ver todas →
          </Link>
        </div>
        {resumen.recientes.length === 0 ? (
          <div className="empty">
            <h3>Aún no hay facturas</h3>
            <p>Emita su primer comprobante fiscal electrónico.</p>
            <Link to="/facturas/nueva" className="btn btn-p btn-sm">
              Crear factura
            </Link>
          </div>
        ) : (
          <div className="tw">
            <table className="tbl">
              <thead>
                <tr>
                  <th>e-NCF</th>
                  <th>Tipo</th>
                  <th>Fecha</th>
                  <th>Estado DGII</th>
                </tr>
              </thead>
              <tbody>
                {resumen.recientes.map((f) => (
                  <tr key={f.id}>
                    <td>
                      <Link to={`/facturas/${f.id}`} className="mono link">
                        {f.encf || "(sin e-NCF)"}
                      </Link>
                    </td>
                    <td>
                      <span className="chip">
                        {f.tipo_ecf} · {TIPO_ECF_LABEL[f.tipo_ecf] ?? "e-CF"}
                      </span>
                    </td>
                    <td className="mut">{new Date(f.creado_en).toLocaleDateString("es-DO")}</td>
                    <td>
                      <span className={ESTADO_BADGE[f.estado]}>{ESTADO_LABEL[f.estado]}</span>
                      {(f.estado === "rechazado" || f.estado === "error") && <div className="row-motivo">{motivoRechazo(f)}</div>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
