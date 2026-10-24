// Portal de clientes (PortalClientes.dc.html): enlace seguro por cliente a su
// historial de e-CF (PDF y XML), su saldo pendiente y los métodos de pago.
// El enlace lleva un token aleatorio que erp-api solo guarda como hash: se
// muestra completo una única vez, al generarlo; se puede revocar y vence.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { erpApi } from "../../lib/erpApi";
import type { Cliente } from "../../lib/erpApi";
import { EMPRESA_NOMBRE, formatoMonto, formatoRnc } from "../../lib/marca";
import { solucionesApi } from "../../lib/solucionesApi";
import type { Canales, ConfigPortal, EnlacePortal, MetodoPagoPortal, PortalPublico } from "../../lib/solucionesApi";
import { AvisoError, IconoBuscar, Modal } from "../ingresos/IngresosShell";
import { useAviso } from "../ingresos/useAviso";
import { enlaceCorreo, enlaceWhatsApp, fechaLegible, mensajeError } from "../ingresos/ingresosUtil";
import { ETIQUETA_METODO, ESTADO_PAGO, urlPortal } from "./portalUtil";
import "./portal.css";

const TODOS_LOS_METODOS: MetodoPagoPortal[] = ["tarjeta", "transferencia", "pago_movil", "deposito"];

const ESTADO_ENLACE: Record<EnlacePortal["estado"], { label: string; badge: string }> = {
  activo: { label: "Activo", badge: "bd b-acep" },
  vencido: { label: "Vencido", badge: "bd b-borr" },
  revocado: { label: "Revocado", badge: "bd b-rech" },
};

const CONFIG_VACIA: ConfigPortal = { banco: "", tipo_cuenta: "", numero_cuenta: "", titular: "", rnc_titular: "", instrucciones: "" };

function mensajeEnlace(cliente: Cliente, url: string): string {
  return (
    `Saludos, ${cliente.nombre}.\n\n` +
    `Desde este enlace puede consultar sus facturas electrónicas (e-CF) con ${EMPRESA_NOMBRE}, descargarlas en PDF y XML ` +
    `y ver su saldo pendiente:\n${url}\n\n¡Gracias por su preferencia!`
  );
}

