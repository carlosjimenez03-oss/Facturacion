// Nómina › Pagos (vista "pagos" de Nomina.dc.html): dispersión de la nómina por transferencia. Las
// nóminas aprobadas se pagan (todas las líneas o por empleado), se descarga el archivo para el banco
// (CSV genérico) y se cierran; cada empleado tiene su recibo de pago.
import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { formatoMonto } from "../../lib/marca";
import { nominaApi, type EmpresaNomina, type Periodo, type PeriodoResumen } from "../../lib/nominaApi";
import { AvisoError, Cargando, Modal, Vacio } from "../ingresos/IngresosShell";
import { useAviso } from "../ingresos/useAviso";
import { fechaLegible, mensajeError } from "../ingresos/ingresosUtil";
import { Encabezado, NominaShell } from "./NominaShell";
import { ESTADO_PERIODO, capitalizar, descargarBlob, formatoCedula, refrescarAvisos } from "./nominaUtil";

export function PagosNominaPage() {
  const [periodos, setPeriodos] = useState<PeriodoResumen[] | null>(null);
  const [empresa, setEmpresa] = useState<EmpresaNomina | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pagar, setPagar] = useState<Periodo | null>(null);
  const [seleccion, setSeleccion] = useState<Set<string>>(new Set());
  const [referencia, setReferencia] = useState("");
  const [errorPago, setErrorPago] = useState<string | null>(null);
  const [ocupado, setOcupado] = useState(false);
  const [toast, mostrarAviso] = useAviso();

  const cargar = useCallback(async () => {
    try {
      const [p, e] = await Promise.all([nominaApi.periodos(), nominaApi.empresa()]);
      setPeriodos(p.filter((x) => x.estado !== "borrador"));
      setEmpresa(e);
      setError(null);
    } catch (err) {
      setPeriodos([]);
      setError(mensajeError(err));
    }
  }, []);

  useEffect(() => {
    void cargar();
  }, [cargar]);

  const porPagar = (periodos ?? []).find((p) => p.estado === "aprobada");
  const cuenta = empresa?.cuenta_origen ? `${empresa.banco ?? "Cuenta"} ****${empresa.cuenta_origen.replace(/\D/g, "").slice(-4)}` : "—";

  async function archivoBanco(p: PeriodoResumen) {
    try {
      const blob = await nominaApi.archivoBanco(p.id);
      descargarBlob(`nomina_${p.anio}${String(p.mes).padStart(2, "0")}${p.quincena ? `_q${p.quincena}` : ""}_banco.csv`, blob);
      mostrarAviso("Archivo para el banco descargado", { detalle: "CSV genérico: cárguelo en la plantilla de su banco." });
    } catch (err) {
      mostrarAviso("No se pudo descargar el archivo", { detalle: mensajeError(err), error: true });
    }
  }

  async function abrirPago(p: PeriodoResumen) {
    try {
      const detalle = await nominaApi.periodo(p.id);
      setPagar(detalle);
      setSeleccion(new Set(detalle.lineas.filter((l) => !l.pagado).map((l) => l.empleado_id)));
      setReferencia("");
      setErrorPago(null);
    } catch (err) {
      mostrarAviso("No se pudo abrir la nómina", { detalle: mensajeError(err), error: true });
    }
  }

  async function confirmarPago() {
    if (!pagar) return;
    setOcupado(true);
    try {
      const pendientes = pagar.lineas.filter((l) => !l.pagado);
      const todos = pendientes.every((l) => seleccion.has(l.empleado_id));
      const r = await nominaApi.pagar(pagar.id, { empleado_ids: todos ? null : [...seleccion], referencia: referencia || null });
      mostrarAviso(r.estado === "pagada" ? `Pago de nómina de ${r.nombre} confirmado` : `Pago registrado para ${seleccion.size} empleado${seleccion.size === 1 ? "" : "s"}`);
      setPagar(null);
      refrescarAvisos();
      void cargar();
    } catch (err) {
      setErrorPago(mensajeError(err));
    } finally {
      setOcupado(false);
    }
  }

  async function cerrar(p: PeriodoResumen) {
    try {
      await nominaApi.cerrar(p.id);
      mostrarAviso(`Nómina de ${p.nombre} cerrada`);
      void cargar();
    } catch (err) {
      mostrarAviso("No se pudo cerrar la nómina", { detalle: mensajeError(err), error: true });
    }
  }

  return (
    <NominaShell>
      <Encabezado
        titulo="Pagos"
        sub="Dispersión de la nómina por transferencia bancaria"
        acciones={
          <button type="button" className="btn btn-o" disabled={!porPagar} onClick={() => porPagar && void archivoBanco(porPagar)}>
            Descargar archivo para el banco
          </button>
        }
      />
      {periodos && !porPagar && (
        <div className="al al-w">
          <span>
            Calcula y aprueba la nómina para programar el pago. <Link to="/nomina/periodos" className="link">Ir a Nómina</Link>
          </span>
        </div>
      )}
      {error && <AvisoError titulo="No se pudieron cargar los pagos." mensaje={error} onReintentar={() => void cargar()} />}
      <section className="card">
        {periodos === null ? (
          <Cargando />
        ) : periodos.length === 0 ? (
          <Vacio titulo="Sin pagos programados" texto="Aprueba una nómina para programar el pago por transferencia." />
        ) : (
          <div className="tw">
            <table className="tbl" style={{ minWidth: 820 }}>
              <thead>
                <tr>
                  <th>Período</th>
                  <th>Fecha de pago</th>
                  <th className="num">Empleados</th>
                  <th className="num">Neto (RD$)</th>
                  <th>Cuenta</th>
                  <th>Estado</th>
                  <th style={{ textAlign: "right" }}>Acción</th>
                </tr>
              </thead>
              <tbody>
                {periodos.map((p) => (
                  <tr key={p.id}>
                    <td style={{ fontWeight: 500 }}>
                      <Link to={`/nomina/periodos/${p.id}`} className="link">{capitalizar(p.nombre)}</Link>
                    </td>
                    <td className="mut">{fechaLegible(p.fecha_pago)}</td>
                    <td className="num">{p.estado === "aprobada" && p.pagados ? `${p.pagados}/${p.empleados}` : p.empleados}</td>
                    <td className="num" style={{ fontWeight: 600 }}>{formatoMonto(p.totales.neto)}</td>
                    <td className="mono mut">{cuenta}</td>
                    <td><span className={ESTADO_PERIODO[p.estado].badge}>{ESTADO_PERIODO[p.estado].label}</span></td>
                    <td>
                      <div className="fila-acc">
                        {p.estado === "aprobada" && (
                          <>
                            <button type="button" className="btn btn-g btn-sm" onClick={() => void archivoBanco(p)}>Archivo banco</button>
                            <button type="button" className="btn btn-p btn-sm" onClick={() => void abrirPago(p)}>Confirmar pago</button>
                          </>
                        )}
                        {p.estado === "calculada" && <Link to={`/nomina/periodos/${p.id}`} className="btn btn-g btn-sm">Aprobar</Link>}
                        {p.estado === "pagada" && <button type="button" className="btn btn-g btn-sm" onClick={() => void cerrar(p)}>Cerrar</button>}
                        {p.estado !== "calculada" && <Link to={`/nomina/periodos/${p.id}/recibos`} className="btn btn-g btn-sm">Comprobantes</Link>}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {pagar && (
        <Modal
          titulo={`Confirmar pago · ${pagar.nombre}`}
          ancho={720}
          onCerrar={() => setPagar(null)}
          pie={
            <>
              <button type="button" className="btn btn-o" onClick={() => setPagar(null)}>Cancelar</button>
              <button type="button" className="btn btn-p" disabled={ocupado || seleccion.size === 0} onClick={() => void confirmarPago()}>
                Confirmar pago ({seleccion.size})
              </button>
            </>
          }
        >
          <div className="tw">
            <table className="tbl">
              <thead>
                <tr><th /><th>Empleado</th><th>Cuenta</th><th className="num">Neto (RD$)</th><th /></tr>
              </thead>
              <tbody>
                {pagar.lineas.map((l) => (
                  <tr key={l.empleado_id}>
                    <td>
                      <input
                        type="checkbox"
                        aria-label={`Pagar a ${l.nombre}`}
                        disabled={l.pagado}
                        checked={l.pagado || seleccion.has(l.empleado_id)}
                        onChange={(e) => {
                          const s = new Set(seleccion);
                          if (e.target.checked) s.add(l.empleado_id);
                          else s.delete(l.empleado_id);
                          setSeleccion(s);
                        }}
                      />
                    </td>
                    <td>{l.nombre}<span className="nm-sub mono">{formatoCedula(l.cedula)}</span></td>
                    <td className="mono mut">{l.cuenta_bancaria ? `${l.banco ?? ""} ${l.cuenta_bancaria}` : "Sin cuenta (efectivo o cheque)"}</td>
                    <td className="num">{formatoMonto(l.neto)}</td>
                    <td>{l.pagado && <span className="bd b-acep">Pagado</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="fld">
            <label className="lbl" htmlFor="pref">Referencia de la transferencia (opcional)</label>
            <input id="pref" className="inp" value={referencia} onChange={(e) => setReferencia(e.target.value)} />
          </div>
          {errorPago && <AvisoError mensaje={errorPago} />}
        </Modal>
      )}
      {toast}
    </NominaShell>
  );
}
