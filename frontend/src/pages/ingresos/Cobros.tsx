import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { IconAlerta } from "../../layout/icons";
import { bancosApi } from "../../lib/bancosApi";
import type { CuentaBancaria } from "../../lib/bancosApi";
import { erpApi } from "../../lib/erpApi";
import type { Cobro, CuentaPorCobrar, EstadoCuentaCobrar, MetodoCobro } from "../../lib/erpApi";
import { formatoMonto, formatoRnc } from "../../lib/marca";
import { ESTADO_PAGO_EN_LINEA, pagosApi } from "../../lib/pagosApi";
import type { PagoEnLinea } from "../../lib/pagosApi";
import { empresaActual } from "../../lib/configuracionApi";
import { AvisoError, Buscador, Cargando, Estadistica, IngresosEncabezado, Modal, SinResultados, Vacio } from "./IngresosShell";
import { useAviso } from "./useAviso";
import { etiquetaCuenta } from "../bancos/bancosUtil";
import { descargarCsv, enlaceWhatsApp, fechaIso, fechaLegible, hoyIso, mensajeError, mesActualLegible } from "./ingresosUtil";

type Pestana = "pendientes" | "vencidas" | "cobradas" | "todas";

const PESTANAS: { id: Pestana; label: string; test: (c: CuentaPorCobrar) => boolean }[] = [
  { id: "pendientes", label: "Pendientes", test: (c) => c.estado !== "cobrada" },
  { id: "vencidas", label: "Vencidas", test: (c) => c.estado === "vencida" },
  { id: "cobradas", label: "Cobradas", test: (c) => c.estado === "cobrada" },
  { id: "todas", label: "Todas", test: () => true },
];

const ESTADO_CXC: Record<EstadoCuentaCobrar, { label: string; badge: string }> = {
  pendiente: { label: "Pendiente", badge: "bd b-proc" },
  vencida: { label: "Vencida", badge: "bd b-rech" },
  cobrada: { label: "Cobrada", badge: "bd b-acep" },
};

const METODOS: { value: MetodoCobro; label: string }[] = [
  { value: "transferencia", label: "Transferencia" },
  { value: "efectivo", label: "Efectivo" },
  { value: "tarjeta", label: "Tarjeta" },
  { value: "cheque", label: "Cheque" },
];

const METODO_LABEL = Object.fromEntries(METODOS.map((m) => [m.value, m.label])) as Record<MetodoCobro, string>;

interface FormCobro {
  factura_id: string;
  monto: string;
  metodo: MetodoCobro;
  cuenta_destino: string;
  /** Cuenta de Bancos donde entra el dinero ("" = solo el texto libre de cuenta_destino). */
  cuenta_bancaria_id: string;
  referencia: string;
  fecha: string;
}

function formVacio(): FormCobro {
  return { factura_id: "", monto: "", metodo: "transferencia", cuenta_destino: "", cuenta_bancaria_id: "", referencia: "", fecha: hoyIso() };
}

function textoRecordatorio(c: CuentaPorCobrar): string {
  const vencida = c.estado === "vencida";
  return (
    `Saludos, ${c.cliente_nombre}.\n\n` +
    `Le recordamos amablemente que la factura ${c.encf ?? ""} tiene un saldo pendiente de RD$ ${formatoMonto(c.pendiente)}, ` +
    `${vencida ? "vencido desde" : "con vencimiento el"} ${fechaLegible(c.vence)}.\n\n` +
    `Si ya realizó el pago, por favor ignore este mensaje y disculpe la molestia. ¡Gracias por su preferencia!\n${empresaActual().nombre}`
  );
}

