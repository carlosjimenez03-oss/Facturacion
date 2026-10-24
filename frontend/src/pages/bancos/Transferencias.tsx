// Pestaña Transferencias de Bancos.dc.html: "Nueva transferencia entre cuentas" (con confirmación; entre
// monedas distintas pide lo recibido en destino) y la lista de transferencias registradas, que se pueden anular.
import { useCallback, useEffect, useState } from "react";
import { IconAlerta } from "../../layout/icons";
import { bancosApi } from "../../lib/bancosApi";
import type { Transferencia } from "../../lib/bancosApi";
import { AvisoError, Cargando, Modal, Vacio } from "../ingresos/IngresosShell";
import { fechaLegible, hoyIso, mensajeError } from "../ingresos/ingresosUtil";
import type { PestanaBancosProps } from "./Bancos";
import { etiquetaCuenta, montoMoneda, simboloMoneda } from "./bancosUtil";

interface FormTransferencia {
  origen: string;
  destino: string;
  monto: string;
  monto_destino: string;
  tasa_cambio: string;
  fecha: string;
  concepto: string;
  referencia: string;
}

export function TransferenciasTab({ cuenta, cuentas, recargarCuentas, mostrarAviso }: PestanaBancosProps) {
  const activas = cuentas.filter((c) => c.activa);
  const [form, setForm] = useState<FormTransferencia>(() => ({
    origen: cuenta.activa ? cuenta.id : (activas[0]?.id ?? ""),
    destino: activas.find((c) => c.id !== (cuenta.activa ? cuenta.id : activas[0]?.id))?.id ?? "",
    monto: "",
    monto_destino: "",
    tasa_cambio: "",
    fecha: hoyIso(),
    concepto: "",
    referencia: "",
  }));
  const [lista, setLista] = useState<Transferencia[]>([]);
  const [cargando, setCargando] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [errorForm, setErrorForm] = useState<string | null>(null);
  const [confirmar, setConfirmar] = useState(false);
  const [guardando, setGuardando] = useState(false);
  const [anular, setAnular] = useState<Transferencia | null>(null);
  const [anulando, setAnulando] = useState(false);
  const [soloCuenta, setSoloCuenta] = useState(false);

  const cargar = useCallback(() => {
    setCargando(true);
    bancosApi
      .listarTransferencias(soloCuenta ? { cuenta_id: cuenta.id } : undefined)
      .then((t) => {
        setLista(t);
        setError(null);
      })
      .catch((err) => {
        setLista([]);
        setError(mensajeError(err));
      })
      .finally(() => setCargando(false));
  }, [soloCuenta, cuenta.id]);

  useEffect(cargar, [cargar]);

  const origen = cuentas.find((c) => c.id === form.origen);
  const destino = cuentas.find((c) => c.id === form.destino);
  const distintaMoneda = !!origen && !!destino && origen.moneda !== destino.moneda;
  const ambasUsd = origen?.moneda === "USD" && destino?.moneda === "USD";
  const monto = Number(form.monto) || 0;

  function set<K extends keyof FormTransferencia>(campo: K, valor: FormTransferencia[K]) {
    setForm((prev) => ({ ...prev, [campo]: valor }));
    setErrorForm(null);
  }

  function validar(): string | null {
    if (!origen || !destino) return "Elija la cuenta de origen y la de destino.";
    if (origen.id === destino.id) return "La cuenta de origen y la de destino deben ser distintas.";
    if (!(monto > 0)) return "El monto debe ser mayor que cero.";
    if (!form.fecha) return "Indique la fecha de la transferencia.";
    if (distintaMoneda && !(Number(form.monto_destino) > 0)) return `Indique cuánto se recibe en ${destino.nombre} (${simboloMoneda(destino.moneda)}).`;
    if (ambasUsd && !(Number(form.tasa_cambio) > 0)) return "Indique la tasa de cambio (RD$ por US$) para el asiento en pesos.";
    // Un sobregiro se permite (cuentas con línea de crédito); se avisa en la confirmación.
    return null;
  }

  function pedirConfirmacion() {
    const e = validar();
    if (e) return setErrorForm(e);
    setConfirmar(true);
  }

  async function registrar() {
    if (!origen || !destino) return;
    setGuardando(true);
    try {
      const t = await bancosApi.registrarTransferencia({
        cuenta_origen_id: origen.id,
        cuenta_destino_id: destino.id,
        fecha: form.fecha,
        monto: monto.toFixed(2),
        monto_destino: distintaMoneda ? Number(form.monto_destino).toFixed(2) : null,
        tasa_cambio: ambasUsd ? Number(form.tasa_cambio).toFixed(4) : null,
        concepto: form.concepto.trim() || null,
        referencia: form.referencia.trim() || null,
      });
      setConfirmar(false);
      setForm((prev) => ({ ...prev, monto: "", monto_destino: "", tasa_cambio: "", concepto: "", referencia: "" }));
      mostrarAviso(`Transferencia de ${montoMoneda(t.monto, t.moneda_origen)} registrada`, { detalle: `${t.cuenta_origen_nombre} → ${t.cuenta_destino_nombre}` });
      cargar();
      recargarCuentas();
    } catch (err) {
      setConfirmar(false);
      setErrorForm(mensajeError(err));
    } finally {
      setGuardando(false);
    }
  }

  async function confirmarAnular() {
    if (!anular) return;
    setAnulando(true);
    try {
      await bancosApi.anularTransferencia(anular.id);
      mostrarAviso("Transferencia anulada", { detalle: `${anular.cuenta_origen_nombre} → ${anular.cuenta_destino_nombre} · ${montoMoneda(anular.monto, anular.moneda_origen)}` });
      cargar();
      recargarCuentas();
    } catch (err) {
      mostrarAviso("No se pudo anular la transferencia", { detalle: mensajeError(err), error: true });
    } finally {
      setAnular(null);
      setAnulando(false);
    }
  }

  const sobregiro = !!origen && monto > Number(origen.saldo_actual) + 0.004;

  return (
    <>
      <section className="card no-imprimir">
        <div className="card-h">
          <h2>Nueva transferencia entre cuentas</h2>
          {origen && <span className="hint">Disponible en origen: {montoMoneda(origen.saldo_actual, origen.moneda)}</span>}
        </div>
        {activas.length < 2 ? (
          <div className="card-b">
            <div className="al al-i">
              <IconAlerta />
              <span>Necesita al menos dos cuentas activas para transferir entre ellas (por ejemplo, el banco y la caja chica).</span>
            </div>
          </div>
        ) : (
          <div className="card-b form-grid">
            <div className="fld">
              <label className="lbl" htmlFor="to">Origen</label>
              <select id="to" className="inp" value={form.origen} onChange={(e) => set("origen", e.target.value)}>
                {activas.map((c) => (
                  <option key={c.id} value={c.id}>
                    {etiquetaCuenta(c)}
                    {c.moneda === "USD" ? " (US$)" : ""}
                  </option>
                ))}
              </select>
            </div>
            <div className="fld">
              <label className="lbl" htmlFor="td">Destino</label>
              <select id="td" className="inp" value={form.destino} onChange={(e) => set("destino", e.target.value)}>
                <option value="">Elija la cuenta…</option>
                {activas
                  .filter((c) => c.id !== form.origen)
                  .map((c) => (
                    <option key={c.id} value={c.id}>
                      {etiquetaCuenta(c)}
                      {c.moneda === "USD" ? " (US$)" : ""}
                    </option>
                  ))}
              </select>
            </div>
            <div className="fld">
              <label className="lbl" htmlFor="tm">
                Monto ({simboloMoneda(origen?.moneda)}) <span className="req">*</span>
              </label>
              <input id="tm" type="number" min="0" step="0.01" className="inp" style={{ textAlign: "right" }} value={form.monto} onChange={(e) => set("monto", e.target.value)} placeholder="0.00" />
            </div>
            {distintaMoneda && (
              <div className="fld">
                <label className="lbl" htmlFor="tmd">
                  Recibido en destino ({simboloMoneda(destino?.moneda)}) <span className="req">*</span>
                </label>
                <input id="tmd" type="number" min="0" step="0.01" className="inp" style={{ textAlign: "right" }} value={form.monto_destino} onChange={(e) => set("monto_destino", e.target.value)} placeholder="0.00" />
                {monto > 0 && Number(form.monto_destino) > 0 && (
                  <span className="hint">
                    Tasa: {(origen?.moneda === "USD" ? Number(form.monto_destino) / monto : monto / Number(form.monto_destino)).toFixed(4)} RD$ por US$
                  </span>
                )}
              </div>
            )}
            {ambasUsd && (
              <div className="fld">
                <label className="lbl" htmlFor="ttc">Tasa (RD$ por US$)</label>
                <input id="ttc" type="number" min="0" step="0.0001" className="inp" style={{ textAlign: "right" }} value={form.tasa_cambio} onChange={(e) => set("tasa_cambio", e.target.value)} />
              </div>
            )}
            <div className="fld">
              <label className="lbl" htmlFor="tf">Fecha</label>
              <input id="tf" type="date" className="inp" value={form.fecha} max={hoyIso()} onChange={(e) => set("fecha", e.target.value)} />
            </div>
            <div className="fld">
              <label className="lbl" htmlFor="tc">Concepto</label>
              <input id="tc" className="inp" value={form.concepto} onChange={(e) => set("concepto", e.target.value)} placeholder="Fondos para pago de nómina" />
            </div>
            <div className="fld">
              <label className="lbl" htmlFor="tr">Referencia</label>
              <input id="tr" className="inp mono" value={form.referencia} onChange={(e) => set("referencia", e.target.value)} placeholder="TRF-88213" />
            </div>
            {errorForm && (
              <div className="al al-d fld-full" role="alert">
                <IconAlerta />
                <span>
                  <b>{errorForm}</b>
                </span>
              </div>
            )}
            <div className="row fld-full" style={{ justifyContent: "flex-end" }}>
              <button type="button" className="btn btn-p" onClick={pedirConfirmacion} disabled={!monto}>
                Registrar transferencia
              </button>
            </div>
          </div>
        )}
      </section>

      <section className="card">
        <div className="card-h">
          <div>
            <h2>Transferencias registradas</h2>
            {!cargando && !error && <span className="hint">{lista.length === 1 ? "1 transferencia" : `${lista.length} transferencias`}</span>}
          </div>
          <label className="row hint" style={{ gap: 6, cursor: "pointer" }}>
            <input type="checkbox" checked={soloCuenta} onChange={(e) => setSoloCuenta(e.target.checked)} style={{ accentColor: "#0F766E" }} />
            Solo de {cuenta.nombre}
          </label>
        </div>
        {error && (
          <div className="card-b" style={{ paddingTop: 0 }}>
            <AvisoError titulo="No se pudieron cargar las transferencias." mensaje={error} onReintentar={cargar} />
          </div>
        )}
        {cargando && lista.length === 0 && !error && <Cargando />}
        {!cargando && !error && lista.length === 0 && <Vacio titulo="Aún no hay transferencias" texto="Los traspasos entre sus cuentas (banco a caja chica, entre bancos) se registran aquí con sus dos movimientos." />}
        {lista.length > 0 && (
          <div className="tw">
            <table className="tbl" style={{ minWidth: 860 }}>
              <thead>
                <tr>
                  <th>Fecha</th>
                  <th>Origen</th>
                  <th>Destino</th>
                  <th>Concepto · Referencia</th>
                  <th className="num">Monto</th>
                  <th>Estado</th>
                  <th style={{ textAlign: "right" }}>Acciones</th>
                </tr>
              </thead>
              <tbody>
                {lista.map((t) => (
                  <tr key={t.id}>
                    <td className="mut nowrap">{fechaLegible(t.fecha)}</td>
                    <td>{t.cuenta_origen_nombre}</td>
                    <td>{t.cuenta_destino_nombre}</td>
                    <td>
                      {t.concepto || "—"}
                      {t.referencia && <div className="hint mono">{t.referencia}</div>}
                    </td>
                    <td className="num" style={{ textDecoration: t.anulada ? "line-through" : undefined }}>
                      {montoMoneda(t.monto, t.moneda_origen)}
                      {t.moneda_destino !== t.moneda_origen && <div className="hint">→ {montoMoneda(t.monto_destino, t.moneda_destino)}</div>}
                    </td>
                    <td>{t.anulada ? <span className="bd b-rech">Anulada</span> : <span className="bd b-acep">Registrada</span>}</td>
                    <td>
                      <div className="fila-acc">
                        {!t.anulada && (
                          <button type="button" className="btn btn-g btn-sm" style={{ color: "#B91C1C" }} onClick={() => setAnular(t)}>
                            Anular
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {confirmar && origen && destino && (
        <Modal
          titulo="¿Registrar esta transferencia?"
          onCerrar={() => setConfirmar(false)}
          pie={
            <>
              <button type="button" className="btn btn-o" onClick={() => setConfirmar(false)}>
                Cancelar
              </button>
              <button type="button" className="btn btn-p" onClick={registrar} disabled={guardando}>
                {guardando ? "Registrando…" : "Sí, transferir"}
              </button>
            </>
          }
        >
          <span>
            Sale <b style={{ color: "#111827" }}>{montoMoneda(monto, origen.moneda)}</b> de <b style={{ color: "#111827" }}>{etiquetaCuenta(origen)}</b> y entra{" "}
            <b style={{ color: "#111827" }}>{montoMoneda(distintaMoneda ? form.monto_destino : monto, destino.moneda)}</b> a <b style={{ color: "#111827" }}>{etiquetaCuenta(destino)}</b> el{" "}
            {fechaLegible(form.fecha)}
            {form.concepto.trim() ? ` · ${form.concepto.trim()}` : ""}. Los dos movimientos se registran juntos.
          </span>
          {sobregiro && (
            <div className="al al-w">
              <IconAlerta />
              <span>El monto supera el saldo disponible en la cuenta de origen ({montoMoneda(origen.saldo_actual, origen.moneda)}).</span>
            </div>
          )}
        </Modal>
      )}

      {anular && (
        <Modal
          titulo="¿Anular esta transferencia?"
          onCerrar={() => setAnular(null)}
          pie={
            <>
              <button type="button" className="btn btn-o" onClick={() => setAnular(null)}>
                Cancelar
              </button>
              <button type="button" className="btn btn-d" onClick={confirmarAnular} disabled={anulando}>
                {anulando ? "Anulando…" : "Sí, anular"}
              </button>
            </>
          }
        >
          <span>
            Se anulan los dos movimientos de la transferencia del {fechaLegible(anular.fecha)} de {anular.cuenta_origen_nombre} a {anular.cuenta_destino_nombre} por{" "}
            <b style={{ color: "#111827" }}>{montoMoneda(anular.monto, anular.moneda_origen)}</b>.
          </span>
        </Modal>
      )}
    </>
  );
}
