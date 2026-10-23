// Pre-cálculos IT-1 (ITBIS mensual), IR-2 (ISR de sociedades, anual) e IR-17 (retenciones
// de ISR), según PreCalculo.dc.html del prototipo. Las líneas, el resultado y el resumen
// los calcula erp-api; aquí se muestran, se editan los ajustes manuales y se copian los
// valores para transcribirlos en la Oficina Virtual de la DGII. Los números de línea son
// de este pre-cálculo, NO las casillas del formulario oficial: se transcribe por concepto.
import { Fragment, useCallback, useEffect, useMemo, useState } from "react";
import { Link, Navigate, useNavigate, useParams } from "react-router-dom";
import { IconAlerta } from "../../layout/icons";
import { impuestosApi } from "../../lib/impuestosApi";
import type { AjusteEditable, Casilla, FormularioPreCalculo, MarcarPresentadaPayload, PreCalculo } from "../../lib/impuestosApi";
import { EMPRESA_NOMBRE, EMPRESA_RNC, MARCA, formatoMonto, formatoRnc } from "../../lib/marca";
import { AvisoError, Cargando, Vacio } from "../ingresos/IngresosShell";
import { hoyIso, mensajeError } from "../ingresos/ingresosUtil";
import { useAviso } from "../ingresos/useAviso";
import { Advertencias, EstadoDeclaracionBadge, ModalCopiaManual, ModalPresentada, SelectorPeriodo } from "./ImpuestosShell";
import {
  copiarTexto,
  etiquetaPeriodo,
  fechaDgii,
  montoContable,
  nombrePeriodo,
  periodoAnterior,
  periodosRecientes,
  textoVencimiento,
  useAnio,
  useDeclaraciones,
  usePeriodoMensual,
} from "./impuestosUtil";

const PESTANAS: { id: FormularioPreCalculo; label: string; nombre: string; descripcion: string }[] = [
  { id: "it1", label: "IT-1 · ITBIS mensual", nombre: "IT-1", descripcion: "Declaración jurada mensual del ITBIS" },
  { id: "ir2", label: "IR-2 · ISR sociedades", nombre: "IR-2", descripcion: "Declaración jurada anual de sociedades" },
  { id: "ir17", label: "IR-17 · Retenciones ISR", nombre: "IR-17", descripcion: "Declaración y pago de retenciones de ISR" },
];

const ES_NUMERO = /^-?\d+(\.\d+)?$/;

const AVISO_LINEAS =
  "Los números L1, L2… son líneas de este pre-cálculo, no las casillas del formulario oficial de la DGII: transcriba cada valor buscando su concepto en el formulario.";

/** Valor tal como lo copia el prototipo: sin separador de miles y los negativos entre paréntesis. */
const valorPlano = (v: string) => montoContable(v).replace(/,/g, "");

/** Valor del campo de un ajuste tal como llega del servidor ("1500.5" -> "1500.5", "0.00" -> "0"). */
const valorCampo = (a: AjusteEditable) => String(Number(a.valor) || 0);

/** Texto del campo -> valor que se envía: "" (vacío) borra el manual y vuelve al sugerido. */
const valorEnvio = (v: string) => (v.trim() === "" ? "" : (Number(v) || 0).toFixed(2));

