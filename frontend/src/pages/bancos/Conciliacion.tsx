// Pestaña Conciliación de Bancos.dc.html: el extracto del banco (CSV importado) frente a los registros en
// libros, con las sugerencias por monto y fecha, la conciliación automática o manual, la creación del
// movimiento de una línea sin registro (comisiones, cargos), el cuadre (saldo según el banco, libros y
// partidas en tránsito) y el cierre al corte, que se puede reabrir.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ChangeEvent } from "react";
import { IconAlerta } from "../../layout/icons";
import { bancosApi } from "../../lib/bancosApi";
import type {
  Conciliacion,
  LineaExtracto,
  MovimientoBancario,
  ResultadoImportacion,
  ResumenConciliacion,
  SugerenciaConciliacion,
  TipoMovimientoManual,
} from "../../lib/bancosApi";
import { ErpApiError } from "../../lib/erpApi";
import { contabilidadApi } from "../../lib/contabilidadApi";
import type { CuentaContable } from "../../lib/contabilidadApi";
import { formatoMonto } from "../../lib/marca";
import { AvisoError, Cargando, Modal, Vacio } from "../ingresos/IngresosShell";
import { fechaLegible, hoyIso, mensajeError } from "../ingresos/ingresosUtil";
import type { PestanaBancosProps } from "./Bancos";
import { mesDeFecha, montoMoneda, TIPO_MOVIMIENTO_LABEL, TIPOS_MOVIMIENTO_MANUAL } from "./bancosUtil";
import { ImportarExtracto } from "./ImportarExtracto";

function IconoCheck() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.2} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M20 6 9 17l-5-5" />
    </svg>
  );
}

/** Por qué no se puede cerrar todavía (lo mismo que verifica el backend antes de responder 409). */
function razonesNoCierre(r: ResumenConciliacion, conciliadaHasta: string | null, hasta: string, moneda: string): string[] {
  const razones: string[] = [];
  if (r.lineas_pendientes > 0)
    razones.push(
      `Quedan ${r.lineas_pendientes} ${r.lineas_pendientes === 1 ? "línea" : "líneas"} del extracto sin conciliar hasta el corte: concílielas con su registro o cree el movimiento que falta.`,
    );
  if (Math.abs(Number(r.diferencia)) >= 0.005)
    razones.push(
      `El saldo según el banco (${montoMoneda(r.saldo_banco, moneda)}) no cuadra con el saldo en libros menos las partidas en tránsito (${montoMoneda(r.libros_ajustado, moneda)}): diferencia de ${montoMoneda(r.diferencia, moneda)}.`,
    );
  if (conciliadaHasta && hasta <= conciliadaHasta) razones.push(`La cuenta ya está conciliada hasta el ${fechaLegible(conciliadaHasta)}: elija un corte posterior.`);
  if (hasta > hoyIso()) razones.push("El corte no puede ser una fecha futura.");
  return razones;
}

const ORIGEN_SALDO: Record<ResumenConciliacion["origen_saldo_banco"], string> = {
  extracto: "Saldo final indicado al importar el extracto",
  ultima_linea: "Columna de saldo de la última línea del extracto",
  calculado: "Calculado con las líneas importadas",
};