export function CobrosPage() {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const [cuentas, setCuentas] = useState<CuentaPorCobrar[]>([]);
  const [cobros, setCobros] = useState<Cobro[]>([]);
  const [bancos, setBancos] = useState<CuentaBancaria[]>([]);
  const [enLinea, setEnLinea] = useState<PagoEnLinea[]>([]);
  const [cargando, setCargando] = useState(true);
  const [errorCuentas, setErrorCuentas] = useState<string | null>(null);
  const [errorCobros, setErrorCobros] = useState<string | null>(null);
  const [pestana, setPestana] = useState<Pestana>("pendientes");
  const [q, setQ] = useState("");
  const [abierto, setAbierto] = useState(false);
  const [form, setForm] = useState<FormCobro>(formVacio);
  const [errorForm, setErrorForm] = useState<string | null>(null);
  const [aviso, setAvisoForm] = useState<string | null>(null);
  const [guardando, setGuardando] = useState(false);
  const [anular, setAnular] = useState<Cobro | null>(null);
  const [anulando, setAnulando] = useState(false);
  const [toast, mostrarToast] = useAviso();
  const formRef = useRef<HTMLElement>(null);

  const cargar = useCallback(async () => {
    setCargando(true);
    const [cxc, hist, ctas, linea] = await Promise.allSettled([
      erpApi.listarCuentasPorCobrar({ estado: "todas" }),
      erpApi.listarCobros(),
      bancosApi.listarCuentas(),
      pagosApi.listar(),
    ]);
    if (cxc.status === "fulfilled") {
      setCuentas(cxc.value);
      setErrorCuentas(null);
    } else {
      setCuentas([]);
      setErrorCuentas(mensajeError(cxc.reason));
    }
    if (hist.status === "fulfilled") {
      setCobros(hist.value);
      setErrorCobros(null);
    } else {
      setCobros([]);
      setErrorCobros(mensajeError(hist.reason));
    }
    // Sin Bancos (servicio caído o sin cuentas) queda solo el texto libre de la cuenta de destino.
    setBancos(ctas.status === "fulfilled" ? ctas.value : []);
    // Pagos con tarjeta desde el portal de clientes (fase 7): la sección solo aparece si hay alguno.
    setEnLinea(linea.status === "fulfilled" ? linea.value : []);
    setCargando(false);
  }, []);

  useEffect(() => {
    void cargar();
  }, [cargar]);

  const porCobrar = useMemo(() => cuentas.filter((c) => c.estado !== "cobrada" && Number(c.pendiente) > 0), [cuentas]);

  function abrirForm(c?: CuentaPorCobrar) {
    setErrorForm(null);
    setAvisoForm(null);
    setForm({ ...formVacio(), factura_id: c?.factura_id ?? "", monto: c ? String(Number(c.pendiente)) : "" });
    setAbierto(true);
    window.setTimeout(() => formRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 0);
  }

  // ?factura=<id>: la ficha de la factura enlaza aquí para registrar su cobro.
  const facturaParam = params.get("factura");
  useEffect(() => {
    if (cargando || !facturaParam) return;
    const c = cuentas.find((x) => x.factura_id === facturaParam);
    if (c && Number(c.pendiente) > 0 && c.estado !== "cobrada") {
      abrirForm(c);
    } else if (!errorCuentas) {
      abrirForm();
      setAvisoForm(
        c
          ? `La factura ${c.encf ?? ""} ya está cobrada por completo.`
          : "Esa factura no tiene saldo por cobrar: solo las ventas a crédito enviadas a la DGII generan cuentas por cobrar.",
      );
    }
    setParams({}, { replace: true });
  }, [cargando, facturaParam]);

  const filas = useMemo(() => {
    const texto = q.trim().toLowerCase();
    const p = PESTANAS.find((x) => x.id === pestana)!;
    return cuentas
      .filter(p.test)
      .filter((c) => !texto || [c.encf, c.cliente_nombre, c.cliente_rnc, fechaLegible(c.vence)].join(" ").toLowerCase().includes(texto))
      .sort((a, b) => fechaIso(a.vence).localeCompare(fechaIso(b.vence)));
  }, [cuentas, pestana, q]);

  const totalPorCobrar = porCobrar.reduce((s, c) => s + (Number(c.pendiente) || 0), 0);
  const vencidas = porCobrar.filter((c) => c.estado === "vencida");
  const totalVencido = vencidas.reduce((s, c) => s + (Number(c.pendiente) || 0), 0);
  const mes = hoyIso().slice(0, 7);
  const cobrosMes = cobros.filter((c) => !c.anulado && fechaIso(c.fecha).startsWith(mes));
  const cobradoMes = cobrosMes.reduce((s, c) => s + (Number(c.monto) || 0), 0);
  const cuentasDestino = useMemo(() => [...new Set(cobros.map((c) => c.cuenta_destino).filter((x): x is string => Boolean(x)))], [cobros]);

  const seleccionada = porCobrar.find((c) => c.factura_id === form.factura_id);
  // Los cobros son en RD$: solo las cuentas activas en pesos (el backend rechaza las demás con 422).
  const cuentasBanco = bancos.filter((c) => c.activa && c.moneda === "DOP");
  const cuentaBanco = bancos.find((c) => c.id === form.cuenta_bancaria_id);
  const nombreBanco = (id: string | null) => (id ? bancos.find((c) => c.id === id) : undefined);

  function set<K extends keyof FormCobro>(campo: K, valor: FormCobro[K]) {
    setForm((prev) => ({ ...prev, [campo]: valor }));
    setErrorForm(null);
  }

  function elegirFactura(facturaId: string) {
    const c = porCobrar.find((x) => x.factura_id === facturaId);
    setForm((prev) => ({ ...prev, factura_id: facturaId, monto: c ? String(Number(c.pendiente)) : "" }));
    setErrorForm(null);
  }

  async function registrar() {
    const monto = Number(form.monto);
    if (!seleccionada) return setErrorForm("Elija la factura a la que se aplica el cobro.");
    if (!(monto > 0)) return setErrorForm("El monto recibido debe ser mayor que cero.");
    if (monto > Number(seleccionada.pendiente) + 0.004)
      return setErrorForm(`El monto supera lo pendiente de la factura (RD$ ${formatoMonto(seleccionada.pendiente)}).`);
    if (!form.fecha) return setErrorForm("Indique la fecha del cobro.");
    setGuardando(true);
    try {
      const cobro = await erpApi.registrarCobro({
        factura_id: seleccionada.factura_id,
        fecha: form.fecha,
        monto: monto.toFixed(2),
        metodo: form.metodo,
        cuenta_destino: cuentaBanco ? etiquetaCuenta(cuentaBanco) : form.cuenta_destino.trim() || null,
        cuenta_bancaria_id: form.cuenta_bancaria_id || null,
        referencia: form.referencia.trim() || null,
      });
      setAbierto(false);
      setForm(formVacio());
      mostrarToast("Cobro registrado", { detalle: `RD$ ${formatoMonto(cobro.monto)} aplicado a ${cobro.encf ?? seleccionada.encf ?? "la factura"}` });
      void cargar();
    } catch (err) {
      setErrorForm(mensajeError(err));
    } finally {
      setGuardando(false);
    }
  }

  async function confirmarAnular() {
    if (!anular) return;
    setAnulando(true);
    try {
      await erpApi.anularCobro(anular.id);
      mostrarToast("Cobro anulado", { detalle: `RD$ ${formatoMonto(anular.monto)} vuelve a quedar pendiente en ${anular.encf ?? "la factura"}` });
      setAnular(null);
      void cargar();
    } catch (err) {
      setAnular(null);
      mostrarToast("No se pudo anular el cobro", { detalle: mensajeError(err), error: true });
    } finally {
      setAnulando(false);
    }
  }

  function exportar() {
    descargarCsv(`Cuentas_por_cobrar_${hoyIso()}.csv`, [
      ["e-NCF", "Cliente", "RNC", "Emisión", "Vence", "Total (RD$)", "Cobrado (RD$)", "Pendiente (RD$)", "Estado"],
      ...filas.map((c) => [
        c.encf,
        c.cliente_nombre,
        c.cliente_rnc,
        fechaLegible(c.fecha_emision),
        fechaLegible(c.vence),
        Number(c.total).toFixed(2),
        Number(c.cobrado).toFixed(2),
        Number(c.pendiente).toFixed(2),
        ESTADO_CXC[c.estado]?.label ?? c.estado,
      ]),
    ]);
  }

  return (
    <div className="content">
      <IngresosEncabezado
        titulo="Cobros"
        sub="Cuentas por cobrar y pagos recibidos de clientes"
        acciones={
          <>
            <button type="button" className="btn btn-o" onClick={exportar} disabled={filas.length === 0}>
              Exportar
            </button>
            <button type="button" className="btn btn-p" onClick={() => (abierto ? setAbierto(false) : abrirForm())}>
              {abierto ? "Cerrar formulario" : "+ Registrar cobro"}
            </button>
          </>
        }
      />

      {abierto && (
        <section className="card" ref={formRef}>
          <div className="card-h">
            <h2>Registrar cobro</h2>
            <span className="hint">Se aplicará a la factura seleccionada</span>
          </div>
          <div className="card-b form-grid">
            {aviso && (
              <div className="al al-w fld-full">
                <IconAlerta />
                <span>{aviso}</span>
              </div>
            )}
            {!cargando && porCobrar.length === 0 && (
              <div className="al al-i fld-full">
                <IconAlerta />
                <span>
                  <b>No hay facturas pendientes de cobro.</b> Las ventas a crédito enviadas a la DGII aparecen aquí hasta que se cobran por completo.
                </span>
              </div>
            )}
            <div className="fld">
              <label className="lbl" htmlFor="cb-fac">
                Factura <span className="req">*</span>
              </label>
              <select id="cb-fac" className="inp" value={form.factura_id} onChange={(e) => elegirFactura(e.target.value)} disabled={porCobrar.length === 0}>
                <option value="">Elija una factura…</option>
                {porCobrar.map((c) => (
                  <option key={c.factura_id} value={c.factura_id}>
                    {c.encf} · {c.cliente_nombre}
                  </option>
                ))}
              </select>
            </div>
            <div className="fld">
              <label className="lbl" htmlFor="cb-monto">
                Monto recibido (RD$) <span className="req">*</span>
              </label>
              <input
                id="cb-monto"
                type="number"
                min="0"
                step="0.01"
                className="inp"
                style={{ textAlign: "right" }}
                value={form.monto}
                onChange={(e) => set("monto", e.target.value)}
                placeholder="0.00"
              />
            </div>
            <div className="fld">
              <label className="lbl" htmlFor="cb-met">Método</label>
              <select id="cb-met" className="inp" value={form.metodo} onChange={(e) => set("metodo", e.target.value as MetodoCobro)}>
                {METODOS.map((m) => (
                  <option key={m.value} value={m.value}>
                    {m.label}
                  </option>
                ))}
              </select>
            </div>
            {cuentasBanco.length > 0 && (
              <div className="fld">
                <label className="lbl" htmlFor="cb-banco">Cuenta bancaria</label>
                <select id="cb-banco" className="inp" value={form.cuenta_bancaria_id} onChange={(e) => set("cuenta_bancaria_id", e.target.value)}>
                  <option value="">Sin cuenta de Bancos</option>
                  {cuentasBanco.map((c) => (
                    <option key={c.id} value={c.id}>
                      {etiquetaCuenta(c)}
                    </option>
                  ))}
                </select>
                <span className="hint">{cuentaBanco ? "El cobro entra a esta cuenta y se concilia con el extracto." : "Elija la cuenta para verlo en Bancos y conciliarlo."}</span>
              </div>
            )}
            {!cuentaBanco && (
              <div className="fld">
                <label className="lbl" htmlFor="cb-cta">Cuenta de destino</label>
                <input
                  id="cb-cta"
                  className="inp"
                  list="cb-cuentas"
                  value={form.cuenta_destino}
                  onChange={(e) => set("cuenta_destino", e.target.value)}
                  placeholder={form.metodo === "efectivo" ? "Caja chica" : "Banco Popular ****4471"}
                />
                <datalist id="cb-cuentas">
                  {cuentasDestino.map((c) => (
                    <option key={c} value={c} />
                  ))}
                </datalist>
              </div>
            )}
            <div className="fld">
              <label className="lbl" htmlFor="cb-ref">Referencia</label>
              <input
                id="cb-ref"
                className="inp mono"
                value={form.referencia}
                onChange={(e) => set("referencia", e.target.value)}
                placeholder={form.metodo === "cheque" ? "N.º de cheque" : "N.º de transacción o recibo"}
              />
            </div>
            <div className="fld">
              <label className="lbl" htmlFor="cb-fecha">Fecha</label>
              <input id="cb-fecha" type="date" className="inp" value={form.fecha} max={hoyIso()} onChange={(e) => set("fecha", e.target.value)} />
            </div>
            {seleccionada && (
              <div className="fld-full hint">
                {seleccionada.cliente_nombre}
                {seleccionada.cliente_rnc && ` · RNC ${formatoRnc(seleccionada.cliente_rnc)}`} · Total RD$ {formatoMonto(seleccionada.total)} · Cobrado RD${" "}
                {formatoMonto(seleccionada.cobrado)} · <b style={{ color: "#111827" }}>Pendiente RD$ {formatoMonto(seleccionada.pendiente)}</b> ·{" "}
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
              <button type="button" className="btn btn-p" onClick={registrar} disabled={guardando || porCobrar.length === 0}>
                {guardando ? "Registrando…" : "Registrar cobro"}
              </button>
            </div>
          </div>
        </section>
      )}

      {errorCuentas && <AvisoError titulo="No se pudieron cargar las cuentas por cobrar." mensaje={errorCuentas} onReintentar={() => void cargar()} />}

      <div className="grid-stats">
        <Estadistica k="Por cobrar" v={cargando ? "—" : `RD$ ${formatoMonto(totalPorCobrar)}`} s={`${porCobrar.length} ${porCobrar.length === 1 ? "factura pendiente" : "facturas pendientes"}`} />
        <Estadistica k="Vencido" v={cargando ? "—" : `RD$ ${formatoMonto(totalVencido)}`} s={`${vencidas.length} ${vencidas.length === 1 ? "factura vencida" : "facturas vencidas"}`} />
        <Estadistica k="Cobrado este mes" v={cargando ? "—" : `RD$ ${formatoMonto(cobradoMes)}`} s={`${mesActualLegible()} · ${cobrosMes.length} ${cobrosMes.length === 1 ? "cobro" : "cobros"}`} />
      </div>

      <section className="card">
        <div className="tabs" role="tablist" aria-label="Filtrar cuentas por cobrar">
          {PESTANAS.map((p) => (
            <button key={p.id} type="button" role="tab" aria-selected={pestana === p.id} className={pestana === p.id ? "tab on" : "tab"} onClick={() => setPestana(p.id)}>
              {p.label} <span className="chip chip-sm">{cuentas.filter(p.test).length}</span>
            </button>
          ))}
        </div>
        <div className="filters">
          <Buscador valor={q} onCambiar={setQ} placeholder="Buscar en Cobros" />
        </div>

        {cargando && <Cargando />}
        {!cargando && !errorCuentas && cuentas.length === 0 && (
          <Vacio titulo="No hay cuentas por cobrar" texto="Cuando emita facturas a crédito, aparecerán aquí hasta que el cliente las pague." />
        )}
        {!cargando && cuentas.length > 0 && filas.length === 0 && (
          <SinResultados
            q={q}
            onLimpiar={() => {
              setQ("");
              setPestana("todas");
            }}
          />
        )}
        {!cargando && filas.length > 0 && (
          <div className="tw">
            <table className="tbl" style={{ minWidth: 960 }}>
              <thead>
                <tr>
                  <th>e-NCF</th>
                  <th>Cliente</th>
                  <th>Vence</th>
                  <th className="num">Total (RD$)</th>
                  <th className="num">Pendiente (RD$)</th>
                  <th>Estado</th>
                  <th style={{ textAlign: "right" }}>Acciones</th>
                </tr>
              </thead>
              <tbody>
                {filas.map((c) => {
                  const est = ESTADO_CXC[c.estado] ?? { label: c.estado, badge: "bd b-borr" };
                  const debe = c.estado !== "cobrada" && Number(c.pendiente) > 0;
                  return (
                    <tr key={c.factura_id} className="fila-link" onClick={() => navigate(`/facturas/${c.factura_id}`)}>
                      <td className="nowrap">
                        <Link to={`/facturas/${c.factura_id}`} className="link mono" onClick={(e) => e.stopPropagation()}>
                          {c.encf || "(sin e-NCF)"}
                        </Link>
                      </td>
                      <td>
                        <div className="strong">{c.cliente_nombre}</div>
                        {c.cliente_rnc && <div className="hint mono">{formatoRnc(c.cliente_rnc)}</div>}
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
                      <td className="num" style={{ fontWeight: 600, color: debe ? "#111827" : undefined }}>
                        {formatoMonto(c.pendiente)}
                      </td>
                      <td>
                        <span className={est.badge}>{est.label}</span>
                      </td>
                      <td onClick={(e) => e.stopPropagation()}>
                        <div className="fila-acc">
                          {debe && (
                            <button type="button" className="btn btn-g btn-sm" onClick={() => abrirForm(c)}>
                              Registrar cobro
                            </button>
                          )}
                          {debe && (
                            <a className="btn btn-g btn-sm" href={enlaceWhatsApp(textoRecordatorio(c), c.cliente_telefono)} target="_blank" rel="noreferrer" title="Abre WhatsApp con el mensaje listo para enviar">
                              Recordatorio
                            </a>
                          )}
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
          <h2>Historial de cobros</h2>
          {!cargando && !errorCobros && <span className="hint">{cobros.length === 1 ? "1 cobro registrado" : `${cobros.length} cobros registrados`}</span>}
        </div>
        {cargando && <Cargando />}
        {!cargando && errorCobros && (
          <div className="card-b">
            <AvisoError titulo="No se pudo cargar el historial de cobros." mensaje={errorCobros} onReintentar={() => void cargar()} />
          </div>
        )}
        {!cargando && !errorCobros && cobros.length === 0 && <Vacio titulo="Aún no hay cobros registrados" texto="Cada pago que reciba de un cliente quedará anotado aquí." />}
        {!cargando && cobros.length > 0 && (
          <div className="tw">
            <table className="tbl" style={{ minWidth: 900 }}>
              <thead>
                <tr>
                  <th>Fecha</th>
                  <th>e-NCF</th>
                  <th>Cliente</th>
                  <th>Método</th>
                  <th>Cuenta · Referencia</th>
                  <th className="num">Monto (RD$)</th>
                  <th>Estado</th>
                  <th style={{ textAlign: "right" }}>Acciones</th>
                </tr>
              </thead>
              <tbody>
                {[...cobros]
                  .sort((a, b) => fechaIso(b.fecha).localeCompare(fechaIso(a.fecha)) || b.creado_en.localeCompare(a.creado_en))
                  .map((c) => (
                    <tr key={c.id}>
                      <td className="nowrap">{fechaLegible(c.fecha)}</td>
                      <td className="mono">
                        <Link to={`/facturas/${c.factura_id}`} className="link mono">
                          {c.encf || "(sin e-NCF)"}
                        </Link>
                      </td>
                      <td>{c.cliente_nombre}</td>
                      <td>{METODO_LABEL[c.metodo] ?? c.metodo}</td>
                      <td>
                        {(nombreBanco(c.cuenta_bancaria_id)?.nombre ?? c.cuenta_destino) || "—"}
                        {c.referencia && <div className="hint mono">{c.referencia}</div>}
                      </td>
                      <td className="num" style={{ textDecoration: c.anulado ? "line-through" : undefined }}>
                        {formatoMonto(c.monto)}
                      </td>
                      <td>{c.anulado ? <span className="bd b-rech">Anulado</span> : <span className="bd b-acep">Aplicado</span>}</td>
                      <td>
                        <div className="fila-acc">
                          {!c.anulado && (
                            <button type="button" className="btn btn-g btn-sm" style={{ color: "#B91C1C" }} onClick={() => setAnular(c)}>
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

      {enLinea.length > 0 && (
        <section className="card" data-pagos-en-linea>
          <div className="card-h">
            <h2>Pagos en línea</h2>
            <span className="hint">Pagos con tarjeta desde el portal de clientes · los aprobados ya están en el historial de cobros</span>
          </div>
          <div className="tw">
            <table className="tbl" style={{ minWidth: 980 }}>
              <thead>
                <tr>
                  <th>Fecha</th>
                  <th>e-NCF</th>
                  <th>Cliente</th>
                  <th className="num">Monto (RD$)</th>
                  <th>Estado</th>
                  <th>Autorización · RRN</th>
                  <th>Orden</th>
                  <th>Cobro</th>
                </tr>
              </thead>
              <tbody>
                {enLinea.map((p) => (
                  <tr key={p.id} data-orden={p.orden}>
                    <td className="nowrap">{new Date(p.creado_en).toLocaleDateString("es-DO", { day: "2-digit", month: "2-digit", year: "numeric" })}</td>
                    <td className="mono">
                      <Link to={`/facturas/${p.factura_id}`} className="link mono">
                        {p.encf || "(sin e-NCF)"}
                      </Link>
                    </td>
                    <td>{p.cliente_nombre}</td>
                    <td className="num">{formatoMonto(p.monto)}</td>
                    <td>
                      <span className={ESTADO_PAGO_EN_LINEA[p.estado].badge}>{ESTADO_PAGO_EN_LINEA[p.estado].label}</span>
                      {p.proveedor === "simulado" && <div className="hint">Simulado</div>}
                      {p.estado === "declinado" && p.respuesta && <div className="hint">{p.respuesta}</div>}
                    </td>
                    <td className="mono">
                      {p.codigo_autorizacion || p.rrn ? (
                        <>
                          {p.codigo_autorizacion ?? "—"}
                          <div className="hint mono">RRN {p.rrn ?? "—"}</div>
                        </>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td className="mono hint">
                      {p.orden}
                      {p.orden_proveedor && p.proveedor === "azul" && <div>Azul {p.orden_proveedor}</div>}
                    </td>
                    <td>
                      {p.cobro_id ? (
                        p.cobro_anulado ? <span className="bd b-rech">Anulado</span> : <span className="bd b-acep">Registrado</span>
                      ) : p.estado === "aprobado" ? (
                        <span className="bd b-rech">Revisar</span>
                      ) : (
                        "—"
                      )}
                      {p.incidencia && (
                        <div className="hint" style={{ color: "#B45309", maxWidth: 260 }}>
                          {p.incidencia}
                        </div>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {anular && (
        <Modal
          titulo="¿Anular este cobro?"
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
            El cobro de <b style={{ color: "#111827" }}>RD$ {formatoMonto(anular.monto)}</b> del {fechaLegible(anular.fecha)} a la factura{" "}
            <b className="mono" style={{ color: "#111827" }}>
              {anular.encf}
            </b>{" "}
            ({anular.cliente_nombre}) quedará anulado y ese monto volverá a estar pendiente de cobro.
          </span>
        </Modal>
      )}

      {toast}
    </div>
  );
}
