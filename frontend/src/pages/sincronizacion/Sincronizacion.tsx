// Centro de sincronización (Sincronizacion.dc.html del prototipo): estado de cada sincronización con terceros
// — envío de e-CF a la DGII y su resultado, salidas de inventario, aprobaciones comerciales (ACECF) y su entrega
// al proveedor —, el modo contingencia (calculado por erp-api de los fallos seguidos con la DGII), las facturas
// emitidas en papel y el historial de envíos. Los reintentos usan los endpoints que ya existen: facturas-api
// (actualizar estado / enviar) y la bandeja de Compras (aprobar / rechazar).
import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../../lib/api";
import { comprasApi } from "../../lib/comprasApi";
import { TIPO_ECF_LABEL } from "../../lib/estados";
import { sincronizacionApi } from "../../lib/operacionApi";
import type {
  CanalSincronizacion,
  EnviosDia,
  EventoSincronizacion,
  FacturaPapel,
  ItemSincronizacion,
  ResultadoEvento,
  ResumenSincronizacion,
} from "../../lib/operacionApi";
import { formatoMonto, formatoRnc } from "../../lib/marca";
import { AvisoError, Cargando, Vacio } from "../ingresos/IngresosShell";
import { useAviso } from "../ingresos/useAviso";
import { fechaLegible, mensajeError } from "../ingresos/ingresosUtil";
import { FacturaPapelModal } from "./FacturaPapelModal";
import { CANAL, duracion, fechaHora, hora } from "./sincronizacionUtil";
import "../ingresos/ingresos.css";
import "./sincronizacion.css";

const RESULTADO_EVENTO: Record<ResultadoEvento, { label: string; badge: string }> = {
  ok: { label: "Correcto", badge: "bd b-acep" },
  error: { label: "Error", badge: "bd b-rech" },
  rechazado: { label: "Rechazado", badge: "bd b-rech" },
  en_proceso: { label: "En proceso", badge: "bd b-pend" },
};

const RESULTADO_DIA: Record<EnviosDia["resultado"], { label: string; badge: string }> = {
  completado: { label: "Completado", badge: "bd b-acep" },
  con_rechazos: { label: "Con rechazos", badge: "bd b-rech" },
  con_errores: { label: "Con errores", badge: "bd b-pend" },
  sin_envios: { label: "Sin envíos", badge: "bd b-borr" },
};

const ESTADO_ITEM: Record<ItemSincronizacion["estado"], { label: string; badge: string }> = {
  error: { label: "Con error", badge: "bd b-rech" },
  pendiente: { label: "Pendiente de sincronizar", badge: "bd b-pend" },
  en_curso: { label: "En proceso", badge: "bd b-env" },
};

const MODULOS = [
  ["recepcion", "Envío de e-CF"],
  ["consulta_resultado", "Consulta de resultado"],
  ["aprobacion_comercial", "Aprobación comercial (ACECF)"],
  ["anulacion_encf", "Anulación de e-NCF (ANECF)"],
  ["recepcion_rfce", "Resumen de consumo (RFCE)"],
  ["receptor_recepcion", "e-CF recibido de proveedor"],
];

const PAGINA_HISTORIAL = 25;
const MAXIMO_REINTENTOS = 20;

