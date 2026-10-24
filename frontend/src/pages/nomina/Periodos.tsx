// Nómina › Nómina: períodos (mensuales o quincenales) con su estado y totales, y alta de un período.
import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { formatoMonto } from "../../lib/marca";
import { nominaApi, type EmpresaNomina, type PeriodoResumen, type TipoPeriodo } from "../../lib/nominaApi";
import { AvisoError, Cargando, Modal, Vacio } from "../ingresos/IngresosShell";
import { useAviso } from "../ingresos/useAviso";
import { fechaLegible, mensajeError } from "../ingresos/ingresosUtil";
import { Encabezado, NominaShell, PestanasNomina } from "./NominaShell";
import { ESTADO_PERIODO, MESES, capitalizar, refrescarAvisos } from "./nominaUtil";

export function PeriodosPage() {
  const navigate = useNavigate();
  const hoy = new Date();
  const [anio, setAnio] = useState(hoy.getFullYear());
  const [periodos, setPeriodos] = useState<PeriodoResumen[] | null>(null);
  const [empresa, setEmpresa] = useState<EmpresaNomina | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [nuevo, setNuevo] = useState<{ tipo: TipoPeriodo; anio: number; mes: number; quincena: 1 | 2; fecha_pago: string } | null>(null);
  const [errorNuevo, setErrorNuevo] = useState<string | null>(null);
  const [creando, setCreando] = useState(false);
  const [toast, mostrarAviso] = useAviso();

  const cargar = useCallback(async () => {
    try {
      const [p, e] = await Promise.all([nominaApi.periodos({ anio }), nominaApi.empresa()]);
      setPeriodos(p);
      setEmpresa(e);
      setError(null);
    } catch (err) {
      setPeriodos([]);
      setError(mensajeError(err));
    }
  }, [anio]);

  useEffect(() => {
    void cargar();
  }, [cargar]);

  function abrirNuevo() {
    setErrorNuevo(null);
    setNuevo({ tipo: empresa?.frecuencia_pago ?? "mensual", anio: hoy.getFullYear(), mes: hoy.getMonth() + 1, quincena: hoy.getDate() > 15 ? 2 : 1, fecha_pago: "" });
  }

  async function crear(calcular: boolean) {
    if (!nuevo) return;
    setCreando(true);
    try {
      let p = await nominaApi.crearPeriodo({
        tipo: nuevo.tipo, anio: nuevo.anio, mes: nuevo.mes, quincena: nuevo.tipo === "quincenal" ? nuevo.quincena : null,
        fecha_pago: nuevo.fecha_pago || null,
      });
      if (calcular) p = await nominaApi.calcular(p.id);
      refrescarAvisos();
      mostrarAviso(calcular ? `Nómina de ${p.nombre} calculada` : `Período ${p.nombre} creado`);
      navigate(`/nomina/periodos/${p.id}`);
    } catch (err) {
      setErrorNuevo(mensajeError(err));
    } finally {
      setCreando(false);
    }
  }

  return (
    <NominaShell>
      <Encabezado
        titulo="Nómina"
        sub="Períodos de pago, cálculo de TSS, ISR e INFOTEP y aprobación"
        acciones={
          <>
            <select className="inp inp-auto" style={{ minWidth: 110 }} value={anio} onChange={(e) => setAnio(Number(e.target.value))} aria-label="Año">
              {[hoy.getFullYear() + 1, hoy.getFullYear(), hoy.getFullYear() - 1, hoy.getFullYear() - 2].map((a) => (
                <option key={a} value={a}>{a}</option>
              ))}
            </select>
            <button type="button" className="btn btn-p" onClick={abrirNuevo}>+ Nuevo período</button>
          </>
        }
      />
      <PestanasNomina />
      {error && <AvisoError titulo="No se pudieron cargar las nóminas." mensaje={error} onReintentar={() => void cargar()} />}
      <section className="card">
        {periodos === null ? (
          <Cargando />
        ) : periodos.length === 0 ? (
          <Vacio titulo={`Sin nóminas en ${anio}`} texto="Crea el período del mes (o la quincena) y calcula la nómina con las tasas vigentes.">
            <button type="button" className="btn btn-p" onClick={abrirNuevo}>+ Nuevo período</button>
          </Vacio>
        ) : (
          <div className="tw">
            <table className="tbl" style={{ minWidth: 760 }}>
              <thead>
                <tr>
                  <th>Período</th>
                  <th>Fecha de pago</th>
                  <th className="num">Empleados</th>
                  <th className="num">Neto (RD$)</th>
                  <th className="num">Costo total (RD$)</th>
                  <th>Estado</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {periodos.map((p) => (
                  <tr key={p.id} className="fila-link" onClick={() => navigate(`/nomina/periodos/${p.id}`)}>
                    <td style={{ fontWeight: 500 }}>
                      <Link to={`/nomina/periodos/${p.id}`} className="link" onClick={(e) => e.stopPropagation()}>{capitalizar(p.nombre)}</Link>
                      <span className="nm-sub">{p.tipo === "quincenal" ? "Quincenal" : "Mensual"} · {fechaLegible(p.fecha_inicio)} al {fechaLegible(p.fecha_fin)}</span>
                    </td>
                    <td className="mut">{fechaLegible(p.fecha_pago)}</td>
                    <td className="num">{p.empleados}</td>
                    <td className="num" style={{ fontWeight: 600 }}>{p.estado === "borrador" ? "—" : formatoMonto(p.totales.neto)}</td>
                    <td className="num">{p.estado === "borrador" ? "—" : formatoMonto(p.totales.costo_total)}</td>
                    <td><span className={ESTADO_PERIODO[p.estado].badge}>{ESTADO_PERIODO[p.estado].label}</span></td>
                    <td className="right"><span className="link-strong">Ver →</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {nuevo && (
        <Modal
          titulo="Nuevo período de nómina"
          onCerrar={() => setNuevo(null)}
          pie={
            <>
              <button type="button" className="btn btn-o" onClick={() => void crear(false)} disabled={creando}>Crear sin calcular</button>
              <button type="button" className="btn btn-p" onClick={() => void crear(true)} disabled={creando}>{creando ? "Calculando…" : "Crear y calcular"}</button>
            </>
          }
        >
          <div className="form-grid">
            <div className="fld">
              <label className="lbl" htmlFor="pt">Frecuencia</label>
              <select id="pt" className="inp" value={nuevo.tipo} onChange={(e) => setNuevo({ ...nuevo, tipo: e.target.value as TipoPeriodo })}>
                <option value="mensual">Mensual</option>
                <option value="quincenal">Quincenal</option>
              </select>
            </div>
            <div className="fld">
              <label className="lbl" htmlFor="pm">Mes</label>
              <select id="pm" className="inp" value={nuevo.mes} onChange={(e) => setNuevo({ ...nuevo, mes: Number(e.target.value) })}>
                {MESES.map((m, i) => (
                  <option key={m} value={i + 1}>{capitalizar(m)}</option>
                ))}
              </select>
            </div>
            <div className="fld">
              <label className="lbl" htmlFor="pa">Año</label>
              <input id="pa" type="number" className="inp" value={nuevo.anio} onChange={(e) => setNuevo({ ...nuevo, anio: Number(e.target.value) })} />
            </div>
            {nuevo.tipo === "quincenal" && (
              <div className="fld">
                <label className="lbl" htmlFor="pq">Quincena</label>
                <select id="pq" className="inp" value={nuevo.quincena} onChange={(e) => setNuevo({ ...nuevo, quincena: Number(e.target.value) as 1 | 2 })}>
                  <option value={1}>1.ª (del 1 al 15)</option>
                  <option value={2}>2.ª (del 16 a fin de mes)</option>
                </select>
              </div>
            )}
            <div className="fld">
              <label className="lbl" htmlFor="pf">Fecha de pago</label>
              <input id="pf" type="date" className="inp" value={nuevo.fecha_pago} onChange={(e) => setNuevo({ ...nuevo, fecha_pago: e.target.value })} />
              <span className="hint">Vacía: el día {empresa?.dia_pago ?? 30} del mes (fin de quincena si es quincenal).</span>
            </div>
          </div>
          <p className="hint" style={{ margin: 0 }}>
            Entran los empleados activos con tipo de pago {nuevo.tipo}. Quien ingresa o sale a mitad del período cobra la parte proporcional.
          </p>
          {errorNuevo && <AvisoError mensaje={errorNuevo} />}
        </Modal>
      )}
      {toast}
    </NominaShell>
  );
}
