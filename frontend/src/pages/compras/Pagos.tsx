import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { IconAlerta } from "../../layout/icons";
import { bancosApi } from "../../lib/bancosApi";
import type { CuentaBancaria } from "../../lib/bancosApi";
import { comprasApi } from "../../lib/comprasApi";
import type { CuentaPorPagar, MetodoPago, PagoProveedor } from "../../lib/comprasApi";
import { formatoMonto, formatoRnc } from "../../lib/marca";
import { AvisoError, Buscador, Cargando, Estadistica, Modal, SinResultados, Vacio } from "../ingresos/IngresosShell";
import { useAviso } from "../ingresos/useAviso";
import { centavos, descargarCsv, fechaIso, fechaLegible, hoyIso, mensajeError, mesActualLegible } from "../ingresos/ingresosUtil";
import { etiquetaCuenta } from "../bancos/bancosUtil";
import { nombrePeriodo } from "../impuestos/impuestosUtil";
import { ComprasEncabezado } from "./ComprasShell";
import {
  ESTADO_CXP,
  METODO_LABEL,
  METODOS_PAGO,
  OPCIONES_RETENCION_ISR,
  mensajeCompras,
  personaDeRnc,
  suma,
  tieneRetenciones,
} from "./comprasUtil";

interface FormPago {
  registro606_id: string;
  fecha: string;
  monto: string;
  montoManual: boolean;
  metodo: MetodoPago;
  cuenta_origen: string;
  /** Cuenta de Bancos de la que sale el dinero ("" = solo el texto libre de cuenta_origen). */
  cuenta_bancaria_id: string;
  referencia: string;
  tipo_retencion_isr: string;
  retencion_isr: string;
  retencion_itbis: string;
}

function formVacio(): FormPago {
  return {
    registro606_id: "",
    fecha: hoyIso(),
    monto: "",
    montoManual: false,
    metodo: "transferencia",
    cuenta_origen: "",
    cuenta_bancaria_id: "",
    referencia: "",
    tipo_retencion_isr: "",
    retencion_isr: "",
    retencion_itbis: "",
  };
}

/** "0.00" -> "" para que los campos se vean limpios. */
function limpio(v: string | number | null | undefined): string {
  const n = Number(v);
  return !v || !Number.isFinite(n) || n === 0 ? "" : String(n);
}

