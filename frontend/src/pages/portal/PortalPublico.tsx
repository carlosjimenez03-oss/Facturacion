// Página pública del portal de clientes (/p/:token): sin sesión, de solo
// lectura. erp-api valida el token y solo devuelve los e-CF aceptados del
// cliente de ese enlace; los PDF y XML también pasan por erp-api con el token.
// Fase 7: «Pagar con tarjeta» lleva al cliente a la página de pago del banco
// (Azul) con un formulario firmado por erp-api; la tarjeta nunca pasa por aquí.
import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { formatoMonto, formatoRnc, MARCA } from "../../lib/marca";
import { enviarAPasarela, pagosApi, recordarPortal } from "../../lib/pagosApi";
import { ErpApiError, solucionesApi } from "../../lib/solucionesApi";
import type { FacturaPortal, PortalPublico } from "../../lib/solucionesApi";
import { Modal } from "../ingresos/IngresosShell";
import { fechaLegible, mensajeError } from "../ingresos/ingresosUtil";
import { ESTADO_PAGO, ETIQUETA_METODO } from "./portalUtil";
import "./portal.css";

export function PortalPublicoPage() {
  const { token = "" } = useParams();
  const [datos, setDatos] = useState<PortalPublico | null>(null);
  const [error, setError] = useState<{ noValido: boolean; texto: string } | null>(null);
  const [pagar, setPagar] = useState<FacturaPortal | null>(null);
  const [monto, setMonto] = useState("");
  const [enviando, setEnviando] = useState(false);
  const [errorPago, setErrorPago] = useState<string | null>(null);

  useEffect(() => {
    document.title = `Mis facturas · ${MARCA}`;
    solucionesApi
      .portalPublico(token)
      .then(setDatos, (err) => setError({ noValido: err instanceof ErpApiError && err.status === 404, texto: mensajeError(err) }));
  }, [token]);

  if (error) {
    return (
      <div className="pp">
        <div className="pp-in">
          <section className="card">
            <div className="empty">
              <h3>{error.noValido ? "Este enlace no es válido" : "No se pudo abrir el portal"}</h3>
              <p>{error.noValido ? "El enlace venció, fue revocado o está incompleto. Pida uno nuevo a la empresa que se lo envió." : error.texto}</p>
            </div>
          </section>
        </div>
      </div>
    );
  }
  if (!datos) {
    return (
      <div className="pp">
        <p className="mut" style={{ textAlign: "center" }}>
          Cargando…
        </p>
      </div>
    );
  }

  const t = datos.transferencia;
  const enLinea = datos.pago_en_linea;
  const puedePagar = (f: FacturaPortal) => Boolean(enLinea) && Number(f.pendiente) > 0;

  function abrirPago(f: FacturaPortal) {
    setPagar(f);
    setMonto(String(Number(f.pendiente).toFixed(2)));
    setErrorPago(null);
  }

  async function continuarAlPago() {
    if (!pagar || !enLinea) return;
    const parcial = enLinea.permitir_parcial && Number(monto) !== Number(pagar.pendiente);
    if (parcial && !(Number(monto) > 0 && Number(monto) <= Number(pagar.pendiente))) {
      setErrorPago(`Escriba un monto entre RD$ 0.01 y RD$ ${formatoMonto(pagar.pendiente)}.`);
      return;
    }
    setEnviando(true);
    setErrorPago(null);
    try {
      const pago = await pagosApi.iniciar(token, pagar.id, parcial ? Number(monto).toFixed(2) : null);
      recordarPortal(pago.referencia, token);
      enviarAPasarela(pago);
    } catch (err) {
      setErrorPago(mensajeError(err));
      setEnviando(false);
    }
  }

  const botonPagar = (f: FacturaPortal) =>
    puedePagar(f) && (
      <button type="button" className="btn btn-p btn-sm" data-pagar={f.encf} onClick={() => abrirPago(f)}>
        Pagar con tarjeta
      </button>
    );

  return (
    <div className="pp">
      <div className="pp-in">
        <div className="pp-head">
          <div>
            <span className="pp-marca">{datos.empresa}</span>
            <h1>Facturas de {datos.cliente_nombre}</h1>
            <p>
              {datos.cliente_rnc ? `RNC/Cédula ${formatoRnc(datos.cliente_rnc)} · ` : ""}Comprobantes fiscales electrónicos aceptados por la DGII.
            </p>
          </div>
          <div className="card stat" style={{ minWidth: 220 }}>
            <span className="k">Saldo pendiente</span>
            <span className="v" data-saldo>
              RD$ {formatoMonto(datos.saldo_pendiente)}
            </span>
          </div>
        </div>

        <section className="card">
          <div className="card-h">
            <h2>Mis facturas</h2>
            <span className="hint">{datos.facturas.length} comprobante(s)</span>
          </div>
          {datos.facturas.length === 0 ? (
            <p className="pad mut">Aún no hay comprobantes para mostrar.</p>
          ) : (
            <>
              <ul className="pp-lista">
                {datos.facturas.map((f) => (
                  <li key={f.id}>
                    <div className="row" style={{ justifyContent: "space-between" }}>
                      <b className="mono">{f.encf}</b>
                      <span className={ESTADO_PAGO[f.estado_pago].badge}>{ESTADO_PAGO[f.estado_pago].label}</span>
                    </div>
                    <span className="hint">
                      {fechaLegible(f.fecha_emision)} · Total RD$ {formatoMonto(f.total)}
                      {Number(f.pendiente) > 0 ? ` · Pendiente RD$ ${formatoMonto(f.pendiente)}` : ""}
                    </span>
                    <div className="row">
                      {botonPagar(f)}
                      <a className="btn btn-o btn-sm" href={solucionesApi.portalPdfUrl(token, f.id)} target="_blank" rel="noopener noreferrer">
                        PDF
                      </a>
                      <a className="btn btn-o btn-sm" href={solucionesApi.portalXmlUrl(token, f.id)}>
                        XML
                      </a>
                    </div>
                  </li>
                ))}
              </ul>
              <div className="tw pp-tabla">
                <table className="tbl" style={{ minWidth: 640 }}>
                  <thead>
                    <tr>
                      <th>e-NCF</th>
                      <th>Fecha</th>
                      <th className="num">Total (RD$)</th>
                      <th className="num">Pendiente (RD$)</th>
                      <th>Estado de pago</th>
                      <th style={{ textAlign: "right" }}>Descargas</th>
                    </tr>
                  </thead>
                  <tbody>
                    {datos.facturas.map((f) => (
                      <tr key={f.id}>
                        <td className="mono">{f.encf}</td>
                        <td>{fechaLegible(f.fecha_emision)}</td>
                        <td className="num">{formatoMonto(f.total)}</td>
                        <td className="num">{Number(f.pendiente) > 0 ? formatoMonto(f.pendiente) : "—"}</td>
                        <td>
                          <span className={ESTADO_PAGO[f.estado_pago].badge}>{ESTADO_PAGO[f.estado_pago].label}</span>
                          {f.vence && Number(f.pendiente) > 0 && <span className="hint"> vence {fechaLegible(f.vence)}</span>}
                        </td>
                        <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                          {botonPagar(f)}
                          <a className="btn btn-g btn-sm" href={solucionesApi.portalPdfUrl(token, f.id)} target="_blank" rel="noopener noreferrer">
                            PDF
                          </a>
                          <a className="btn btn-g btn-sm" href={solucionesApi.portalXmlUrl(token, f.id)}>
                            XML
                          </a>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </section>

        {datos.metodos_pago.length > 0 && (
          <section className="card">
            <div className="card-h">
              <h2>Cómo pagar</h2>
            </div>
            <div className="card-b pp-pagos">
              {datos.metodos_pago.map((m) => (
                <div key={m} className="pp-pago" data-metodo={m}>
                  <b>{ETIQUETA_METODO[m]}</b>
                  {m === "transferencia" &&
                    (t ? (
                      <>
                        <span>
                          {t.banco} {t.tipo_cuenta ? `· ${t.tipo_cuenta}` : ""}
                        </span>
                        <span className="mono">{t.numero_cuenta}</span>
                        <span>
                          {t.titular}
                          {t.rnc_titular ? ` · RNC ${formatoRnc(t.rnc_titular)}` : ""}
                        </span>
                        {t.instrucciones && <span className="hint">{t.instrucciones}</span>}
                        <span className="hint">Indique el e-NCF de la factura en la referencia de la transferencia.</span>
                      </>
                    ) : (
                      <span className="hint">Solicite los datos bancarios a {datos.empresa}.</span>
                    ))}
                  {m === "tarjeta" &&
                    (enLinea ? (
                      <>
                        <span>Pague en línea con «Pagar con tarjeta» en la factura.</span>
                        <span className="hint">
                          El pago lo procesa {enLinea.nombre} en su página segura: {datos.empresa} no recibe los datos de su tarjeta.
                        </span>
                        {enLinea.pruebas && (
                          <span className="bd b-proc nodot" style={{ alignSelf: "flex-start" }}>
                            Ambiente de pruebas: no se cobra dinero real
                          </span>
                        )}
                      </>
                    ) : (
                      <>
                        <span className="bd b-borr nodot" style={{ alignSelf: "flex-start" }}>
                          Próximamente
                        </span>
                        <span className="hint">El pago con tarjeta en línea aún no está disponible.</span>
                      </>
                    ))}
                  {(m === "pago_movil" || m === "deposito") && <span className="hint">Coordine el pago con {datos.empresa}.</span>}
                </div>
              ))}
            </div>
          </section>
        )}

        <p className="pp-pie">
          Enlace privado de solo lectura · válido hasta el {fechaLegible(datos.expira_en)} · {MARCA}
        </p>
      </div>

      {pagar && enLinea && (
        <Modal
          titulo="Pagar con tarjeta"
          onCerrar={() => !enviando && setPagar(null)}
          pie={
            <>
              <button type="button" className="btn btn-o" onClick={() => setPagar(null)} disabled={enviando}>
                Cancelar
              </button>
              <button type="button" className="btn btn-p" onClick={() => void continuarAlPago()} disabled={enviando}>
                {enviando ? "Abriendo la página de pago…" : "Continuar al pago seguro"}
              </button>
            </>
          }
        >
          <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            <span>
              Factura <b className="mono">{pagar.encf}</b> · saldo pendiente <b>RD$ {formatoMonto(pagar.pendiente)}</b>
            </span>
            {enLinea.permitir_parcial ? (
              <div className="fld">
                <label className="lbl" htmlFor="pp-monto">
                  Monto a pagar (RD$)
                </label>
                <input id="pp-monto" className="inp mono" inputMode="decimal" value={monto} onChange={(e) => setMonto(e.target.value.replace(/[^0-9.]/g, ""))} />
                <span className="hint">Puede pagar una parte del saldo.</span>
              </div>
            ) : (
              <span>
                Monto a pagar: <b data-monto-pago>RD$ {formatoMonto(pagar.pendiente)}</b>
              </span>
            )}
            <span className="hint">
              Lo llevaremos a la página de pago de {enLinea.nombre}. Allí escribe los datos de su tarjeta; {datos.empresa} no los recibe ni los guarda.
            </span>
            {enLinea.pruebas && <span className="bd b-proc nodot">Ambiente de pruebas: no se cobra dinero real</span>}
            {errorPago && (
              <div className="al al-d" role="alert">
                <span>{errorPago}</span>
              </div>
            )}
          </div>
        </Modal>
      )}
    </div>
  );
}
