// Pestaña Asientos de Contabilidad.dc.html: "Asientos del período" con filtros por período, origen y estado,
// "Generar asientos del período" (con lo creado/actualizado/anulado y los documentos que no se pudieron
// contabilizar, con el enlace para corregirlos) y las acciones contabilizar / anular.
import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { IconAlerta } from "../../layout/icons";
import { contabilidadApi } from "../../lib/contabilidadApi";
import type { AsientoResumen, DocumentoNoContabilizado, EstadoAsiento, OrigenAsiento, ResultadoGeneracion } from "../../lib/contabilidadApi";
import { formatoMonto } from "../../lib/marca";
import { AvisoError, Buscador, Cargando, Modal, Vacio } from "../ingresos/IngresosShell";
import { fechaLegible, mensajeError } from "../ingresos/ingresosUtil";
import { nombrePeriodo, periodosRecientes } from "../impuestos/impuestosUtil";
import type { MostrarAviso } from "./Contabilidad";
import { enlaceCorreccion, ESTADO_ASIENTO, ORIGEN_LABEL, ORIGENES_ASIENTO, rangoDePeriodo, sePuedeAnular, usePeriodoContable } from "./contabilidadUtil";

export function NoContabilizados({ lista }: { lista: DocumentoNoContabilizado[] }) {
  if (lista.length === 0) return null;
  return (
    <div className="tw">
      <table className="tbl" style={{ minWidth: 860 }}>
        <thead>
          <tr>
            <th>Fecha</th>
            <th>Documento</th>
            <th>Motivo</th>
            <th className="num">Monto (RD$)</th>
            <th style={{ textAlign: "right" }}>Corregir</th>
          </tr>
        </thead>
        <tbody>
          {lista.map((d) => {
            const e = enlaceCorreccion(d);
            return (
              <tr key={`${d.origen}-${d.origen_id}`}>
                <td className="mut nowrap">{fechaLegible(d.fecha)}</td>
                <td>
                  {d.descripcion}
                  <div className="hint">
                    {ORIGEN_LABEL[d.origen] ?? d.origen}
                    {d.referencia && (
                      <>
                        {" · "}
                        <span className="mono">{d.referencia}</span>
                      </>
                    )}
                  </div>
                </td>
                <td className="err-t" style={{ whiteSpace: "normal" }}>
                  {d.motivo}
                </td>
                <td className="num">{d.monto != null ? formatoMonto(d.monto) : "—"}</td>
                <td>
                  <div className="fila-acc">
                    <Link className="btn btn-g btn-sm" to={e.to}>
                      {e.label}
                    </Link>
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function ModalAnular({ asiento, onCerrar, onAnulado }: { asiento: Pick<AsientoResumen, "id" | "numero" | "concepto" | "origen">; onCerrar: () => void; onAnulado: () => void }) {
  const [motivo, setMotivo] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [anulando, setAnulando] = useState(false);

  async function anular() {
    setAnulando(true);
    try {
      await contabilidadApi.anularAsiento(asiento.id, motivo.trim() || undefined);
      onAnulado();
    } catch (err) {
      setError(mensajeError(err));
      setAnulando(false);
    }
  }

  return (
    <Modal
      titulo={`¿Anular el asiento ${asiento.numero}?`}
      onCerrar={onCerrar}
      pie={
        <>
          <button type="button" className="btn btn-o" onClick={onCerrar}>
            Cancelar
          </button>
          <button type="button" className="btn btn-d" onClick={anular} disabled={anulando}>
            {anulando ? "Anulando…" : "Sí, anular"}
          </button>
        </>
      }
    >
      <span>
        {asiento.concepto}. El asiento queda anulado (no se borra) y deja de sumar en los reportes.
        {asiento.origen === "depreciacion" && " Después puede volver a calcular la depreciación del mes."}
      </span>
      <div className="fld">
        <label className="lbl" htmlFor="an-mot">Motivo</label>
        <input id="an-mot" className="inp" value={motivo} onChange={(e) => setMotivo(e.target.value)} placeholder="Error en el monto, duplicado…" />
      </div>
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

export function AsientosTab({ mostrarAviso }: { mostrarAviso: MostrarAviso }) {
  const navigate = useNavigate();
  const [periodo, setPeriodo] = usePeriodoContable();
  const [origen, setOrigen] = useState<"" | OrigenAsiento | "automatico">("");
  const [estado, setEstado] = useState<"" | EstadoAsiento>("");
  const [q, setQ] = useState("");
  const [qAplicada, setQAplicada] = useState("");
  const [asientos, setAsientos] = useState<AsientoResumen[]>([]);
  const [cargando, setCargando] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [pendientes, setPendientes] = useState<ResultadoGeneracion | null>(null);
  const [resultado, setResultado] = useState<ResultadoGeneracion | null>(null);
  const [generando, setGenerando] = useState(false);
  const [ocupado, setOcupado] = useState<string | null>(null);
  const [anular, setAnular] = useState<AsientoResumen | null>(null);
  const rango = rangoDePeriodo(periodo);

  useEffect(() => {
    const t = window.setTimeout(() => setQAplicada(q.trim()), 350);
    return () => window.clearTimeout(t);
  }, [q]);

  const cargar = useCallback(async () => {
    setCargando(true);
    const r = rangoDePeriodo(periodo);
    const [lista, pend] = await Promise.allSettled([
      contabilidadApi.listarAsientos({ ...r, origen: origen || undefined, estado: estado || undefined, q: qAplicada || undefined }),
      contabilidadApi.pendientesDeContabilizar(r),
    ]);
    if (lista.status === "fulfilled") {
      setAsientos(lista.value);
      setError(null);
    } else {
      setAsientos([]);
      setError(mensajeError(lista.reason));
    }
    setPendientes(pend.status === "fulfilled" ? pend.value : null);
    setCargando(false);
  }, [periodo, origen, estado, qAplicada]);

  useEffect(() => {
    void cargar();
  }, [cargar]);

  useEffect(() => setResultado(null), [periodo]);

  async function generar() {
    setGenerando(true);
    try {
      const res = await contabilidadApi.generarAsientos(rango.desde, rango.hasta);
      setResultado(res);
      const hechos = res.creados + res.actualizados + res.anulados;
      mostrarAviso(hechos ? "Asientos del período generados" : "Los asientos ya estaban al día", {
        detalle: `${res.creados} creados · ${res.actualizados} actualizados · ${res.anulados} anulados${res.no_contabilizados.length ? ` · ${res.no_contabilizados.length} sin contabilizar` : ""}`,
      });
      void cargar();
    } catch (err) {
      mostrarAviso("No se pudieron generar los asientos", { detalle: mensajeError(err), error: true });
    } finally {
      setGenerando(false);
    }
  }

  async function contabilizar(a: AsientoResumen) {
    setOcupado(a.id);
    try {
      await contabilidadApi.contabilizarAsiento(a.id);
      mostrarAviso("Asiento contabilizado", { detalle: `${a.numero} · ${a.concepto}` });
      void cargar();
    } catch (err) {
      mostrarAviso(`No se pudo contabilizar ${a.numero}`, { detalle: mensajeError(err), error: true });
    } finally {
      setOcupado(null);
    }
  }

  const nombre = nombrePeriodo(periodo);
  const porHacer = pendientes?.pendientes ?? [];
  const sinCuenta = (resultado ?? pendientes)?.no_contabilizados ?? [];
  const filtrando = Boolean(origen || estado || qAplicada);
  const totalDebito = asientos.filter((a) => a.estado === "contabilizado").reduce((s, a) => s + Number(a.total_debito), 0);

  return (
    <>
      <section className="card card-b ct-ctl">
        <div className="fld">
          <label className="lbl" htmlFor="as-per">Período</label>
          <select id="as-per" className="inp" value={periodo} onChange={(e) => setPeriodo(e.target.value)}>
            {periodosRecientes(24).map((p) => (
              <option key={p} value={p}>
                {nombrePeriodo(p, true)}
              </option>
            ))}
          </select>
        </div>
        <div className="fld">
          <label className="lbl" htmlFor="as-org">Origen</label>
          <select id="as-org" className="inp" value={origen} onChange={(e) => setOrigen(e.target.value as typeof origen)}>
            <option value="">Todos</option>
            <option value="automatico">Automáticos</option>
            {ORIGENES_ASIENTO.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </div>
        <div className="fld">
          <label className="lbl" htmlFor="as-est">Estado</label>
          <select id="as-est" className="inp" value={estado} onChange={(e) => setEstado(e.target.value as typeof estado)}>
            <option value="">Todos</option>
            <option value="borrador">Borradores</option>
            <option value="contabilizado">Contabilizados</option>
            <option value="anulado">Anulados</option>
          </select>
        </div>
        <Buscador valor={q} onCambiar={setQ} placeholder="Buscar número, concepto o referencia" />
        <button type="button" className="btn btn-o" onClick={generar} disabled={generando}>
          {generando ? "Generando…" : "Generar asientos del período"}
        </button>
        <Link className="btn btn-p" to="/contabilidad/asientos/nuevo">
          + Nuevo asiento
        </Link>
      </section>

      {!resultado && porHacer.length > 0 && (
        <div className="al al-w" role="status">
          <IconAlerta />
          <span style={{ flex: 1 }}>
            <b>
              {porHacer.length} {porHacer.length === 1 ? "documento" : "documentos"} de {nombre} por contabilizar:
            </b>{" "}
            {["crear", "actualizar", "anular"]
              .map((acc) => [acc, porHacer.filter((p) => p.accion === acc).length] as const)
              .filter(([, n]) => n > 0)
              .map(([acc, n]) => `${n} por ${acc}`)
              .join(" · ")}
            . Genere los asientos del período para ponerlos al día.
          </span>
        </div>
      )}

      {resultado && (
        <section className="card">
          <div className="card-h">
            <div>
              <h2>Asientos generados · {nombre}</h2>
              <span className="hint">
                {fechaLegible(resultado.desde)} – {fechaLegible(resultado.hasta)} · volver a generar no duplica: solo crea lo que falta y actualiza lo que cambió
              </span>
            </div>
            <button type="button" className="btn btn-g btn-sm" onClick={() => setResultado(null)}>
              Cerrar
            </button>
          </div>
          <div className="card-b ct-gen">
            <div className="card stat">
              <span className="k">Creados</span>
              <span className="v">{resultado.creados}</span>
            </div>
            <div className="card stat">
              <span className="k">Actualizados</span>
              <span className="v">{resultado.actualizados}</span>
            </div>
            <div className="card stat">
              <span className="k">Anulados</span>
              <span className="v">{resultado.anulados}</span>
            </div>
            <div className="card stat">
              <span className="k">Sin cambios</span>
              <span className="v">{resultado.sin_cambios}</span>
            </div>
            <div className="card stat">
              <span className="k">No contabilizados</span>
              <span className={resultado.no_contabilizados.length ? "v down" : "v"}>{resultado.no_contabilizados.length}</span>
            </div>
          </div>
        </section>
      )}

      {sinCuenta.length > 0 && (
        <section className="card card-danger">
          <div className="card-h">
            <div>
              <h2>Documentos que no se pudieron contabilizar</h2>
              <span className="hint">Corrija el motivo y vuelva a generar los asientos del período</span>
            </div>
            <span className="chip">{sinCuenta.length}</span>
          </div>
          <NoContabilizados lista={sinCuenta} />
        </section>
      )}

      {error && <AvisoError titulo="No se pudieron cargar los asientos." mensaje={error} onReintentar={() => void cargar()} />}

      <section className="card">
        <div className="card-h">
          <div>
            <h2>Asientos del período</h2>
            {!error && <span className="hint">{asientos.length === 1 ? "1 asiento" : `${asientos.length} asientos`} · contabilizados por RD$ {formatoMonto(totalDebito)}</span>}
          </div>
        </div>
        {cargando && asientos.length === 0 && <Cargando />}
        {!cargando && !error && asientos.length === 0 && !filtrando && (
          <Vacio titulo={`No hay asientos en ${nombre}`} texto="Genere los asientos del período desde las facturas, cobros, compras, pagos y movimientos bancarios, o registre un asiento manual.">
            <button type="button" className="btn btn-p btn-sm" onClick={generar} disabled={generando}>
              Generar asientos del período
            </button>
          </Vacio>
        )}
        {!cargando && !error && asientos.length === 0 && filtrando && (
          <Vacio titulo="Sin resultados" texto="No hay asientos con estos filtros.">
            <button
              type="button"
              className="btn btn-o btn-sm"
              onClick={() => {
                setOrigen("");
                setEstado("");
                setQ("");
              }}
            >
              Ver todos
            </button>
          </Vacio>
        )}
        {asientos.length > 0 && (
          <div className="tw" style={{ opacity: cargando ? 0.6 : 1 }}>
            <table className="tbl" style={{ minWidth: 860 }}>
              <thead>
                <tr>
                  <th>N.º</th>
                  <th>Fecha</th>
                  <th>Concepto</th>
                  <th>Origen</th>
                  <th className="num">Monto (RD$)</th>
                  <th>Estado</th>
                  <th style={{ textAlign: "right" }}>Acciones</th>
                </tr>
              </thead>
              <tbody>
                {asientos.map((a) => {
                  const est = ESTADO_ASIENTO[a.estado] ?? { label: a.estado, badge: "bd b-borr" };
                  return (
                    <tr key={a.id} className={a.estado === "anulado" ? "fila-link ct-anulado" : "fila-link"} onClick={() => navigate(`/contabilidad/asientos/${a.id}`)}>
                      <td className="mono nowrap">
                        <Link to={`/contabilidad/asientos/${a.id}`} className="link mono" onClick={(e) => e.stopPropagation()}>
                          {a.numero}
                        </Link>
                      </td>
                      <td className="mut nowrap">{fechaLegible(a.fecha)}</td>
                      <td>
                        {a.concepto}
                        {a.referencia && <div className="hint mono">{a.referencia}</div>}
                      </td>
                      <td>
                        <span className="chip">{a.origen_etiqueta}</span>
                      </td>
                      <td className="num" style={{ textDecoration: a.estado === "anulado" ? "line-through" : undefined }}>
                        {formatoMonto(a.total_debito)}
                        {!a.cuadrado && <div className="err-t" style={{ fontSize: 12 }}>Descuadrado</div>}
                      </td>
                      <td>
                        <span className={est.badge}>{est.label}</span>
                      </td>
                      <td onClick={(e) => e.stopPropagation()}>
                        <div className="fila-acc">
                          {a.estado === "borrador" && (
                            <button type="button" className="btn btn-g btn-sm" onClick={() => contabilizar(a)} disabled={ocupado === a.id || !a.cuadrado} title={a.cuadrado ? undefined : "El asiento no cuadra"}>
                              {ocupado === a.id ? "Contabilizando…" : "Contabilizar"}
                            </button>
                          )}
                          {sePuedeAnular(a) && (
                            <button type="button" className="btn btn-g btn-sm" style={{ color: "#B91C1C" }} onClick={() => setAnular(a)}>
                              Anular
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

      {anular && (
        <ModalAnular
          asiento={anular}
          onCerrar={() => setAnular(null)}
          onAnulado={() => {
            mostrarAviso("Asiento anulado", { detalle: `${anular.numero} · ${anular.concepto}` });
            setAnular(null);
            void cargar();
          }}
        />
      )}
    </>
  );
}