export function ConciliacionTab({ cuenta, recargarCuentas, mostrarAviso }: PestanaBancosProps) {
  const [hasta, setHasta] = useState("");
  const [datos, setDatos] = useState<Conciliacion | null>(null);
  const [sugerencias, setSugerencias] = useState<SugerenciaConciliacion[]>([]);
  const [cargando, setCargando] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [ocupado, setOcupado] = useState<string | null>(null);
  const [lineaSel, setLineaSel] = useState<string | null>(null);
  const [archivo, setArchivo] = useState<File | null>(null);
  const [omitidas, setOmitidas] = useState<ResultadoImportacion | null>(null);
  const [crearDesde, setCrearDesde] = useState<LineaExtracto | null>(null);
  const [cerrar, setCerrar] = useState(false);
  const [reabrir, setReabrir] = useState(false);
  const inputArchivo = useRef<HTMLInputElement>(null);
  const moneda = cuenta.moneda;

  const cargar = useCallback(async () => {
    setCargando(true);
    const corte = hasta || undefined;
    const [c, s] = await Promise.allSettled([bancosApi.conciliacion(cuenta.id, { hasta: corte }), bancosApi.sugerencias(cuenta.id, { hasta: corte })]);
    if (c.status === "fulfilled") {
      setDatos(c.value);
      setError(null);
    } else {
      setDatos(null);
      setError(mensajeError(c.reason));
    }
    setSugerencias(s.status === "fulfilled" ? s.value : []);
    setCargando(false);
  }, [cuenta.id, hasta]);

  useEffect(() => {
    void cargar();
  }, [cargar]);

  /** Tras cada cambio: vuelve a leer la conciliación y los saldos de las tarjetas. */
  function refrescar() {
    void cargar();
    recargarCuentas();
  }

  async function accion(clave: string, fn: () => Promise<unknown>, ok: string, detalle?: string) {
    setOcupado(clave);
    try {
      await fn();
      mostrarAviso(ok, detalle ? { detalle } : undefined);
      setLineaSel(null);
      refrescar();
    } catch (err) {
      mostrarAviso("No se pudo completar la operación", { detalle: mensajeError(err), error: true });
    } finally {
      setOcupado(null);
    }
  }

  const sugerenciaDe = useMemo(() => new Map(sugerencias.map((s) => [s.linea.id, s])), [sugerencias]);
  const lineas = datos?.lineas ?? [];
  const movimientos = datos?.movimientos_sin_conciliar ?? [];
  const seleccionada = lineas.find((l) => l.id === lineaSel) ?? null;
  const r = datos?.resumen;
  const corte = datos?.hasta ?? hasta;
  const pct = r && r.lineas_total > 0 ? Math.round((r.lineas_conciliadas / r.lineas_total) * 100) : 0;
  const razones = r && datos ? razonesNoCierre(r, datos.conciliada_hasta, datos.hasta, moneda) : [];

  function marcar(l: LineaExtracto) {
    if (l.conciliada) {
      void accion(`l-${l.id}`, () => bancosApi.desconciliarLinea(l.id), "Línea desconciliada", l.descripcion);
      return;
    }
    const s = sugerenciaDe.get(l.id);
    if (s) {
      void accion(`l-${l.id}`, () => bancosApi.conciliarLinea(l.id, s.movimiento.origen, s.movimiento.id), "Línea conciliada", `${l.descripcion} ↔ ${s.movimiento.descripcion}`);
    } else {
      setLineaSel(lineaSel === l.id ? null : l.id);
    }
  }

  function conciliarCon(m: MovimientoBancario) {
    if (!seleccionada) return;
    void accion(`m-${m.id}`, () => bancosApi.conciliarLinea(seleccionada.id, m.origen, m.id), "Línea conciliada", `${seleccionada.descripcion} ↔ ${m.descripcion}`);
  }

  async function automatico() {
    setOcupado("auto");
    try {
      const res = await bancosApi.conciliarAutomatico(cuenta.id, { hasta: hasta || undefined });
      mostrarAviso(res.conciliadas ? `${res.conciliadas} ${res.conciliadas === 1 ? "línea conciliada" : "líneas conciliadas"} automáticamente` : "No hubo coincidencias nuevas", {
        detalle: res.conciliadas ? "Mismo monto y fechas cercanas (±3 días); revise las que quedan pendientes." : "Concilie a mano o cree el movimiento de las líneas sin registro.",
      });
      setLineaSel(null);
      refrescar();
    } catch (err) {
      mostrarAviso("No se pudo conciliar automáticamente", { detalle: mensajeError(err), error: true });
    } finally {
      setOcupado(null);
    }
  }

  function elegirArchivo(e: ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0];
    e.target.value = "";
    if (f) setArchivo(f);
  }

  function importado(res: ResultadoImportacion) {
    setArchivo(null);
    setOmitidas(res.errores.length ? res : null);
    mostrarAviso(res.importadas ? `${res.importadas} ${res.importadas === 1 ? "línea importada" : "líneas importadas"}` : "No había líneas nuevas", {
      detalle: [res.duplicadas ? `${res.duplicadas} ya estaban importadas` : "", res.errores.length ? `${res.errores.length} ${res.errores.length === 1 ? "fila omitida" : "filas omitidas"}` : ""].filter(Boolean).join(" · ") || undefined,
    });
    // Sin corte elegido, el backend toma la fecha de la última línea importada.
    refrescar();
  }

  return (
    <>
      <section className="card card-b bk-ctl no-imprimir">
        <div className="fld">
          <label className="lbl" htmlFor="cc-hasta">Conciliar hasta (corte)</label>
          <input id="cc-hasta" type="date" className="inp" value={hasta || datos?.hasta || ""} max={hoyIso()} onChange={(e) => setHasta(e.target.value)} />
        </div>
        <span className="hint grow">
          {datos?.conciliada_hasta ? `Conciliada hasta el ${fechaLegible(datos.conciliada_hasta)} · se muestran las líneas posteriores.` : "Aún no tiene conciliaciones cerradas."}
        </span>
        <input ref={inputArchivo} type="file" accept=".csv,.txt,text/csv" className="bk-file" onChange={elegirArchivo} aria-label="Archivo CSV del estado de cuenta" tabIndex={-1} />
        <button type="button" className="btn btn-o" onClick={() => inputArchivo.current?.click()}>
          Importar extracto (CSV)
        </button>
        <button type="button" className="btn btn-p" onClick={automatico} disabled={!!ocupado || sugerencias.length === 0}>
          {ocupado === "auto" ? "Conciliando…" : "Conciliar automáticamente"}
        </button>
      </section>

      {omitidas && (
        <div className="al al-w" role="status">
          <IconAlerta />
          <div style={{ flex: 1 }}>
            <b>
              {omitidas.errores.length} {omitidas.errores.length === 1 ? "fila del archivo no se importó" : "filas del archivo no se importaron"}:
            </b>
            <ul className="bk-lista">
              {omitidas.errores.slice(0, 8).map((e) => (
                <li key={e.fila}>
                  Fila {e.fila}: {e.motivo}
                </li>
              ))}
              {omitidas.errores.length > 8 && <li>… y {omitidas.errores.length - 8} más</li>}
            </ul>
          </div>
          <button type="button" className="btn btn-g btn-sm" onClick={() => setOmitidas(null)}>
            Cerrar
          </button>
        </div>
      )}

      {error && <AvisoError titulo="No se pudo cargar la conciliación." mensaje={error} onReintentar={() => void cargar()} />}
      {cargando && !datos && (
        <section className="card">
          <Cargando />
        </section>
      )}

      {datos?.ultima_conciliacion && (
        <div className="al al-i" role="status">
          <IconAlerta />
          <span style={{ flex: 1 }}>
            <b>Conciliada al {fechaLegible(datos.ultima_conciliacion.hasta)}.</b> Saldo según el banco {montoMoneda(datos.ultima_conciliacion.saldo_banco, moneda)} ·{" "}
            {datos.ultima_conciliacion.lineas_conciliadas} líneas conciliadas · {datos.ultima_conciliacion.movimientos_en_transito} partidas en tránsito · cerrada el{" "}
            {fechaLegible(datos.ultima_conciliacion.cerrada_en)}. Hasta esa fecha no se pueden registrar ni conciliar movimientos.
          </span>
          <button type="button" className="btn btn-o btn-sm" onClick={() => setReabrir(true)}>
            Reabrir
          </button>
        </div>
      )}

      {r && (r.advertencias ?? []).length > 0 && (
        <div className="al al-w" role="status">
          <IconAlerta />
          <div>
            <b>Revise antes de cerrar:</b>
            <ul className="bk-lista">
              {r.advertencias.map((a) => (
                <li key={a}>{a}</li>
              ))}
            </ul>
          </div>
        </div>
      )}

      {r && (
        <div className="bk-stats">
          <div className="card stat">
            <span className="k">Saldo según el banco</span>
            <span className="v">{montoMoneda(r.saldo_banco, moneda)}</span>
            <span className="s">{ORIGEN_SALDO[r.origen_saldo_banco]}</span>
          </div>
          <div className="card stat">
            <span className="k">Saldo en libros</span>
            <span className="v">{montoMoneda(r.saldo_libros, moneda)}</span>
            <span className="s">Al {fechaLegible(corte)}</span>
          </div>
          <div className="card stat">
            <span className="k">Partidas en tránsito</span>
            <span className="v">{montoMoneda(Number(r.transito_entradas) - Number(r.transito_salidas), moneda)}</span>
            <span className="s">
              {r.movimientos_en_transito} · depósitos {formatoMonto(r.transito_entradas)} · cheques y pagos {formatoMonto(r.transito_salidas)}
            </span>
          </div>
          <div className="card stat">
            <span className="k">Diferencia</span>
            <span className={Math.abs(Number(r.diferencia)) >= 0.005 ? "v down" : "v"}>{montoMoneda(r.diferencia, moneda)}</span>
            <span className="s">Banco − (libros − tránsito) = {montoMoneda(r.libros_ajustado, moneda)}</span>
          </div>
        </div>
      )}

      {datos && sugerencias.length > 0 && (
        <section className="card no-imprimir">
          <div className="card-h">
            <div>
              <h2>Sugerencias de conciliación</h2>
              <span className="hint">Mismo monto y fechas cercanas; la referencia coincidente suma puntos</span>
            </div>
            <span className="chip">{sugerencias.length}</span>
          </div>
          <div className="bk-sug">
            {sugerencias.map((s) => (
              <div key={s.linea.id} className="bk-sug-i">
                <div className="bk-sug-t">
                  <span>
                    <b style={{ color: "#111827" }}>{s.linea.descripcion}</b> <span className="mut">({fechaLegible(s.linea.fecha)})</span> ↔ {s.movimiento.descripcion}{" "}
                    <span className="mut">({fechaLegible(s.movimiento.fecha)})</span>
                  </span>
                  <span className="hint">
                    {s.motivo} · puntaje {s.puntaje}
                  </span>
                </div>
                <span className="bk-sug-m">{montoMoneda(s.linea.monto, moneda)}</span>
                <button
                  type="button"
                  className="btn btn-o btn-sm"
                  disabled={!!ocupado}
                  onClick={() =>
                    void accion(`s-${s.linea.id}`, () => bancosApi.conciliarLinea(s.linea.id, s.movimiento.origen, s.movimiento.id), "Línea conciliada", `${s.linea.descripcion} ↔ ${s.movimiento.descripcion}`)
                  }
                >
                  {ocupado === `s-${s.linea.id}` ? "Conciliando…" : "Conciliar"}
                </button>
              </div>
            ))}
          </div>
        </section>
      )}

      {datos && (
        <div className="bk-split">
          <section className="card">
            <div className="card-h">
              <div>
                <h2>Conciliación · {mesDeFecha(corte)}</h2>
                <span className="hint">Marque los movimientos del extracto que coinciden con sus registros</span>
              </div>
              <span className="chip">
                {r?.lineas_conciliadas ?? 0} de {r?.lineas_total ?? 0} conciliados
              </span>
            </div>
            <div style={{ padding: "0 20px 12px" }}>
              <div className="pb">
                <span style={{ width: `${pct}%` }} />
              </div>
            </div>
            {lineas.length === 0 ? (
              <Vacio
                titulo="No hay líneas del extracto en este período"
                texto="Descargue el estado de cuenta en CSV desde la banca en línea de su banco e impórtelo: se detectan las columnas de fecha, descripción, referencia, débito/crédito y balance."
              >
                <button type="button" className="btn btn-p btn-sm" onClick={() => inputArchivo.current?.click()}>
                  Importar extracto (CSV)
                </button>
              </Vacio>
            ) : (
              <div className="tw">
                <table className="tbl" style={{ minWidth: 600 }}>
                  <thead>
                    <tr>
                      <th>Conciliado</th>
                      <th>Fecha</th>
                      <th>Extracto bancario</th>
                      <th>Registro en el sistema</th>
                      <th className="num">Monto ({moneda === "USD" ? "US$" : "RD$"})</th>
                    </tr>
                  </thead>
                  <tbody>
                    {lineas.map((l) => {
                      const s = sugerenciaDe.get(l.id);
                      const n = Number(l.monto);
                      return (
                        <tr key={l.id} className={l.id === lineaSel ? "bk-sel" : undefined}>
                          <td>
                            <input
                              type="checkbox"
                              checked={l.conciliada}
                              onChange={() => marcar(l)}
                              disabled={ocupado === `l-${l.id}`}
                              aria-label={`Conciliar ${l.descripcion}`}
                              style={{ width: 18, height: 18, accentColor: "#0F766E" }}
                            />
                          </td>
                          <td className="mut nowrap">{fechaLegible(l.fecha)}</td>
                          <td>
                            {l.descripcion}
                            {l.referencia && <div className="hint mono">{l.referencia}</div>}
                          </td>
                          <td>
                            {l.movimiento ? (
                              <span className="bk-reg">
                                <IconoCheck />
                                {l.movimiento.descripcion}
                              </span>
                            ) : s ? (
                              <span className="mut">Sugerido: {s.movimiento.descripcion}</span>
                            ) : (
                              <span className="bk-acc-l">
                                <button type="button" className="btn btn-g btn-sm" onClick={() => setCrearDesde(l)}>
                                  Sin registro: crear movimiento
                                </button>
                                <button type="button" className="btn btn-g btn-sm" onClick={() => setLineaSel(lineaSel === l.id ? null : l.id)} aria-pressed={l.id === lineaSel}>
                                  {l.id === lineaSel ? "Elegir en libros →" : "Emparejar"}
                                </button>
                              </span>
                            )}
                          </td>
                          <td className={n < 0 ? "num bk-deb" : "num bk-cre"}>{formatoMonto(n)}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
            <div className="bk-pie">
              <span className="hint">
                {razones.length === 0 ? `Todo cuadra al ${fechaLegible(corte)}: puede cerrar la conciliación.` : razones[0]}
              </span>
              <button type="button" className="btn btn-p" onClick={() => setCerrar(true)}>
                Cerrar conciliación
              </button>
            </div>
          </section>

          <section className="card">
            <div className="card-h">
              <div>
                <h2>Registros en libros sin conciliar</h2>
                <span className="hint">
                  {seleccionada
                    ? `Elija el registro de ${montoMoneda(seleccionada.monto, moneda)} para «${seleccionada.descripcion}»`
                    : "Cobros, pagos y movimientos que el banco aún no refleja"}
                </span>
              </div>
              <span className="chip">{movimientos.length}</span>
            </div>
            {movimientos.length === 0 ? (
              <Vacio titulo="Todo está conciliado" texto="No hay registros en libros pendientes de aparecer en el extracto." />
            ) : (
              <div className="tw">
                <table className="tbl" style={{ minWidth: 520 }}>
                  <thead>
                    <tr>
                      <th>Fecha</th>
                      <th>Registro</th>
                      <th className="num">Monto</th>
                      <th style={{ textAlign: "right" }}>Acciones</th>
                    </tr>
                  </thead>
                  <tbody>
                    {movimientos.map((m) => {
                      const igual = seleccionada ? Number(seleccionada.monto).toFixed(2) === Number(m.monto).toFixed(2) : false;
                      return (
                        <tr key={`${m.origen}-${m.id}`} className={igual ? "bk-match" : undefined}>
                          <td className="mut nowrap">{fechaLegible(m.fecha)}</td>
                          <td>
                            {m.descripcion}
                            <div className="hint">
                              {TIPO_MOVIMIENTO_LABEL[m.tipo] ?? m.tipo}
                              {m.referencia ? ` · ${m.referencia}` : ""}
                            </div>
                          </td>
                          <td className={Number(m.monto) < 0 ? "num bk-deb" : "num bk-cre"}>{formatoMonto(m.monto)}</td>
                          <td>
                            <div className="fila-acc">
                              {seleccionada && (
                                <button
                                  type="button"
                                  className="btn btn-o btn-sm"
                                  disabled={!igual || !!ocupado}
                                  title={igual ? undefined : "El monto no coincide con la línea del extracto"}
                                  onClick={() => conciliarCon(m)}
                                >
                                  {ocupado === `m-${m.id}` ? "Conciliando…" : "Conciliar"}
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
        </div>
      )}

      {archivo && <ImportarExtracto cuenta={cuenta} archivo={archivo} onCerrar={() => setArchivo(null)} onImportado={importado} />}

      {crearDesde && (
        <CrearMovimiento
          linea={crearDesde}
          moneda={moneda}
          onCerrar={() => setCrearDesde(null)}
          onCreado={(l) => {
            setCrearDesde(null);
            mostrarAviso("Movimiento creado y conciliado", { detalle: `${l.descripcion} · ${montoMoneda(l.monto, moneda)}` });
            refrescar();
          }}
        />
      )}

      {cerrar && r && datos && (
        <CerrarConciliacion
          cuentaId={cuenta.id}
          hasta={datos.hasta}
          resumen={r}
          razones={razones}
          moneda={moneda}
          onCerrar={() => setCerrar(false)}
          onCerrada={() => {
            setCerrar(false);
            mostrarAviso(`Conciliación de ${mesDeFecha(datos.hasta)} cerrada`, { detalle: `Al ${fechaLegible(datos.hasta)} · ${cuenta.nombre}` });
            refrescar();
          }}
        />
      )}

      {reabrir && datos?.ultima_conciliacion && (
        <Modal
          titulo="¿Reabrir la conciliación?"
          onCerrar={() => setReabrir(false)}
          pie={
            <>
              <button type="button" className="btn btn-o" onClick={() => setReabrir(false)}>
                Cancelar
              </button>
              <button
                type="button"
                className="btn btn-p"
                disabled={!!ocupado}
                onClick={() => {
                  const c = datos.ultima_conciliacion!;
                  setReabrir(false);
                  void accion("reabrir", () => bancosApi.reabrirConciliacion(c.id), "Conciliación reabierta", `Al ${fechaLegible(c.hasta)}: puede volver a conciliar y registrar movimientos de esas fechas.`);
                }}
              >
                Sí, reabrir
              </button>
            </>
          }
        >
          <span>
            Se reabre la conciliación al <b style={{ color: "#111827" }}>{fechaLegible(datos.ultima_conciliacion.hasta)}</b>: sus líneas vuelven a poder conciliarse y desconciliarse, y se pueden
            registrar o anular movimientos de esas fechas. Solo se puede reabrir la última.
          </span>
        </Modal>
      )}
    </>
  );
}

/** "Sin registro: crear gasto bancario": registra la línea como movimiento y la deja conciliada. */
function CrearMovimiento({
  linea,
  moneda,
  onCerrar,
  onCreado,
}: {
  linea: LineaExtracto;
  moneda: string;
  onCerrar: () => void;
  onCreado: (l: LineaExtracto) => void;
}) {
  const entra = Number(linea.monto) > 0;
  const opciones = TIPOS_MOVIMIENTO_MANUAL.filter((t) => t.entra === entra);
  const [tipo, setTipo] = useState<TipoMovimientoManual>(entra ? "deposito" : /comisi/i.test(linea.descripcion) ? "comision" : "cargo_bancario");
  const [descripcion, setDescripcion] = useState(linea.descripcion);
  const [contable, setContable] = useState("");
  const [tasa, setTasa] = useState("");
  const [catalogo, setCatalogo] = useState<CuentaContable[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [guardando, setGuardando] = useState(false);

  useEffect(() => {
    contabilidadApi
      .listarCuentas({ solo_movimiento: true, activa: true })
      .then(setCatalogo)
      .catch(() => setCatalogo([]));
  }, []);

  async function crear() {
    if (moneda === "USD" && !(Number(tasa) > 0)) return setError("Indique la tasa de cambio (RD$ por US$).");
    setGuardando(true);
    try {
      onCreado(
        await bancosApi.crearMovimientoDesdeLinea(linea.id, {
          tipo,
          descripcion: descripcion.trim() || null,
          cuenta_contable_id: contable || null,
          tasa_cambio: moneda === "USD" ? Number(tasa).toFixed(4) : null,
        }),
      );
    } catch (err) {
      setError(mensajeError(err));
      setGuardando(false);
    }
  }

  return (
    <Modal
      titulo="Crear movimiento desde el extracto"
      onCerrar={onCerrar}
      ancho={560}
      pie={
        <>
          <button type="button" className="btn btn-o" onClick={onCerrar}>
            Cancelar
          </button>
          <button type="button" className="btn btn-p" onClick={crear} disabled={guardando}>
            {guardando ? "Creando…" : "Crear y conciliar"}
          </button>
        </>
      }
    >
      <span>
        {fechaLegible(linea.fecha)} · <b style={{ color: "#111827" }}>{linea.descripcion}</b> · {montoMoneda(linea.monto, moneda)}
        {linea.referencia ? ` · ${linea.referencia}` : ""}. Se registra en la cuenta con esa fecha y monto y queda conciliado con la línea.
      </span>
      <div className="fld">
        <label className="lbl" htmlFor="cm-tipo">Tipo</label>
        <select id="cm-tipo" className="inp" value={tipo} onChange={(e) => setTipo(e.target.value as TipoMovimientoManual)}>
          {opciones.map((t) => (
            <option key={t.value} value={t.value}>
              {t.label}
            </option>
          ))}
        </select>
      </div>
      <div className="fld">
        <label className="lbl" htmlFor="cm-desc">Descripción</label>
        <input id="cm-desc" className="inp" value={descripcion} onChange={(e) => setDescripcion(e.target.value)} />
      </div>
      <div className="fld">
        <label className="lbl" htmlFor="cm-cc">Contrapartida contable</label>
        <select id="cm-cc" className="inp" value={contable} onChange={(e) => setContable(e.target.value)}>
          <option value="">Por defecto ({entra ? (tipo === "interes" ? "ingresos financieros" : "caja general") : tipo === "retiro" ? "caja general" : "gastos bancarios"})</option>
          {catalogo.map((c) => (
            <option key={c.id} value={c.id}>
              {c.codigo} · {c.nombre}
            </option>
          ))}
        </select>
      </div>
      {moneda === "USD" && (
        <div className="fld">
          <label className="lbl" htmlFor="cm-tasa">Tasa (RD$ por US$)</label>
          <input id="cm-tasa" type="number" min="0" step="0.0001" className="inp" value={tasa} onChange={(e) => setTasa(e.target.value)} />
        </div>
      )}
      {error && (
        <div className="al al-d" role="alert">
          <IconAlerta />
          <span>
            <b>{error}</b>
          </span>
        </div>
      )}
    </Modal>
  );
}

/** "Cerrar conciliación" con el cuadre a la vista y la explicación del 409 si el backend no la deja cerrar. */
function CerrarConciliacion({
  cuentaId,
  hasta,
  resumen,
  razones,
  moneda,
  onCerrar,
  onCerrada,
}: {
  cuentaId: string;
  hasta: string;
  resumen: ResumenConciliacion;
  razones: string[];
  moneda: string;
  onCerrar: () => void;
  onCerrada: () => void;
}) {
  const [saldo, setSaldo] = useState(Number(resumen.saldo_banco).toFixed(2));
  const [notas, setNotas] = useState("");
  const [error, setError] = useState<{ mensaje: string; conflicto: boolean } | null>(null);
  const [guardando, setGuardando] = useState(false);

  async function confirmar() {
    if (saldo.trim() !== "" && !Number.isFinite(Number(saldo))) return setError({ mensaje: "El saldo según el banco no es un monto válido.", conflicto: false });
    setGuardando(true);
    setError(null);
    try {
      const cambiado = saldo.trim() !== "" && Number(saldo).toFixed(2) !== Number(resumen.saldo_banco).toFixed(2);
      await bancosApi.cerrarConciliacion(cuentaId, { hasta, saldo_banco: cambiado ? Number(saldo).toFixed(2) : null, notas: notas.trim() || null });
      onCerrada();
    } catch (err) {
      setError({ mensaje: mensajeError(err), conflicto: err instanceof ErpApiError && err.status === 409 });
      setGuardando(false);
    }
  }

  const ajustado = Number(resumen.libros_ajustado);
  const diferencia = (Number(saldo) || 0) - ajustado;

  return (
    <Modal
      titulo={`Cerrar conciliación al ${fechaLegible(hasta)}`}
      onCerrar={onCerrar}
      ancho={580}
      pie={
        <>
          <button type="button" className="btn btn-o" onClick={onCerrar}>
            Cancelar
          </button>
          <button type="button" className="btn btn-p" onClick={confirmar} disabled={guardando}>
            {guardando ? "Cerrando…" : "Cerrar conciliación"}
          </button>
        </>
      }
    >
      <span>Al cerrarla, lo registrado en Bancos con fecha hasta el corte queda bloqueado hasta que la reabra. Los cobros y pagos nuevos con esas fechas pasan como partidas en tránsito.</span>
      <dl className="bk-resumen">
        <dt>Saldo en libros</dt>
        <dd>{montoMoneda(resumen.saldo_libros, moneda)}</dd>
        <dt>− Depósitos en tránsito</dt>
        <dd>{montoMoneda(resumen.transito_entradas, moneda)}</dd>
        <dt>+ Cheques y pagos en circulación</dt>
        <dd>{montoMoneda(resumen.transito_salidas, moneda)}</dd>
        <dt>Libros ajustado</dt>
        <dd>{montoMoneda(ajustado, moneda)}</dd>
        <dt>Diferencia con el banco</dt>
        <dd style={{ color: Math.abs(diferencia) >= 0.005 ? "#B91C1C" : "#15803D" }}>{montoMoneda(diferencia, moneda)}</dd>
      </dl>
      <div className="fld">
        <label className="lbl" htmlFor="cz-saldo">Saldo según el estado de cuenta ({moneda === "USD" ? "US$" : "RD$"})</label>
        <input id="cz-saldo" type="number" step="0.01" className="inp" style={{ textAlign: "right" }} value={saldo} onChange={(e) => setSaldo(e.target.value)} />
        <span className="hint">{ORIGEN_SALDO[resumen.origen_saldo_banco]}; corríjalo si su estado de cuenta dice otra cosa.</span>
      </div>
      <div className="fld">
        <label className="lbl" htmlFor="cz-notas">Notas</label>
        <input id="cz-notas" className="inp" value={notas} onChange={(e) => setNotas(e.target.value)} placeholder="Revisado por…" />
      </div>
      {(resumen.advertencias ?? []).length > 0 && (
        <div className="al al-w" role="status">
          <IconAlerta />
          <div>
            {resumen.advertencias.map((a) => (
              <div key={a}>{a}</div>
            ))}
          </div>
        </div>
      )}
      {razones.length > 0 && !error && (
        <div className="al al-w" role="status">
          <IconAlerta />
          <div>
            <b>Todavía no cuadra:</b>
            <ul className="bk-lista">
              {razones.map((x) => (
                <li key={x}>{x}</li>
              ))}
            </ul>
          </div>
        </div>
      )}
      {error && (
        <div className="al al-d" role="alert">
          <IconAlerta />
          <div>
            <b>{error.conflicto ? "No se puede cerrar la conciliación todavía. " : ""}</b>
            {error.mensaje}
            {error.conflicto && razones.length > 0 && (
              <ul className="bk-lista">
                {razones.map((x) => (
                  <li key={x}>{x}</li>
                ))}
              </ul>
            )}
            {error.conflicto && <div style={{ marginTop: 6 }}>Concilie o cree el movimiento de cada línea pendiente, corrija el saldo si no coincide con su estado de cuenta, y vuelva a intentarlo.</div>}
          </div>
        </div>
      )}
    </Modal>
  );
}
