import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import { Toast } from "../../components/detalle/Toast";
import type { Aviso } from "../../components/detalle/Toast";
import { IconAlerta } from "../../layout/icons";
import { erpApi, ErpApiError } from "../../lib/erpApi";
import type { Cliente, ClientePayload, CuentaPorCobrar, TipoContacto, TipoIdentificacion } from "../../lib/erpApi";
import { formatoMonto, formatoRnc } from "../../lib/marca";
import { padronApi, type ResultadoPadron } from "../../lib/padronApi";
import { estadoPadron, fechaPadron, rncParaPadron, textoRegimen } from "../../lib/padronUtil";
import {
  correoValido,
  errorIdentificacion,
  mensajeErp,
  payloadDesde,
  TIPO_CONTACTO_LABEL,
  TIPO_IDENTIFICACION_LABEL,
  tipoPorDigitos,
} from "./contactos";
import { ModalEliminar } from "./ModalEliminar";

const CONDICIONES = [0, 15, 30, 45, 60];

interface Campos {
  tipo: TipoContacto;
  tipoIdentificacion: TipoIdentificacion | "";
  rnc: string;
  nombre: string;
  correo: string;
  telefono: string;
  direccion: string;
  condicionPagoDias: string;
  limiteCredito: string;
  notas: string;
  activo: boolean;
}

const VACIO: Campos = {
  tipo: "cliente",
  tipoIdentificacion: "rnc",
  rnc: "",
  nombre: "",
  correo: "",
  telefono: "",
  direccion: "",
  condicionPagoDias: "0",
  limiteCredito: "",
  notas: "",
  activo: true,
};

function camposDesde(c: Cliente): Campos {
  return {
    tipo: c.tipo,
    tipoIdentificacion: c.tipo_identificacion ?? "",
    rnc: c.rnc ?? "",
    nombre: c.nombre,
    correo: c.correo ?? "",
    telefono: c.telefono ?? "",
    direccion: c.direccion ?? "",
    condicionPagoDias: String(c.condicion_pago_dias ?? 0),
    limiteCredito: c.limite_credito ?? "",
    notas: c.notas ?? "",
    activo: c.activo,
  };
}

function fechaCorta(iso: string): string {
  const [a, m, d] = iso.slice(0, 10).split("-");
  return d ? `${d}/${m}/${a}` : iso;
}

const ESTADO_CUENTA: Record<CuentaPorCobrar["estado"], { label: string; cls: string }> = {
  pendiente: { label: "Pendiente", cls: "bd b-pend" },
  vencida: { label: "Vencida", cls: "bd b-rech" },
  cobrada: { label: "Cobrada", cls: "bd b-acep" },
};