export function PortalClientesPage() {
  const [q, setQ] = useState("");
  const [sugerencias, setSugerencias] = useState<Cliente[]>([]);
  const [buscando, setBuscando] = useState(false);
  const [cliente, setCliente] = useState<Cliente | null>(null);
  const [metodos, setMetodos] = useState<MetodoPagoPortal[]>(["tarjeta", "transferencia"]);
  const [agregando, setAgregando] = useState(false);
  const [enlaces, setEnlaces] = useState<EnlacePortal[]>([]);
  const [nuevo, setNuevo] = useState<EnlacePortal | null>(null);
  const [copiado, setCopiado] = useState(false);
  const [generando, setGenerando] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [vista, setVista] = useState<PortalPublico | null>(null);
  const [verVista, setVerVista] = useState(false);
  const [config, setConfig] = useState<ConfigPortal>(CONFIG_VACIA);
  const [editarBanco, setEditarBanco] = useState(false);
  const [guardandoBanco, setGuardandoBanco] = useState(false);
  const [enviar, setEnviar] = useState(false);
  const [canales, setCanales] = useState<Canales>({ correo: false, whatsapp: false });
  const [enviandoCorreo, setEnviandoCorreo] = useState(false);
  const [ayuda, setAyuda] = useState(false);
  const [toast, mostrarToast] = useAviso();
  const busqueda = useRef(0);

  useEffect(() => {
    solucionesApi.configPortal().then(
      (c) => setConfig({ ...CONFIG_VACIA, ...c }),
      () => undefined,
    );
    solucionesApi.canales().then(setCanales, () => undefined);
  }, []);

  // Búsqueda de clientes (≥ 3 letras) en Contactos de erp-api.
  useEffect(() => {
    const texto = q.trim();
    if (cliente || texto.length < 3) {
      setSugerencias([]);
      return;
    }
    const n = ++busqueda.current;
    setBuscando(true);
    const t = window.setTimeout(async () => {
      try {
        const r = await erpApi.listarClientes({ q: texto, activo: true });
        if (n === busqueda.current) setSugerencias(r.filter((c) => c.tipo !== "proveedor").slice(0, 8));
      } catch (err) {
        if (n === busqueda.current) setError(mensajeError(err));
      } finally {
        if (n === busqueda.current) setBuscando(false);
      }
    }, 250);
    return () => window.clearTimeout(t);
  }, [q, cliente]);

  const cargarEnlaces = useCallback(async (c: Cliente) => {
    try {
      setEnlaces(await solucionesApi.listarEnlaces({ cliente_id: c.id }));
    } catch (err) {
      setError(mensajeError(err));
    }
  }, []);

  function elegir(c: Cliente) {
    setCliente(c);
    setQ(c.nombre);
    setSugerencias([]);
    setNuevo(null);
    setVista(null);
    setVerVista(false);
    setCopiado(false);
    setError(null);
    void cargarEnlaces(c);
  }

  const url = nuevo?.token ? urlPortal(nuevo.token) : "";
  const activos = enlaces.filter((e) => e.estado === "activo");

  async function generar() {
    if (!cliente) return;
    setGenerando(true);
    setError(null);
    try {
      const e = await solucionesApi.crearEnlace({ cliente_id: cliente.id, metodos_pago: metodos });
      setNuevo(e);
      setCopiado(false);
      setVista(null);
      void cargarEnlaces(cliente);
      mostrarToast("Enlace del portal generado", { detalle: `Vence el ${fechaLegible(e.expira_en)}` });
    } catch (err) {
      setError(mensajeError(err));
    } finally {
      setGenerando(false);
    }
  }

  async function copiar() {
    try {
      await navigator.clipboard.writeText(url);
      setCopiado(true);
      mostrarToast("Enlace copiado al portapapeles");
    } catch {
      mostrarToast("No se pudo copiar", { detalle: "Seleccione el enlace y cópielo manualmente.", error: true });
    }
  }

  async function revocar(e: EnlacePortal) {
    try {
      await solucionesApi.revocarEnlace(e.id);
      if (nuevo?.id === e.id) {
        setNuevo(null);
        setVista(null);
        setVerVista(false);
      }
      if (cliente) void cargarEnlaces(cliente);
      mostrarToast("Enlace revocado", { detalle: "Quien lo tenga ya no podrá abrir el portal." });
    } catch (err) {
      mostrarToast("No se pudo revocar", { detalle: mensajeError(err), error: true });
    }
  }

  async function alternarVista() {
    if (verVista) return setVerVista(false);
    if (!nuevo?.token) return;
    try {
      setVista(await solucionesApi.portalPublico(nuevo.token, true));
      setVerVista(true);
    } catch (err) {
      setError(mensajeError(err));
    }
  }

  async function guardarBanco() {
    setGuardandoBanco(true);
    try {
      setConfig({ ...CONFIG_VACIA, ...(await solucionesApi.guardarConfigPortal(config)) });
      setEditarBanco(false);
      mostrarToast("Datos bancarios guardados", { detalle: "Los verán los clientes que paguen por transferencia." });
    } catch (err) {
      mostrarToast("No se pudieron guardar", { detalle: mensajeError(err), error: true });
    } finally {
      setGuardandoBanco(false);
    }
  }

  async function enviarPorCorreo() {
    if (!cliente?.correo || !url) return;
    setEnviandoCorreo(true);
    try {
      await solucionesApi.enviarCorreo({ para: cliente.correo, asunto: `Sus facturas con ${EMPRESA_NOMBRE}`, mensaje: mensajeEnlace(cliente, url) });
      setEnviar(false);
      mostrarToast(`Enlace enviado a ${cliente.nombre}`, { detalle: cliente.correo });
    } catch (err) {
      mostrarToast("No se pudo enviar el correo", { detalle: mensajeError(err), error: true });
    } finally {
      setEnviandoCorreo(false);
    }
  }

  const opcionesPago = useMemo(() => TODOS_LOS_METODOS.filter((m) => !metodos.includes(m)), [metodos]);
  const sinBanco = metodos.includes("transferencia") && !config.numero_cuenta;
  const mostrarSugerencias = !cliente && q.trim().length >= 3;

  return (
    <div className="content portal" style={{ maxWidth: 1120 }}>
      <div className="ph">
        <div>
          <h1>Portal de clientes</h1>
          <p>
            Envía a tus clientes un enlace a su historial de facturas: desde allí descargan sus e-CF (PDF y XML) y ven lo pendiente. ¡Todo desde un
            solo lugar!
          </p>
        </div>
      </div>

      <section className="card portal-banner">
        <div aria-hidden="true" className="portal-banner-ilus">
          <div>
            <span style={{ width: "60%", background: "#F9A8D4" }} />
            <span />
            <span />
            <span style={{ width: "70%", background: "#14B8A6" }} />
            <span />
          </div>
        </div>
        <div style={{ flex: "1 1 360px", padding: "16px 0" }}>
          <b style={{ color: "#111827" }}>¡Sácale el máximo provecho a las funciones del portal!</b>
          <p style={{ margin: "4px 0 0", fontSize: 13.5, color: "#374151" }}>
            Descubre cómo simplificar tu vida y gestionar las cuentas de tus clientes desde dónde estés.
          </p>
        </div>
        <div className="row" style={{ padding: "12px 0" }}>
          <button type="button" className="btn btn-o btn-sm" onClick={() => setAyuda(true)}>
            Ir a la ayuda
          </button>
        </div>
      </section>

      <section className="card">
        <div className="card-h">
          <h2>Enviar enlace a mis clientes</h2>
        </div>
        <div className="card-b" style={{ display: "flex", flexDirection: "column", gap: 20 }}>
          {error && <AvisoError mensaje={error} />}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))", gap: 20 }}>
            <div className="fld" style={{ position: "relative" }}>
              <label className="lbl" htmlFor="cli">
                Cliente
              </label>
              <label className="tb-search" style={{ maxWidth: "none", flex: "none" }}>
                <IconoBuscar />
                <input
                  id="cli"
                  className="inp"
                  placeholder="Buscar…"
                  value={q}
                  autoComplete="off"
                  onChange={(e) => {
                    setQ(e.target.value);
                    if (cliente && e.target.value !== cliente.nombre) {
                      setCliente(null);
                      setNuevo(null);
                      setEnlaces([]);
                      setVerVista(false);
                    }
                  }}
                  style={{ paddingRight: 12, background: "#fff" }}
                />
              </label>
              <span className="hint">
                {cliente
                  ? `${cliente.rnc ? `RNC/Cédula ${formatoRnc(cliente.rnc)}` : "Sin RNC/cédula"}${cliente.correo ? ` · ${cliente.correo}` : ""} · saldo RD$ ${formatoMonto(cliente.saldo)}`
                  : "Ingresa al menos 3 letras para iniciar la búsqueda"}
              </span>
              {mostrarSugerencias && (
                <div className="pop" style={{ left: 0, right: 0, width: "auto", top: 72 }}>
                  <div className="pop-sec">
                    {sugerencias.map((c) => (
                      <button key={c.id} type="button" className="pop-i" onClick={() => elegir(c)}>
                        <span className="co-av">{c.nombre[0]}</span>
                        <span style={{ flex: 1, textAlign: "left" }}>
                          <b style={{ display: "block", color: "#111827" }}>{c.nombre}</b>
                          <span className="hint">
                            {c.rnc ? `RNC ${formatoRnc(c.rnc)}` : "Sin RNC"}
                            {c.correo ? ` · ${c.correo}` : ""}
                          </span>
                        </span>
                      </button>
                    ))}
                    {!buscando && sugerencias.length === 0 && (
                      <div style={{ padding: "12px 10px", color: "#6B7280" }}>
                        No hay clientes con ese nombre. <Link to="/contactos/nuevo">Crear cliente</Link>
                      </div>
                    )}
                    {buscando && sugerencias.length === 0 && <div style={{ padding: "12px 10px", color: "#6B7280" }}>Buscando…</div>}
                  </div>
                </div>
              )}
            </div>
            <div className="fld">
              <label className="lbl" htmlFor="link">
                Enlace al portal
              </label>
              <div style={{ display: "flex" }}>
                <input
                  id="link"
                  className="inp mono"
                  value={url}
                  readOnly
                  placeholder={cliente ? "Genere un enlace nuevo para este cliente" : ""}
                  style={{ borderRadius: "8px 0 0 8px", background: "#F8FAFC" }}
                />
                {url ? (
                  <button type="button" className="btn btn-o" style={{ borderRadius: "0 8px 8px 0", marginLeft: -1 }} onClick={copiar}>
                    {copiado ? "Copiado" : "Copiar"}
                  </button>
                ) : (
                  <button
                    type="button"
                    className="btn btn-o"
                    style={{ borderRadius: "0 8px 8px 0", marginLeft: -1 }}
                    onClick={generar}
                    disabled={!cliente || generando || !cliente.rnc}
                  >
                    {generando ? "Generando…" : "Generar"}
                  </button>
                )}
              </div>
              <span className="hint">
                {cliente && !cliente.rnc
                  ? "El cliente necesita RNC o cédula: el portal muestra las facturas emitidas a esa identificación."
                  : url
                    ? `Vence el ${fechaLegible(nuevo?.expira_en)}. Por seguridad, el enlace completo solo se muestra ahora.`
                    : "Enlace privado y de solo lectura, válido por 90 días. Puede revocarlo cuando quiera."}
              </span>
            </div>
          </div>

          <div className="fld">
            <span className="lbl">Métodos de pago disponibles</span>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center" }}>
              <button
                type="button"
                className="portal-mas"
                onClick={() => setAgregando(!agregando)}
                aria-label="Agregar método de pago"
                disabled={opcionesPago.length === 0}
              >
                +
              </button>
              {metodos.map((m) => (
                <span key={m} className="chip portal-chip">
                  {ETIQUETA_METODO[m]}
                  <button type="button" onClick={() => setMetodos(metodos.filter((x) => x !== m))} aria-label={`Quitar ${ETIQUETA_METODO[m]}`}>
                    ×
                  </button>
                </span>
              ))}
              {agregando && (
                <div className="row">
                  {opcionesPago.map((m) => (
                    <button
                      key={m}
                      type="button"
                      className="btn btn-o btn-sm"
                      onClick={() => {
                        setMetodos([...metodos, m]);
                        setAgregando(false);
                      }}
                    >
                      + {ETIQUETA_METODO[m]}
                    </button>
                  ))}
                </div>
              )}
            </div>
            <span className="hint">
              {metodos.includes("tarjeta") && "Pago con tarjeta: el portal lo muestra como «Próximamente» (aún no hay pasarela de pago). "}
              Los cambios aplican a los enlaces que genere desde ahora.
            </span>
          </div>

          <div className="portal-banco">
            <div className="row" style={{ justifyContent: "space-between" }}>
              <span>
                <b>Datos para transferencia</b>{" "}
                <span className="hint">
                  {config.numero_cuenta
                    ? `${config.banco ?? ""} · ${config.tipo_cuenta ?? ""} ${config.numero_cuenta} · ${config.titular ?? ""}`
                    : "Sin configurar"}
                </span>
              </span>
              <button type="button" className="btn btn-g btn-sm" onClick={() => setEditarBanco(!editarBanco)}>
                {editarBanco ? "Cerrar" : config.numero_cuenta ? "Editar" : "Configurar"}
              </button>
            </div>
            {sinBanco && !editarBanco && (
              <span className="hint" style={{ color: "#92400E" }}>
                Configure la cuenta bancaria para que sus clientes vean a dónde transferir.
              </span>
            )}
            {editarBanco && (
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 12 }}>
                {(
                  [
                    ["banco", "Banco"],
                    ["tipo_cuenta", "Tipo de cuenta"],
                    ["numero_cuenta", "Número de cuenta"],
                    ["titular", "Titular"],
                    ["rnc_titular", "RNC del titular"],
                    ["instrucciones", "Instrucciones (opcional)"],
                  ] as const
                ).map(([campo, etiqueta]) => (
                  <label key={campo} className="fld">
                    <span className="lbl">{etiqueta}</span>
                    <input className="inp" value={config[campo] ?? ""} onChange={(e) => setConfig({ ...config, [campo]: e.target.value })} />
                  </label>
                ))}
                <div className="row" style={{ alignSelf: "end" }}>
                  <button type="button" className="btn btn-p btn-sm" onClick={guardarBanco} disabled={guardandoBanco}>
                    {guardandoBanco ? "Guardando…" : "Guardar datos bancarios"}
                  </button>
                </div>
              </div>
            )}
          </div>

          <div className="row" style={{ justifyContent: "flex-end" }}>
            <button type="button" className="btn btn-o" onClick={alternarVista} disabled={!url}>
              {verVista ? "Ocultar vista previa" : "Vista previa"}
            </button>
            <button type="button" className="btn btn-p" onClick={() => setEnviar(true)} disabled={!url}>
              Enviar enlace
            </button>
          </div>
        </div>
      </section>

      {verVista && vista && cliente && (
        <section className="card" style={{ borderStyle: "dashed" }}>
          <div className="card-h">
            <h2>Vista previa · lo que verá {cliente.nombre}</h2>
            <span className="chip">Saldo pendiente RD$ {formatoMonto(vista.saldo_pendiente)}</span>
          </div>
          {vista.facturas.length === 0 ? (
            <p className="pad mut">Este cliente aún no tiene e-CF aceptados por la DGII.</p>
          ) : (
            <div className="tw">
              <table className="tbl" style={{ minWidth: 640 }}>
                <thead>
                  <tr>
                    <th>e-NCF</th>
                    <th>Fecha</th>
                    <th className="num">Total (RD$)</th>
                    <th>Estado de pago</th>
                    <th style={{ textAlign: "right" }}>Descargas</th>
                  </tr>
                </thead>
                <tbody>
                  {vista.facturas.map((f) => (
                    <tr key={f.id}>
                      <td className="mono">{f.encf}</td>
                      <td>{fechaLegible(f.fecha_emision)}</td>
                      <td className="num">{formatoMonto(f.total)}</td>
                      <td>
                        <span className={ESTADO_PAGO[f.estado_pago].badge}>{ESTADO_PAGO[f.estado_pago].label}</span>
                      </td>
                      <td style={{ textAlign: "right" }}>
                        <a
                          className="btn btn-g btn-sm"
                          href={solucionesApi.portalPdfUrl(nuevo!.token!, f.id)}
                          target="_blank"
                          rel="noopener noreferrer"
                        >
                          PDF
                        </a>
                        <a className="btn btn-g btn-sm" href={solucionesApi.portalXmlUrl(nuevo!.token!, f.id)}>
                          XML
                        </a>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}

      {cliente && enlaces.length > 0 && (
        <section className="card">
          <div className="card-h">
            <h2>Enlaces de {cliente.nombre}</h2>
            <span className="hint">{activos.length} activo(s)</span>
          </div>
          <div className="tw">
            <table className="tbl" style={{ minWidth: 640 }}>
              <thead>
                <tr>
                  <th>Creado</th>
                  <th>Vence</th>
                  <th>Métodos de pago</th>
                  <th className="num">Accesos</th>
                  <th>Estado</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {enlaces.map((e) => (
                  <tr key={e.id}>
                    <td>{fechaLegible(e.creado_en)}</td>
                    <td>{fechaLegible(e.expira_en)}</td>
                    <td>{e.metodos_pago.map((m) => ETIQUETA_METODO[m]).join(", ") || "—"}</td>
                    <td className="num">{e.accesos}</td>
                    <td>
                      <span className={ESTADO_ENLACE[e.estado].badge}>{ESTADO_ENLACE[e.estado].label}</span>
                    </td>
                    <td style={{ textAlign: "right" }}>
                      {e.estado === "activo" && (
                        <button type="button" className="btn btn-g btn-sm" onClick={() => revocar(e)}>
                          Revocar
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {enviar && cliente && url && (
        <Modal
          titulo={`Enviar enlace a ${cliente.nombre}`}
          onCerrar={() => setEnviar(false)}
          pie={
            <button type="button" className="btn btn-o" onClick={() => setEnviar(false)}>
              Cerrar
            </button>
          }
        >
          <textarea
            className="inp"
            readOnly
            rows={7}
            value={mensajeEnlace(cliente, url)}
            style={{ height: "auto", padding: 10 }}
            aria-label="Mensaje"
          />
          <div className="row">
            <a
              className="btn btn-p"
              href={enlaceWhatsApp(mensajeEnlace(cliente, url), cliente.telefono)}
              target="_blank"
              rel="noopener noreferrer"
              onClick={() => setEnviar(false)}
            >
              WhatsApp{cliente.telefono ? "" : " (elegir contacto)"}
            </a>
            <a
              className="btn btn-o"
              href={enlaceCorreo(`Sus facturas con ${EMPRESA_NOMBRE}`, mensajeEnlace(cliente, url), cliente.correo)}
              onClick={() => setEnviar(false)}
            >
              Abrir mi correo
            </a>
            {canales.correo && cliente.correo && (
              <button type="button" className="btn btn-o" onClick={enviarPorCorreo} disabled={enviandoCorreo}>
                {enviandoCorreo ? "Enviando…" : `Enviar desde Kontara a ${cliente.correo}`}
              </button>
            )}
          </div>
          <span className="hint">WhatsApp y «Abrir mi correo» abren el mensaje listo; usted lo envía desde su teléfono o su correo.</span>
        </Modal>
      )}

      {ayuda && (
        <Modal
          titulo="Cómo funciona el portal de clientes"
          onCerrar={() => setAyuda(false)}
          pie={
            <button type="button" className="btn btn-p" onClick={() => setAyuda(false)}>
              Entendido
            </button>
          }
        >
          <ol style={{ margin: 0, paddingLeft: 18, display: "flex", flexDirection: "column", gap: 6 }}>
            <li>Busque el cliente (debe tener RNC o cédula en Contactos) y elija los métodos de pago que verá.</li>
            <li>Genere el enlace y cópielo o envíelo por WhatsApp o correo. Solo se muestra completo al generarlo.</li>
            <li>El cliente ve sus e-CF aceptados por la DGII, descarga el PDF y el XML y consulta su saldo, sin crear una cuenta.</li>
            <li>El enlace vence a los 90 días; puede revocarlo antes desde la lista de enlaces del cliente.</li>
          </ol>
        </Modal>
      )}
      {toast}
    </div>
  );
}
