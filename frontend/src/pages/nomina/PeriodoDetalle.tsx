// Nómina › Nómina · <período> (vista "nomina" de Nomina.dc.html): cálculo por empleado (salario,
// horas extra, otros ingresos, AFP, SFS, ISR, otros descuentos, neto y aportes patronales), novedades,
// recálculo, aprobación ("Aprobar y programar pago"), reapertura y eliminación.
import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { formatoMonto } from "../../lib/marca";
import { nominaApi, type LineaNomina, type Novedades, type Periodo } from "../../lib/nominaApi";
import { AvisoError, Cargando, Estadistica, Modal, Vacio } from "../ingresos/IngresosShell";
import { useAviso } from "../ingresos/useAviso";
import { fechaLegible, mensajeError } from "../ingresos/ingresosUtil";
import { Encabezado, NominaShell } from "./NominaShell";
import { ESTADO_PERIODO, capitalizar, formatoCedula, porcentaje, refrescarAvisos } from "./nominaUtil";

const CAMPOS_NOVEDAD: { k: keyof Novedades; label: string; ayuda: string }[] = [
  { k: "horas_extra_35", label: "Horas extra al 35 %", ayuda: "Hasta 68 h semanales (CT art. 203)" },
  { k: "horas_extra_100", label: "Horas extra al 100 %", ayuda: "En exceso de 68 h semanales" },
  { k: "otros_ingresos", label: "Comisiones / otros ingresos", ayuda: "Cotizan TSS y pagan ISR" },
  { k: "bonificacion", label: "Bonificación", ayuda: "Paga ISR y 0.5 % INFOTEP; no cotiza TSS" },
  { k: "ingresos_exentos", label: "Ingresos exentos", ayuda: "Viáticos y reembolsos" },
  { k: "regalia", label: "Regalía pascual", ayuda: "No cotiza TSS; exenta de ISR" },
  { k: "otros_descuentos", label: "Otros descuentos", ayuda: "Préstamos, adelantos, cooperativa" },
];

const m = (v: string | null | undefined) => formatoMonto(v);