export function SincronizacionPage() {
  const [resumen, setResumen] = useState<ResumenSincronizacion | null>(null);
  const [pendientes, setPendientes] = useState<ItemSincronizacion[]>([]);
  const [dias, setDias] = useState<EnviosDia[]>([]);
  const [papel, setPapel] = useState<FacturaPapel[]>([]);
  const [cargando, setCargando] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [canal, setCanal] = useState<CanalSincronizacion | "">("");
  const [ocupado, setOcupado] = useState<string | null>(null);
  const [reintentando, setReintentando] = useState(false);
  const [modalPapel, setModalPapel] = useState<FacturaPapel | "nueva" | null>(null);
  const [ahora, setAhora] = useState(() => Date.now());
  const [toast, mostrarToast] = useAviso();

  const cargar = useCallback(() => {
    setError(null);
    return Promise.all([sincronizacionApi.resumen(), sincronizacionApi.pendientes(), sincronizacionApi.enviosPorDia(7), sincronizacionApi.facturasPapel()])
      .then(([r, p, d, fp]) => {
        setResumen(r);
        setPendientes(p);
        setDias(d);
        setPapel(fp);
      })
      .catch((err) => setError(mensajeError(err)))
      .finally(() => setCargando(false));
  }, []);

  useEffect(() => {
    void cargar();
  }, [cargar]);

  // Reloj de la cuenta regresiva del próximo intento y del tiempo en contingencia.
  useEffect(() => {
    const t = window.setInterval(() => setAhora(Date.now()), 15_000);
    return () => window.clearInterval(t);
  }, []);

  /** Ejecuta la acción de reintento de un pendiente con el endpoint que ya existe para ella. */
  async function ejecutar(item: ItemSincronizacion): Promise<string> {
    switch (item.accion) {
      case "actualizar_estado": {
        const f = await api.actualizarEstado(item.referencia_id);
        return f.estado === "enviado" ? "La DGII sigue procesándolo" : `Estado: ${f.estado}`;
      }
      case "reenviar": {
        const r = await api.enviar(item.referencia_id);
        if (!r.track_id) throw new Error(r.error ?? "La DGII no devolvió trackId");
        return `Enviado · Track ID ${r.track_id}`;
      }
      case "reintentar_aprobacion":
      case "decidir_aprobacion": {
        const e = item.decision === "rechazado" ? await comprasApi.rechazar(item.referencia_id, item.motivo ?? "") : await comprasApi.aprobar(item.referencia_id);
        return e.estado_aprobacion === "aprobado" ? "Aprobación comercial aceptada por la DGII" : "Rechazo comercial aceptado por la DGII";
      }
      case "reintentar_entrega": {
        const r = await sincronizacionApi.reintentarEntrega(item.referencia_id);
        if (!r.enviado_al_emisor) throw new Error(r.error_envio_emisor ?? "No se pudo entregar");
        return "ACECF entregado al proveedor";
      }
      default:
        return "";
    }
  }

  async function reintentar(item: ItemSincronizacion) {
    if (item.accion === "reenviar" && !window.confirm(`¿Enviar el e-CF ${item.documento ?? ""} a la DGII?`)) return;
    setOcupado(item.id);
    try {
      const detalle = await ejecutar(item);
      mostrarToast(`${item.documento ?? "Documento"} sincronizado`, { detalle });
    } catch (err) {
      mostrarToast(`No se pudo sincronizar ${item.documento ?? ""}`.trim(), { detalle: mensajeError(err), error: true });
    } finally {
      setOcupado(null);
      void cargar();
    }
  }

  /** "Reintentar conexión ahora": prueba con el primer pendiente de la DGII y, si responde, sigue con los demás. */
  async function reintentarConexion() {
    const cola = pendientes.filter((i) => (i.canal === "dgii_envio" || i.canal === "dgii_resultado") && i.accion).slice(0, MAXIMO_REINTENTOS);
    if (cola.length === 0) {
      await cargar();
      mostrarToast("No hay envíos pendientes de la DGII", { detalle: "El estado se actualizó." });
      return;
    }
    setReintentando(true);
    let ok = 0;
    let fallo: string | null = null;
    for (const item of cola) {
      try {
        await ejecutar(item);
        ok += 1;
      } catch (err) {
        fallo = mensajeError(err);
        if (ok === 0) break; // la DGII sigue sin responder: no se insiste con el resto
      }
    }
    setReintentando(false);
    await cargar();
    if (ok === 0) mostrarToast("La DGII sigue sin responder", { detalle: fallo ?? "Se reintentará automáticamente. Puede seguir facturando.", error: true });
    else mostrarToast("Conexión con la DGII restablecida", { detalle: `${ok} de ${cola.length} comprobantes sincronizados${fallo ? ` · último error: ${fallo}` : ""}.` });
  }

  const c = resumen?.contingencia;
  const visibles = pendientes.filter((i) => !canal || i.canal === canal);
  const enviosPendientes = pendientes.filter((i) => i.canal === "dgii_envio" || i.canal === "dgii_resultado");
  const proximo = c?.proximo_intento ? Math.max(0, Math.round((new Date(c.proximo_intento).getTime() - ahora) / 1000)) : null;

  return (
    <div className="content">
      {c?.activa && (
        <div className="al al-o sy-banner" role="alert">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="M2 8.8a15 15 0 0 1 4.2-2.6M10.7 5.1A15 15 0 0 1 22 8.8M5 12.5a10 10 0 0 1 5.2-2.4M16.9 11.5a10 10 0 0 1 2.1 1M8.5 16a5 5 0 0 1 7 0M12 20h.01M2 2l20 20" />
          </svg>
          <div className="sy-banner-t">
            <b>Modo contingencia activo desde las {hora(c.desde).replace(/\.$/, "")}.</b> Los servicios de la DGII no responden ({c.errores_consecutivos} intentos seguidos fallaron
            {c.ultimo_error ? `: ${c.ultimo_error}` : ""}). Las facturas se siguen emitiendo; las que no llegaron a la DGII se envían al restablecerse la
            conexión. Puede entregarlas al cliente con la leyenda de contingencia.
          </div>
          <button type="button" className="btn btn-o btn-sm" onClick={reintentarConexion} disabled={reintentando}>
            {reintentando ? "Reintentando…" : "Reintentar conexión ahora"}
          </button>
        </div>
      )}

      <div className="ph">
        <div>
          <h1>Centro de sincronización</h1>
          <p>Envío de comprobantes a la DGII, resultados, inventario, aprobaciones comerciales y facturas en papel</p>
        </div>
        <div className="row">
          {!c?.activa && (
            <button type="button" className="btn btn-o" onClick={reintentarConexion} disabled={reintentando || cargando}>
              {reintentando ? "Reintentando…" : "Reintentar envíos"}
            </button>
          )}
          <button type="button" className="btn btn-o" onClick={() => void cargar()} disabled={cargando}>
            Actualizar
          </button>
        </div>
      </div>

      {error && <AvisoError titulo="No se pudo cargar el estado de la sincronización." mensaje={error} onReintentar={() => void cargar()} />}

      <div className="sy-stats">
        <div className="card stat">
          <span className="k">Pendientes de envío</span>
          <span className="v" style={{ color: c && c.pendientes + (resumen?.en_proceso_dgii ?? 0) > 0 ? "#C2410C" : undefined }}>
            {resumen ? c!.pendientes + resumen.en_proceso_dgii : "—"}
          </span>
          <span className="s">
            {resumen
              ? `${c!.pendientes} sin llegar a la DGII · ${resumen.en_proceso_dgii} en proceso · RD$ ${formatoMonto(Number(c!.monto_pendiente) + Number(resumen.monto_en_proceso))}`
              : "—"}
          </span>
        </div>
        <div className="card stat">
          <span className="k">{c?.activa ? "Tiempo en contingencia" : "Conexión con la DGII"}</span>
          <span className="v">{c ? (c.activa ? duracion(c.desde, ahora) : resumen!.errores_7d ? "Con fallas" : "En línea") : "—"}</span>
          <span className="s">
            {c?.activa ? "Límite recomendado: 72 horas" : resumen ? `${resumen.eventos_7d} eventos en 7 días · ${resumen.errores_7d} con error` : "—"}
          </span>
        </div>
        <div className="card stat">
          <span className="k">Último intento</span>
          <span className="v">{c?.ultimo_intento ? hora(c.ultimo_intento) : "—"}</span>
          <span className="s">
            {proximo !== null ? (proximo > 0 ? `Próxima consulta en ${proximo} s · automático` : "Consulta automática en curso") : c?.ultimo_intento ? fechaLegible(c.ultimo_intento) : "Sin envíos registrados"}
          </span>
        </div>
        <div className="card stat">
          <span className="k">Facturas en papel</span>
          <span className="v">{c ? c.facturas_papel : "—"}</span>
          <span className="s">{c?.activa ? "Registradas en esta contingencia" : "Registradas este mes"}</span>
        </div>
      </div>

      <div className="sy-split">
        <section className="card sy-main">
          <div className="card-h">
            <h2>Pendientes de sincronizar</h2>
            <div className="row">
              <select className="inp inp-sm sy-filtro" aria-label="Filtrar por canal" value={canal} onChange={(e) => setCanal(e.target.value as CanalSincronizacion | "")}>
                <option value="">Todos los canales</option>
                {(Object.keys(CANAL) as CanalSincronizacion[]).map((k) => (
                  <option key={k} value={k}>
                    {CANAL[k].label}
                  </option>
                ))}
              </select>
              <span className={pendientes.length ? "bd b-pend" : "bd b-acep"}>{pendientes.length} pendientes</span>
            </div>
          </div>
          {cargando && <Cargando />}
          {!cargando && !error && visibles.length === 0 && (
            <Vacio titulo="Todo sincronizado" texto="No hay envíos, resultados, salidas de inventario ni aprobaciones pendientes." />
          )}
          {!cargando && visibles.length > 0 && (
            <div className="tw">
              <table className="tbl sy-tbl" style={{ minWidth: 660 }}>
                <thead>
                  <tr>
                    <th>e-NCF</th>
                    <th>Cliente / proveedor</th>
                    <th className="num">Total (RD$)</th>
                    <th>Estado</th>
                    <th style={{ textAlign: "right" }}>Acción</th>
                  </tr>
                </thead>
                <tbody>
                  {visibles.map((i) => (
                    <tr key={i.id}>
                      <td className="nowrap">
                        {i.enlace ? (
                          <Link className="link mono" to={i.enlace}>
                            {i.documento ?? "—"}
                          </Link>
                        ) : (
                          <span className="mono">{i.documento ?? "—"}</span>
                        )}
                        <div className="hint">{CANAL[i.canal].corto}</div>
                      </td>
                      <td className="sy-quien" title={i.tipo_ecf ? TIPO_ECF_LABEL[i.tipo_ecf] : undefined}>
                        {i.contraparte ?? "Consumidor final"}
                        {i.detalle && <div className={i.estado === "error" ? "hint sy-err" : "hint"}>{i.detalle}</div>}
                      </td>
                      <td className="num" style={{ fontWeight: 600 }}>
                        {i.monto ? formatoMonto(i.monto) : "—"}
                      </td>
                      <td>
                        <span className={ESTADO_ITEM[i.estado].badge}>{ESTADO_ITEM[i.estado].label}</span>
                        <div className="hint nowrap">Desde {fechaHora(i.fecha)}</div>
                        {i.automatico && <div className="hint">Se reintenta solo</div>}
                      </td>
                      <td>
                        <div className="fila-acc sy-acc">
                          <AccionItem item={i} ocupado={ocupado === i.id || reintentando} onReintentar={() => reintentar(i)} />
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {enviosPendientes.length > 0 && (
            <div className="sy-pie">
              <Link to="/facturas" className="link-strong">
                {enviosPendientes.length === 1 ? "Ver la factura pendiente de la DGII →" : `Ver las ${enviosPendientes.length} facturas pendientes de la DGII →`}
              </Link>
            </div>
          )}
        </section>

        <div className="sy-side">
          {c && c.lotes.length > 0 ? (
            <section className="card">
              <div className="card-h">
                <h2>Progreso por lotes</h2>
              </div>
              <div className="card-b sy-canales">
                {c.lotes.map((l) => {
                  const hechos = l.aceptados + l.rechazados;
                  return (
                    <div key={l.id} className="sy-canal">
                      <div className="sy-canal-h">
                        <span>
                          <span className="mono">{l.id}</span> · {l.documentos} e-CF
                        </span>
                        <span className="mut">{l.estado === "en_cola" ? "En cola" : `${hechos} de ${l.documentos}`}</span>
                      </div>
                      <div className={l.estado === "interrumpido" ? "pb warn" : l.rechazados ? "pb bad" : "pb"}>
                        <span style={{ width: `${l.documentos ? (hechos * 100) / l.documentos : 0}%` }} />
                      </div>
                    </div>
                  );
                })}
              </div>
            </section>
          ) : (
            <section className="card">
              <div className="card-h">
                <h2>Estado por canal</h2>
                <span className="hint">Calculado de los datos</span>
              </div>
              <div className="card-b sy-canales">
                {(resumen?.canales ?? []).map((k) => {
                  const total = k.pendientes + k.errores;
                  return (
                    <div key={k.canal} className="sy-canal" title={k.detalle ?? undefined}>
                      <div className="sy-canal-h">
                        <span>{k.titulo}</span>
                        <span className={k.errores ? "sy-err" : "mut"}>
                          {k.estado === "ok" ? "Al día" : `${k.errores ? `${k.errores} con error` : ""}${k.errores && k.pendientes ? " · " : ""}${k.pendientes ? `${k.pendientes} en curso` : ""}`}
                        </span>
                      </div>
                      <div className={k.errores ? "pb bad" : k.pendientes ? "pb warn" : "pb"}>
                        <span style={{ width: total ? `${Math.max(8, (k.pendientes * 100) / total)}%` : "100%" }} />
                      </div>
                      <span className="hint">
                        {k.descripcion}
                        {k.ultima_actividad ? ` · último: ${fechaHora(k.ultima_actividad)}` : ""}
                      </span>
                    </div>
                  );
                })}
                {!resumen && <Cargando />}
              </div>
            </section>
          )}

          <section className="card sy-papel">
            <div className="card-b sy-papel-b">
              <div className="sy-papel-h">
                <span className="sy-papel-ic">
                  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z" />
                    <path d="M14 3v6h6M8 13h8M8 17h5" />
                  </svg>
                </span>
                <h3>Registrar facturas manuales en papel</h3>
              </div>
              <p>
                Si hubo una caída total (sin sistema ni firma local), capture aquí los comprobantes emitidos en papel para que se integren a la contabilidad y a
                los formatos 607.
              </p>
              <button type="button" className="btn btn-o" onClick={() => setModalPapel("nueva")}>
                Registrar factura en papel
              </button>
            </div>
          </section>
        </div>
      </div>

      {papel.length > 0 && (
        <section className="card">
          <div className="card-h">
            <h2>Facturas en papel</h2>
            <span className="hint">Emita su e-CF de reemplazo (código de modificación 4) al restablecerse el servicio</span>
          </div>
          <div className="tw">
            <table className="tbl" style={{ minWidth: 760 }}>
              <thead>
                <tr>
                  <th>NCF</th>
                  <th>Fecha</th>
                  <th>Cliente</th>
                  <th className="num">ITBIS (RD$)</th>
                  <th className="num">Total (RD$)</th>
                  <th>e-CF de reemplazo</th>
                  <th style={{ textAlign: "right" }}>Acciones</th>
                </tr>
              </thead>
              <tbody>
                {papel.map((f) => (
                  <tr key={f.id}>
                    <td className="mono">{f.ncf}</td>
                    <td className="nowrap">{fechaLegible(f.fecha)}</td>
                    <td>
                      {f.cliente_nombre ?? "Consumidor final"}
                      {f.cliente_rnc && <div className="hint mono">{formatoRnc(f.cliente_rnc)}</div>}
                    </td>
                    <td className="num">{formatoMonto(f.itbis)}</td>
                    <td className="num" style={{ fontWeight: 600 }}>
                      {formatoMonto(f.total)}
                    </td>
                    <td>{f.encf_reemplazo ? <span className="mono">{f.encf_reemplazo}</span> : <span className="bd b-pend">Pendiente</span>}</td>
                    <td>
                      <div className="fila-acc">
                        <button type="button" className="btn btn-g btn-sm" onClick={() => setModalPapel(f)}>
                          {f.encf_reemplazo ? "Editar" : "Anotar e-CF"}
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      <section className="card">
        <div className="card-h">
          <h2>Historial de envíos</h2>
          <span className="hint">Últimos 7 días</span>
        </div>
        {!cargando && dias.length === 0 && <Vacio titulo="Sin envíos en los últimos 7 días" texto="Los envíos a la DGII y sus resultados aparecen aquí." />}
        {dias.length > 0 && (
          <div className="tw">
            <table className="tbl" style={{ minWidth: 760 }}>
              <thead>
                <tr>
                  <th>Fecha</th>
                  <th className="num">Documentos</th>
                  <th className="num">Aceptados</th>
                  <th className="num">Rechazados</th>
                  <th className="num">Errores</th>
                  <th>Origen</th>
                  <th>Resultado</th>
                </tr>
              </thead>
              <tbody>
                {dias.map((d) => (
                  <tr key={d.fecha}>
                    <td className="mut">{fechaLegible(d.fecha)}</td>
                    <td className="num">{d.documentos}</td>
                    <td className="num" style={{ color: "#15803D", fontWeight: 600 }}>
                      {d.aceptados}
                    </td>
                    <td className="num" style={{ color: d.rechazados ? "#B91C1C" : "#9CA3AF", fontWeight: 600 }}>
                      {d.rechazados}
                    </td>
                    <td className="num" style={{ color: d.errores ? "#C2410C" : "#9CA3AF", fontWeight: 600 }}>
                      {d.errores}
                    </td>
                    <td>{d.origen === "con_fallas" ? "Con fallas de conexión" : "En línea"}</td>
                    <td>
                      <span className={RESULTADO_DIA[d.resultado].badge}>{RESULTADO_DIA[d.resultado].label}</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <Bitacora version={resumen?.eventos_7d} />

      {modalPapel && (
        <FacturaPapelModal
          factura={modalPapel === "nueva" ? null : modalPapel}
          onCerrar={() => setModalPapel(null)}
          onGuardada={(f, nueva) => {
            setModalPapel(null);
            mostrarToast(nueva ? "Comprobante en papel registrado" : "Factura en papel actualizada", {
              detalle: `${f.ncf} · RD$ ${formatoMonto(f.total)}.${f.encf_reemplazo ? "" : " Se pedirá su e-CF de reemplazo al reconectar."}`,
            });
            void cargar();
          }}
          onEliminada={(f) => {
            setModalPapel(null);
            mostrarToast("Factura en papel eliminada", { detalle: f.ncf });
            void cargar();
          }}
        />
      )}
      {toast}
    </div>
  );
}

function AccionItem({ item, ocupado, onReintentar }: { item: ItemSincronizacion; ocupado: boolean; onReintentar: () => void }) {
  const etiqueta: Partial<Record<NonNullable<ItemSincronizacion["accion"]>, string>> = {
    actualizar_estado: "Consultar estado",
    reenviar: "Reenviar",
    reintentar_aprobacion: item.decision === "rechazado" ? "Reintentar rechazo" : "Reintentar aprobación",
    decidir_aprobacion: "Aprobar",
    reintentar_entrega: "Reintentar entrega",
  };
  return (
    <>
      {item.accion && (
        <button type="button" className={item.estado === "error" ? "btn btn-p btn-sm" : "btn btn-o btn-sm"} onClick={onReintentar} disabled={ocupado}>
          {ocupado ? "…" : etiqueta[item.accion]}
        </button>
      )}
      {item.enlace && (
        <Link className="btn btn-g btn-sm" to={item.enlace}>
          Abrir
        </Link>
      )}
    </>
  );
}

/** Auditoría de facturacion.envios_dgii con filtros (módulo, resultado, e-NCF y fechas). */
function Bitacora({ version }: { version?: number }) {
  const [eventos, setEventos] = useState<EventoSincronizacion[]>([]);
  const [modulo, setModulo] = useState("");
  const [resultado, setResultado] = useState<ResultadoEvento | "">("");
  const [q, setQ] = useState("");
  const [desde, setDesde] = useState("");
  const [hasta, setHasta] = useState("");
  const [hayMas, setHayMas] = useState(false);
  const [cargando, setCargando] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const buscar = useCallback(
    (offset: number) => {
      setCargando(true);
      setError(null);
      sincronizacionApi
        .historial({ modulo: modulo || undefined, resultado: resultado || undefined, q: q.trim() || undefined, desde: desde || undefined, hasta: hasta || undefined, limit: PAGINA_HISTORIAL, offset })
        .then((lista) => {
          setEventos((prev) => (offset ? [...prev, ...lista] : lista));
          setHayMas(lista.length === PAGINA_HISTORIAL);
        })
        .catch((err) => setError(mensajeError(err)))
        .finally(() => setCargando(false));
    },
    [modulo, resultado, q, desde, hasta],
  );

  useEffect(() => {
    const t = window.setTimeout(() => buscar(0), 250);
    return () => window.clearTimeout(t);
  }, [buscar, version]);

  return (
    <section className="card">
      <div className="card-h">
        <h2>Bitácora de la DGII</h2>
        <span className="hint">Cada envío, consulta y aprobación auditados</span>
      </div>
      <div className="sy-filtros">
        <select className="inp inp-sm" aria-label="Módulo" value={modulo} onChange={(e) => setModulo(e.target.value)}>
          <option value="">Todos los módulos</option>
          {MODULOS.map(([v, l]) => (
            <option key={v} value={v}>
              {l}
            </option>
          ))}
        </select>
        <select className="inp inp-sm" aria-label="Resultado" value={resultado} onChange={(e) => setResultado(e.target.value as ResultadoEvento | "")}>
          <option value="">Todos los resultados</option>
          {(Object.keys(RESULTADO_EVENTO) as ResultadoEvento[]).map((r) => (
            <option key={r} value={r}>
              {RESULTADO_EVENTO[r].label}
            </option>
          ))}
        </select>
        <input className="inp inp-sm" type="search" placeholder="e-NCF, Track ID o error" aria-label="Buscar en la bitácora" value={q} onChange={(e) => setQ(e.target.value)} />
        <input className="inp inp-sm" type="date" aria-label="Desde" value={desde} onChange={(e) => setDesde(e.target.value)} />
        <input className="inp inp-sm" type="date" aria-label="Hasta" value={hasta} onChange={(e) => setHasta(e.target.value)} />
      </div>
      {error && (
        <div className="pad">
          <AvisoError mensaje={error} onReintentar={() => buscar(0)} />
        </div>
      )}
      {!cargando && !error && eventos.length === 0 && <Vacio titulo="Sin eventos" texto="No hay envíos registrados con estos filtros." />}
      {eventos.length > 0 && (
        <div className="tw">
          <table className="tbl" style={{ minWidth: 900 }}>
            <thead>
              <tr>
                <th>Fecha y hora</th>
                <th>Módulo</th>
                <th>e-NCF</th>
                <th>Track ID</th>
                <th>Respuesta</th>
                <th>Resultado</th>
              </tr>
            </thead>
            <tbody>
              {eventos.map((e) => (
                <tr key={e.id}>
                  <td className="mut nowrap">{fechaHora(e.creado_en)}</td>
                  <td>{e.modulo_label}</td>
                  <td className="mono nowrap">{e.encf ?? "—"}</td>
                  <td className="mono mut sy-track">{e.track_id ?? "—"}</td>
                  <td className={e.error ? "sy-err" : undefined}>{e.error ?? ([e.codigo, e.estado].filter(Boolean).join(" · ") || "—")}</td>
                  <td>
                    <span className={RESULTADO_EVENTO[e.resultado].badge}>{RESULTADO_EVENTO[e.resultado].label}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {cargando && <Cargando />}
      {hayMas && !cargando && (
        <div className="sy-pie">
          <button type="button" className="btn btn-o btn-sm" onClick={() => buscar(eventos.length)}>
            Cargar más
          </button>
        </div>
      )}
    </section>
  );
}
