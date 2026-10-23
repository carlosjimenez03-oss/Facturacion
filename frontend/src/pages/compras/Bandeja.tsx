import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { comprasApi } from "../../lib/comprasApi";
import type { EcfRecibido, EcfRecibidoResumen, EstadoAprobacion } from "../../lib/comprasApi";
import { formatoMonto, formatoRnc } from "../../lib/marca";
import { AvisoError, Buscador, Cargando } from "../ingresos/IngresosShell";
import { useAviso } from "../ingresos/useAviso";
import { fechaLegible, mensajeError } from "../ingresos/ingresosUtil";
import { nombrePeriodo, periodosRecientes } from "../impuestos/impuestosUtil";
import { IconoActualizar, IconoOk } from "./ComprasShell";
import { DecisionEcf } from "./DecisionEcf";
import { ESTADO_APROBACION, tipoEcfTexto } from "./comprasUtil";
import "../ingresos/ingresos.css";
import "./compras.css";

type Filtro = "todas" | EstadoAprobacion;

const FILTROS: { id: Filtro; label: string }[] = [
  { id: "todas", label: "Todas" },
  { id: "pendiente", label: "Pendientes" },
  { id: "aprobado", label: "Aprobadas" },
  { id: "rechazado", label: "Rechazadas" },
];

export function IconoBandeja() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M22 12h-6l-2 3h-4l-2-3H2" />
      <path d="M5.5 5.1 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.5-6.9A2 2 0 0 0 16.8 4H7.2a2 2 0 0 0-1.7 1.1z" />
    </svg>
  );
}