export function ContactoForm() {
  const navigate = useNavigate();
  const location = useLocation();
  const { id } = useParams<{ id: string }>();
  const [cliente, setCliente] = useState<Cliente | null>(null);
  const [campos, setCampos] = useState<Campos>(VACIO);
  const [cargando, setCargando] = useState(Boolean(id));
  const [errorCarga, setErrorCarga] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [guardando, setGuardando] = useState(false);
  const [duplicado, setDuplicado] = useState<Cliente | null>(null);
  const [verificado, setVerificado] = useState<string | null>(null);
  /** Padrón de RNC de la DGII para el número escrito (null = sin consultar). */
  const [padron, setPadron] = useState<(ResultadoPadron & { rnc: string }) | null>(null);
  const [consultandoPadron, setConsultandoPadron] = useState(false);
  /** El último nombre que llenó el padrón (se reemplaza al verificar otro número; lo escrito a mano no). */
  const nombreAuto = useRef("");
  const [cuentas, setCuentas] = useState<CuentaPorCobrar[] | null>(null);
  const [errorCuentas, setErrorCuentas] = useState<string | null>(null);
  const [eliminar, setEliminar] = useState<{ bloqueo?: string } | null>(null);
  const [eliminando, setEliminando] = useState(false);
  const [aviso, setAviso] = useState<Aviso | null>(null);
  const cerrarAviso = useCallback(() => setAviso(null), []);

  // Aviso que deja otra pantalla al navegar aquí (p. ej. "Contacto guardado" tras crearlo).
  useEffect(() => {
    const recibido = (location.state as { aviso?: Aviso } | null)?.aviso;
    if (recibido) setAviso(recibido);
  }, [location.state]);

  useEffect(() => {
    if (!id) {
      setCliente(null);
      setCampos(VACIO);
      return;
    }
    setCargando(true);
    erpApi
      .obtenerCliente(id)
      .then((c) => {
        setCliente(c);
        setCampos(camposDesde(c));
        setErrorCarga(null);
      })
      .catch((err) =>
        setErrorCarga(err instanceof ErpApiError && err.status === 404 ? "Este contacto no existe o fue eliminado." : mensajeErp(err)),
      )
      .finally(() => setCargando(false));
  }, [id]);

  const rncCliente = cliente?.rnc;
  const esCliente = cliente != null && cliente.tipo !== "proveedor";
  // Estado DGII del contacto guardado (panel lateral): del padrón, sin pulsar nada.
  useEffect(() => {
    const rnc = cliente && cliente.tipo_identificacion !== "pasaporte" ? rncParaPadron(cliente.rnc ?? "") : null;
    if (!rnc) return;
    let vigente = true;
    padronApi
      .consultar(rnc)
      .then((r) => vigente && setPadron({ ...r, rnc }))
      .catch(() => undefined);
    return () => {
      vigente = false;
    };
  }, [cliente]);
  useEffect(() => {
    if (!rncCliente || !esCliente) return;
    erpApi
      .listarCuentasPorCobrar({ cliente_rnc: rncCliente, estado: "todas" })
      .then((lista) => {
        setCuentas(lista);
        setErrorCuentas(null);
      })
      .catch((err) => setErrorCuentas(mensajeErp(err)));
  }, [rncCliente, esCliente]);

  function set<K extends keyof Campos>(campo: K, valor: Campos[K]) {
    setCampos((prev) => ({ ...prev, [campo]: valor }));
  }

  function cambiarRnc(valor: string) {
    const limpio = campos.tipoIdentificacion === "pasaporte" ? valor.toUpperCase() : valor.replace(/[^\d-]/g, "");
    const detectado = campos.tipoIdentificacion === "pasaporte" ? null : tipoPorDigitos(limpio);
    setCampos((prev) => ({ ...prev, rnc: limpio, tipoIdentificacion: detectado ?? prev.tipoIdentificacion }));
    setDuplicado(null);
    setVerificado(null);
    setPadron(null);
  }

  async function consultarPadron(rnc: string) {
    setConsultandoPadron(true);
    try {
      const r = await padronApi.consultar(rnc);
      setPadron({ ...r, rnc });
      if (r.tipo === "encontrado") {
        const p = r.contribuyente;
        // El nombre se llena si está vacío o si era el de otra consulta.
        setCampos((prev) => {
          if (prev.nombre.trim() && prev.nombre !== nombreAuto.current) return prev;
          nombreAuto.current = p.razon_social;
          return { ...prev, nombre: p.razon_social };
        });
      }
    } catch {
      setPadron({ tipo: "no_disponible", mensaje: "No se pudo consultar el padrón de la DGII en este momento.", rnc });
    } finally {
      setConsultandoPadron(false);
    }
  }

  async function verificar() {
    setDuplicado(null);
    setVerificado(null);
    setPadron(null);
    const problema = errorIdentificacion(campos.tipoIdentificacion, campos.rnc);
    if (problema) return setError(problema);
    setError(null);
    const rnc = campos.tipoIdentificacion === "pasaporte" ? null : rncParaPadron(campos.rnc);
    const enPadron = rnc ? consultarPadron(rnc) : Promise.resolve();
    try {
      const existente = await erpApi.clientePorRnc(campos.rnc);
      if (existente.id !== cliente?.id) setDuplicado(existente);
      else setVerificado("Este es el número registrado para este contacto.");
    } catch (err) {
      if (err instanceof ErpApiError && err.status === 404) {
        setVerificado("Formato válido y sin registrar en sus contactos.");
      } else {
        setError(mensajeErp(err));
      }
    }
    await enPadron;
  }

  function validar(): string | null {
    if (!campos.nombre.trim()) return "Escriba el nombre o la razón social.";
    if (campos.tipoIdentificacion && !campos.rnc.trim()) return `Escriba el número de ${TIPO_IDENTIFICACION_LABEL[campos.tipoIdentificacion]}.`;
    const problemaId = errorIdentificacion(campos.tipoIdentificacion, campos.rnc);
    if (problemaId) return problemaId;
    if (!correoValido(campos.correo)) return "Revise el correo: debe tener el formato nombre@empresa.do.";
    const dias = Number(campos.condicionPagoDias);
    if (!Number.isInteger(dias) || dias < 0 || dias > 365) return "La condición de pago debe ser un número de días entre 0 y 365.";
    if (campos.limiteCredito && !(Number(campos.limiteCredito) >= 0)) return "El límite de crédito debe ser un monto positivo.";
    return null;
  }

  function payload(): ClientePayload {
    const sinId = !campos.tipoIdentificacion;
    return {
      tipo: campos.tipo,
      tipo_identificacion: campos.tipoIdentificacion || null,
      rnc: sinId ? null : campos.tipoIdentificacion === "pasaporte" ? campos.rnc.trim() : campos.rnc.replace(/\D/g, ""),
      nombre: campos.nombre.trim(),
      correo: campos.correo.trim() || null,
      telefono: campos.telefono.trim() || null,
      direccion: campos.direccion.trim() || null,
      condicion_pago_dias: Number(campos.condicionPagoDias) || 0,
      limite_credito: campos.limiteCredito ? Number(campos.limiteCredito).toFixed(2) : null,
      notas: campos.notas.trim() || null,
      activo: campos.activo,
    };
  }

  async function guardar() {
    const problema = validar();
    if (problema) return setError(problema);
    setError(null);
    setGuardando(true);
    try {
      if (cliente) {
        const actualizado = await erpApi.actualizarCliente(cliente.id, payload());
        setCliente(actualizado);
        setCampos(camposDesde(actualizado));
        setAviso({ texto: "Cambios guardados" });
      } else {
        const creado = await erpApi.crearCliente(payload());
        navigate(`/contactos/${creado.id}`, { replace: true, state: { aviso: { texto: "Contacto guardado", detalle: creado.nombre } } });
      }
    } catch (err) {
      setError(mensajeErp(err));
    } finally {
      setGuardando(false);
    }
  }

  async function confirmarEliminar() {
    if (!cliente) return;
    setEliminando(true);
    try {
      await erpApi.eliminarCliente(cliente.id);
      navigate("/contactos");
    } catch (err) {
      if (err instanceof ErpApiError && err.status === 409) setEliminar({ bloqueo: err.message });
      else {
        setEliminar(null);
        setError(mensajeErp(err));
      }
    } finally {
      setEliminando(false);
    }
  }

  async function desactivar() {
    if (!cliente) return;
    setEliminando(true);
    try {
      const actualizado = await erpApi.actualizarCliente(cliente.id, payloadDesde(cliente, { activo: false }));
      setCliente(actualizado);
      setCampos(camposDesde(actualizado));
      setEliminar(null);
      setAviso({ texto: "Contacto desactivado", detalle: "Ya no aparecerá al facturar; su historial se conserva." });
    } catch (err) {
      setEliminar(null);
      setError(mensajeErp(err));
    } finally {
      setEliminando(false);
    }
  }

  if (cargando) return <div className="content"><p className="mut">Cargando…</p></div>;

  if (errorCarga) {
    return (
      <div className="content">
        <div className="ph">
          <div>
            <div className="crumb">
              <Link to="/contactos">Contactos</Link>
            </div>
            <h1>Contacto</h1>
          </div>
        </div>
        <div className="al al-d" role="alert">
          <IconAlerta />
          <span>
            <b>No se pudo abrir el contacto.</b> {errorCarga}
          </span>
        </div>
        <div>
          <Link to="/contactos" className="btn btn-o">
            Volver a contactos
          </Link>
        </div>
      </div>
    );
  }

  const condicionLibre = !CONDICIONES.includes(Number(campos.condicionPagoDias));
  const saldo = Number(cliente?.saldo) || 0;
  const limite = Number(cliente?.limite_credito) || 0;

  return (
    <div className="content">
      <div className="ph">
        <div>
          <div className="crumb">
            <Link to="/contactos">Contactos</Link>
            <span>/</span>
            <span>{cliente ? cliente.nombre : "Nuevo"}</span>
          </div>
          <div className="row" style={{ gap: 12 }}>
            <h1>{cliente ? cliente.nombre : "Nuevo contacto"}</h1>
            {cliente && <span className="chip">{TIPO_CONTACTO_LABEL[cliente.tipo]}</span>}
            {cliente && !cliente.activo && <span className="bd b-borr">Inactivo</span>}
          </div>
          <p>
            {cliente
              ? [cliente.rnc ? `${cliente.tipo_identificacion ? TIPO_IDENTIFICACION_LABEL[cliente.tipo_identificacion] : "RNC"} ${formatoRnc(cliente.rnc)}` : "Sin identificación", cliente.correo]
                  .filter(Boolean)
                  .join(" · ")
              : "Cliente o proveedor con su RNC o cédula y condiciones de pago"}
          </p>
        </div>
        {cliente && (
          <div className="row">
            <button type="button" className="btn btn-do" onClick={() => setEliminar({})}>
              Eliminar
            </button>
            {cliente.tipo === "proveedor" ? (
              <Link to="/inventario/compras/nueva" className="btn btn-p">
                Registrar compra
              </Link>
            ) : (
              <Link to={`/facturas/nueva?cliente=${cliente.id}`} className="btn btn-p">
                Facturar
              </Link>
            )}
          </div>
        )}
      </div>

      <div style={{ display: "flex", flexWrap: "wrap", gap: 16, alignItems: "flex-start" }}>
        <div style={{ flex: "2 1 600px", minWidth: 0, display: "flex", flexDirection: "column", gap: 16 }}>
          <section className="card">
            <div className="card-h">
              <h2>Datos del contacto</h2>
              <div className="seg" role="group" aria-label="Tipo de contacto">
                {(Object.keys(TIPO_CONTACTO_LABEL) as TipoContacto[]).map((t) => (
                  <button key={t} type="button" className={campos.tipo === t ? "on" : ""} onClick={() => set("tipo", t)} aria-pressed={campos.tipo === t}>
                    {t === "ambos" ? "Ambos" : TIPO_CONTACTO_LABEL[t]}
                  </button>
                ))}
              </div>
            </div>
            <div className="card-b" style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 16, alignItems: "start" }}>
              <div className="fld">
                <label className="lbl" htmlFor="c-tid">Tipo de identificación</label>
                <select
                  id="c-tid"
                  className="inp"
                  value={campos.tipoIdentificacion}
                  onChange={(e) => {
                    set("tipoIdentificacion", e.target.value as Campos["tipoIdentificacion"]);
                    setDuplicado(null);
                    setVerificado(null);
                  }}
                >
                  <option value="rnc">RNC (9 dígitos)</option>
                  <option value="cedula">Cédula (11 dígitos)</option>
                  <option value="pasaporte">Pasaporte (extranjero)</option>
                  <option value="">Sin identificación (consumidor final)</option>
                </select>
              </div>
              {campos.tipoIdentificacion && (
                <div className="fld">
                  <label className="lbl" htmlFor="c-rnc">
                    {TIPO_IDENTIFICACION_LABEL[campos.tipoIdentificacion]} <span className="req">*</span>
                  </label>
                  <div style={{ display: "flex", gap: 8 }}>
                    <input
                      id="c-rnc"
                      className="inp mono"
                      value={campos.rnc}
                      onChange={(e) => cambiarRnc(e.target.value)}
                      placeholder={campos.tipoIdentificacion === "pasaporte" ? "Número de pasaporte" : "9 u 11 dígitos"}
                      inputMode={campos.tipoIdentificacion === "pasaporte" ? "text" : "numeric"}
                    />
                    {campos.tipoIdentificacion !== "pasaporte" && (
                      <button type="button" className="btn btn-o" onClick={verificar} disabled={!campos.rnc.trim()}>
                        Verificar
                      </button>
                    )}
                  </div>
                </div>
              )}
              <div className="fld">
                <label className="lbl" htmlFor="c-nom">
                  Nombre o razón social <span className="req">*</span>
                </label>
                <input id="c-nom" className="inp" value={campos.nombre} onChange={(e) => set("nombre", e.target.value)} />
              </div>
              <div className="fld">
                <label className="lbl" htmlFor="c-mail">Correo</label>
                <input id="c-mail" type="email" className="inp" value={campos.correo} onChange={(e) => set("correo", e.target.value)} placeholder="correo@empresa.do" />
              </div>
              <div className="fld">
                <label className="lbl" htmlFor="c-tel">Teléfono / WhatsApp</label>
                <input id="c-tel" type="tel" className="inp" value={campos.telefono} onChange={(e) => set("telefono", e.target.value)} placeholder="809-555-0000" />
              </div>
              <div className="fld" style={{ gridColumn: "1 / -1" }}>
                <label className="lbl" htmlFor="c-dir">Dirección</label>
                <input id="c-dir" className="inp" value={campos.direccion} onChange={(e) => set("direccion", e.target.value)} placeholder="Calle, número, sector, ciudad" />
              </div>
              {duplicado && (
                <div className="al al-w" style={{ gridColumn: "1 / -1" }}>
                  <IconAlerta />
                  <span>
                    Ya existe un contacto con este número: <b>{duplicado.nombre}</b>. <Link to={`/contactos/${duplicado.id}`}>Ver contacto</Link>
                  </span>
                </div>
              )}
              {verificado && (
                <div className="al al-i" style={{ gridColumn: "1 / -1" }}>
                  <span>{verificado}</span>
                </div>
              )}
              {consultandoPadron && <span className="hint" style={{ gridColumn: "1 / -1" }}>Consultando el padrón de la DGII…</span>}
              {padron && padron.rnc === rncParaPadron(campos.rnc) && <PadronResultado resultado={padron} />}
            </div>
          </section>

          <section className="card">
            <div className="card-h">
              <h2>Condiciones de venta</h2>
              <span className="hint">Se aplican al facturarle</span>
            </div>
            <div className="card-b" style={{ display: "flex", flexDirection: "column", gap: 16 }}>
              <div className="fld">
                <span className="lbl" id="c-cond-l">Condición de pago</span>
                <div className="row">
                  <div className="seg" role="group" aria-labelledby="c-cond-l">
                    {CONDICIONES.map((d) => (
                      <button
                        key={d}
                        type="button"
                        className={!condicionLibre && Number(campos.condicionPagoDias) === d ? "on" : ""}
                        onClick={() => set("condicionPagoDias", String(d))}
                        aria-pressed={!condicionLibre && Number(campos.condicionPagoDias) === d}
                      >
                        {d === 0 ? "Contado" : `${d} días`}
                      </button>
                    ))}
                  </div>
                  <input
                    className="inp"
                    type="number"
                    min="0"
                    max="365"
                    step="1"
                    style={{ width: 110 }}
                    value={campos.condicionPagoDias}
                    onChange={(e) => set("condicionPagoDias", e.target.value)}
                    aria-label="Días de crédito"
                  />
                  <span className="hint">días</span>
                </div>
                <span className="hint">
                  {Number(campos.condicionPagoDias) > 0
                    ? `Al facturarle se propondrá venta a crédito con vencimiento a ${campos.condicionPagoDias} días.`
                    : "Al facturarle se propondrá pago de contado."}
                </span>
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 16 }}>
                <div className="fld">
                  <label className="lbl" htmlFor="c-lim">Límite de crédito (RD$)</label>
                  <input
                    id="c-lim"
                    className="inp"
                    type="number"
                    min="0"
                    step="any"
                    value={campos.limiteCredito}
                    onChange={(e) => set("limiteCredito", e.target.value)}
                    placeholder="Sin límite"
                    style={{ textAlign: "right" }}
                  />
                </div>
                <div className="fld">
                  <span className="lbl">Estado</span>
                  <div style={{ display: "flex", alignItems: "center", gap: 12, minHeight: 40 }}>
                    <button
                      type="button"
                      className={campos.activo ? "sw on" : "sw"}
                      onClick={() => set("activo", !campos.activo)}
                      aria-pressed={campos.activo}
                      aria-label="Contacto activo"
                    />
                    <span style={{ fontWeight: 600, color: "#111827" }}>{campos.activo ? "Activo" : "Inactivo"}</span>
                    <span className="hint">{campos.activo ? "Aparece al facturar" : "No aparece al facturar"}</span>
                  </div>
                </div>
              </div>
              <div className="fld">
                <label className="lbl" htmlFor="c-notas">Notas</label>
                <textarea id="c-notas" className="inp" rows={3} value={campos.notas} onChange={(e) => set("notas", e.target.value)} placeholder="Persona de contacto, horario de entrega, acuerdos…" />
              </div>
            </div>
          </section>

          {error && (
            <div className="al al-d" role="alert">
              <IconAlerta />
              <span>{error}</span>
            </div>
          )}

          <div className="row" style={{ justifyContent: "flex-end" }}>
            <Link to="/contactos" className="btn btn-o">
              Cancelar
            </Link>
            <button type="button" className="btn btn-p" onClick={guardar} disabled={guardando || Boolean(duplicado)}>
              {guardando ? "Guardando…" : cliente ? "Guardar cambios" : "Guardar contacto"}
            </button>
          </div>
        </div>

        {cliente && (
          <div style={{ flex: "1 1 320px", minWidth: 0, display: "flex", flexDirection: "column", gap: 16 }}>
            <section className="card">
              <div className="stat">
                <span className="k">Saldo por cobrar</span>
                <span className="v">RD$ {formatoMonto(saldo)}</span>
                <span className="s">
                  {limite > 0
                    ? `Límite de crédito RD$ ${formatoMonto(limite)} · disponible RD$ ${formatoMonto(Math.max(0, limite - saldo))}`
                    : cliente.condicion_pago_dias > 0
                      ? `Crédito a ${cliente.condicion_pago_dias} días, sin límite`
                      : "Cliente de contado"}
                </span>
                {limite > 0 && (
                  <div className={saldo > limite ? "pb bad" : saldo > limite * 0.8 ? "pb warn" : "pb"} style={{ marginTop: 4 }}>
                    <span style={{ width: `${Math.min(100, (saldo / limite) * 100)}%` }} />
                  </div>
                )}
              </div>
            </section>

            {esCliente && (
              <section className="card">
                <div className="card-h">
                  <h2>Facturas a crédito</h2>
                  {cuentas && cuentas.length > 0 && <span className="chip">{cuentas.filter((c) => c.estado !== "cobrada").length} pendientes</span>}
                </div>
                {!cliente.rnc && <p className="pad hint">Sin RNC o cédula: no se pueden asociar facturas a este contacto.</p>}
                {errorCuentas && <p className="pad err-t">No se pudieron consultar las cuentas por cobrar. {errorCuentas}</p>}
                {cliente.rnc && !errorCuentas && cuentas == null && <p className="pad mut">Cargando…</p>}
                {cuentas && cuentas.length === 0 && <p className="pad mut">No tiene facturas a crédito.</p>}
                {cuentas && cuentas.length > 0 && (
                  <div className="tw">
                    <table className="tbl">
                      <thead>
                        <tr>
                          <th>e-NCF</th>
                          <th className="num">Pendiente</th>
                          <th>Estado</th>
                        </tr>
                      </thead>
                      <tbody>
                        {cuentas.map((c) => (
                          <tr key={c.factura_id}>
                            <td>
                              <Link to={`/facturas/${c.factura_id}`} className="mono link">
                                {c.encf ?? "(sin e-NCF)"}
                              </Link>
                              <div className="hint nowrap">
                                {c.estado === "vencida" && c.dias_vencida > 0 ? `Venció hace ${c.dias_vencida} días` : `Vence ${fechaCorta(c.vence)}`}
                              </div>
                            </td>
                            <td className="num" style={{ fontWeight: 600, color: "#111827" }}>
                              {formatoMonto(c.pendiente)}
                              <div className="hint" style={{ fontWeight: 400 }}>de {formatoMonto(c.total)}</div>
                            </td>
                            <td>
                              <span className={ESTADO_CUENTA[c.estado]?.cls ?? "bd b-borr"}>
                                {ESTADO_CUENTA[c.estado]?.label ?? c.estado}
                              </span>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </section>
            )}

            <section className="card">
              <dl className="kv card-b">
                <dt>Estado DGII</dt>
                <dd data-estado-dgii-panel>
                  <EstadoDgii resultado={padron && padron.rnc === rncParaPadron(cliente.rnc ?? "") ? padron : null} pasaporte={cliente.tipo_identificacion === "pasaporte" || !cliente.rnc} />
                </dd>
                <dt>Creado</dt>
                <dd>{new Date(cliente.creado_en).toLocaleString("es-DO")}</dd>
                <dt>Actualizado</dt>
                <dd>{new Date(cliente.actualizado_en).toLocaleString("es-DO")}</dd>
              </dl>
            </section>
          </div>
        )}
      </div>

      {eliminar && cliente && (
        <ModalEliminar
          nombre={cliente.nombre}
          bloqueo={eliminar.bloqueo}
          procesando={eliminando}
          onCancelar={() => setEliminar(null)}
          onEliminar={confirmarEliminar}
          onDesactivar={desactivar}
        />
      )}
      <Toast aviso={aviso} onCerrar={cerrarAviso} />
    </div>
  );
}

/** Resultado del padrón bajo el número: nombre, estado (badge), régimen y aviso si no está activo. */
function PadronResultado({ resultado }: { resultado: ResultadoPadron }) {
  if (resultado.tipo !== "encontrado") {
    return (
      <div className="al al-i" style={{ gridColumn: "1 / -1" }} data-padron="sin-datos">
        <span>{resultado.mensaje}</span>
      </div>
    );
  }
  const p = resultado.contribuyente;
  const estado = estadoPadron(p.estado);
  const regimen = textoRegimen(p.regimen);
  return (
    <div className={`al ${estado.aviso ? "al-w" : "al-s"}`} style={{ gridColumn: "1 / -1" }} data-padron="encontrado">
      {estado.aviso && <IconAlerta />}
      <span>
        <b>Padrón de la DGII:</b> {p.razon_social}
        {p.nombre_comercial && <> ({p.nombre_comercial})</>}{" "}
        <span className={estado.cls} data-estado-dgii>
          {estado.label}
        </span>
        {regimen && <> · Régimen {regimen}</>}
        {p.actividad && <span style={{ display: "block", marginTop: 2 }}>{p.actividad}</span>}
        {estado.aviso && <span style={{ display: "block", marginTop: 4 }}>{estado.aviso}</span>}
      </span>
    </div>
  );
}

function EstadoDgii({ resultado, pasaporte }: { resultado: ResultadoPadron | null; pasaporte: boolean }) {
  if (pasaporte) return <span className="bd b-borr nodot">No aplica</span>;
  if (!resultado) {
    return (
      <span className="tip r" data-tip="Pulse «Verificar» para consultarlo en el padrón de RNC de la DGII.">
        <span className="bd b-borr nodot">Sin verificar</span>
      </span>
    );
  }
  if (resultado.tipo === "no_encontrado") {
    return (
      <span className="tip r" data-tip={resultado.mensaje}>
        <span className="bd b-rech nodot">No aparece</span>
      </span>
    );
  }
  if (resultado.tipo === "no_disponible") {
    return (
      <span className="tip r" data-tip={resultado.mensaje}>
        <span className="bd b-borr nodot">Sin verificar</span>
      </span>
    );
  }
  const estado = estadoPadron(resultado.contribuyente.estado);
  const fecha = fechaPadron(resultado.contribuyente.padron_actualizado_en);
  return (
    <span className="tip r" data-tip={`Padrón de RNC de la DGII${fecha ? ` del ${fecha}` : ""}`}>
      <span className={estado.cls}>{estado.label}</span>
    </span>
  );
}
