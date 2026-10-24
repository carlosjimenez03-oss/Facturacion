// Nómina › Configuración (vista "config" de Nomina.dc.html): empresa empleadora (RNC, categoría,
// municipio, frecuencia y día de pago, riesgo SRL, cuenta de origen), tasas vigentes y la tabla de
// parámetros de ley por fecha de vigencia (salario mínimo cotizable, tasas, topes y escala del ISR).
import { useCallback, useEffect, useState } from "react";
import { formatoMonto } from "../../lib/marca";
import { nominaApi, type EmpresaNomina, type EmpresaPayload, type ParametrosNomina, type TramoIsr } from "../../lib/nominaApi";
import { AvisoError, Cargando, Modal } from "../ingresos/IngresosShell";
import { useAviso } from "../ingresos/useAviso";
import { fechaLegible, mensajeError } from "../ingresos/ingresosUtil";
import { Encabezado, NominaShell } from "./NominaShell";
import { CATEGORIAS, MUNICIPIOS, aFraccion, aPorcentaje, payloadEmpresa, porcentaje, refrescarAvisos } from "./nominaUtil";

const SRL = [
  { v: "0.0110", label: "1.10% · Categoría I" },
  { v: "0.0120", label: "1.20% · Categoría II" },
  { v: "0.0130", label: "1.30% · Categoría III" },
];

const TASAS: { k: keyof ParametrosNomina; label: string }[] = [
  { k: "afp_empleado", label: "AFP empleado (%)" },
  { k: "afp_empleador", label: "AFP empleador (%)" },
  { k: "sfs_empleado", label: "SFS empleado (%)" },
  { k: "sfs_empleador", label: "SFS empleador (%)" },
  { k: "srl_empleador", label: "SRL por defecto (%)" },
  { k: "infotep_empleador", label: "INFOTEP empleador (%)" },
  { k: "infotep_bonificacion_empleado", label: "INFOTEP bonificación (%)" },
];

const NUMEROS: { k: keyof ParametrosNomina; label: string }[] = [
  { k: "salario_minimo_cotizable", label: "Salario mínimo cotizable (RD$)" },
  { k: "tope_afp_salarios", label: "Tope AFP (salarios mínimos)" },
  { k: "tope_sfs_salarios", label: "Tope SFS (salarios mínimos)" },
  { k: "tope_srl_salarios", label: "Tope SRL (salarios mínimos)" },
  { k: "divisor_salario_diario", label: "Divisor salario diario" },
  { k: "horas_jornada", label: "Horas de la jornada" },
];

interface FormParametros {
  v: Record<string, string>;
  escala: TramoIsr[];
}

function aForm(p: ParametrosNomina): FormParametros {
  const f: Record<string, string> = { vigente_desde: p.vigente_desde, fuente: p.fuente ?? "", regalia_tope_exento: p.regalia_tope_exento ?? "" };
  TASAS.forEach((t) => (f[t.k] = aPorcentaje(p[t.k] as string)));
  NUMEROS.forEach((t) => (f[t.k] = String(p[t.k])));
  f.recargo_horas_extra = aPorcentaje(p.recargo_horas_extra);
  f.recargo_horas_extra_exceso = aPorcentaje(p.recargo_horas_extra_exceso);
  return { v: f, escala: p.escala_isr.map((t) => ({ ...t, tasa: aPorcentaje(t.tasa) })) };
}

