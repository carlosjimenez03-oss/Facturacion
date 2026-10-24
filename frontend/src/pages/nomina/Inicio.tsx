// Nómina › Inicio (vista "inicio" de Nomina.dc.html): saludo, guía de 4 pasos (con el formulario de
// datos de la empresa), resumen del período a calcular, gráfico "Costo de nómina vs Pago de nómina" e
// indicadores (salario mínimo cotizable y topes de la TSS vigentes).
import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { EMPRESA_NOMBRE, formatoMonto } from "../../lib/marca";
import { nominaApi, type CostoPagoMes, type EmpresaNomina, type ResumenNomina } from "../../lib/nominaApi";
import { AvisoError, Cargando } from "../ingresos/IngresosShell";
import { useAviso } from "../ingresos/useAviso";
import { mensajeError } from "../ingresos/ingresosUtil";
import { Encabezado, NominaShell } from "./NominaShell";
import { CATEGORIAS, MUNICIPIOS, capitalizar, payloadEmpresa, refrescarAvisos, saludo } from "./nominaUtil";

function IconoCheck() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="#15803D" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="12" cy="12" r="9" />
      <path d="m8.5 12 2.5 2.5 4.5-5" />
    </svg>
  );
}

function IconoPendiente() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeDasharray="3 3" aria-hidden="true">
      <circle cx="12" cy="12" r="9" />
    </svg>
  );
}

function IconoFlecha() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" aria-hidden="true">
      <path d="M7 17 17 7M8 7h9v9" />
    </svg>
  );
}