export function PreCalculoPage() {
  const { formulario: param } = useParams<{ formulario: string }>();
  const navigate = useNavigate();
  const [mensual, setMensual] = usePeriodoMensual();
  const [anio, setAnio] = useAnio();
  const [toast, mostrar] = useAviso();
  const pestana = PESTANAS.find((p) => p.id === param);
  const formulario = pestana?.id ?? "it1";
  const anual = formulario === "ir2";
  const periodo = anual ? anio : mensual;
  const hoy = new Date().getFullYear();
  const { buscar, actualizar } = useDeclaraciones([String(hoy), String(hoy - 1), periodo.slice(0, 4)]);

  const [datos, setDatos] = useState<PreCalculo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [intento, setIntento] = useState(0);
  const [valores, setValores] = useState<Record<string, string>>({});
  const [guardando, setGuardando] = useState(false);
  const [errorAjustes, setErrorAjustes] = useState<string | null>(null);
  const [modal, setModal] = useState(false);
  const [copiaManual, setCopiaManual] = useState<string | null>(null);

  const aplicar = useCallback((p: PreCalculo) => {
    setDatos(p);
    setValores(Object.fromEntries(p.ajustes.map((a) => [a.clave, valorCampo(a)])));
  }, []);

  useEffect(() => {
    if (!pestana) return;
    let vigente = true;
    setDatos(null);
    setError(null);
    setErrorAjustes(null);
    impuestosApi
      .preCalculo(formulario, periodo)
      .then((p) => vigente && aplicar(p))
      .catch((err) => vigente && setError(mensajeError(err)));
    return () => {
      vigente = false;
    };
  }, [pestana, formulario, periodo, intento, aplicar]);

  const opciones = useMemo(() => {
    if (anual) {
      const anios = Array.from({ length: 5 }, (_, i) => String(hoy - i));
      if (!anios.includes(anio)) anios.push(anio);
      return anios.map((a) => ({ valor: a, label: etiquetaPeriodo(`Ejercicio ${a}`, buscar("ir2", a)) }));
    }
    const lista = periodosRecientes();
    if (!lista.includes(mensual)) lista.push(mensual);
    return lista.map((p) => ({ valor: p, label: etiquetaPeriodo(nombrePeriodo(p, true), buscar(formulario, p)) }));
  }, [anual, anio, mensual, formulario, hoy, buscar]);

  if (!pestana) return <Navigate to="/impuestos/precalculo/it1" replace />;

  const declaracion = buscar(formulario, periodo);
  const presentada = declaracion?.estado === "presentada";
  const titulo = datos?.titulo || `Pre-cálculo ${pestana.nombre} · ${anual ? `ejercicio ${anio}` : nombrePeriodo(mensual)}`;

  function irA(id: FormularioPreCalculo) {
    if (id === "ir2") return navigate(`/impuestos/precalculo/ir2?anio=${anual ? anio : mensual.slice(0, 4)}`);
    // Desde el IR-2 se vuelve al mes anterior, o a diciembre si se estaba viendo otro ejercicio.
    const p = anual && anio !== periodoAnterior().slice(0, 4) ? `${anio}12` : mensual;
    navigate(`/impuestos/precalculo/${id}?periodo=${p}`);
  }

  async function copiar() {
    if (!datos) return;
    // Los números son líneas de este pre-cálculo, no casillas del formulario oficial: se
    // copian como "Línea N" para que nadie los tome por el número de casilla de la DGII.
    const texto =
      `${titulo}\n${AVISO_LINEAS}\nLínea\tConcepto\tValor\n` +
      datos.secciones.map((s) => s.casillas.map((c) => `Línea ${c.codigo}\t${c.etiqueta}\t${valorPlano(c.valor)}`).join("\n")).join("\n") +
      `\n\t${datos.resultado.etiqueta}\t${valorPlano(datos.resultado.valor)}`;
    if (await copiarTexto(texto)) mostrar("Valores copiados al portapapeles", { detalle: "Péguelos en la Oficina Virtual o en Excel" });
    else setCopiaManual(texto);
  }

  /** Envía solo los ajustes indicados (los demás, sugeridos o manuales, no se tocan). */
  async function enviarAjustes(cambios: Record<string, string>, aviso: string) {
    setGuardando(true);
    setErrorAjustes(null);
    try {
      aplicar(await impuestosApi.guardarAjustes(formulario, periodo, cambios));
      mostrar(aviso, { detalle: `${pestana!.nombre} recalculado` });
    } catch (err) {
      setErrorAjustes(mensajeError(err));
    } finally {
      setGuardando(false);
    }
  }

  function guardarAjustes() {
    if (!datos) return;
    const cambios = Object.fromEntries(datos.ajustes.filter((a) => modificado(a)).map((a) => [a.clave, valorEnvio(valores[a.clave] ?? "")]));
    void enviarAjustes(cambios, "Ajustes guardados");
  }

  function restablecer(a: AjusteEditable) {
    void enviarAjustes({ [a.clave]: "" }, a.sugerido != null ? `${a.etiqueta}: valor sugerido restablecido` : `${a.etiqueta}: valor manual quitado`);
  }

  async function marcar(payload: MarcarPresentadaPayload) {
    actualizar(await impuestosApi.marcarPresentada(formulario, periodo, payload));
    setModal(false);
    mostrar(`${pestana!.nombre} marcado como presentado`, { detalle: anual ? `Ejercicio ${anio}` : nombrePeriodo(mensual, true) });
  }

  async function desmarcar() {
    try {
      actualizar(await impuestosApi.desmarcarPresentada(formulario, periodo));
      mostrar(`${pestana!.nombre} vuelve a estar pendiente`);
    } catch (err) {
      mostrar("No se pudo desmarcar la declaración", { detalle: mensajeError(err), error: true });
    }
  }

  /** El campo cambió respecto de lo que devolvió el servidor (vaciarlo cuenta como cambio si había un manual). */
  function modificado(a: AjusteEditable): boolean {
    const v = valores[a.clave] ?? "";
    if (v.trim() === "") return a.manual;
    return (Number(v) || 0) !== (Number(a.valor) || 0);
  }
  const cambiado = !!datos && datos.ajustes.some(modificado);

  return (
    <div className="content">
      <div className="pc-imp-h">
        <div>
          <b>{EMPRESA_NOMBRE}</b>
          {EMPRESA_RNC && `RNC ${formatoRnc(EMPRESA_RNC)}`}
        </div>
        <div style={{ textAlign: "right" }}>
          Generado por {MARCA} el {fechaDgii(hoyIso())}
          <br />
          Documento de referencia
        </div>
      </div>
      <div className="ph">
        <div>
          <div className="crumb no-imprimir">
            <Link to="/impuestos/formatos/606">Impuestos</Link>
            <span>/</span>
            <span>Pre-cálculos</span>
          </div>
          <h1>{titulo}</h1>
          <p>{datos?.subtitulo || pestana.descripcion}</p>
        </div>
        <div className="row imp-acciones no-imprimir">
          <SelectorPeriodo valor={periodo} opciones={opciones} onCambiar={anual ? setAnio : setMensual} etiqueta={anual ? "Ejercicio" : "Período"} />
          <button type="button" className="btn btn-o" onClick={() => window.print()} disabled={!datos}>
            Imprimir / PDF
          </button>
          <button type="button" className="btn btn-p" onClick={copiar} disabled={!datos}>
            Copiar valores para la Oficina Virtual
          </button>
        </div>
      </div>

      <div className="tabs no-imprimir" role="tablist" aria-label="Declaración">
        {PESTANAS.map((t) => (
          <button key={t.id} type="button" role="tab" aria-selected={formulario === t.id} className={formulario === t.id ? "tab on" : "tab"} onClick={() => irA(t.id)}>
            {t.label}
          </button>
        ))}
      </div>

      {error && <AvisoError titulo={`No se pudo calcular el ${pestana.nombre}.`} mensaje={error} onReintentar={() => setIntento((n) => n + 1)} />}
      {!error && !datos && <Cargando />}

      {datos && (
        <>
          {datos.nota && (
            <div className="al al-i">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <circle cx="12" cy="12" r="9" />
                <path d="M12 11v5M12 8h.01" />
              </svg>
              <span>{datos.nota}</span>
            </div>
          )}
          <Advertencias lista={datos.advertencias} />

          <div className="pc-layout">
            <div className="pc-main">
              {datos.secciones.length > 0 && <p className="hint pc-lineas">{AVISO_LINEAS}</p>}
              {datos.secciones.length === 0 && (
                <section className="card">
                  <Vacio titulo="Sin movimientos en el período" texto="No hay comprobantes ni compras registradas para calcular este formulario." />
                </section>
              )}
              {datos.secciones.map((s) => (
                <section key={s.numero} className="card">
                  <div className="card-h pc-sec-h">
                    <h2>
                      <span className="mono pc-sec-n">{s.numero}</span>
                      {s.titulo}
                    </h2>
                  </div>
                  <div>
                    {s.casillas.map((c) => (
                      <FilaCasilla key={c.codigo} casilla={c} />
                    ))}
                  </div>
                </section>
              ))}
            </div>

            <aside className="pc-side">
              <section className="card pc-res">
                <div className="card-b">
                  <span className="pc-res-k">
                    {datos.resultado.etiqueta} <EstadoDeclaracionBadge declaracion={declaracion} />
                  </span>
                  <span className="pc-res-v">RD$ {montoContable(datos.resultado.valor)}</span>
                  <span className="hint">{textoVencimiento(datos.vence || declaracion?.vence, declaracion)}</span>
                  {presentada && (declaracion?.referencia || declaracion?.monto) && (
                    <span className="hint">
                      {declaracion.referencia && (
                        <>
                          Confirmación <span className="mono">{declaracion.referencia}</span>
                        </>
                      )}
                      {declaracion.referencia && declaracion.monto && " · "}
                      {declaracion.monto && `RD$ ${formatoMonto(declaracion.monto)} pagados`}
                    </span>
                  )}
                  <div className="row no-imprimir" style={{ marginTop: 4 }}>
                    {presentada ? (
                      <button type="button" className="btn btn-o btn-sm" onClick={desmarcar}>
                        Desmarcar presentada
                      </button>
                    ) : (
                      <button type="button" className="btn btn-o btn-sm" onClick={() => setModal(true)}>
                        Marcar como presentada
                      </button>
                    )}
                  </div>
                </div>
              </section>

              {datos.resumen.length > 0 && (
                <section className="card">
                  <div className="card-h">
                    <h3>Composición</h3>
                  </div>
                  <dl className="kv card-b" style={{ gridTemplateColumns: "1fr auto", margin: 0 }}>
                    {datos.resumen.map((r) => (
                      <Fragment key={r.etiqueta}>
                        <dt>{r.etiqueta}</dt>
                        <dd className="num">{ES_NUMERO.test(r.valor) ? montoContable(r.valor) : r.valor}</dd>
                      </Fragment>
                    ))}
                  </dl>
                </section>
              )}

              {datos.ajustes.length > 0 && (
                <section className="card no-imprimir">
                  <div className="card-h">
                    <div>
                      <h3>Ajustes manuales</h3>
                      <span className="hint">Valores que el sistema no puede conocer. Deje un campo vacío para volver al valor sugerido.</span>
                    </div>
                  </div>
                  <div className="card-b stack">
                    {datos.ajustes.map((a) => (
                      <div key={a.clave} className="pc-ajuste">
                        <label className="lbl" htmlFor={`aj-${a.clave}`}>
                          {a.etiqueta}
                          {a.ayuda && (
                            <span className="tip r" data-tip={a.ayuda}>
                              <button type="button" className="info" aria-label={`Explicación: ${a.etiqueta}`}>
                                <IconoInfo />
                              </button>
                            </span>
                          )}
                        </label>
                        <input
                          id={`aj-${a.clave}`}
                          type="number"
                          step="0.01"
                          className="inp inp-sm"
                          style={{ textAlign: "right" }}
                          value={valores[a.clave] ?? ""}
                          placeholder={a.sugerido != null ? Number(a.sugerido).toFixed(2) : "0.00"}
                          onChange={(e) => setValores((v) => ({ ...v, [a.clave]: e.target.value }))}
                          aria-describedby={`aj-${a.clave}-origen`}
                        />
                        <span id={`aj-${a.clave}-origen`} className="hint pc-ajuste-origen">
                          {a.manual ? (
                            <>
                              <span className="bd b-borr">Manual</span>
                              {a.sugerido != null && ` · sugerido RD$ ${montoContable(a.sugerido)}`}
                              {" · "}
                              <button type="button" className="btn btn-g btn-sm" onClick={() => restablecer(a)} disabled={guardando}>
                                Restablecer
                              </button>
                            </>
                          ) : a.sugerido != null ? (
                            "Sugerido por el sistema"
                          ) : (
                            "Sin valor manual"
                          )}
                        </span>
                      </div>
                    ))}
                    {errorAjustes && (
                      <div className="al al-d" role="alert">
                        <IconAlerta />
                        <span>
                          <b>No se guardaron los ajustes.</b> {errorAjustes}
                        </span>
                      </div>
                    )}
                    <div className="row" style={{ justifyContent: "flex-end" }}>
                      <button type="button" className="btn btn-p btn-sm" onClick={guardarAjustes} disabled={!cambiado || guardando}>
                        {guardando ? "Recalculando…" : "Guardar y recalcular"}
                      </button>
                    </div>
                  </div>
                </section>
              )}

              <div className="al al-w" style={{ fontSize: 13 }}>
                <IconAlerta />
                <span>Pre-cálculo de referencia. Revise los valores con su contador antes de presentar la declaración en la Oficina Virtual de la DGII.</span>
              </div>
            </aside>
          </div>
        </>
      )}

      {modal && (
        <ModalPresentada
          titulo={`${pestana.nombre} de ${anual ? `ejercicio ${anio}` : nombrePeriodo(mensual)} presentado`}
          montoSugerido={datos?.resultado.valor}
          onCerrar={() => setModal(false)}
          onConfirmar={marcar}
        />
      )}
      {copiaManual != null && <ModalCopiaManual texto={copiaManual} onCerrar={() => setCopiaManual(null)} />}
      {toast}
    </div>
  );
}

function IconoInfo() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" aria-hidden="true">
      <circle cx="12" cy="12" r="9" />
      <path d="M12 11v5M12 8h.01" />
    </svg>
  );
}

/** Una línea del pre-cálculo: número de línea, concepto (con explicación) y valor; subtotales y total resaltados. */
function FilaCasilla({ casilla: c }: { casilla: Casilla }) {
  return (
    <div className={c.tipo === "normal" ? "pc-casilla" : `pc-casilla ${c.tipo}`}>
      <span className="mono pc-c" title={`Línea ${c.codigo} de este pre-cálculo (no es la casilla del formulario oficial)`}>
        L{c.codigo}
      </span>
      <span className="pc-l">
        {c.etiqueta}
        {c.ayuda && (
          <span className="tip" data-tip={c.ayuda}>
            <button type="button" className="info" aria-label={`Explicación: ${c.etiqueta}`}>
              <IconoInfo />
            </button>
          </span>
        )}
      </span>
      <span className="num">{montoContable(c.valor)}</span>
    </div>
  );
}