export function BandejaPage() {
  const [ecfs, setEcfs] = useState<EcfRecibidoResumen[]>([]);
  const [cargando, setCargando] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filtro, setFiltro] = useState<Filtro>("todas");
  const [q, setQ] = useState("");
  const [periodo, setPeriodo] = useState("");
  const [selId, setSelId] = useState<string | null>(null);
  const [detalle, setDetalle] = useState<EcfRecibido | null>(null);
  const [errorDetalle, setErrorDetalle] = useState<string | null>(null);
  const [toast, mostrarToast] = useAviso();
  const panelRef = useRef<HTMLElement>(null);
  const idsPrevios = useRef(new Set<string>());

  const cargar = useCallback(
    async (avisar = false) => {
      setCargando(true);
      try {
        const lista = await comprasApi.listarBandeja({ periodo: periodo || undefined });
        const nuevos = lista.filter((e) => !idsPrevios.current.has(e.id)).length;
        idsPrevios.current = new Set(lista.map((e) => e.id));
        setEcfs(lista);
        setError(null);
        if (avisar) mostrarToast(nuevos > 0 ? `Bandeja actualizada · ${nuevos} e-CF nuevos` : "Bandeja actualizada · no hay e-CF nuevos de proveedores");
      } catch (err) {
        setEcfs([]);
        setError(mensajeError(err));
      } finally {
        setCargando(false);
      }
    },
    [periodo, mostrarToast],
  );

  useEffect(() => {
    void cargar();
  }, [cargar]);

  const filas = useMemo(() => {
    const texto = q.trim().toLowerCase();
    return ecfs
      .filter((e) => filtro === "todas" || e.estado_aprobacion === filtro)
      .filter((e) => !texto || [e.razon_social_emisor, e.rnc_emisor, formatoRnc(e.rnc_emisor), e.encf].join(" ").toLowerCase().includes(texto));
  }, [ecfs, filtro, q]);

  const seleccionado = filas.find((e) => e.id === selId) ?? filas[0] ?? null;

  const cargarDetalle = useCallback((id: string) => {
    setErrorDetalle(null);
    comprasApi
      .obtenerEcfRecibido(id)
      .then(setDetalle)
      .catch((err) => {
        setDetalle(null);
        setErrorDetalle(mensajeError(err));
      });
  }, []);

  useEffect(() => {
    if (!seleccionado) {
      setDetalle(null);
      return;
    }
    if (detalle?.id !== seleccionado.id) {
      setDetalle(null);
      cargarDetalle(seleccionado.id);
    }
  }, [seleccionado?.id, cargarDetalle]);

  function elegir(id: string) {
    setSelId(id);
    if (window.matchMedia("(max-width: 1000px)").matches) {
      window.setTimeout(() => panelRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 0);
    }
  }

  function aplicarCambio(e: EcfRecibido | null) {
    if (!e) {
      if (seleccionado) cargarDetalle(seleccionado.id);
      return;
    }
    setDetalle(e);
    setEcfs((prev) => prev.map((x) => (x.id === e.id ? { ...x, ...e } : x)));
  }

  const vacio = filtro === "rechazado"
    ? { titulo: "No ha rechazado ningún comprobante", texto: "Cuando rechace un e-CF de un proveedor, aparecerá aquí con el motivo enviado." }
    : { titulo: "Todo al día", texto: "No hay comprobantes en esta vista. Los nuevos e-CF de proveedores llegarán automáticamente." };

  return (
    <div className="content">
      <div className="ph">
        <div>
          <h1>Bandeja de entrada</h1>
          <p>e-CF recibidos de proveedores. El acuse de recibo se envía automáticamente; la aprobación comercial la decide usted.</p>
        </div>
        <button type="button" className="btn btn-o" onClick={() => void cargar(true)} disabled={cargando}>
          <IconoActualizar />
          Actualizar
        </button>
      </div>

      {error && <AvisoError titulo="No se pudo cargar la bandeja de entrada." mensaje={error} onReintentar={() => void cargar()} />}

      <div className="cp-split">
        <section className="card cp-main">
          <div className="tabs" role="tablist" aria-label="Estado comercial" style={{ padding: "0 12px" }}>
            {FILTROS.map((f) => (
              <button key={f.id} type="button" role="tab" aria-selected={filtro === f.id} className={filtro === f.id ? "tab on" : "tab"} onClick={() => setFiltro(f.id)}>
                {f.label} <span className="chip chip-sm">{ecfs.filter((e) => f.id === "todas" || e.estado_aprobacion === f.id).length}</span>
              </button>
            ))}
          </div>
          <div className="filters">
            <Buscador valor={q} onCambiar={setQ} placeholder="Buscar por proveedor, RNC o e-NCF" />
            <select className="inp inp-auto" value={periodo} onChange={(e) => setPeriodo(e.target.value)} aria-label="Período de emisión">
              <option value="">Todos los períodos</option>
              {periodosRecientes(18).map((p) => (
                <option key={p} value={p}>
                  {nombrePeriodo(p, true)}
                </option>
              ))}
            </select>
          </div>
          {cargando && ecfs.length === 0 && <Cargando />}
          {!cargando && filas.length === 0 && !error && (
            <div className="empty">
              <span className="ei">
                <IconoBandeja />
              </span>
              <h3>{q ? "Sin resultados" : vacio.titulo}</h3>
              <p>{q ? `No hay e-CF que coincidan con «${q}».` : vacio.texto}</p>
              {q && (
                <button type="button" className="btn btn-o btn-sm" onClick={() => setQ("")}>
                  Limpiar búsqueda
                </button>
              )}
            </div>
          )}
          {filas.length > 0 && (
            <div className="tw">
              <table className="tbl" style={{ minWidth: 640 }}>
                <thead>
                  <tr>
                    <th>Proveedor</th>
                    <th>RNC</th>
                    <th>e-NCF</th>
                    <th>Fecha</th>
                    <th className="num">Monto (RD$)</th>
                    <th>
                      <span className="tip r" data-tip="Aprobación comercial: confirma al proveedor que acepta la mercancía o el servicio facturado. Es distinta del acuse de recibo técnico.">
                        Estado comercial ⓘ
                      </span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {filas.map((e) => {
                    const est = ESTADO_APROBACION[e.estado_aprobacion] ?? { label: e.estado_aprobacion, badge: "bd b-borr" };
                    return (
                      <tr key={e.id} className={e.id === seleccionado?.id ? "cp-fila sel" : "cp-fila"} onClick={() => elegir(e.id)}>
                        <td>
                          <button type="button" className="cp-prov" onClick={(ev) => { ev.stopPropagation(); elegir(e.id); }} aria-pressed={e.id === seleccionado?.id}>
                            {e.razon_social_emisor || formatoRnc(e.rnc_emisor)}
                          </button>
                        </td>
                        <td className="mono mut nowrap">{formatoRnc(e.rnc_emisor)}</td>
                        <td className="mono">{e.encf}</td>
                        <td className="mut nowrap">{fechaLegible(e.fecha_emision)}</td>
                        <td className="num" style={{ fontWeight: 600, color: "#111827" }}>
                          {formatoMonto(e.monto_total)}
                        </td>
                        <td>
                          <span className={est.badge}>{est.label}</span>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </section>

        {seleccionado && (
          <section className="card cp-side" ref={panelRef} aria-label="e-CF seleccionado">
            <PanelEcf resumen={seleccionado} detalle={detalle} error={errorDetalle} onReintentar={() => cargarDetalle(seleccionado.id)} onCambio={aplicarCambio} avisar={mostrarToast} />
          </section>
        )}
      </div>
      {toast}
    </div>
  );
}

function PanelEcf({
  resumen,
  detalle,
  error,
  onReintentar,
  onCambio,
  avisar,
}: {
  resumen: EcfRecibidoResumen;
  detalle: EcfRecibido | null;
  error: string | null;
  onReintentar: () => void;
  onCambio: (e: EcfRecibido | null) => void;
  avisar: (texto: string, opciones?: { detalle?: string; error?: boolean }) => void;
}) {
  const e = detalle?.id === resumen.id ? detalle : null;
  const est = ESTADO_APROBACION[resumen.estado_aprobacion] ?? { label: resumen.estado_aprobacion, badge: "bd b-borr" };
  return (
    <>
      <div className="card-h">
        <div style={{ minWidth: 0 }}>
          <h2>{resumen.razon_social_emisor || formatoRnc(resumen.rnc_emisor)}</h2>
          <span className="hint">RNC {formatoRnc(resumen.rnc_emisor)} · Emisor electrónico</span>
        </div>
        <span className={est.badge}>{est.label}</span>
      </div>
      <div className="card-b cp-col">
        <dl className="kv">
          <dt>e-NCF</dt>
          <dd className="mono">{resumen.encf}</dd>
          <dt>Tipo</dt>
          <dd>{tipoEcfTexto(resumen.tipo_ecf)}</dd>
          <dt>Fecha de emisión</dt>
          <dd>{fechaLegible(resumen.fecha_emision)}</dd>
          <dt>Acuse de recibo</dt>
          <dd>
            <span className="ok-t">
              <IconoOk size={14} />
              Enviado automáticamente
            </span>
          </dd>
        </dl>
        {error && <AvisoError titulo="No se pudo abrir el e-CF." mensaje={error} onReintentar={onReintentar} />}
        {!e && !error && <p className="mut" style={{ margin: 0 }}>Cargando detalle…</p>}
        {e && (
          <>
            <div className="cp-caja">
              <table className="tbl">
                <thead>
                  <tr>
                    <th>Descripción</th>
                    <th className="num">Importe</th>
                  </tr>
                </thead>
                <tbody>
                  {e.items.map((it) => (
                    <tr key={it.linea}>
                      <td>{it.descripcion}</td>
                      <td className="num">{formatoMonto(it.monto)}</td>
                    </tr>
                  ))}
                  {e.items.length === 0 && (
                    <tr>
                      <td colSpan={2} className="mut">
                        El XML no trae líneas de detalle.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
            <dl className="kv cp-tot">
              <dt>Subtotal</dt>
              <dd className="num">{formatoMonto(e.subtotal)}</dd>
              <dt>ITBIS</dt>
              <dd className="num">{formatoMonto(e.itbis_total)}</dd>
              <dt className="t">Total</dt>
              <dd className="num t">RD$ {formatoMonto(e.monto_total)}</dd>
            </dl>
            <DecisionEcf ecf={e} onCambio={onCambio} avisar={avisar} enlaceDetalle />
          </>
        )}
      </div>
    </>
  );
}