export function NominaInicio() {
  const navigate = useNavigate();
  const [resumen, setResumen] = useState<ResumenNomina | null>(null);
  const [empresa, setEmpresa] = useState<EmpresaNomina | null>(null);
  const [meses, setMeses] = useState<CostoPagoMes[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [paso, setPaso] = useState<number | null>(null);
  const [form, setForm] = useState({ rnc: "", categoria: CATEGORIAS[0], municipio: MUNICIPIOS[0] });
  const [errorForm, setErrorForm] = useState<string | null>(null);
  const [ocupado, setOcupado] = useState(false);
  const [toast, mostrarAviso] = useAviso();

  const cargar = useCallback(async () => {
    try {
      const [r, e] = await Promise.all([nominaApi.resumen(), nominaApi.empresa()]);
      setResumen(r);
      setEmpresa(e);
      setForm({ rnc: e.rnc ?? "", categoria: e.categoria ?? CATEGORIAS[0], municipio: e.municipio ?? MUNICIPIOS[0] });
      setError(null);
      nominaApi
        .costoPago(r.periodo?.anio ?? r.periodo_sugerido.anio)
        .then(setMeses)
        .catch(() => setMeses([]));
    } catch (err) {
      setError(mensajeError(err));
    }
  }, []);

  useEffect(() => {
    void cargar();
  }, [cargar]);

  if (error) {
    return (
      <NominaShell>
        <Encabezado titulo={`¡${saludo()}!`} sub="Aquí puedes ver tus resúmenes y accesos rápidos." />
        <AvisoError titulo="No se pudo cargar Nómina." mensaje={error} onReintentar={() => void cargar()} />
      </NominaShell>
    );
  }
  if (!resumen || !empresa) {
    return (
      <NominaShell>
        <Cargando />
      </NominaShell>
    );
  }

  const hechos = [true, resumen.empresa_completa, resumen.empleados_activos > 0, resumen.nomina_calculada];
  const primeroPendiente = hechos.findIndex((h) => !h);
  const sel = paso ?? (primeroPendiente === -1 ? 0 : primeroPendiente);
  const periodo = resumen.periodo;
  const calculado = !!periodo && periodo.estado !== "borrador";
  const nombrePeriodo = capitalizar(periodo?.nombre ?? resumen.periodo_sugerido.nombre);
  const nombre = resumen.contacto_nombre || EMPRESA_NOMBRE;

  async function guardarEmpresa() {
    if (!empresa) return;
    const rnc = form.rnc.replace(/\D/g, "");
    if (rnc.length !== 9 && rnc.length !== 11) return setErrorForm("El RNC lleva 9 dígitos (o 11 si es una cédula).");
    setOcupado(true);
    try {
      await nominaApi.guardarEmpresa({ ...payloadEmpresa(empresa), rnc, categoria: form.categoria, municipio: form.municipio });
      mostrarAviso("Datos de la empresa guardados");
      setPaso(2);
      setErrorForm(null);
      refrescarAvisos();
      void cargar();
    } catch (err) {
      setErrorForm(mensajeError(err));
    } finally {
      setOcupado(false);
    }
  }

  async function calcularNomina() {
    if (!resumen) return;
    setOcupado(true);
    try {
      let id = periodo?.id;
      if (!id) {
        const s = resumen.periodo_sugerido;
        const creado = await nominaApi.crearPeriodo({ tipo: s.tipo, anio: s.anio, mes: s.mes, quincena: s.tipo === "quincenal" ? 1 : null });
        id = creado.id;
      }
      const p = periodo && !["borrador", "calculada"].includes(periodo.estado) ? periodo : await nominaApi.calcular(id);
      refrescarAvisos();
      navigate(`/nomina/periodos/${p.id}`);
    } catch (err) {
      mostrarAviso("No se pudo calcular la nómina", { detalle: mensajeError(err), error: true });
    } finally {
      setOcupado(false);
    }
  }

  const PASOS = [
    { label: "Crea tu cuenta en Nómina", titulo: "Cuenta creada", desc: "Tu cuenta está lista.", cta: "Ver resumen", accion: () => setPaso(1) },
    {
      label: "Completa los datos de tu empresa (RNC)",
      titulo: "Completa los datos de tu empresa",
      desc: resumen.empresa_completa
        ? `RNC ${empresa.rnc} · ${empresa.categoria} · ${empresa.municipio}. Puedes cambiarlos en Configuración.`
        : "Agrega RNC, categoría y municipio de tu empresa para continuar con la configuración.",
      cta: resumen.empresa_completa ? "Ir a configuración" : "Completar empresa",
      accion: resumen.empresa_completa ? () => navigate("/nomina/configuracion") : () => void guardarEmpresa(),
    },
    {
      label: "Registra o importa empleados",
      titulo: "Registra o importa empleados",
      desc:
        resumen.empleados_activos > 0
          ? `Tienes ${resumen.empleados_activos} empleado${resumen.empleados_activos === 1 ? "" : "s"} activo${resumen.empleados_activos === 1 ? "" : "s"}.`
          : "Agrega a tu equipo manualmente o importa un archivo CSV (Excel) con cédula y salario.",
      cta: "Ir a empleados",
      accion: () => navigate("/nomina/empleados"),
    },
    {
      label: "Calcula tu primera nómina",
      titulo: "Calcula tu primera nómina",
      desc: "Calculamos TSS, ISR e INFOTEP automáticamente según las tasas vigentes.",
      cta: "Calcular nómina",
      accion: () => void calcularNomina(),
    },
  ];
  const actual = PASOS[sel];

  const ind = resumen.indicadores;
  const anioInd = ind ? ind.fecha.slice(0, 4) : String(resumen.periodo_sugerido.anio);
  const conDatos = meses.filter((m) => Number(m.costo) > 0);
  const maximo = Math.max(1, ...conDatos.map((m) => Number(m.costo)));

  return (
    <NominaShell>
      <Encabezado titulo={`¡${saludo()}, ${nombre}!`} sub="Aquí puedes ver tus resúmenes y accesos rápidos." />

      <section className="card onb" aria-label="Primeros pasos">
        <div className="onb-steps">
          {PASOS.map((p, i) => (
            <button
              key={p.label}
              type="button"
              className={`onb-step${i === sel ? " on" : ""}${hechos[i] ? " done" : ""}`}
              onClick={() => setPaso(i)}
              aria-current={i === sel ? "step" : undefined}
            >
              {hechos[i] ? <IconoCheck /> : <IconoPendiente />}
              <span>{p.label}</span>
            </button>
          ))}
          <span className="hint onb-count">
            <b>{resumen.pasos_completados}</b> de 4 pasos completados
          </span>
        </div>
        <div className="onb-panel">
          <span className="onb-ic" aria-hidden="true">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.6} strokeLinecap="round" strokeLinejoin="round">
              <rect x="5" y="3" width="14" height="18" rx="1.5" />
              <path d="M9 7h2M13 7h2M9 11h2M13 11h2M10 21v-4h4v4" />
            </svg>
          </span>
          <h3>{actual.titulo}</h3>
          <p>{actual.desc}</p>
          {sel === 1 && !resumen.empresa_completa && (
            <div className="nm-form3">
              <div className="fld">
                <label className="lbl" htmlFor="nrnc">RNC</label>
                <input id="nrnc" className="inp mono" value={form.rnc} placeholder="1-31-89754-2" onChange={(e) => setForm({ ...form, rnc: e.target.value })} />
              </div>
              <div className="fld">
                <label className="lbl" htmlFor="ncat">Categoría</label>
                <select id="ncat" className="inp" value={form.categoria} onChange={(e) => setForm({ ...form, categoria: e.target.value })}>
                  {CATEGORIAS.map((c) => (
                    <option key={c}>{c}</option>
                  ))}
                </select>
              </div>
              <div className="fld">
                <label className="lbl" htmlFor="nmun">Municipio</label>
                <select id="nmun" className="inp" value={form.municipio} onChange={(e) => setForm({ ...form, municipio: e.target.value })}>
                  {MUNICIPIOS.map((c) => (
                    <option key={c}>{c}</option>
                  ))}
                </select>
              </div>
            </div>
          )}
          {errorForm && <AvisoError mensaje={errorForm} />}
          <div className="row">
            <button type="button" className="btn onb-cta" onClick={actual.accion} disabled={ocupado}>
              {actual.cta} <IconoFlecha />
            </button>
          </div>
        </div>
      </section>

      <section className="card nm-sum" aria-label="Resumen del período">
        {[
          { i: "▦", k: "Periodo a calcular", v: nombrePeriodo },
          { i: "☺", k: "No. empleados", v: String(periodo?.empleados || resumen.empleados_activos) },
          { i: "$", k: "Total pago", v: calculado ? `RD$ ${formatoMonto(periodo?.totales.neto)}` : "-" },
          { i: "◎", k: "Total costo", v: calculado ? `RD$ ${formatoMonto(periodo?.totales.costo_total)}` : "-" },
        ].map((x) => (
          <div key={x.k} className="nm-sum-i">
            <span className="nm-sum-ic" aria-hidden="true">{x.i}</span>
            <div>
              <b>{x.k}</b>
              <span className="mut">{x.v}</span>
            </div>
          </div>
        ))}
        <button type="button" className="btn nm-calc" onClick={() => void calcularNomina()} disabled={ocupado}>
          {periodo && !["borrador", "calculada"].includes(periodo.estado) ? "Ver nómina" : "Calcular nómina"}
        </button>
      </section>

      <div className="nm-cols">
        <section className="card" style={{ flex: "3 1 480px" }}>
          <div className="card-h">
            <h2>Costo de nómina vs Pago de nómina</h2>
            {conDatos.length > 0 && (
              <span className="nm-leyenda">
                <span><i style={{ background: "#0F766E" }} />Costo total empresa</span>
                <span><i style={{ background: "#99D5CE" }} />Pago neto a empleados</span>
              </span>
            )}
          </div>
          <div className="card-b">
            {conDatos.length > 0 ? (
              <>
                <div className="nm-bars" role="img" aria-label="Costo total y pago neto por mes">
                  {conDatos.slice(-6).map((m) => (
                    <div key={m.mes} className="nm-bar-g" title={`${capitalizar(m.nombre)}: costo RD$ ${formatoMonto(m.costo)} · neto RD$ ${formatoMonto(m.neto)}`}>
                      <div className="nm-bar" style={{ height: `${(Number(m.costo) / maximo) * 100}%` }} />
                      <div className="nm-bar neto" style={{ height: `${(Number(m.neto) / maximo) * 100}%` }} />
                    </div>
                  ))}
                </div>
                <div className="nm-bars-x">
                  {conDatos.slice(-6).map((m) => (
                    <span key={m.mes}>{m.nombre}</span>
                  ))}
                </div>
                {calculado && periodo && (
                  <p className="hint" style={{ margin: "12px 0 0" }}>
                    {capitalizar(periodo.nombre)}: costo RD$ {formatoMonto(periodo.totales.costo_total)} · pago neto RD$ {formatoMonto(periodo.totales.neto)}
                  </p>
                )}
              </>
            ) : (
              <div className="empty">
                <span className="ei">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.7} strokeLinecap="round" aria-hidden="true">
                    <path d="M3 3v18h18M7 15l4-4 3 3 5-6" />
                  </svg>
                </span>
                <h3>Aún no hay nóminas calculadas</h3>
                <p>Pulsa «Calcular nómina» para ver el costo y el pago de {nombrePeriodo.toLowerCase()}.</p>
              </div>
            )}
          </div>
        </section>
        <section className="card" style={{ flex: "2 1 360px" }}>
          <div className="card-h">
            <h2>Indicadores</h2>
            <span className="hint">Topes y salario mínimo {anioInd}</span>
          </div>
          {ind ? (
            [
              { s: "SM", k: "Salario mínimo", v: ind.salario_minimo_cotizable },
              { s: "AFP", k: "AFP (tope)", v: ind.tope_afp },
              { s: "SFS", k: "SFS (tope)", v: ind.tope_sfs },
              { s: "SRL", k: "SRL (tope)", v: ind.tope_srl },
            ].map((x) => (
              <div key={x.s} className="nm-ind">
                <span className="nm-ind-ic" aria-hidden="true">{x.s}</span>
                <div style={{ flex: 1 }}>
                  <div className="hint">{anioInd}</div>
                  <b>{x.k}</b>
                </div>
                <span className="nm-ind-v">RD$ {formatoMonto(x.v)}</span>
              </div>
            ))
          ) : (
            <p className="pad mut">Sin parámetros vigentes: agréguelos en Configuración.</p>
          )}
          {ind && (
            <p className="hint pad" style={{ paddingTop: 0 }}>
              Salario mínimo cotizable de la TSS vigente desde el {ind.vigente_desde.split("-").reverse().join("/")}. ISR: exento hasta RD$ {formatoMonto(ind.exento_isr_mensual)} al mes.
            </p>
          )}
        </section>
      </div>
      {toast}
    </NominaShell>
  );
}