export function PeriodoDetalle() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const [p, setP] = useState<Periodo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [ocupado, setOcupado] = useState(false);
  const [editar, setEditar] = useState<{ linea: LineaNomina; datos: Novedades } | null>(null);
  const [errorNov, setErrorNov] = useState<string | null>(null);
  const [confirmarEliminar, setConfirmarEliminar] = useState(false);
  const [toast, mostrarAviso] = useAviso();

  const cargar = useCallback(async () => {
    try {
      setP(await nominaApi.periodo(id));
      setError(null);
    } catch (err) {
      setError(mensajeError(err));
    }
  }, [id]);

  useEffect(() => {
    void cargar();
  }, [cargar]);

  async function accion(fn: () => Promise<Periodo>, ok: string) {
    setOcupado(true);
    try {
      setP(await fn());
      refrescarAvisos();
      mostrarAviso(ok);
    } catch (err) {
      mostrarAviso("No se pudo completar la acción", { detalle: mensajeError(err), error: true });
    } finally {
      setOcupado(false);
    }
  }

  async function guardarNovedades() {
    if (!editar) return;
    setOcupado(true);
    try {
      const datos = Object.fromEntries(Object.entries(editar.datos).map(([k, v]) => [k, v === "" ? "0" : v])) as unknown as Novedades;
      setP(await nominaApi.guardarNovedades(id, editar.linea.empleado_id, datos));
      mostrarAviso("Novedades guardadas", { detalle: `${editar.linea.nombre}: nómina recalculada` });
      setEditar(null);
    } catch (err) {
      setErrorNov(mensajeError(err));
    } finally {
      setOcupado(false);
    }
  }

  async function sugerirRegalia() {
    if (!editar) return;
    try {
      const r = await nominaApi.regaliaSugerida(id, editar.linea.empleado_id);
      setEditar({ ...editar, datos: { ...editar.datos, regalia: r.regalia } });
    } catch (err) {
      setErrorNov(mensajeError(err));
    }
  }

  async function eliminar() {
    try {
      await nominaApi.eliminarPeriodo(id);
      refrescarAvisos();
      navigate("/nomina/periodos");
    } catch (err) {
      setConfirmarEliminar(false);
      mostrarAviso("No se pudo eliminar", { detalle: mensajeError(err), error: true });
    }
  }

  if (error) {
    return (
      <NominaShell>
        <Encabezado titulo="Nómina" />
        <AvisoError titulo="No se pudo cargar la nómina." mensaje={error} onReintentar={() => void cargar()} />
        <Link to="/nomina/periodos" className="link">← Volver a los períodos</Link>
      </NominaShell>
    );
  }
  if (!p) {
    return (
      <NominaShell>
        <Cargando />
      </NominaShell>
    );
  }

  const calculada = p.estado !== "borrador";
  const editable = p.estado === "borrador" || p.estado === "calculada";
  const t = p.totales;
  const tasa = (k: string, def: string) => String(p.parametros?.[k] ?? def);

  return (
    <NominaShell>
      <div className="crumb no-imprimir">
        <Link to="/nomina/periodos">Nómina</Link>
        <span>/</span>
        <span>{capitalizar(p.nombre)}</span>
      </div>
      <Encabezado
        titulo={`Nómina · ${p.nombre}`}
        sub={
          <>
            Período {p.tipo} · pago el {fechaLegible(p.fecha_pago)} · <span className={ESTADO_PERIODO[p.estado].badge}>{ESTADO_PERIODO[p.estado].label}</span>
          </>
        }
        acciones={
          <>
            {editable && (
              <button type="button" className="btn btn-o" disabled={ocupado} onClick={() => void accion(() => nominaApi.calcular(id), `Nómina de ${p.nombre} calculada`)}>
                {calculada ? "Recalcular" : "Calcular nómina"}
              </button>
            )}
            {editable && (
              <button type="button" className="btn btn-p" disabled={ocupado || p.estado !== "calculada" || p.lineas.length === 0} onClick={() => void accion(() => nominaApi.aprobar(id), `Nómina aprobada · pago programado para el ${fechaLegible(p.fecha_pago)}`)}>
                Aprobar y programar pago
              </button>
            )}
            {p.estado === "aprobada" && (
              <>
                <button type="button" className="btn btn-o" disabled={ocupado || p.pagados > 0} onClick={() => void accion(() => nominaApi.reabrir(id), "Nómina reabierta")}>
                  Reabrir
                </button>
                <span className="btn btn-p" aria-disabled="true" style={{ opacity: 0.8, cursor: "default" }}>Nómina aprobada ✓</span>
              </>
            )}
            {calculada && (
              <Link to={`/nomina/periodos/${id}/recibos`} className="btn btn-o">Recibos de pago</Link>
            )}
            {(p.estado === "aprobada" || p.estado === "pagada") && (
              <Link to="/nomina/pagos" className="btn btn-o">Ir a Pagos</Link>
            )}
          </>
        }
      />

      {calculada && (
        <div className="grid-stats">
          <Estadistica k="Total ingresos" v={`RD$ ${m(t.total_ingresos)}`} s={`${p.empleados} empleado${p.empleados === 1 ? "" : "s"}`} />
          <Estadistica k="Deducciones" v={`RD$ ${m(t.total_descuentos)}`} s={`TSS ${m(String(Number(t.afp) + Number(t.sfs)))} · ISR ${m(t.isr)}`} />
          <Estadistica k="Neto a pagar" v={`RD$ ${m(t.neto)}`} />
          <Estadistica k="Costo total empresa" v={`RD$ ${m(t.costo_total)}`} s={`Aportes patronales ${m(t.total_aportes_empleador)}`} />
        </div>
      )}

      <section className="card">
        {p.lineas.length === 0 ? (
          <Vacio titulo="La nómina no tiene empleados" texto={`Registra empleados con tipo de pago ${p.tipo} y vuelve a calcular.`}>
            <Link to="/nomina/empleados" className="btn btn-p">Ir a empleados</Link>
          </Vacio>
        ) : !calculada ? (
          <div className="empty">
            <h3>Nómina sin calcular</h3>
            <p>Calcula la nómina para ver deducciones de TSS, ISR y aportes patronales.</p>
            <button type="button" className="btn btn-p" disabled={ocupado} onClick={() => void accion(() => nominaApi.calcular(id), `Nómina de ${p.nombre} calculada`)}>
              Calcular nómina
            </button>
          </div>
        ) : (
          <div className="tw">
            <table className="tbl nm-tbl" style={{ minWidth: 1040 }}>
              <thead>
                <tr>
                  <th>Empleado</th>
                  <th className="num">Salario</th>
                  <th className="num">Horas extra</th>
                  <th className="num">Otros ingresos</th>
                  <th className="num">AFP {porcentaje(tasa("afp_empleado", "0.0287"))}</th>
                  <th className="num">SFS {porcentaje(tasa("sfs_empleado", "0.0304"))}</th>
                  <th className="num">ISR</th>
                  <th className="num">Otros desc.</th>
                  <th className="num">Neto a pagar</th>
                  <th className="num">Aportes patronales</th>
                  {editable && <th />}
                </tr>
              </thead>
              <tbody>
                {p.lineas.map((l) => {
                  const otros = Number(l.otros_ingresos) + Number(l.bonificacion) + Number(l.ingresos_exentos) + Number(l.regalia);
                  const desc = Number(l.otros_descuentos) + Number(l.infotep_empleado ?? 0);
                  return (
                    <tr key={l.empleado_id}>
                      <td>
                        {l.nombre}
                        <span className="nm-sub mono">{formatoCedula(l.cedula)}</span>
                      </td>
                      <td className="num">{m(l.salario)}</td>
                      <td className="num">{Number(l.horas_extra) ? m(l.horas_extra) : "—"}</td>
                      <td className="num">{otros ? m(String(otros)) : "—"}</td>
                      <td className="num">{m(l.afp)}</td>
                      <td className="num">{m(l.sfs)}</td>
                      <td className="num">{m(l.isr)}</td>
                      <td className="num">{desc ? m(String(desc)) : "—"}</td>
                      <td className="num" style={{ fontWeight: 600 }}>{m(l.neto)}</td>
                      <td className="num" title={`AFP ${m(l.afp_empleador)} · SFS ${m(l.sfs_empleador)} · SRL ${m(l.srl)} · INFOTEP ${m(l.infotep)}`}>{m(l.total_aportes_empleador)}</td>
                      {editable && (
                        <td className="right">
                          <button
                            type="button"
                            className="btn btn-g btn-sm"
                            onClick={() => {
                              setErrorNov(null);
                              setEditar({ linea: l, datos: Object.fromEntries(CAMPOS_NOVEDAD.map((c) => [c.k, Number(l[c.k]) ? l[c.k] : ""])) as unknown as Novedades });
                            }}
                          >
                            Novedades
                          </button>
                        </td>
                      )}
                    </tr>
                  );
                })}
              </tbody>
              <tfoot>
                <tr>
                  <td>Total</td>
                  <td className="num">{m(t.salario)}</td>
                  <td className="num">{m(t.horas_extra)}</td>
                  <td className="num">{m(String(Number(t.otros_ingresos) + Number(t.bonificacion) + Number(t.ingresos_exentos) + Number(t.regalia)))}</td>
                  <td className="num">{m(t.afp)}</td>
                  <td className="num">{m(t.sfs)}</td>
                  <td className="num">{m(t.isr)}</td>
                  <td className="num">{m(String(Number(t.otros_descuentos) + Number(t.infotep_empleado)))}</td>
                  <td className="num">{m(t.neto)}</td>
                  <td className="num">{m(t.total_aportes_empleador)}</td>
                  {editable && <td />}
                </tr>
              </tfoot>
            </table>
          </div>
        )}
      </section>

      {calculada && (
        <section className="card">
          <div className="card-h">
            <h2>Aportes del empleador</h2>
            <span className="hint">Topes del salario mínimo cotizable RD$ {m(tasa("salario_minimo_cotizable", "0"))} · SRL {porcentaje(p.tasa_srl)}</span>
          </div>
          <div className="tw">
            <table className="tbl">
              <thead>
                <tr><th>Concepto</th><th className="num">Tasa</th><th className="num">Monto (RD$)</th></tr>
              </thead>
              <tbody>
                <tr><td>AFP (pensiones)</td><td className="num">{porcentaje(tasa("afp_empleador", "0.071"))}</td><td className="num">{m(t.afp_empleador)}</td></tr>
                <tr><td>SFS (salud)</td><td className="num">{porcentaje(tasa("sfs_empleador", "0.0709"))}</td><td className="num">{m(t.sfs_empleador)}</td></tr>
                <tr><td>SRL (riesgos laborales)</td><td className="num">{porcentaje(p.tasa_srl)}</td><td className="num">{m(t.srl)}</td></tr>
                <tr><td>INFOTEP</td><td className="num">{porcentaje(tasa("infotep_empleador", "0.01"))}</td><td className="num">{m(t.infotep)}</td></tr>
              </tbody>
            </table>
          </div>
        </section>
      )}

      {editable && (
        <div className="row no-imprimir">
          <button type="button" className="btn btn-do btn-sm" onClick={() => setConfirmarEliminar(true)}>Eliminar período</button>
        </div>
      )}

      {editar && (
        <Modal
          titulo={`Novedades · ${editar.linea.nombre}`}
          ancho={680}
          onCerrar={() => setEditar(null)}
          pie={
            <>
              <button type="button" className="btn btn-o" onClick={() => setEditar(null)}>Cancelar</button>
              <button type="button" className="btn btn-p" disabled={ocupado} onClick={() => void guardarNovedades()}>{ocupado ? "Recalculando…" : "Guardar y recalcular"}</button>
            </>
          }
        >
          <p className="hint" style={{ margin: 0 }}>Salario mensual RD$ {m(editar.linea.salario_mensual)}. Montos en RD$; horas en horas.</p>
          <div className="nm-nov">
            {CAMPOS_NOVEDAD.map((c) => (
              <div key={c.k} className="fld">
                <label className="lbl" htmlFor={`nv-${c.k}`}>{c.label}</label>
                <input
                  id={`nv-${c.k}`}
                  type="number"
                  min="0"
                  step={c.k.startsWith("horas") ? "0.5" : "0.01"}
                  className="inp"
                  placeholder="0"
                  value={editar.datos[c.k]}
                  onChange={(e) => setEditar({ ...editar, datos: { ...editar.datos, [c.k]: e.target.value } })}
                />
                <span className="hint">{c.ayuda}</span>
                {c.k === "regalia" && (
                  <button type="button" className="btn btn-g btn-sm" style={{ alignSelf: "flex-start" }} onClick={() => void sugerirRegalia()}>
                    Calcular regalía (1/12 del año)
                  </button>
                )}
              </div>
            ))}
          </div>
          {errorNov && <AvisoError mensaje={errorNov} />}
        </Modal>
      )}

      {confirmarEliminar && (
        <Modal
          titulo={`Eliminar la nómina de ${p.nombre}`}
          onCerrar={() => setConfirmarEliminar(false)}
          pie={
            <>
              <button type="button" className="btn btn-o" onClick={() => setConfirmarEliminar(false)}>Cancelar</button>
              <button type="button" className="btn btn-d" onClick={() => void eliminar()}>Eliminar</button>
            </>
          }
        >
          <p style={{ margin: 0 }}>Se borran el período y sus novedades. Los empleados no se modifican.</p>
        </Modal>
      )}
      {toast}
    </NominaShell>
  );
}
