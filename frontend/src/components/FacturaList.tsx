import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { IconMas, IconRechazo } from "../layout/icons";
import { ESTADO_BADGE, ESTADO_LABEL, ESTADOS_PENDIENTES, TIPO_ECF_LABEL } from "../lib/estados";
import type { FacturaResumen } from "../lib/types";
import { motivoRechazo, useFacturas } from "../lib/useFacturas";

type Filtro = "todas" | "aceptadas" | "pendientes" | "rechazadas";

const FILTROS: { id: Filtro; label: string; test: (f: FacturaResumen) => boolean }[] = [
  { id: "todas", label: "Todas", test: () => true },
  { id: "aceptadas", label: "Aceptadas", test: (f) => f.estado === "aceptado" },
  { id: "pendientes", label: "En proceso", test: (f) => ESTADOS_PENDIENTES.includes(f.estado) },
  { id: "rechazadas", label: "Rechazadas", test: (f) => f.estado === "rechazado" || f.estado === "error" },
];

export function FacturaList() {
  const { facturas, cargando, error, recargar } = useFacturas();
  const [filtro, setFiltro] = useState<Filtro>("todas");
  const [tipo, setTipo] = useState("");
  const [q, setQ] = useState("");

  // Refrescar al entrar, por si se emitió o firmó algo en otra pantalla.
  useEffect(() => recargar(), []);

  const rechazadas = facturas.filter(FILTROS[3].test);
  const filas = useMemo(() => {
    const texto = q.trim().toLowerCase();
    const f = FILTROS.find((x) => x.id === filtro)!;
    return [...facturas]
      .filter(f.test)
      .filter((x) => !tipo || String(x.tipo_ecf) === tipo)
      .filter((x) => !texto || `${x.encf ?? ""} ${x.track_id ?? ""}`.toLowerCase().includes(texto))
      .sort((a, b) => b.creado_en.localeCompare(a.creado_en));
  }, [facturas, filtro, tipo, q]);

  return (
    <div className="content">
      <div className="ph">
        <div>
          <div className="crumb">Ingresos / Facturas de venta</div>
          <h1>Facturas de venta</h1>
          <p>Comprobantes fiscales electrónicos y su estado ante la DGII</p>
        </div>
        <Link className="btn btn-p" to="/facturas/nueva">
          <IconMas /> Nueva factura
        </Link>
      </div>

      {rechazadas.length > 0 && (
        <section className="card card-danger">
          <div className="card-b stack-sm">
            <div className="row danger-title">
              <IconRechazo />
              <b>{rechazadas.length === 1 ? "1 factura rechazada por la DGII" : `${rechazadas.length} facturas rechazadas por la DGII`}</b>
            </div>
            {rechazadas.slice(0, 5).map((f) => (
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

      <section className="card">
        <div className="tabs" role="tablist" aria-label="Filtrar por estado">
          {FILTROS.map((f) => (
            <button
              key={f.id}
              type="button"
              role="tab"
              aria-selected={filtro === f.id}
              className={filtro === f.id ? "tab on" : "tab"}
              onClick={() => setFiltro(f.id)}
            >
              {f.label} <span className="chip chip-sm">{facturas.filter(f.test).length}</span>
            </button>
          ))}
        </div>
        <div className="filters">
          <div className="tb-search">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" aria-hidden="true">
              <circle cx="11" cy="11" r="7" />
              <path d="m20 20-3.5-3.5" />
            </svg>
            <input className="inp" type="search" placeholder="Buscar por e-NCF o Track ID" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Buscar facturas" />
          </div>
          <select className="inp inp-auto" value={tipo} onChange={(e) => setTipo(e.target.value)} aria-label="Tipo de e-CF">
            <option value="">Todos los tipos</option>
            {Object.entries(TIPO_ECF_LABEL).map(([k, v]) => (
              <option key={k} value={k}>
                {k} · {v}
              </option>
            ))}
          </select>
        </div>

        {cargando && <p className="pad mut">Cargando…</p>}
        {error && <p className="pad error">{error}</p>}
        {!cargando && !error && facturas.length === 0 && (
          <div className="empty">
            <h3>No hay facturas todavía</h3>
            <p>Cree su primer comprobante fiscal electrónico.</p>
            <Link className="btn btn-p btn-sm" to="/facturas/nueva">
              Crear la primera
            </Link>
          </div>
        )}
        {!cargando && facturas.length > 0 && filas.length === 0 && (
          <div className="empty">
            <h3>Sin resultados</h3>
            <p>No hay facturas con estos filtros.</p>
          </div>
        )}
        {filas.length > 0 && (
          <div className="tw">
            <table className="tbl">
              <thead>
                <tr>
                  <th>e-NCF</th>
                  <th>Tipo</th>
                  <th>Estado DGII</th>
                  <th>
                    <span className="tip" data-tip="Identificador que devuelve la DGII al recibir el e-CF. Sirve para consultar el resultado del envío.">
                      Track ID ⓘ
                    </span>
                  </th>
                  <th>Creada</th>
                  <th className="right">Acciones</th>
                </tr>
              </thead>
              <tbody>
                {filas.map((f) => (
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
                    <td>
                      <span className={ESTADO_BADGE[f.estado]}>{ESTADO_LABEL[f.estado] ?? f.estado}</span>
                      {(f.estado === "rechazado" || f.estado === "error") && <div className="row-motivo">{motivoRechazo(f)}</div>}
                    </td>
                    <td className="mono mut" title={f.track_id ?? ""}>
                      {f.track_id ? `${f.track_id.slice(0, 8)}…${f.track_id.slice(-4)}` : "—"}
                    </td>
                    <td className="mut nowrap">{new Date(f.creado_en).toLocaleString("es-DO")}</td>
                    <td className="right">
                      <Link to={`/facturas/${f.id}`} className="btn btn-g btn-sm">
                        Ver
                      </Link>
                      {f.estado === "borrador" && (
                        <Link to={`/facturas/${f.id}/editar`} className="btn btn-g btn-sm">
                          Editar
                        </Link>
                      )}
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
