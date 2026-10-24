// Nómina › Reportes: resumen de la autodeterminación de la TSS (con CSV), pre-cálculo del IR-3
// (retenciones de asalariados del mes) y resumen anual para el IR-13. Solo cuentan las nóminas
// aprobadas, pagadas o cerradas.
import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { formatoMonto } from "../../lib/marca";
import { nominaApi, type ReporteIr13, type ReporteIr3, type ReporteTss } from "../../lib/nominaApi";
import { AvisoError, Cargando, Estadistica } from "../ingresos/IngresosShell";
import { useAviso } from "../ingresos/useAviso";
import { descargarCsv, fechaLegible, mensajeError } from "../ingresos/ingresosUtil";
import { Encabezado, NominaShell, PestanasNomina } from "./NominaShell";
import { MESES, capitalizar, descargarBlob, formatoCedula } from "./nominaUtil";

const m = formatoMonto;

export function ReportesNominaPage() {
  const [params, setParams] = useSearchParams();
  const hoy = new Date();
  const anio = Number(params.get("anio")) || hoy.getFullYear();
  const mes = Number(params.get("mes")) || hoy.getMonth() + 1;
  const [tss, setTss] = useState<ReporteTss | null>(null);
  const [ir3, setIr3] = useState<ReporteIr3 | null>(null);
  const [ir13, setIr13] = useState<ReporteIr13 | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [toast, mostrarAviso] = useAviso();

  const cargar = useCallback(async () => {
    setTss(null);
    try {
      const [a, b, c] = await Promise.all([nominaApi.reporteTss(anio, mes), nominaApi.reporteIr3(anio, mes), nominaApi.reporteIr13(anio)]);
      setTss(a);
      setIr3(b);
      setIr13(c);
      setError(null);
    } catch (err) {
      setError(mensajeError(err));
    }
  }, [anio, mes]);

  useEffect(() => {
    void cargar();
  }, [cargar]);

  const cambiar = (a: number, mm: number) => setParams({ anio: String(a), mes: String(mm) });

  async function bajarTss() {
    try {
      descargarBlob(`tss_autodeterminacion_${anio}${String(mes).padStart(2, "0")}.csv`, await nominaApi.archivoTss(anio, mes));
      mostrarAviso("Resumen TSS descargado", { detalle: "CSV con las columnas de la autodeterminación." });
    } catch (err) {
      mostrarAviso("No se pudo descargar", { detalle: mensajeError(err), error: true });
    }
  }

  function bajarIr13() {
    if (!ir13) return;
    descargarCsv(`ir13_resumen_${anio}.csv`, [
      ["Cédula", "Nombre", "Salarios gravados", "Ingresos exentos", "Aportes TSS", "Renta neta", "ISR retenido"],
      ...ir13.empleados.map((e) => [e.cedula, e.nombre, e.salarios, e.exentos, e.tss, e.renta_neta, e.isr]),
    ]);
  }

  return (
    <NominaShell>
      <Encabezado
        titulo="Nómina"
        sub="Reportes para la TSS y la DGII"
        acciones={
          <>
            <select className="inp inp-auto" style={{ minWidth: 140 }} value={mes} onChange={(e) => cambiar(anio, Number(e.target.value))} aria-label="Mes">
              {MESES.map((x, i) => <option key={x} value={i + 1}>{capitalizar(x)}</option>)}
            </select>
            <select className="inp inp-auto" style={{ minWidth: 100 }} value={anio} onChange={(e) => cambiar(Number(e.target.value), mes)} aria-label="Año">
              {[hoy.getFullYear() + 1, hoy.getFullYear(), hoy.getFullYear() - 1, hoy.getFullYear() - 2].map((a) => <option key={a} value={a}>{a}</option>)}
            </select>
          </>
        }
      />
      <PestanasNomina />
      {error && <AvisoError titulo="No se pudieron cargar los reportes." mensaje={error} onReintentar={() => void cargar()} />}
      {!tss && !error && <Cargando />}
      {tss && ir3 && (
        <>
          {tss.nominas_pendientes > 0 && (
            <div className="al al-w">
              <span>Hay {tss.nominas_pendientes} nómina{tss.nominas_pendientes === 1 ? "" : "s"} de {tss.nombre} sin aprobar: no entran en estos reportes hasta que se aprueben.</span>
            </div>
          )}
          <div className="grid-stats">
            <Estadistica k="TSS a pagar" v={`RD$ ${m(tss.totales.total)}`} s={`Vence el ${fechaLegible(tss.vence)} (3.er día hábil)`} />
            <Estadistica k="INFOTEP" v={`RD$ ${m(tss.totales.infotep)}`} s="1 % de la nómina + 0.5 % de bonificaciones" />
            <Estadistica k="IR-3 · ISR retenido" v={`RD$ ${m(ir3.totales.isr)}`} s={`Vence el ${fechaLegible(ir3.vence)}`} />
            <Estadistica k="Asalariados" v={String(ir3.asalariados)} s={`${ir3.retenidos} con retención de ISR`} />
          </div>

          <section className="card">
            <div className="card-h">
              <h2>Resumen TSS · autodeterminación de {tss.nombre}</h2>
              <button type="button" className="btn btn-o btn-sm" onClick={() => void bajarTss()} disabled={tss.empleados.length === 0}>Descargar CSV</button>
            </div>
            <p className="hint pad" style={{ paddingBottom: 0 }}>
              CSV con las columnas de la autodeterminación (cédula, salario cotizable, salario ISR, otras remuneraciones, regalía). No es el archivo de ancho fijo
              del SUIR+: úselo para llenar la plantilla de la TSS. Topes: AFP RD$ {m(tss.tope_afp)} · SFS RD$ {m(tss.tope_sfs)} · SRL RD$ {m(tss.tope_srl)}.
            </p>
            <div className="tw">
              <table className="tbl nm-tbl" style={{ minWidth: 960 }}>
                <thead>
                  <tr>
                    <th>Empleado</th><th className="num">Salario cotizable</th><th className="num">Regalía</th><th className="num">AFP emp.</th><th className="num">SFS emp.</th>
                    <th className="num">AFP patronal</th><th className="num">SFS patronal</th><th className="num">SRL</th><th className="num">Total TSS</th>
                  </tr>
                </thead>
                <tbody>
                  {tss.empleados.length === 0 && (
                    <tr><td colSpan={9} className="mut" style={{ textAlign: "center", padding: 24 }}>Sin nóminas aprobadas en {tss.nombre}.</td></tr>
                  )}
                  {tss.empleados.map((e) => (
                    <tr key={e.empleado_id}>
                      <td>{e.nombre}<span className="nm-sub mono">{formatoCedula(e.cedula)}</span></td>
                      <td className="num">{m(e.salario_cotizable)}</td><td className="num">{m(e.regalia)}</td><td className="num">{m(e.afp)}</td><td className="num">{m(e.sfs)}</td>
                      <td className="num">{m(e.afp_empleador)}</td><td className="num">{m(e.sfs_empleador)}</td><td className="num">{m(e.srl)}</td><td className="num" style={{ fontWeight: 600 }}>{m(e.total)}</td>
                    </tr>
                  ))}
                </tbody>
                {tss.empleados.length > 0 && (
                  <tfoot>
                    <tr>
                      <td>Total</td><td className="num">{m(tss.totales.salario_cotizable)}</td><td className="num">{m(tss.totales.regalia)}</td><td className="num">{m(tss.totales.afp)}</td>
                      <td className="num">{m(tss.totales.sfs)}</td><td className="num">{m(tss.totales.afp_empleador)}</td><td className="num">{m(tss.totales.sfs_empleador)}</td>
                      <td className="num">{m(tss.totales.srl)}</td><td className="num">{m(tss.totales.total)}</td>
                    </tr>
                  </tfoot>
                )}
              </table>
            </div>
          </section>

          <section className="card">
            <div className="card-h">
              <h2>IR-3 · Retenciones de asalariados de {ir3.nombre}</h2>
              <span className="hint">Se declara y paga en la Oficina Virtual de la DGII a más tardar el día 10</span>
            </div>
            <div className="tw">
              <table className="tbl nm-tbl" style={{ minWidth: 760 }}>
                <thead>
                  <tr><th>Empleado</th><th className="num">Ingresos gravados</th><th className="num">Exentos</th><th className="num">TSS (AFP + SFS)</th><th className="num">Base ISR</th><th className="num">ISR retenido</th></tr>
                </thead>
                <tbody>
                  {ir3.empleados.map((e) => (
                    <tr key={e.empleado_id}>
                      <td>{e.nombre}<span className="nm-sub mono">{formatoCedula(e.cedula)}</span></td>
                      <td className="num">{m(e.ingresos_gravados)}</td><td className="num">{m(e.ingresos_exentos)}</td><td className="num">{m(e.tss)}</td>
                      <td className="num">{m(e.base_isr)}</td><td className="num" style={{ fontWeight: 600 }}>{m(e.isr)}</td>
                    </tr>
                  ))}
                  {ir3.empleados.length === 0 && <tr><td colSpan={6} className="mut" style={{ textAlign: "center", padding: 24 }}>Sin retenciones en {ir3.nombre}.</td></tr>}
                </tbody>
                {ir3.empleados.length > 0 && (
                  <tfoot>
                    <tr>
                      <td>Total</td><td className="num">{m(ir3.totales.ingresos_gravados)}</td><td className="num">{m(ir3.totales.ingresos_exentos)}</td>
                      <td className="num">{m(ir3.totales.tss)}</td><td className="num">{m(ir3.totales.base_isr)}</td><td className="num">{m(ir3.totales.isr)}</td>
                    </tr>
                  </tfoot>
                )}
              </table>
            </div>
          </section>

          {ir13 && (
            <section className="card">
              <div className="card-h">
                <h2>IR-13 · Resumen anual {anio}</h2>
                <button type="button" className="btn btn-o btn-sm" onClick={bajarIr13} disabled={ir13.empleados.length === 0}>Descargar CSV</button>
              </div>
              <div className="tw">
                <table className="tbl nm-tbl" style={{ minWidth: 760 }}>
                  <thead>
                    <tr><th>Empleado</th><th className="num">Salarios gravados</th><th className="num">Exentos</th><th className="num">TSS</th><th className="num">Renta neta</th><th className="num">ISR retenido</th></tr>
                  </thead>
                  <tbody>
                    {ir13.empleados.map((e) => (
                      <tr key={e.empleado_id}>
                        <td>{e.nombre}<span className="nm-sub mono">{formatoCedula(e.cedula)}</span></td>
                        <td className="num">{m(e.salarios)}</td><td className="num">{m(e.exentos)}</td><td className="num">{m(e.tss)}</td>
                        <td className="num">{m(e.renta_neta)}</td><td className="num" style={{ fontWeight: 600 }}>{m(e.isr)}</td>
                      </tr>
                    ))}
                    {ir13.empleados.length === 0 && <tr><td colSpan={6} className="mut" style={{ textAlign: "center", padding: 24 }}>Sin nóminas aprobadas en {anio}.</td></tr>}
                  </tbody>
                  {ir13.empleados.length > 0 && (
                    <tfoot>
                      <tr>
                        <td>Total</td><td className="num">{m(ir13.totales.salarios)}</td><td className="num">{m(ir13.totales.exentos)}</td><td className="num">{m(ir13.totales.tss)}</td>
                        <td className="num">{m(ir13.totales.renta_neta)}</td><td className="num">{m(ir13.totales.isr)}</td>
                      </tr>
                    </tfoot>
                  )}
                </table>
              </div>
            </section>
          )}
        </>
      )}
      {toast}
    </NominaShell>
  );
}
