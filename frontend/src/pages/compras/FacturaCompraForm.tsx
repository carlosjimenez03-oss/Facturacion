import { useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import { IconAlerta } from "../../layout/icons";
import { bancosApi } from "../../lib/bancosApi";
import type { CuentaBancaria } from "../../lib/bancosApi";
import { comprasApi } from "../../lib/comprasApi";
import type { FacturaCompra, FacturaCompraActualizar, MetodoPago, SugerenciaRetencion } from "../../lib/comprasApi";
import { erpApi } from "../../lib/erpApi";
import type { Cliente } from "../../lib/erpApi";
import { inventarioApi } from "../../lib/inventarioApi";
import { formatoMonto, formatoRnc } from "../../lib/marca";
import type { Compra, Proveedor } from "../../lib/types";
import { AvisoError, Modal } from "../ingresos/IngresosShell";
import { useAviso } from "../ingresos/useAviso";
import { centavos, fechaLegible, hoyIso, mensajeError } from "../ingresos/ingresosUtil";
import { nombrePeriodo } from "../impuestos/impuestosUtil";
import { etiquetaCuenta } from "../bancos/bancosUtil";
import { Ayuda, IconoCalendario, IconoDocumento, IconoEscudo, IconoOk } from "./ComprasShell";
import {
  ESTADO_CXP,
  METODO_LABEL,
  METODOS_PAGO,
  OPCIONES_BIENES_SERVICIOS,
  etiquetaBienesServicios,
  mensajeCompras,
  personaDeRnc,
  sePuedeEliminar,
  tipoEcfTexto,
} from "./comprasUtil";
import "../ingresos/ingresos.css";
import "./compras.css";

type Persona = "fisica" | "juridica" | "exterior";

const PERSONAS: { id: Persona; label: string }[] = [
  { id: "fisica", label: "Persona física" },
  { id: "juridica", label: "Persona jurídica" },
  { id: "exterior", label: "Del exterior" },
];

const ORIGEN: Record<string, string> = {
  receptor_recepcion: "e-CF recibido en la bandeja de entrada",
  auto_emitido: "Comprobante emitido por usted (E41/E47)",
  compras: "Registrada a mano en Compras",
  manual: "Registrada a mano en el Formato 606",
};

interface ProveedorForm {
  contacto_id: string | null;
  nombre: string;
  rnc: string;
}

type Guardada = { texto: string; detalle?: string; error?: boolean };

export function FacturaCompraForm() {
  const { id } = useParams<{ id: string }>();
  return id ? <EditarFacturaCompra key={id} id={id} /> : <NuevaFacturaCompra />;
}

/** "2026-10-30" -> "octubre 2026" (período en que se declaran las retenciones del pago). */
function periodoDeFecha(fecha: string): string {
  return nombrePeriodo(fecha.slice(0, 7).replace("-", ""));
}

/** "IR-17", "IT-1" o "IR-17 e IT-1" según las retenciones del pago. */
function declaraciones(p: { retencion_isr: string; retencion_itbis: string }): string {
  const isr = Number(p.retencion_isr) > 0;
  const itbis = Number(p.retencion_itbis) > 0;
  return isr && itbis ? "IR-17 e IT-1" : isr ? "IR-17" : "IT-1";
}

function Migas({ actual }: { actual: string }) {
  return (
    <div className="crumb">
      <Link to="/compras/facturas">Compras</Link>
      <span>/</span>
      <span>{actual}</span>
    </div>
  );
}

function ErrorForm({ texto }: { texto: string | null }) {
  if (!texto) return null;
  return (
    <div className="al al-d" role="alert">
      <IconAlerta />
      <span>
        <b>{texto}</b>
      </span>
    </div>
  );
}

/** Compras de inventario (entradas de mercancía) para vincular; si el servicio no responde, la lista queda vacía. */
function useComprasInventario() {
  const [compras, setCompras] = useState<Compra[]>([]);
  const [proveedores, setProveedores] = useState<Proveedor[]>([]);
  useEffect(() => {
    void Promise.allSettled([inventarioApi.listarCompras(), inventarioApi.listarProveedores()]).then(([c, p]) => {
      setCompras(c.status === "fulfilled" ? c.value : []);
      setProveedores(p.status === "fulfilled" ? p.value : []);
    });
  }, []);
  const etiqueta = (c: Compra) =>
    [c.numero_documento || "Sin documento", proveedores.find((p) => p.id === c.proveedor_id)?.nombre, fechaLegible(c.creado_en), c.estado === "borrador" ? "borrador" : null]
      .filter(Boolean)
      .join(" · ");
  return { compras, etiqueta };
}

function SelectCompraInventario({ valor, onCambiar }: { valor: string; onCambiar: (v: string) => void }) {
  const { compras, etiqueta } = useComprasInventario();
  return (
    <div className="fld">
      <label className="lbl" htmlFor="fc-compra">Entrada de mercancía (inventario)</label>
      <select id="fc-compra" className="inp" value={valor} onChange={(e) => onCambiar(e.target.value)}>
        <option value="">Sin vincular</option>
        {valor && !compras.some((c) => c.id === valor) && <option value={valor}>Compra vinculada</option>}
        {compras.map((c) => (
          <option key={c.id} value={c.id}>
            {etiqueta(c)}
          </option>
        ))}
      </select>
    </div>
  );
}

// --- Nueva factura de compra (Compra.dc.html) -------------------------------------------------

function NuevaFacturaCompra() {
  const navigate = useNavigate();
  const [persona, setPersona] = useState<Persona>("juridica");
  const [emisor, setEmisor] = useState(true);
  const [prov, setProv] = useState<ProveedorForm>({ contacto_id: null, nombre: "", rnc: "" });
  const [sugerencias, setSugerencias] = useState<Cliente[]>([]);
  const [pickerAbierto, setPickerAbierto] = useState(false);
  const [buscando, setBuscando] = useState(false);
  const [ncf, setNcf] = useState("");
  const [tipo, setTipo] = useState("2");
  const [fecha, setFecha] = useState(hoyIso);
  const [vence, setVence] = useState("");
  const [concepto, setConcepto] = useState("");
  const [servicios, setServicios] = useState("");
  const [bienes, setBienes] = useState("");
  const [itbis, setItbis] = useState("");
  const [itbisManual, setItbisManual] = useState(false);
  const [compraId, setCompraId] = useState("");
  const [pagar, setPagar] = useState(false);
  const [fechaPago, setFechaPago] = useState(hoyIso);
  const [metodo, setMetodo] = useState<MetodoPago>("transferencia");
  const [cuenta, setCuenta] = useState("");
  const [cuentaBancariaId, setCuentaBancariaId] = useState("");
  const [bancos, setBancos] = useState<CuentaBancaria[]>([]);
  const [referencia, setReferencia] = useState("");
  const [sug, setSug] = useState<SugerenciaRetencion | null>(null);
  const [errorSug, setErrorSug] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [guardando, setGuardando] = useState(false);
  const busquedaId = useRef(0);

  const esExterior = persona === "exterior";
  const informal = !esExterior && !emisor;
  const montoAntes = centavos((Number(servicios) || 0) + (Number(bienes) || 0));
  const itbisCalculado = centavos(montoAntes * 0.18);
  const itbisValor = itbisManual ? Number(itbis) || 0 : itbisCalculado;
  const total = centavos(montoAntes + itbisValor);
  const retenciones = sug ? centavos(Number(sug.retencion_isr) + Number(sug.retencion_itbis)) : 0;
  const neto = centavos(total - retenciones);
  // Igual que Pagos: los pagos son en RD$, solo cuentas activas en pesos.
  const cuentasBanco = bancos.filter((c) => c.activa && c.moneda === "DOP");
  const cuentaBanco = cuentasBanco.find((c) => c.id === cuentaBancariaId);

  // Cuentas de Bancos para el pago inmediato (sin Bancos queda solo el texto libre).
  useEffect(() => {
    bancosApi
      .listarCuentas()
      .then(setBancos)
      .catch(() => setBancos([]));
  }, []);

  // Busca contactos (proveedores) mientras se escribe el nombre o el RNC.
  useEffect(() => {
    const texto = prov.nombre.trim();
    if (!pickerAbierto || prov.contacto_id || texto.length < 2) {
      setSugerencias([]);
      return;
    }
    const n = ++busquedaId.current;
    setBuscando(true);
    const t = window.setTimeout(() => {
      erpApi
        .listarClientes({ q: texto, activo: true })
        .then((r) => n === busquedaId.current && setSugerencias(r.filter((c) => c.tipo !== "cliente").slice(0, 8)))
        .catch(() => n === busquedaId.current && setSugerencias([]))
        .finally(() => n === busquedaId.current && setBuscando(false));
    }, 250);
    return () => window.clearTimeout(t);
  }, [prov.nombre, prov.contacto_id, pickerAbierto]);

  // Retenciones sugeridas por erp-api (tasas vigentes al mes del pago).
  useEffect(() => {
    setErrorSug(null);
    if (esExterior || !(montoAntes > 0)) {
      setSug(null);
      return;
    }
    let vigente = true;
    const t = window.setTimeout(() => {
      comprasApi
        .sugerirRetenciones({
          persona,
          tipo_bienes_servicios: Number(tipo),
          monto: montoAntes.toFixed(2),
          itbis: itbisValor.toFixed(2),
          emisor_electronico: emisor,
          fecha_pago: pagar ? fechaPago || undefined : undefined,
        })
        .then((s) => vigente && setSug(s))
        .catch((err) => {
          if (!vigente) return;
          setSug(null);
          setErrorSug(mensajeCompras(err));
        });
    }, 300);
    return () => {
      vigente = false;
      window.clearTimeout(t);
    };
  }, [persona, tipo, montoAntes, itbisValor, emisor, pagar, fechaPago, esExterior]);

  function elegirPersona(p: Persona) {
    setPersona(p);
    setEmisor(p === "juridica");
    setError(null);
  }

  function elegirContacto(c: Cliente) {
    setProv({ contacto_id: c.id, nombre: c.nombre, rnc: c.rnc ?? "" });
    setPickerAbierto(false);
    if (c.rnc) setPersona(personaDeRnc(c.rnc));
    if (c.condicion_pago_dias > 0 && !vence && fecha) {
      const d = new Date(`${fecha}T12:00:00`);
      d.setDate(d.getDate() + c.condicion_pago_dias);
      setVence(d.toISOString().slice(0, 10));
    }
    setError(null);
  }

  async function buscarPorRnc() {
    const rnc = prov.rnc.replace(/\D/g, "");
    if (prov.contacto_id || prov.nombre.trim() || (rnc.length !== 9 && rnc.length !== 11)) return;
    try {
      const c = await erpApi.clientePorRnc(rnc);
      setProv({ contacto_id: c.id, nombre: c.nombre, rnc: c.rnc ?? rnc });
    } catch {
      // sin contacto con ese RNC: se escribe el nombre a mano
    }
  }

  function validar(): string | null {
    const rnc = prov.rnc.replace(/\D/g, "");
    if (rnc.length !== 9 && rnc.length !== 11) return "Indique el RNC (9 dígitos) o la cédula (11) del proveedor.";
    if (!ncf.trim()) return "Indique el NCF o e-NCF del comprobante del proveedor.";
    if (/^E4[137]/i.test(ncf.trim())) return "Los comprobantes E41, E43 y E47 se emiten desde Facturas, no se registran aquí.";
    if (!fecha) return "Indique la fecha del comprobante.";
    if (vence && vence < fecha) return "El vencimiento no puede ser anterior a la fecha del comprobante.";
    if (!(montoAntes > 0)) return "Indique el monto de los servicios y/o de los bienes.";
    if (itbisManual && !(Number(itbis) >= 0)) return "El ITBIS facturado no es válido.";
    if (pagar && !fechaPago) return "Indique la fecha del pago.";
    if (pagar && fechaPago < fecha) return "La fecha de pago no puede ser anterior a la fecha del comprobante.";
    return null;
  }

  async function guardar() {
    const problema = validar();
    if (problema) return setError(problema);
    setGuardando(true);
    setError(null);
    let factura: FacturaCompra;
    try {
      factura = await comprasApi.crearFactura({
        proveedor_rnc: prov.rnc.replace(/\D/g, ""),
        proveedor_nombre: prov.nombre.trim() || null,
        contacto_id: prov.contacto_id,
        compra_inventario_id: compraId || null,
        ncf: ncf.trim().toUpperCase(),
        fecha_comprobante: fecha,
        vence: vence || null,
        tipo_bienes_servicios: Number(tipo),
        concepto: concepto.trim() || null,
        monto_servicios: (Number(servicios) || 0).toFixed(2),
        monto_bienes: (Number(bienes) || 0).toFixed(2),
        itbis_facturado: itbisValor.toFixed(2),
      });
    } catch (err) {
      setError(mensajeCompras(err));
      setGuardando(false);
      return;
    }
    let guardada: Guardada = { texto: "Compra guardada", detalle: `${factura.proveedor_nombre} · ${factura.ncf} · RD$ ${formatoMonto(factura.total)}` };
    if (pagar) {
      try {
        const pago = await comprasApi.registrarPago({
          registro606_id: factura.registro606_id,
          fecha: fechaPago,
          monto: neto.toFixed(2),
          metodo,
          cuenta_origen: cuentaBanco ? etiquetaCuenta(cuentaBanco) : cuenta.trim() || null,
          cuenta_bancaria_id: cuentaBanco?.id ?? null,
          referencia: referencia.trim() || null,
          tipo_retencion_isr: sug && Number(sug.retencion_isr) > 0 ? sug.tipo_retencion_isr : null,
          retencion_isr: sug ? sug.retencion_isr : "0.00",
          retencion_itbis: sug ? sug.retencion_itbis : "0.00",
        });
        guardada = {
          texto: retenciones > 0 ? `Compra guardada · retenciones registradas para el ${declaraciones(pago)} de ${periodoDeFecha(pago.fecha)}` : "Compra guardada y pagada",
          detalle: `Pago de RD$ ${formatoMonto(pago.monto)} a ${factura.proveedor_nombre}`,
        };
      } catch (err) {
        guardada = { texto: "Compra guardada, pero el pago no se registró", detalle: mensajeCompras(err), error: true };
      }
    }
    navigate(`/compras/facturas/${factura.registro606_id}`, { state: { guardada } });
  }

  const idLabel = persona === "juridica" ? "RNC" : "Cédula";

  return (
    <div className="content">
      <div className="ph">
        <div>
          <Migas actual="Nueva factura de compra" />
          <h1>Nueva factura de compra</h1>
          <p>Registre la compra y el sistema calcula las retenciones de ISR e ITBIS según el proveedor y el tipo de gasto</p>
        </div>
        <div className="row">
          <Link className="btn btn-o" to="/compras/facturas">
            Cancelar
          </Link>
          <button type="button" className="btn btn-p" onClick={guardar} disabled={guardando || informal || esExterior}>
            {guardando ? "Guardando…" : "Guardar compra"}
          </button>
        </div>
      </div>

      <div className="cp-split">
        <div className="cp-form-main">
          <section className="card">
            <div className="card-h">
              <h2>Proveedor</h2>
              {prov.contacto_id && <span className="chip">Contacto registrado</span>}
            </div>
            <div className="card-b cp-col" style={{ gap: 18 }}>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 24 }}>
                <div className="fld">
                  <span className="lbl">Tipo de persona</span>
                  <div className="seg" role="group" aria-label="Tipo de persona">
                    {PERSONAS.map((p) => (
                      <button key={p.id} type="button" className={persona === p.id ? "on" : ""} aria-pressed={persona === p.id} onClick={() => elegirPersona(p.id)}>
                        {p.label}
                      </button>
                    ))}
                  </div>
                </div>
                <div className="fld">
                  <span className="lbl">
                    ¿Es emisor electrónico?{" "}
                    <Ayuda
                      etiqueta="Qué significa emisor electrónico"
                      texto="Si el proveedor emite e-CF, se registra su e-NCF. Si no, usted debe emitir un Comprobante de Compras (E41) a su nombre."
                    />
                  </span>
                  <div className="seg" role="group" aria-label="Emisor electrónico">
                    <button type="button" className={emisor && !esExterior ? "on" : ""} aria-pressed={emisor && !esExterior} onClick={() => setEmisor(true)} disabled={esExterior}>
                      Sí
                    </button>
                    <button type="button" className={!emisor || esExterior ? "on" : ""} aria-pressed={!emisor || esExterior} onClick={() => setEmisor(false)} disabled={esExterior}>
                      No
                    </button>
                  </div>
                </div>
              </div>

              {informal && (
                <div className="al al-i">
                  <IconoDocumento />
                  <span>
                    <b>Se emite un Comprobante de Compras E41</b> porque el proveedor no es emisor electrónico. Se envía a la DGII con las retenciones y queda
                    aquí como factura de compra.{" "}
                    <Link className="link" to="/facturas/nueva?tipo=41">
                      Emitir comprobante E41
                    </Link>
                  </span>
                </div>
              )}
              {esExterior && (
                <div className="al al-i">
                  <IconoDocumento />
                  <span>
                    <b>A un proveedor del exterior se le emite un comprobante de Pagos al exterior (E47).</b> No lleva retención en el 606.{" "}
                    <Link className="link" to="/facturas/nueva?tipo=47">
                      Emitir comprobante E47
                    </Link>
                  </span>
                </div>
              )}

              {!informal && !esExterior && (
                <div className="cp-grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))" }}>
                  <div className="fld">
                    <label className="lbl" htmlFor="fc-rnc">
                      {idLabel} <span className="req">*</span>
                    </label>
                    <input
                      id="fc-rnc"
                      className="inp mono"
                      value={prov.contacto_id ? formatoRnc(prov.rnc) : prov.rnc}
                      disabled={Boolean(prov.contacto_id)}
                      inputMode="numeric"
                      placeholder={persona === "juridica" ? "1-31-77889-1" : "001-0987654-3"}
                      onChange={(e) => {
                        setProv({ ...prov, rnc: e.target.value.replace(/[^\d-]/g, "") });
                        setError(null);
                      }}
                      onBlur={() => void buscarPorRnc()}
                    />
                  </div>
                  <div className="fld">
                    <label className="lbl" htmlFor="fc-prov">Nombre o razón social</label>
                    {prov.contacto_id ? (
                      <div className="picker-sel">
                        <IconoOk size={16} />
                        <b style={{ flex: 1, minWidth: 0 }}>{prov.nombre}</b>
                        <button
                          type="button"
                          className="btn btn-g btn-sm"
                          onClick={() => {
                            setProv({ contacto_id: null, nombre: "", rnc: "" });
                            setPickerAbierto(true);
                          }}
                        >
                          Cambiar
                        </button>
                      </div>
                    ) : (
                      <div className="picker">
                        <input
                          id="fc-prov"
                          className="inp"
                          value={prov.nombre}
                          autoComplete="off"
                          placeholder="Buscar en Contactos por nombre o RNC"
                          onFocus={() => setPickerAbierto(true)}
                          onBlur={() => window.setTimeout(() => setPickerAbierto(false), 150)}
                          onChange={(e) => {
                            setProv({ ...prov, nombre: e.target.value });
                            setPickerAbierto(true);
                            setError(null);
                          }}
                          aria-autocomplete="list"
                          aria-expanded={pickerAbierto && sugerencias.length > 0}
                        />
                        {pickerAbierto && prov.nombre.trim().length >= 2 && (sugerencias.length > 0 || !buscando) && (
                          <div className="picker-list" role="listbox">
                            {sugerencias.map((c) => (
                              <button key={c.id} type="button" role="option" aria-selected={false} className="picker-i" onMouseDown={(e) => e.preventDefault()} onClick={() => elegirContacto(c)}>
                                <b>{c.nombre}</b>
                                <span className="hint mono">{c.rnc ? formatoRnc(c.rnc) : "Sin RNC"}</span>
                              </button>
                            ))}
                            {sugerencias.length === 0 && <div className="picker-i hint">Sin coincidencias en Contactos: se guarda con este nombre.</div>}
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                </div>
              )}
            </div>
          </section>

          {!informal && !esExterior && (
            <>
              <section className="card">
                <div className="card-h">
                  <h2>Comprobante y clasificación</h2>
                </div>
                <div className="card-b cp-grid">
                  <div className="fld">
                    <label className="lbl" htmlFor="fc-ncf">
                      NCF o e-NCF del proveedor <span className="req">*</span>
                    </label>
                    <input
                      id="fc-ncf"
                      className="inp mono"
                      value={ncf}
                      maxLength={19}
                      placeholder="E310000006652"
                      onChange={(e) => {
                        setNcf(e.target.value.toUpperCase());
                        setError(null);
                      }}
                    />
                  </div>
                  <div className="fld cp-span2">
                    <label className="lbl" htmlFor="fc-tipo">
                      Tipo de bien o servicio (606) <span className="req">*</span>
                    </label>
                    <select id="fc-tipo" className="inp" value={tipo} onChange={(e) => setTipo(e.target.value)}>
                      {OPCIONES_BIENES_SERVICIOS.map((o) => (
                        <option key={o.value} value={o.value}>
                          {o.label}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div className="fld">
                    <label className="lbl" htmlFor="fc-fecha">Fecha del comprobante</label>
                    <input id="fc-fecha" type="date" className="inp" value={fecha} max={hoyIso()} onChange={(e) => setFecha(e.target.value)} />
                  </div>
                  <div className="fld">
                    <label className="lbl" htmlFor="fc-vence">Vence</label>
                    <input id="fc-vence" type="date" className="inp" value={vence} min={fecha} onChange={(e) => setVence(e.target.value)} />
                    <span className="hint">Vacío: los días de crédito del contacto (30 por defecto).</span>
                  </div>
                  <SelectCompraInventario valor={compraId} onCambiar={setCompraId} />
                </div>
              </section>

              <section className="card">
                <div className="card-h">
                  <h2>Detalle</h2>
                </div>
                <div className="card-b cp-grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))" }}>
                  <div className="fld cp-full">
                    <label className="lbl" htmlFor="fc-con">Concepto</label>
                    <input id="fc-con" className="inp" value={concepto} placeholder="Servicio de vigilancia · octubre 2026" onChange={(e) => setConcepto(e.target.value)} />
                  </div>
                  <div className="fld">
                    <label className="lbl" htmlFor="fc-serv">Servicios antes de ITBIS (RD$)</label>
                    <input id="fc-serv" type="number" min="0" step="0.01" className="inp" style={{ textAlign: "right" }} value={servicios} placeholder="0.00" onChange={(e) => setServicios(e.target.value)} />
                  </div>
                  <div className="fld">
                    <label className="lbl" htmlFor="fc-bien">Bienes antes de ITBIS (RD$)</label>
                    <input id="fc-bien" type="number" min="0" step="0.01" className="inp" style={{ textAlign: "right" }} value={bienes} placeholder="0.00" onChange={(e) => setBienes(e.target.value)} />
                  </div>
                  <div className="fld">
                    <label className="lbl" htmlFor="fc-itbis">ITBIS facturado {itbisManual ? "" : "(18%)"}</label>
                    <input
                      id="fc-itbis"
                      type="number"
                      min="0"
                      step="0.01"
                      className="inp"
                      style={{ textAlign: "right" }}
                      value={itbisManual ? itbis : itbisCalculado ? itbisCalculado.toFixed(2) : ""}
                      placeholder="0.00"
                      onChange={(e) => {
                        setItbisManual(true);
                        setItbis(e.target.value);
                      }}
                    />
                    {itbisManual && (
                      <button type="button" className="btn btn-g btn-sm" style={{ alignSelf: "flex-start" }} onClick={() => setItbisManual(false)}>
                        Calcular 18%
                      </button>
                    )}
                  </div>
                  <div className="fld">
                    <span className="lbl">Total del comprobante</span>
                    <div className="inp cp-calc" style={{ fontWeight: 600 }}>
                      {formatoMonto(total)}
                    </div>
                  </div>
                </div>
              </section>

              <section className="card">
                <div className="card-h">
                  <h2>Pago</h2>
                  <label className="cp-check">
                    <input type="checkbox" checked={pagar} onChange={(e) => setPagar(e.target.checked)} />
                    Ya la pagué
                  </label>
                </div>
                {pagar ? (
                  <div className="card-b cp-grid">
                    <div className="fld">
                      <label className="lbl" htmlFor="fc-fp">
                        Fecha de pago{" "}
                        <Ayuda etiqueta="Por qué importa la fecha de pago" texto="Las retenciones se declaran en el período en que se paga la factura, no en el de emisión." />
                      </label>
                      <input id="fc-fp" type="date" className="inp" value={fechaPago} min={fecha} max={hoyIso()} onChange={(e) => setFechaPago(e.target.value)} />
                    </div>
                    <div className="fld">
                      <label className="lbl" htmlFor="fc-met">Forma de pago</label>
                      <select id="fc-met" className="inp" value={metodo} onChange={(e) => setMetodo(e.target.value as MetodoPago)}>
                        {METODOS_PAGO.map((m) => (
                          <option key={m.value} value={m.value}>
                            {m.label}
                          </option>
                        ))}
                      </select>
                    </div>
                    {cuentasBanco.length > 0 && (
                      <div className="fld">
                        <label className="lbl" htmlFor="fc-banco">Cuenta bancaria</label>
                        <select id="fc-banco" className="inp" value={cuentaBancariaId} onChange={(e) => setCuentaBancariaId(e.target.value)}>
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
                        <label className="lbl" htmlFor="fc-cta">Cuenta de origen</label>
                        <input id="fc-cta" className="inp" value={cuenta} placeholder={metodo === "efectivo" ? "Caja chica" : "Banco Popular ****4471"} onChange={(e) => setCuenta(e.target.value)} />
                      </div>
                    )}
                    <div className="fld">
                      <label className="lbl" htmlFor="fc-ref">Referencia</label>
                      <input id="fc-ref" className="inp mono" value={referencia} placeholder={metodo === "cheque" ? "N.º de cheque" : "N.º de transacción"} onChange={(e) => setReferencia(e.target.value)} />
                    </div>
                  </div>
                ) : (
                  <p className="pad hint">Queda en Cuentas por pagar como compra a crédito. Las retenciones se aplican al registrar el pago.</p>
                )}
              </section>
            </>
          )}

          <ErrorForm texto={error} />
        </div>

        <aside className="card cp-ret cp-form-side">
          <div className="card-h">
            <h2>Retenciones calculadas</h2>
            <span className="chip b-teal">Automático</span>
          </div>
          <PanelRetenciones
            sug={esExterior || informal ? null : sug}
            error={errorSug}
            itbis={itbisValor}
            total={total}
            neto={neto}
            vacio={esExterior ? "Los pagos al exterior (E47) no llevan retención en el 606." : informal ? "El E41 calcula sus retenciones al emitirlo." : !(montoAntes > 0) ? "Indique los montos para calcular las retenciones." : null}
            nota={
              pagar && fechaPago ? (
                <>
                  Estas retenciones se reportarán en el <b>606, IR-17 e IT-1 de {periodoDeFecha(fechaPago)}</b>, el mes en que se paga la factura ({fechaLegible(fechaPago)}). Si cambia la
                  fecha de pago, cambia el período.
                </>
              ) : (
                <>Las retenciones se reportan en el <b>606, IR-17 e IT-1 del mes en que se paga</b> la factura, no en el de emisión.</>
              )
            }
          />
        </aside>
      </div>
    </div>
  );
}

function PanelRetenciones({
  sug,
  error,
  itbis,
  total,
  neto,
  vacio,
  nota,
}: {
  sug: SugerenciaRetencion | null;
  error: string | null;
  itbis: number;
  total: number;
  neto: number;
  vacio: string | null;
  nota: ReactNode;
}) {
  const pct = (v: string | undefined) => `${Number(v ?? 0).toLocaleString("en-US", { maximumFractionDigits: 2 })}%`;
  const ret = sug ? Number(sug.retencion_isr) + Number(sug.retencion_itbis) : 0;
  return (
    <div className="card-b cp-col">
      <div className="cp-ret-box">
        <div className="cp-ret-h">
          <span className="lbl">
            ISR retenido{" "}
            <span className="tip" data-tip="Impuesto Sobre la Renta retenido al proveedor. Se paga a la DGII en el IR-17 antes del día 10 del mes siguiente al pago.">
              ⓘ
            </span>
          </span>
          <span className="chip cp-pct">{pct(sug?.isr_pct)}</span>
        </div>
        <span className="cp-ret-v">RD$ {formatoMonto(sug?.retencion_isr)}</span>
        <span className="hint">{sug?.tipo_retencion_isr_label ? `${sug.tipo_retencion_isr_label} · sobre el monto antes de ITBIS` : "Sobre el monto antes de ITBIS"}</span>
      </div>
      <div className="cp-ret-box">
        <div className="cp-ret-h">
          <span className="lbl">
            ITBIS retenido{" "}
            <span className="tip r" data-tip="Parte del ITBIS facturado que usted retiene y paga directamente a la DGII en el IT-1.">
              ⓘ
            </span>
          </span>
          <span className="chip cp-pct">{pct(sug?.itbis_pct)}</span>
        </div>
        <span className="cp-ret-v">RD$ {formatoMonto(sug?.retencion_itbis)}</span>
        <span className="hint">Sobre ITBIS facturado de RD$ {formatoMonto(itbis)}</span>
      </div>
      {error ? (
        <AvisoError titulo="No se pudieron calcular las retenciones." mensaje={error} />
      ) : (
        <div className="al al-i" style={{ fontSize: 13 }}>
          <IconoEscudo />
          <span>{sug ? <><b>Regla aplicada:</b> {sug.regla}</> : vacio ?? "Calculando…"}</span>
        </div>
      )}
      <dl className="kv cp-tot" style={{ borderTop: "1px solid #EEF0F2", paddingTop: 14 }}>
        <dt>Total del comprobante</dt>
        <dd className="num">{formatoMonto(total)}</dd>
        <dt>(−) Retenciones</dt>
        <dd className="num">-{formatoMonto(ret)}</dd>
        <dt className="t">Neto a pagar al proveedor</dt>
        <dd className="num t">RD$ {formatoMonto(neto)}</dd>
      </dl>
      <div className="al al-w" style={{ fontSize: 13 }}>
        <IconoCalendario />
        <span>{nota}</span>
      </div>
    </div>
  );
}

// --- Ficha / edición de una factura de compra --------------------------------------------------

function EditarFacturaCompra({ id }: { id: string }) {
  const navigate = useNavigate();
  const location = useLocation();
  const [factura, setFactura] = useState<FacturaCompra | null>(null);
  const [cargando, setCargando] = useState(true);
  const [errorCarga, setErrorCarga] = useState<string | null>(null);
  const [nombre, setNombre] = useState("");
  const [tipo, setTipo] = useState("");
  const [vence, setVence] = useState("");
  const [concepto, setConcepto] = useState("");
  const [compraId, setCompraId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [guardando, setGuardando] = useState(false);
  const [eliminar, setEliminar] = useState(false);
  const [eliminando, setEliminando] = useState(false);
  const [aviso, mostrarAviso] = useAviso();

  function aplicar(f: FacturaCompra) {
    setFactura(f);
    setNombre(f.proveedor_nombre);
    setTipo(f.tipo_bienes_servicios != null ? String(f.tipo_bienes_servicios) : "");
    setVence(f.vence ?? "");
    setConcepto(f.concepto ?? "");
    setCompraId(f.compra_inventario_id ?? "");
  }

  function cargar() {
    setCargando(true);
    setErrorCarga(null);
    comprasApi
      .obtenerFactura(id)
      .then(aplicar)
      .catch((err) => setErrorCarga(mensajeError(err)))
      .finally(() => setCargando(false));
  }

  useEffect(cargar, [id]);

  useEffect(() => {
    const guardada = (location.state as { guardada?: Guardada } | null)?.guardada;
    if (!guardada) return;
    mostrarAviso(guardada.texto, { detalle: guardada.detalle, error: guardada.error });
    navigate(location.pathname, { replace: true, state: null });
  }, [location.state, location.pathname, mostrarAviso, navigate]);

  const cambios = useMemo<FacturaCompraActualizar>(() => {
    if (!factura) return {};
    const c: FacturaCompraActualizar = {};
    if (nombre.trim() && nombre.trim() !== factura.proveedor_nombre) c.proveedor_nombre = nombre.trim();
    if (tipo && Number(tipo) !== factura.tipo_bienes_servicios) c.tipo_bienes_servicios = Number(tipo);
    if ((vence || null) !== (factura.vence || null) && vence) c.vence = vence;
    if ((concepto.trim() || null) !== (factura.concepto || null)) c.concepto = concepto.trim() || null;
    if ((compraId || null) !== (factura.compra_inventario_id || null)) c.compra_inventario_id = compraId || null;
    return c;
  }, [factura, nombre, tipo, vence, concepto, compraId]);
  const sucio = Object.keys(cambios).length > 0;

  async function guardar() {
    if (!factura) return;
    if (vence && vence < factura.fecha_comprobante) return setError("El vencimiento no puede ser anterior a la fecha del comprobante.");
    setGuardando(true);
    setError(null);
    try {
      const f = await comprasApi.actualizarFactura(factura.registro606_id, cambios);
      aplicar(f);
      mostrarAviso("Cambios guardados", { detalle: `${f.proveedor_nombre} · ${f.ncf}` });
    } catch (err) {
      setError(mensajeCompras(err));
    } finally {
      setGuardando(false);
    }
  }

  async function confirmarEliminar() {
    if (!factura) return;
    setEliminando(true);
    try {
      await comprasApi.eliminarFactura(factura.registro606_id);
      navigate("/compras/facturas");
    } catch (err) {
      setEliminar(false);
      setEliminando(false);
      mostrarAviso("No se pudo eliminar la factura", { detalle: mensajeCompras(err), error: true });
    }
  }

  if (cargando && !factura)
    return (
      <div className="content">
        <p className="mut">Cargando…</p>
      </div>
    );
  if (!factura)
    return (
      <div className="content">
        <div className="ph">
          <div>
            <Migas actual="Factura de compra" />
            <h1>Factura de compra</h1>
          </div>
        </div>
        <AvisoError titulo="No se pudo abrir la factura de compra." mensaje={errorCarga ?? "No encontrada"} onReintentar={cargar} />
        <div>
          <Link to="/compras/facturas" className="btn btn-o">
            Volver a facturas de compra
          </Link>
        </div>
      </div>
    );

  const est = ESTADO_CXP[factura.estado] ?? { label: factura.estado, badge: "bd b-borr" };
  const debe = factura.estado !== "pagada" && Number(factura.pendiente) > 0;
  const vigentes = factura.pagos.filter((p) => !p.anulado);

  return (
    <div className="content">
      <div className="ph">
        <div>
          <Migas actual={factura.ncf} />
          <h1 className="row" style={{ gap: 12 }}>
            Factura de compra {factura.ncf}
            <span className={est.badge}>{est.label}</span>
          </h1>
          <p>
            {factura.proveedor_nombre} · {ORIGEN[factura.origen] ?? factura.origen}
          </p>
        </div>
        <div className="row">
          {sePuedeEliminar(factura) && (
            <button type="button" className="btn btn-do" onClick={() => setEliminar(true)}>
              Eliminar
            </button>
          )}
          {debe && (
            <Link className="btn btn-o" to={`/compras/pagos?factura=${factura.registro606_id}`}>
              Registrar pago
            </Link>
          )}
          <button type="button" className="btn btn-p" onClick={guardar} disabled={!sucio || guardando}>
            {guardando ? "Guardando…" : "Guardar cambios"}
          </button>
        </div>
      </div>

      {factura.tipo_bienes_servicios == null && (
        <div className="al al-w">
          <IconAlerta />
          <span>
            <b>Esta compra no está clasificada en el 606.</b> Elija el tipo de bien o servicio para que su ITBIS cuente en el IT-1.
          </span>
        </div>
      )}

      <div className="cp-split">
        <div className="cp-form-main">
          <section className="card">
            <div className="card-h">
              <h2>Proveedor</h2>
              {factura.contacto_id && <span className="chip">Contacto registrado</span>}
            </div>
            <div className="card-b cp-grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))" }}>
              <div className="fld">
                <label className="lbl" htmlFor="fe-rnc">{factura.proveedor_rnc.length === 9 ? "RNC" : "Cédula"}</label>
                <input id="fe-rnc" className="inp mono" value={formatoRnc(factura.proveedor_rnc)} disabled />
              </div>
              <div className="fld">
                <label className="lbl" htmlFor="fe-nom">Nombre o razón social</label>
                <input id="fe-nom" className="inp" value={nombre} onChange={(e) => setNombre(e.target.value)} />
              </div>
            </div>
          </section>

          <section className="card">
            <div className="card-h">
              <h2>Comprobante y clasificación</h2>
              <span className="hint">{tipoEcfTexto(factura.tipo_ecf)}</span>
            </div>
            <div className="card-b cp-grid">
              <div className="fld">
                <label className="lbl" htmlFor="fe-ncf">NCF o e-NCF</label>
                <input id="fe-ncf" className="inp mono" value={factura.ncf} disabled />
              </div>
              <div className="fld cp-span2">
                <label className="lbl" htmlFor="fe-tipo">
                  Tipo de bien o servicio (606) <span className="req">*</span>
                </label>
                <select id="fe-tipo" className="inp" value={tipo} onChange={(e) => setTipo(e.target.value)}>
                  {!tipo && <option value="">Sin clasificar — elija uno</option>}
                  {OPCIONES_BIENES_SERVICIOS.map((o) => (
                    <option key={o.value} value={o.value}>
                      {o.label}
                    </option>
                  ))}
                </select>
              </div>
              <div className="fld">
                <label className="lbl" htmlFor="fe-fecha">Fecha del comprobante</label>
                <input id="fe-fecha" type="date" className="inp" value={factura.fecha_comprobante} disabled />
              </div>
              <div className="fld">
                <label className="lbl" htmlFor="fe-vence">Vence</label>
                <input id="fe-vence" type="date" className="inp" value={vence} min={factura.fecha_comprobante} onChange={(e) => setVence(e.target.value)} />
              </div>
              <SelectCompraInventario valor={compraId} onCambiar={setCompraId} />
              <div className="fld cp-full">
                <label className="lbl" htmlFor="fe-con">Concepto</label>
                <input id="fe-con" className="inp" value={concepto} onChange={(e) => setConcepto(e.target.value)} />
              </div>
            </div>
          </section>

          <section className="card">
            <div className="card-h">
              <h2>Pagos</h2>
              <span className="hint">{vigentes.length === 1 ? "1 pago vigente" : `${vigentes.length} pagos vigentes`}</span>
            </div>
            {factura.pagos.length === 0 ? (
              <p className="pad hint">Aún no hay pagos registrados a esta factura.</p>
            ) : (
              <div className="tw">
                <table className="tbl" style={{ minWidth: 640 }}>
                  <thead>
                    <tr>
                      <th>Fecha</th>
                      <th>Método</th>
                      <th className="num">Monto (RD$)</th>
                      <th className="num">ISR retenido</th>
                      <th className="num">ITBIS retenido</th>
                      <th>Estado</th>
                    </tr>
                  </thead>
                  <tbody>
                    {factura.pagos.map((p) => (
                      <tr key={p.id}>
                        <td className="nowrap">{fechaLegible(p.fecha)}</td>
                        <td>
                          {METODO_LABEL[p.metodo] ?? p.metodo}
                          {p.cuenta_origen && <div className="hint">{p.cuenta_origen}</div>}
                        </td>
                        <td className="num" style={{ textDecoration: p.anulado ? "line-through" : undefined }}>
                          {formatoMonto(p.monto)}
                        </td>
                        <td className="num">{formatoMonto(p.retencion_isr)}</td>
                        <td className="num">{formatoMonto(p.retencion_itbis)}</td>
                        <td>{p.anulado ? <span className="bd b-rech">Anulado</span> : <span className="bd b-acep">Aplicado</span>}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <div className="imp-pie">
              <Link className="link" to="/compras/pagos">
                Ver todos los pagos a proveedores
              </Link>
            </div>
          </section>

          <ErrorForm texto={error} />
        </div>

        <aside className="card cp-ret cp-form-side">
          <div className="card-h">
            <h2>Resumen</h2>
            <span className={est.badge}>{est.label}</span>
          </div>
          <div className="card-b cp-col">
            <dl className="kv cp-tot">
              <dt>Monto antes de ITBIS</dt>
              <dd className="num">{formatoMonto(factura.monto_facturado)}</dd>
              <dt>ITBIS facturado</dt>
              <dd className="num">{formatoMonto(factura.itbis_facturado)}</dd>
              <dt className="t">Total del comprobante</dt>
              <dd className="num t">RD$ {formatoMonto(factura.total)}</dd>
              {Number(factura.notas_credito) > 0 && (
                <>
                  <dt>(−) Notas de crédito</dt>
                  <dd className="num">-{formatoMonto(factura.notas_credito)}</dd>
                </>
              )}
              {Number(factura.retencion_isr) > 0 && (
                <>
                  <dt>(−) ISR retenido</dt>
                  <dd className="num">-{formatoMonto(factura.retencion_isr)}</dd>
                </>
              )}
              {Number(factura.retencion_itbis) > 0 && (
                <>
                  <dt>(−) ITBIS retenido</dt>
                  <dd className="num">-{formatoMonto(factura.retencion_itbis)}</dd>
                </>
              )}
              <dt>(−) Pagado</dt>
              <dd className="num">-{formatoMonto(factura.pagado)}</dd>
              <dt className="t">Pendiente</dt>
              <dd className="num t">RD$ {formatoMonto(factura.pendiente)}</dd>
            </dl>
            <dl className="kv" style={{ borderTop: "1px solid #EEF0F2", paddingTop: 14 }}>
              <dt>Fecha</dt>
              <dd>{fechaLegible(factura.fecha_comprobante)}</dd>
              <dt>Vence</dt>
              <dd>
                {fechaLegible(factura.vence)}
                {factura.estado === "vencida" && factura.dias_vencida > 0 && <span className="err-t">Hace {factura.dias_vencida} días</span>}
              </dd>
              <dt>Tipo 606</dt>
              <dd>{etiquetaBienesServicios(factura.tipo_bienes_servicios)}</dd>
              {factura.tipo_retencion_isr_label && (
                <>
                  <dt>Retención ISR</dt>
                  <dd>{factura.tipo_retencion_isr_label}</dd>
                </>
              )}
              {factura.fecha_pago && (
                <>
                  <dt>Fecha de pago (606)</dt>
                  <dd>{fechaLegible(factura.fecha_pago)}</dd>
                </>
              )}
            </dl>
            {(Number(factura.retencion_isr) > 0 || Number(factura.retencion_itbis) > 0) && factura.fecha_pago && (
              <div className="al al-w" style={{ fontSize: 13 }}>
                <IconoCalendario />
                <span>
                  Las retenciones se declaran en el <b>IR-17 e IT-1 de {periodoDeFecha(factura.fecha_pago)}</b>.{" "}
                  <Link className="link" to={`/compras/retenciones?periodo=${factura.fecha_pago.slice(0, 7).replace("-", "")}`}>
                    Ver retenciones del mes
                  </Link>
                </span>
              </div>
            )}
            <div className="cp-col" style={{ gap: 8 }}>
              {debe && (
                <Link className="btn btn-p" to={`/compras/pagos?factura=${factura.registro606_id}`}>
                  Registrar pago
                </Link>
              )}
              {factura.ecf_recibido_id && (
                <Link className="btn btn-o" to={`/bandeja/${factura.ecf_recibido_id}`}>
                  Ver e-CF recibido
                </Link>
              )}
              {factura.factura_id && (
                <Link className="btn btn-o" to={`/facturas/${factura.factura_id}`}>
                  Ver comprobante emitido
                </Link>
              )}
              <Link className="btn btn-g" to={`/formato-606/registros/${factura.registro606_id}/editar`}>
                Ver renglón en el Formato 606
              </Link>
            </div>
          </div>
        </aside>
      </div>

      {eliminar && (
        <Modal
          titulo="¿Eliminar esta factura de compra?"
          onCerrar={() => setEliminar(false)}
          pie={
            <>
              <button type="button" className="btn btn-o" onClick={() => setEliminar(false)}>
                Cancelar
              </button>
              <button type="button" className="btn btn-d" onClick={confirmarEliminar} disabled={eliminando}>
                {eliminando ? "Eliminando…" : "Sí, eliminar"}
              </button>
            </>
          }
        >
          <span>
            La factura <b className="mono" style={{ color: "#111827" }}>{factura.ncf}</b> de {factura.proveedor_nombre} se quitará de Cuentas por pagar y del Formato 606.
          </span>
        </Modal>
      )}
      {aviso}
    </div>
  );
}