export function PagosProveedoresPage() {
  const [params, setParams] = useSearchParams();
  const [cuentas, setCuentas] = useState<CuentaPorPagar[]>([]);
  const [pagos, setPagos] = useState<PagoProveedor[]>([]);
  const [bancos, setBancos] = useState<CuentaBancaria[]>([]);
  const [cargando, setCargando] = useState(true);
  const [errorCuentas, setErrorCuentas] = useState<string | null>(null);
  const [errorPagos, setErrorPagos] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [abierto, setAbierto] = useState(false);
  const [form, setForm] = useState<FormPago>(formVacio);
  const [errorForm, setErrorForm] = useState<string | null>(null);
  const [avisoForm, setAvisoForm] = useState<string | null>(null);
  const [regla, setRegla] = useState<string | null>(null);
  const [sugiriendo, setSugiriendo] = useState(false);
  const [guardando, setGuardando] = useState(false);
  const [anular, setAnular] = useState<PagoProveedor | null>(null);
  const [errorAnular, setErrorAnular] = useState<string | null>(null);
  const [anulando, setAnulando] = useState(false);
  const [toast, mostrarToast] = useAviso();
  const formRef = useRef<HTMLElement>(null);

  const cargar = useCallback(async () => {
    setCargando(true);
    const [cxp, hist, ctas] = await Promise.allSettled([comprasApi.listarCuentasPorPagar({ estado: "todas" }), comprasApi.listarPagos(), bancosApi.listarCuentas()]);
    if (cxp.status === "fulfilled") {
      setCuentas(cxp.value);
      setErrorCuentas(null);
    } else {
      setCuentas([]);
      setErrorCuentas(mensajeError(cxp.reason));
    }
    if (hist.status === "fulfilled") {
      setPagos(hist.value);
      setErrorPagos(null);
    } else {
      setPagos([]);
      setErrorPagos(mensajeError(hist.reason));
    }
    // Sin Bancos (servicio caído o sin cuentas) queda solo el texto libre de la cuenta de origen.
    setBancos(ctas.status === "fulfilled" ? ctas.value : []);
    setCargando(false);
  }, []);

  useEffect(() => {
    void cargar();
  }, [cargar]);

  const porPagar = useMemo(() => cuentas.filter((c) => c.estado !== "pagada" && Number(c.pendiente) > 0), [cuentas]);
  const seleccionada = porPagar.find((c) => c.registro606_id === form.registro606_id);
  // Los pagos son en RD$: solo las cuentas activas en pesos (el backend rechaza las demás con 422).
  const cuentasBanco = bancos.filter((c) => c.activa && c.moneda === "DOP");
  const cuentaBanco = bancos.find((c) => c.id === form.cuenta_bancaria_id);
  const nombreBanco = (id: string | null) => (id ? bancos.find((c) => c.id === id) : undefined);
  /** Las retenciones se aplican en un solo pago por factura. */
  const conRetenciones = (id: string) => pagos.some((p) => p.registro606_id === id && !p.anulado && tieneRetenciones(p));
  const bloqueadas = seleccionada ? conRetenciones(seleccionada.registro606_id) : false;

  const retIsr = bloqueadas ? 0 : Number(form.retencion_isr) || 0;
  const retItbis = bloqueadas ? 0 : Number(form.retencion_itbis) || 0;
  const retForm = centavos(retIsr + retItbis);
  // Lo que queda por pagar antes de retenciones; si el pago no trae retenciones se mantienen las del 606.
  const disponible = seleccionada ? centavos(Number(seleccionada.pendiente) + Number(seleccionada.retenciones)) : 0;
  const retEfectivas = seleccionada ? (retForm > 0 ? retForm : Number(seleccionada.retenciones)) : 0;
  const netoMax = centavos(Math.max(disponible - retEfectivas, 0));
  const montoValor = form.montoManual ? Number(form.monto) || 0 : netoMax;

  function prellenar(c: CuentaPorPagar | undefined): FormPago {
    const base = { ...formVacio(), registro606_id: c?.registro606_id ?? "" };
    if (!c || conRetenciones(c.registro606_id)) return base;
    // E41: las retenciones que declara el propio e-CF; si no, las clasificadas en el 606.
    const deEcf = Number(c.retencion_isr_ecf) > 0 || Number(c.retencion_itbis_ecf) > 0;
    return {
      ...base,
      tipo_retencion_isr: c.tipo_retencion_isr ? String(c.tipo_retencion_isr) : "",
      retencion_isr: limpio(deEcf ? c.retencion_isr_ecf : c.retencion_isr),
      retencion_itbis: limpio(deEcf ? c.retencion_itbis_ecf : c.retencion_itbis),
    };
  }

  function abrirForm(c?: CuentaPorPagar) {
    setErrorForm(null);
    setAvisoForm(null);
    setRegla(null);
    setForm(prellenar(c));
    setAbierto(true);
    window.setTimeout(() => formRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 0);
  }

  // ?factura=<registro606_id>: Facturas de compra y la ficha de la factura enlazan aquí.
  const facturaParam = params.get("factura");
  useEffect(() => {
    if (cargando || !facturaParam) return;
    const c = cuentas.find((x) => x.registro606_id === facturaParam);
    if (c && c.estado !== "pagada" && Number(c.pendiente) > 0) {
      abrirForm(c);
    } else if (!errorCuentas) {
      abrirForm();
      setAvisoForm(c ? `La factura ${c.ncf} de ${c.proveedor_nombre} ya está pagada por completo.` : "Esa factura no tiene saldo por pagar.");
    }
    setParams({}, { replace: true });
  }, [cargando, facturaParam]);

  function set<K extends keyof FormPago>(campo: K, valor: FormPago[K]) {
    setForm((prev) => ({ ...prev, [campo]: valor }));
    setErrorForm(null);
  }

  async function sugerir() {
    if (!seleccionada) return setErrorForm("Elija primero la factura que va a pagar.");
    if (seleccionada.tipo_ecf === 47) return setRegla("Los pagos al exterior (E47) no tienen tipo de retención en el 606: no se sugieren retenciones.");
    if (seleccionada.tipo_bienes_servicios == null)
      return setErrorForm("Clasifique la factura (tipo de bien o servicio del 606) para sugerir sus retenciones.");
    setSugiriendo(true);
    setErrorForm(null);
    try {
      const s = await comprasApi.sugerirRetenciones({
        persona: personaDeRnc(seleccionada.proveedor_rnc),
        tipo_bienes_servicios: seleccionada.tipo_bienes_servicios,
        monto: seleccionada.monto_facturado,
        itbis: seleccionada.itbis_facturado,
        emisor_electronico: seleccionada.tipo_ecf !== 41,
        fecha_pago: form.fecha || undefined,
      });
      setForm((prev) => ({
        ...prev,
        tipo_retencion_isr: s.tipo_retencion_isr ? String(s.tipo_retencion_isr) : "",
        retencion_isr: limpio(s.retencion_isr),
        retencion_itbis: limpio(s.retencion_itbis),
        montoManual: false,
      }));
      setRegla(`${s.regla} ISR ${Number(s.isr_pct)}% · ITBIS ${Number(s.itbis_pct)}%.`);
    } catch (err) {
      setErrorForm(mensajeCompras(err));
    } finally {
      setSugiriendo(false);
    }
  }

  async function registrar() {
    if (!seleccionada) return setErrorForm("Elija la factura a la que se aplica el pago.");
    if (!form.fecha) return setErrorForm("Indique la fecha del pago.");
    if (fechaIso(form.fecha) < fechaIso(seleccionada.fecha_comprobante)) return setErrorForm("La fecha de pago no puede ser anterior a la fecha del comprobante.");
    if (retIsr < 0 || retItbis < 0) return setErrorForm("Las retenciones no pueden ser negativas.");
    if (retIsr > 0 && !form.tipo_retencion_isr) return setErrorForm("Indique el tipo de retención de ISR.");
    if (retItbis > Number(seleccionada.itbis_facturado) + 0.004)
      return setErrorForm(`El ITBIS retenido supera el ITBIS facturado (RD$ ${formatoMonto(seleccionada.itbis_facturado)}).`);
    if (!(montoValor > 0)) return setErrorForm("El monto pagado debe ser mayor que cero.");
    if (montoValor > netoMax + 0.004) return setErrorForm(`El pago más las retenciones supera lo pendiente: el máximo a pagar es RD$ ${formatoMonto(netoMax)}.`);
    setGuardando(true);
    try {
      const pago = await comprasApi.registrarPago({
        registro606_id: seleccionada.registro606_id,
        fecha: form.fecha,
        monto: montoValor.toFixed(2),
        metodo: form.metodo,
        cuenta_origen: cuentaBanco ? etiquetaCuenta(cuentaBanco) : form.cuenta_origen.trim() || null,
        cuenta_bancaria_id: form.cuenta_bancaria_id || null,
        referencia: form.referencia.trim() || null,
        tipo_retencion_isr: retIsr > 0 ? Number(form.tipo_retencion_isr) : null,
        retencion_isr: retIsr.toFixed(2),
        retencion_itbis: retItbis.toFixed(2),
      });
      setAbierto(false);
      setForm(formVacio());
      const ret = Number(pago.retencion_isr) + Number(pago.retencion_itbis);
      mostrarToast("Pago registrado", {
        detalle:
          `RD$ ${formatoMonto(pago.monto)} a ${pago.proveedor_nombre}` +
          (ret > 0 ? ` · retenciones de RD$ ${formatoMonto(ret)} para el IR-17/IT-1 de ${nombrePeriodo(pago.fecha.slice(0, 7).replace("-", ""))}` : ""),
      });
      void cargar();
    } catch (err) {
      setErrorForm(mensajeCompras(err));
    } finally {
      setGuardando(false);
    }
  }

  async function confirmarAnular() {
    if (!anular) return;
    setAnulando(true);
    setErrorAnular(null);
    try {
      await comprasApi.anularPago(anular.id);
      mostrarToast("Pago anulado", { detalle: `RD$ ${formatoMonto(anular.monto)} vuelve a quedar pendiente en ${anular.ncf ?? "la factura"}` });
      setAnular(null);
      void cargar();
    } catch (err) {
      setErrorAnular(mensajeCompras(err));
    } finally {
      setAnulando(false);
    }
  }

  const filas = useMemo(() => {
    const texto = q.trim().toLowerCase();
    return porPagar
      .filter((c) => !texto || [c.proveedor_nombre, c.proveedor_rnc, formatoRnc(c.proveedor_rnc), c.ncf, fechaLegible(c.vence)].join(" ").toLowerCase().includes(texto))
      .sort((a, b) => fechaIso(a.vence).localeCompare(fechaIso(b.vence)));
  }, [porPagar, q]);

  const historial = useMemo(
    () => [...pagos].sort((a, b) => fechaIso(b.fecha).localeCompare(fechaIso(a.fecha)) || b.creado_en.localeCompare(a.creado_en)),
    [pagos],
  );
  const mes = hoyIso().slice(0, 7);
  const pagosMes = pagos.filter((p) => !p.anulado && fechaIso(p.fecha).startsWith(mes));
  const retenidoMes = suma(pagosMes, (p) => Number(p.retencion_isr) + Number(p.retencion_itbis));
  const cuentasOrigen = useMemo(() => [...new Set(pagos.map((p) => p.cuenta_origen).filter((x): x is string => Boolean(x)))], [pagos]);

  function exportar() {
    descargarCsv(`Pagos_a_proveedores_${hoyIso()}.csv`, [
      ["Fecha", "Proveedor", "RNC/Cédula", "NCF", "Método", "Cuenta", "Referencia", "Monto (RD$)", "ISR retenido (RD$)", "ITBIS retenido (RD$)", "Estado"],
      ...historial.map((p) => [
        fechaLegible(p.fecha),
        p.proveedor_nombre,
        p.proveedor_rnc,
        p.ncf,
        METODO_LABEL[p.metodo] ?? p.metodo,
        p.cuenta_origen,
        p.referencia,
        Number(p.monto).toFixed(2),
        Number(p.retencion_isr).toFixed(2),
        Number(p.retencion_itbis).toFixed(2),
        p.anulado ? "Anulado" : "Aplicado",
      ]),
    ]);
  }

  return (
    <div className="content">
      <ComprasEncabezado
        titulo="Pagos a proveedores"
        sub="Facturas de compra por pagar con sus retenciones aplicadas"
        acciones={
          <>
            <button type="button" className="btn btn-o" onClick={exportar} disabled={historial.length === 0}>
              Exportar
            </button>
            <button type="button" className="btn btn-p" onClick={() => (abierto ? setAbierto(false) : abrirForm())}>
              {abierto ? "Cerrar formulario" : "+ Registrar pago"}
            </button>
          </>
        }
      />

      {abierto && (
        <section className="card" ref={formRef}>
          <div className="card-h">
            <h2>Registrar pago</h2>
            <span className="hint">Las retenciones se reportan en el mes del pago</span>
          </div>
          <div className="card-b form-grid">
            {avisoForm && (
              <div className="al al-w fld-full">
                <IconAlerta />
                <span>{avisoForm}</span>
              </div>
            )}
            {!cargando && porPagar.length === 0 && (
              <div className="al al-i fld-full">
                <IconAlerta />
                <span>
                  <b>No hay facturas de compra por pagar.</b> Los e-CF aprobados de la bandeja, los E41 y las facturas registradas aparecen aquí hasta que se pagan.
                </span>
              </div>
            )}
            <div className="fld">
              <label className="lbl" htmlFor="pg-fac">
                Factura <span className="req">*</span>
              </label>
              <select
                id="pg-fac"
                className="inp"
                value={form.registro606_id}
                onChange={(e) => {
                  const c = porPagar.find((x) => x.registro606_id === e.target.value);
                  setForm((prev) => ({ ...prellenar(c), fecha: prev.fecha, metodo: prev.metodo, cuenta_origen: prev.cuenta_origen, cuenta_bancaria_id: prev.cuenta_bancaria_id, referencia: prev.referencia }));
                  setRegla(null);
                  setErrorForm(null);
                }}
                disabled={porPagar.length === 0}
              >
                <option value="">Elija una factura…</option>
                {porPagar.map((c) => (
                  <option key={c.registro606_id} value={c.registro606_id}>
                    {c.ncf} · {c.proveedor_nombre}
                  </option>
                ))}
              </select>
            </div>
            <div className="fld">
              <label className="lbl" htmlFor="pg-fecha">Fecha de pago</label>
              <input id="pg-fecha" type="date" className="inp" value={form.fecha} max={hoyIso()} onChange={(e) => set("fecha", e.target.value)} />
            </div>
            <div className="fld">
              <label className="lbl" htmlFor="pg-met">Método</label>
              <select id="pg-met" className="inp" value={form.metodo} onChange={(e) => set("metodo", e.target.value as MetodoPago)}>
                {METODOS_PAGO.map((m) => (
                  <option key={m.value} value={m.value}>
                    {m.label}
                  </option>
                ))}
              </select>
            </div>
            {cuentasBanco.length > 0 && (
              <div className="fld">
                <label className="lbl" htmlFor="pg-banco">Cuenta bancaria</label>
                <select id="pg-banco" className="inp" value={form.cuenta_bancaria_id} onChange={(e) => set("cuenta_bancaria_id", e.target.value)}>
                  <option value="">Sin cuenta de Bancos</option>
                  {cuentasBanco.map((c) => (
                    <option key={c.id} value={c.id}>
                      {etiquetaCuenta(c)}
                    </option>
                  ))}
                </select>
                <span className="hint">{cuentaBanco ? "El pago sale de esta cuenta y se concilia con el extracto." : "Elija la cuenta para verlo en Bancos y conciliarlo."}</span>
              </div>
            )}
            {!cuentaBanco && (
              <div className="fld">
                <label className="lbl" htmlFor="pg-cta">Cuenta de origen</label>
                <input
                  id="pg-cta"
                  className="inp"
                  list="pg-cuentas"
                  value={form.cuenta_origen}
                  onChange={(e) => set("cuenta_origen", e.target.value)}
                  placeholder={form.metodo === "efectivo" ? "Caja chica" : "Banco Popular ****4471"}
                />
                <datalist id="pg-cuentas">
                  {cuentasOrigen.map((c) => (
                    <option key={c} value={c} />
                  ))}
                </datalist>
              </div>
            )}
            <div className="fld">
              <label className="lbl" htmlFor="pg-ref">Referencia</label>
              <input
                id="pg-ref"
                className="inp mono"
                value={form.referencia}
                onChange={(e) => set("referencia", e.target.value)}
                placeholder={form.metodo === "cheque" ? "N.º de cheque" : "N.º de transacción"}
              />
            </div>

            <div className="fld">
              <label className="lbl" htmlFor="pg-tisr">Tipo de retención ISR</label>
              <select id="pg-tisr" className="inp" value={form.tipo_retencion_isr} onChange={(e) => set("tipo_retencion_isr", e.target.value)} disabled={bloqueadas}>
                <option value="">Sin retención de ISR</option>
                {OPCIONES_RETENCION_ISR.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </div>
            <div className="fld">
              <label className="lbl" htmlFor="pg-isr">ISR retenido (RD$)</label>
              <input
                id="pg-isr"
                type="number"
                min="0"
                step="0.01"
                className="inp"
                style={{ textAlign: "right" }}
                value={bloqueadas ? "" : form.retencion_isr}
                onChange={(e) => set("retencion_isr", e.target.value)}
                placeholder="0.00"
                disabled={bloqueadas}
              />
            </div>
            <div className="fld">
              <label className="lbl" htmlFor="pg-itbis">ITBIS retenido (RD$)</label>
              <input
                id="pg-itbis"
                type="number"
                min="0"
                step="0.01"
                className="inp"
                style={{ textAlign: "right" }}
                value={bloqueadas ? "" : form.retencion_itbis}
                onChange={(e) => set("retencion_itbis", e.target.value)}
                placeholder="0.00"
                disabled={bloqueadas}
              />
            </div>
            <div className="fld-full cp-sugerir">
              <button type="button" className="btn btn-o btn-sm" onClick={sugerir} disabled={!seleccionada || bloqueadas || sugiriendo}>
                {sugiriendo ? "Calculando…" : "Sugerir retenciones"}
              </button>
              <span className="hint">
                {bloqueadas
                  ? "Esta factura ya tiene retenciones en otro pago (se aplican una sola vez). Anule ese pago si necesita cambiarlas."
                  : regla
                    ? `Regla aplicada: ${regla}`
                    : "Calcula el ISR y el ITBIS a retener según el proveedor, el tipo de gasto del 606 y la tasa vigente en el mes del pago."}
              </span>
            </div>

            <div className="fld-full cp-pago">
              <div className="fld">
                <label className="lbl" htmlFor="pg-monto">
                  Monto pagado (RD$) <span className="req">*</span>
                </label>
                <input
                  id="pg-monto"
                  type="number"
                  min="0"
                  step="0.01"
                  className="inp"
                  style={{ textAlign: "right", fontWeight: 600 }}
                  value={form.montoManual ? form.monto : netoMax ? netoMax.toFixed(2) : ""}
                  onChange={(e) => setForm((prev) => ({ ...prev, monto: e.target.value, montoManual: true }))}
                  placeholder="0.00"
                  disabled={!seleccionada}
                />
              </div>
              {seleccionada && (
                <div className="cp-neto" aria-live="polite">
                  <span>
                    Total <b>RD$ {formatoMonto(seleccionada.total)}</b>
                  </span>
                  {Number(seleccionada.notas_credito) > 0 && (
                    <span>
                      Notas de crédito <b>-{formatoMonto(seleccionada.notas_credito)}</b>
                    </span>
                  )}
                  {Number(seleccionada.pagado) > 0 && (
                    <span>
                      Pagado <b>-{formatoMonto(seleccionada.pagado)}</b>
                    </span>
                  )}
                  <span>
                    Retenciones <b>-{formatoMonto(retEfectivas)}</b>
                  </span>
                  <span className="cp-neto-t">
                    Neto a pagar <b>RD$ {formatoMonto(montoValor)}</b>
                    {montoValor < netoMax - 0.004 && <span className="hint"> · queda RD$ {formatoMonto(netoMax - montoValor)}</span>}
                  </span>
                </div>
              )}
              {form.montoManual && (
                <button type="button" className="btn btn-g btn-sm" onClick={() => set("montoManual", false)}>
                  Pagar el neto completo
                </button>
              )}
            </div>
            {seleccionada && (
              <div className="fld-full hint">
                {seleccionada.proveedor_nombre} · {formatoRnc(seleccionada.proveedor_rnc)} · {seleccionada.ncf} ·{" "}
                {seleccionada.estado === "vencida" ? `vencida desde el ${fechaLegible(seleccionada.vence)}` : `vence el ${fechaLegible(seleccionada.vence)}`}
              </div>
            )}
            {errorForm && (
              <div className="al al-d fld-full" role="alert">
                <IconAlerta />
                <span>
                  <b>{errorForm}</b>
                </span>
              </div>
            )}
            <div className="row fld-full" style={{ justifyContent: "flex-end" }}>
              <button type="button" className="btn btn-o" onClick={() => setAbierto(false)}>
                Cancelar
              </button>
              <button type="button" className="btn btn-p" onClick={registrar} disabled={guardando || porPagar.length === 0}>
                {guardando ? "Registrando…" : "Registrar pago"}
              </button>
            </div>
          </div>
        </section>
      )}

      {errorCuentas && <AvisoError titulo="No se pudieron cargar las facturas por pagar." mensaje={errorCuentas} onReintentar={() => void cargar()} />}

      <div className="grid-stats">
        <Estadistica
          k="Por pagar"
          v={cargando ? "—" : `RD$ ${formatoMonto(suma(porPagar, (c) => c.pendiente))}`}
          s={`${porPagar.length} ${porPagar.length === 1 ? "factura pendiente" : "facturas pendientes"}`}
        />
        <Estadistica k="Retenido este mes" v={cargando ? "—" : `RD$ ${formatoMonto(retenidoMes)}`} s="ISR + ITBIS para el IR-17 y el IT-1" />
        <Estadistica
          k="Pagado este mes"
          v={cargando ? "—" : `RD$ ${formatoMonto(suma(pagosMes, (p) => p.monto))}`}
          s={`${mesActualLegible()} · ${pagosMes.length} ${pagosMes.length === 1 ? "pago" : "pagos"}`}
        />
      </div>

      <section className="card">
        <div className="card-h">
          <h2>Facturas por pagar</h2>
          <Link className="link" to="/compras/facturas">
            Ver todas las facturas de compra
          </Link>
        </div>
        <div className="filters">
          <Buscador valor={q} onCambiar={setQ} placeholder="Buscar en Pagos a proveedores" />
        </div>
        {cargando && <Cargando />}
        {!cargando && !errorCuentas && porPagar.length === 0 && (
          <Vacio titulo="No hay facturas por pagar" texto="Las facturas de compra pendientes aparecerán aquí con sus retenciones y el neto a pagar." />
        )}
        {!cargando && porPagar.length > 0 && filas.length === 0 && <SinResultados q={q} onLimpiar={() => setQ("")} />}
        {!cargando && filas.length > 0 && (
          <div className="tw">
            <table className="tbl" style={{ minWidth: 960 }}>
              <thead>
                <tr>
                  <th>Proveedor</th>
                  <th>Factura</th>
                  <th>Vence</th>
                  <th className="num">Monto (RD$)</th>
                  <th className="num">Retenciones</th>
                  <th className="num">Neto a pagar</th>
                  <th>Estado</th>
                  <th style={{ textAlign: "right" }}>Acciones</th>
                </tr>
              </thead>
              <tbody>
                {filas.map((c) => {
                  const est = ESTADO_CXP[c.estado] ?? { label: c.estado, badge: "bd b-borr" };
                  return (
                    <tr key={c.registro606_id}>
                      <td>
                        <div className="strong">{c.proveedor_nombre}</div>
                        <div className="hint mono">{formatoRnc(c.proveedor_rnc)}</div>
                      </td>
                      <td className="mono nowrap">
                        <Link className="link mono" to={`/compras/facturas/${c.registro606_id}`}>
                          {c.ncf}
                        </Link>
                      </td>
                      <td className="nowrap">
                        {fechaLegible(c.vence)}
                        {c.estado === "vencida" && c.dias_vencida > 0 && (
                          <div className="err-t" style={{ fontSize: 12 }}>
                            Hace {c.dias_vencida} {c.dias_vencida === 1 ? "día" : "días"}
                          </div>
                        )}
                      </td>
                      <td className="num">{formatoMonto(c.total)}</td>
                      <td className="num">{formatoMonto(c.retenciones)}</td>
                      <td className="num" style={{ fontWeight: 600, color: "#111827" }}>
                        {formatoMonto(c.pendiente)}
                      </td>
                      <td>
                        <span className={est.badge}>{est.label}</span>
                      </td>
                      <td>
                        <div className="fila-acc cp-acc">
                          <button type="button" className="btn btn-g btn-sm" onClick={() => abrirForm(c)}>
                            Pagar
                          </button>
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

      <section className="card">
        <div className="card-h">
          <h2>Historial de pagos</h2>
          {!cargando && !errorPagos && <span className="hint">{pagos.length === 1 ? "1 pago registrado" : `${pagos.length} pagos registrados`}</span>}
        </div>
        {cargando && <Cargando />}
        {!cargando && errorPagos && (
          <div className="card-b">
            <AvisoError titulo="No se pudo cargar el historial de pagos." mensaje={errorPagos} onReintentar={() => void cargar()} />
          </div>
        )}
        {!cargando && !errorPagos && pagos.length === 0 && <Vacio titulo="Aún no hay pagos registrados" texto="Cada pago a un proveedor quedará anotado aquí con sus retenciones." />}
        {!cargando && historial.length > 0 && (
          <div className="tw">
            <table className="tbl" style={{ minWidth: 1000 }}>
              <thead>
                <tr>
                  <th>Fecha</th>
                  <th>Proveedor</th>
                  <th>Factura</th>
                  <th>Método · Cuenta</th>
                  <th className="num">Monto (RD$)</th>
                  <th className="num">ISR retenido</th>
                  <th className="num">ITBIS retenido</th>
                  <th>Estado</th>
                  <th style={{ textAlign: "right" }}>Acciones</th>
                </tr>
              </thead>
              <tbody>
                {historial.map((p) => (
                  <tr key={p.id}>
                    <td className="nowrap">{fechaLegible(p.fecha)}</td>
                    <td>
                      <div className="strong">{p.proveedor_nombre}</div>
                      {p.proveedor_rnc && <div className="hint mono">{formatoRnc(p.proveedor_rnc)}</div>}
                    </td>
                    <td className="mono nowrap">
                      <Link className="link mono" to={`/compras/facturas/${p.registro606_id}`}>
                        {p.ncf || "—"}
                      </Link>
                    </td>
                    <td>
                      {METODO_LABEL[p.metodo] ?? p.metodo}
                      {(p.cuenta_bancaria_id || p.cuenta_origen || p.referencia) && (
                        <div className="hint">{[nombreBanco(p.cuenta_bancaria_id)?.nombre ?? p.cuenta_origen, p.referencia].filter(Boolean).join(" · ")}</div>
                      )}
                    </td>
                    <td className="num" style={{ textDecoration: p.anulado ? "line-through" : undefined }}>
                      {formatoMonto(p.monto)}
                    </td>
                    <td className="num">
                      {formatoMonto(p.retencion_isr)}
                      {p.tipo_retencion_isr_label && Number(p.retencion_isr) > 0 && <div className="hint cp-sub">{p.tipo_retencion_isr_label}</div>}
                    </td>
                    <td className="num">{formatoMonto(p.retencion_itbis)}</td>
                    <td>{p.anulado ? <span className="bd b-rech">Anulado</span> : <span className="bd b-acep">Aplicado</span>}</td>
                    <td>
                      <div className="fila-acc cp-acc">
                        {!p.anulado && (
                          <button
                            type="button"
                            className="btn btn-g btn-sm"
                            style={{ color: "#B91C1C" }}
                            onClick={() => {
                              setErrorAnular(null);
                              setAnular(p);
                            }}
                          >
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

      {anular && (
        <Modal
          titulo="¿Anular este pago?"
          onCerrar={() => setAnular(null)}
          pie={
            <>
              <button type="button" className="btn btn-o" onClick={() => setAnular(null)}>
                {errorAnular ? "Cerrar" : "Cancelar"}
              </button>
              {!errorAnular && (
                <button type="button" className="btn btn-d" onClick={confirmarAnular} disabled={anulando}>
                  {anulando ? "Anulando…" : "Sí, anular"}
                </button>
              )}
            </>
          }
        >
          <span>
            El pago de <b style={{ color: "#111827" }}>RD$ {formatoMonto(anular.monto)}</b> del {fechaLegible(anular.fecha)} a {anular.proveedor_nombre} (
            <span className="mono">{anular.ncf}</span>) quedará anulado y ese monto volverá a estar pendiente.
            {tieneRetenciones(anular) && " Sus retenciones se quitan del 606 y, con eso, del IT-1 y el IR-17 del mes."}
          </span>
          {errorAnular && (
            <div className="al al-d" role="alert">
              <IconAlerta />
              <span>
                <b>No se pudo anular el pago.</b> {errorAnular}
              </span>
            </div>
          )}
        </Modal>
      )}

      {toast}
    </div>
  );
}