export function ConfiguracionNominaPage() {
  const [empresa, setEmpresa] = useState<EmpresaPayload | null>(null);
  const [original, setOriginal] = useState<EmpresaNomina | null>(null);
  const [parametros, setParametros] = useState<ParametrosNomina[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [errorEmp, setErrorEmp] = useState<string | null>(null);
  const [editar, setEditar] = useState<{ form: FormParametros; nuevo: boolean } | null>(null);
  const [errorPar, setErrorPar] = useState<string | null>(null);
  const [guardando, setGuardando] = useState(false);
  const [toast, mostrarAviso] = useAviso();

  const cargar = useCallback(async () => {
    try {
      const [e, p] = await Promise.all([nominaApi.empresa(), nominaApi.parametros()]);
      setOriginal(e);
      setEmpresa(payloadEmpresa(e));
      setParametros(p);
      setError(null);
    } catch (err) {
      setError(mensajeError(err));
    }
  }, []);

  useEffect(() => {
    void cargar();
  }, [cargar]);

  const hoy = new Date().toISOString().slice(0, 10);
  const vigente = (parametros ?? []).find((p) => p.vigente_desde <= hoy) ?? parametros?.[0];

  function campo<K extends keyof EmpresaPayload>(k: K, v: EmpresaPayload[K]) {
    setEmpresa((e) => (e ? { ...e, [k]: v } : e));
    setErrorEmp(null);
  }

  async function guardarEmpresa() {
    if (!empresa) return;
    setGuardando(true);
    try {
      const limpio = (v: string | null) => (v && v.trim()) || null;
      const e = await nominaApi.guardarEmpresa({
        ...empresa, rnc: limpio(empresa.rnc), razon_social: limpio(empresa.razon_social), contacto_nombre: limpio(empresa.contacto_nombre),
        banco: limpio(empresa.banco), cuenta_origen: limpio(empresa.cuenta_origen), categoria: limpio(empresa.categoria), municipio: limpio(empresa.municipio),
      });
      setOriginal(e);
      setEmpresa(payloadEmpresa(e));
      refrescarAvisos();
      mostrarAviso("Configuración de nómina guardada");
    } catch (err) {
      setErrorEmp(mensajeError(err));
    } finally {
      setGuardando(false);
    }
  }

  async function guardarParametros() {
    if (!editar) return;
    const f = editar.form.v;
    if (!/^\d{4}-\d{2}-\d{2}$/.test(f.vigente_desde)) return setErrorPar("Indique la fecha desde la que rigen.");
    setGuardando(true);
    try {
      const datos = {
        salario_minimo_cotizable: f.salario_minimo_cotizable,
        ...Object.fromEntries(TASAS.map((t) => [t.k, aFraccion(f[t.k])])),
        tope_afp_salarios: f.tope_afp_salarios, tope_sfs_salarios: f.tope_sfs_salarios, tope_srl_salarios: f.tope_srl_salarios,
        escala_isr: editar.form.escala.map((t) => ({ desde: t.desde || "0", tasa: aFraccion(t.tasa || "0"), fijo: t.fijo || "0" })),
        regalia_tope_exento: f.regalia_tope_exento.trim() ? f.regalia_tope_exento : null,
        divisor_salario_diario: f.divisor_salario_diario, horas_jornada: f.horas_jornada,
        recargo_horas_extra: aFraccion(f.recargo_horas_extra), recargo_horas_extra_exceso: aFraccion(f.recargo_horas_extra_exceso),
        fuente: f.fuente.trim() || null,
      } as Parameters<typeof nominaApi.guardarParametros>[1];
      await nominaApi.guardarParametros(f.vigente_desde, datos);
      mostrarAviso("Parámetros guardados", { detalle: `Vigentes desde el ${fechaLegible(f.vigente_desde)}. Recalcule las nóminas abiertas.` });
      setEditar(null);
      void cargar();
    } catch (err) {
      setErrorPar(mensajeError(err));
    } finally {
      setGuardando(false);
    }
  }

  async function eliminarParametros(p: ParametrosNomina) {
    try {
      await nominaApi.eliminarParametros(p.vigente_desde);
      mostrarAviso("Parámetros eliminados");
      void cargar();
    } catch (err) {
      mostrarAviso("No se pudieron eliminar", { detalle: mensajeError(err), error: true });
    }
  }

  if (error) {
    return (
      <NominaShell>
        <Encabezado titulo="Configuración de nómina" />
        <AvisoError mensaje={error} onReintentar={() => void cargar()} />
      </NominaShell>
    );
  }
  if (!empresa || !parametros) {
    return (
      <NominaShell>
        <Cargando />
      </NominaShell>
    );
  }

  const srlPersonalizada = !SRL.some((s) => Number(s.v) === Number(empresa.tasa_srl));

  return (
    <NominaShell>
      <Encabezado
        titulo="Configuración de nómina"
        sub="Datos de la empresa, frecuencia de pago y tasas de la seguridad social"
        acciones={<button type="button" className="btn btn-p" onClick={() => void guardarEmpresa()} disabled={guardando}>Guardar configuración</button>}
      />
      {original && !original.completa && <div className="al al-w"><span>Completa RNC, categoría y municipio: los pide la TSS y la DGII.</span></div>}
      <section className="card card-b">
        <div className="form-grid">
          <div className="fld">
            <label className="lbl" htmlFor="crnc">RNC</label>
            <input id="crnc" className="inp mono" value={empresa.rnc ?? ""} onChange={(e) => campo("rnc", e.target.value)} placeholder="1-31-89754-2" />
          </div>
          <div className="fld">
            <label className="lbl" htmlFor="crs">Razón social</label>
            <input id="crs" className="inp" value={empresa.razon_social ?? ""} onChange={(e) => campo("razon_social", e.target.value)} />
          </div>
          <div className="fld">
            <label className="lbl" htmlFor="ccat">Categoría</label>
            <input id="ccat" className="inp" list="nm-cat" value={empresa.categoria ?? ""} onChange={(e) => campo("categoria", e.target.value)} />
          </div>
          <div className="fld">
            <label className="lbl" htmlFor="cmun">Municipio</label>
            <input id="cmun" className="inp" list="nm-mun" value={empresa.municipio ?? ""} onChange={(e) => campo("municipio", e.target.value)} />
          </div>
          <div className="fld">
            <label className="lbl" htmlFor="frq">Frecuencia de pago</label>
            <select id="frq" className="inp" value={empresa.frecuencia_pago} onChange={(e) => campo("frecuencia_pago", e.target.value as EmpresaPayload["frecuencia_pago"])}>
              <option value="mensual">Mensual</option>
              <option value="quincenal">Quincenal</option>
            </select>
          </div>
          <div className="fld">
            <label className="lbl" htmlFor="dp">Día de pago</label>
            <input id="dp" type="number" min={1} max={31} className="inp" value={empresa.dia_pago} onChange={(e) => campo("dia_pago", Number(e.target.value))} />
          </div>
          <div className="fld">
            <label className="lbl" htmlFor="srl">Tasa SRL (riesgo laboral)</label>
            <select id="srl" className="inp" value={srlPersonalizada ? "otra" : SRL.find((s) => Number(s.v) === Number(empresa.tasa_srl))?.v} onChange={(e) => campo("tasa_srl", e.target.value === "otra" ? empresa.tasa_srl : e.target.value)}>
              {SRL.map((s) => <option key={s.v} value={s.v}>{s.label}</option>)}
              {srlPersonalizada && <option value="otra">{porcentaje(empresa.tasa_srl)} · personalizada</option>}
            </select>
          </div>
          <div className="fld">
            <label className="lbl" htmlFor="ccont">Responsable de la nómina</label>
            <input id="ccont" className="inp" value={empresa.contacto_nombre ?? ""} onChange={(e) => campo("contacto_nombre", e.target.value)} placeholder="Nombre para el saludo" />
          </div>
          <div className="fld">
            <label className="lbl" htmlFor="cban">Banco de la empresa</label>
            <input id="cban" className="inp" value={empresa.banco ?? ""} onChange={(e) => campo("banco", e.target.value)} />
          </div>
          <div className="fld">
            <label className="lbl" htmlFor="ccta">Cuenta de origen</label>
            <input id="ccta" className="inp mono" value={empresa.cuenta_origen ?? ""} onChange={(e) => campo("cuenta_origen", e.target.value)} />
          </div>
        </div>
        <datalist id="nm-cat">{CATEGORIAS.map((c) => <option key={c} value={c} />)}</datalist>
        <datalist id="nm-mun">{MUNICIPIOS.map((c) => <option key={c} value={c} />)}</datalist>
        {errorEmp && <div style={{ marginTop: 12 }}><AvisoError mensaje={errorEmp} /></div>}
      </section>

      {vigente && (
        <section className="card">
          <div className="card-h">
            <h2>Tasas vigentes {vigente.vigente_desde.slice(0, 4)}</h2>
            <span className="hint">Desde el {fechaLegible(vigente.vigente_desde)}</span>
          </div>
          <div className="tw">
            <table className="tbl">
              <thead>
                <tr><th>Concepto</th><th className="num">Empleado</th><th className="num">Empleador</th><th className="num">Tope mensual (RD$)</th></tr>
              </thead>
              <tbody>
                <tr><td>AFP (pensiones)</td><td className="num">{porcentaje(vigente.afp_empleado)}</td><td className="num">{porcentaje(vigente.afp_empleador)}</td><td className="num">{formatoMonto(vigente.tope_afp)}</td></tr>
                <tr><td>SFS (salud)</td><td className="num">{porcentaje(vigente.sfs_empleado)}</td><td className="num">{porcentaje(vigente.sfs_empleador)}</td><td className="num">{formatoMonto(vigente.tope_sfs)}</td></tr>
                <tr><td>SRL (riesgo laboral)</td><td className="num">—</td><td className="num">{porcentaje(empresa.tasa_srl)}</td><td className="num">{formatoMonto(vigente.tope_srl)}</td></tr>
                <tr><td>INFOTEP</td><td className="num">{porcentaje(vigente.infotep_bonificacion_empleado, 1)} de bonificaciones</td><td className="num">{porcentaje(vigente.infotep_empleador)}</td><td className="num">Sin tope</td></tr>
                <tr><td>ISR asalariados</td><td className="num" colSpan={2}>Exento hasta RD$ {formatoMonto(vigente.exento_isr_anual)} al año</td><td className="num">—</td></tr>
              </tbody>
            </table>
          </div>
        </section>
      )}

      <section className="card">
        <div className="card-h">
          <h2>Parámetros por vigencia</h2>
          <button type="button" className="btn btn-o btn-sm" onClick={() => { setErrorPar(null); if (vigente) setEditar({ form: (() => { const f = aForm(vigente); return { ...f, v: { ...f.v, vigente_desde: "", fuente: "" } }; })(), nuevo: true }); }}>
            + Nueva vigencia
          </button>
        </div>
        <div className="tw">
          <table className="tbl" style={{ minWidth: 760 }}>
            <thead>
              <tr><th>Vigente desde</th><th className="num">Salario mínimo cotizable</th><th className="num">AFP / SFS / SRL (tope)</th><th className="num">ISR exento anual</th><th>Fuente</th><th /></tr>
            </thead>
            <tbody>
              {parametros.map((p) => (
                <tr key={p.vigente_desde}>
                  <td style={{ fontWeight: 500 }}>{fechaLegible(p.vigente_desde)}{p === vigente && <span className="bd b-teal" style={{ marginLeft: 8 }}>Vigente</span>}</td>
                  <td className="num">{formatoMonto(p.salario_minimo_cotizable)}</td>
                  <td className="num">{formatoMonto(p.tope_afp)} / {formatoMonto(p.tope_sfs)} / {formatoMonto(p.tope_srl)}</td>
                  <td className="num">{formatoMonto(p.exento_isr_anual)}</td>
                  <td className="mut" style={{ maxWidth: 320 }}>{p.fuente ?? "—"}</td>
                  <td>
                    <div className="fila-acc">
                      <button type="button" className="btn btn-g btn-sm" onClick={() => { setErrorPar(null); setEditar({ form: aForm(p), nuevo: false }); }}>Editar</button>
                      {parametros.length > 1 && <button type="button" className="btn btn-g btn-sm" style={{ color: "#B91C1C" }} onClick={() => void eliminarParametros(p)}>Eliminar</button>}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="hint pad">Cada nómina usa los parámetros vigentes al inicio de su período y guarda una copia: editar esta tabla no cambia nóminas ya aprobadas.</p>
      </section>

      {editar && (
        <Modal
          titulo={editar.nuevo ? "Nueva vigencia de parámetros" : `Parámetros desde el ${fechaLegible(editar.form.v.vigente_desde)}`}
          ancho={820}
          onCerrar={() => setEditar(null)}
          pie={
            <>
              <button type="button" className="btn btn-o" onClick={() => setEditar(null)}>Cancelar</button>
              <button type="button" className="btn btn-p" onClick={() => void guardarParametros()} disabled={guardando}>Guardar parámetros</button>
            </>
          }
        >
          <div className="form-grid">
            <div className="fld">
              <label className="lbl" htmlFor="pvd">Vigente desde</label>
              <input id="pvd" type="date" className="inp" value={editar.form.v.vigente_desde} disabled={!editar.nuevo} onChange={(e) => setEditar({ ...editar, form: { ...editar.form, v: { ...editar.form.v, vigente_desde: e.target.value } } })} />
            </div>
            {[...NUMEROS, ...TASAS, { k: "recargo_horas_extra" as const, label: "Recargo horas extra (%)" }, { k: "recargo_horas_extra_exceso" as const, label: "Recargo horas en exceso (%)" }].map((c) => (
              <div key={c.k} className="fld">
                <label className="lbl" htmlFor={`pp-${c.k}`}>{c.label}</label>
                <input id={`pp-${c.k}`} type="number" step="any" className="inp" value={editar.form.v[c.k]} onChange={(e) => setEditar({ ...editar, form: { ...editar.form, v: { ...editar.form.v, [c.k]: e.target.value } } })} />
              </div>
            ))}
            <div className="fld">
              <label className="lbl" htmlFor="pp-reg">Regalía: monto exento (RD$)</label>
              <input id="pp-reg" type="number" step="any" className="inp" placeholder="Vacío = exenta sin tope" value={editar.form.v.regalia_tope_exento} onChange={(e) => setEditar({ ...editar, form: { ...editar.form, v: { ...editar.form.v, regalia_tope_exento: e.target.value } } })} />
            </div>
            <div className="fld fld-full">
              <label className="lbl" htmlFor="pp-f">Fuente</label>
              <input id="pp-f" className="inp" value={editar.form.v.fuente} onChange={(e) => setEditar({ ...editar, form: { ...editar.form, v: { ...editar.form.v, fuente: e.target.value } } })} placeholder="Resolución de la TSS / DGII" />
            </div>
          </div>
          <div>
            <b style={{ fontSize: 14 }}>Escala anual del ISR</b>
            <div className="tw" style={{ marginTop: 8 }}>
              <table className="tbl">
                <thead><tr><th>Renta anual desde (RD$)</th><th>Tasa sobre el excedente (%)</th><th>Monto fijo (RD$)</th><th /></tr></thead>
                <tbody>
                  {editar.form.escala.map((t, i) => {
                    const cambiar = (k: keyof TramoIsr, v: string) => {
                      const escala = editar.form.escala.map((x, j) => (j === i ? { ...x, [k]: v } : x));
                      setEditar({ ...editar, form: { ...editar.form, escala } });
                    };
                    return (
                      <tr key={i}>
                        <td><input className="inp" type="number" step="any" aria-label="Desde" value={t.desde} onChange={(e) => cambiar("desde", e.target.value)} /></td>
                        <td><input className="inp" type="number" step="any" aria-label="Tasa" value={t.tasa} onChange={(e) => cambiar("tasa", e.target.value)} /></td>
                        <td><input className="inp" type="number" step="any" aria-label="Fijo" value={t.fijo} onChange={(e) => cambiar("fijo", e.target.value)} /></td>
                        <td>
                          <button type="button" className="btn btn-g btn-sm" onClick={() => setEditar({ ...editar, form: { ...editar.form, escala: editar.form.escala.filter((_, j) => j !== i) } })}>Quitar</button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <button type="button" className="btn btn-g btn-sm" onClick={() => setEditar({ ...editar, form: { ...editar.form, escala: [...editar.form.escala, { desde: "", tasa: "", fijo: "0" }] } })}>
              + Agregar tramo
            </button>
          </div>
          {errorPar && <AvisoError mensaje={errorPar} />}
        </Modal>
      )}
      {toast}
    </NominaShell>
  );
}
