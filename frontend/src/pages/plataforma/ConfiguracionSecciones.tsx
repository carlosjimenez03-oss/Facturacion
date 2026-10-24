import { useEffect, useState, type Dispatch, type ReactNode, type SetStateAction } from "react";
import { IconDocumento, IconSubir } from "../../layout/icons";
import { ACTIVIDADES_ECONOMICAS, OFICINA_VIRTUAL_DGII, PROVINCIAS } from "../../lib/catalogosRD";
import {
  configuracionApi,
  useDatosEmpresa,
  type AmbienteApp,
  type Certificado,
  type Empresa,
  type EmpresaPayload,
  type EnvioConfig,
  type EstadoAmbiente,
  type Secuencia,
  type Usuario,
  type Usuarios,
  type Vendedor,
} from "../../lib/configuracionApi";
import { formatoRnc } from "../../lib/marca";
import { sesionActual } from "../../lib/sesion";
import { encf, fechaCorta, mensajeError, numero, type Avisar } from "./configuracionUtil";

export type RefSeccion = (el: HTMLElement | null) => void;

export function Tarjeta({ id, refSeccion, titulo, extra, children }: { id: string; refSeccion: RefSeccion; titulo: string; extra?: ReactNode; children: ReactNode }) {
  return (
    <section id={id} ref={refSeccion} className="card" style={{ scrollMarginTop: 16 }}>
      <div className="card-h">
        <h2>{titulo}</h2>
        {extra}
      </div>
      {children}
    </section>
  );
}

export function Interruptor({ on, onChange, label, disabled }: { on: boolean; onChange: (v: boolean) => void; label: string; disabled?: boolean }) {
  return <button type="button" className={on ? "sw on" : "sw"} onClick={() => onChange(!on)} aria-pressed={on} aria-label={label} disabled={disabled} />;
}

// --- Datos de la empresa ---------------------------------------------------------

export function SeccionEmpresa({
  refSeccion,
  form,
  setForm,
  empresa,
  cargada,
}: {
  refSeccion: RefSeccion;
  form: EmpresaPayload;
  setForm: Dispatch<SetStateAction<EmpresaPayload>>;
  empresa: Empresa | null;
  cargada: boolean;
}) {
  const set = (campo: keyof EmpresaPayload, valor: string | null) => setForm((f) => ({ ...f, [campo]: valor }));
  const rncFijo = Boolean(empresa?.emisor_rnc_configurado);
  const municipios = PROVINCIAS[form.provincia ?? ""] ?? [];
  const [errLogo, setErrLogo] = useState<string | null>(null);

  function elegirLogo(archivo: File | undefined) {
    setErrLogo(null);
    if (!archivo) return;
    if (!/^image\/(png|jpeg|webp|svg\+xml)$/.test(archivo.type)) return setErrLogo("Use una imagen PNG, JPEG, WebP o SVG.");
    if (archivo.size > 256 * 1024) return setErrLogo("El logo debe pesar 256 KB como máximo.");
    const lector = new FileReader();
    lector.onload = () => set("logo", String(lector.result));
    lector.readAsDataURL(archivo);
  }

  return (
    <Tarjeta
      id="empresa"
      refSeccion={refSeccion}
      titulo="Datos de la empresa"
      extra={empresa && !empresa.configurada ? <span className="bd b-proc nodot">Sin guardar</span> : undefined}
    >
      <fieldset className="card-b cfg-grid" disabled={!cargada} style={{ border: 0, margin: 0, minWidth: 0 }}>
        <div className="fld">
          <label className="lbl" htmlFor="cfg-rs">Razón social <span className="req">*</span></label>
          <input id="cfg-rs" className="inp" value={form.razon_social} onChange={(e) => set("razon_social", e.target.value)} />
        </div>
        <div className="fld">
          <label className="lbl" htmlFor="cfg-nc">Nombre comercial</label>
          <input id="cfg-nc" className="inp" value={form.nombre_comercial ?? ""} onChange={(e) => set("nombre_comercial", e.target.value)} />
        </div>
        <div className="fld">
          <label className="lbl" htmlFor="cfg-rnc">RNC</label>
          <input
            id="cfg-rnc"
            className="inp mono"
            value={rncFijo ? formatoRnc(form.rnc) : (form.rnc ?? "")}
            onChange={(e) => set("rnc", e.target.value)}
            disabled={rncFijo}
            title={rncFijo ? "Es el RNC del emisor autorizado (variable EMISOR_RNC del despliegue)" : undefined}
          />
        </div>
        <div className="fld">
          <label className="lbl" htmlFor="cfg-act">Actividad económica</label>
          <input id="cfg-act" className="inp" list="cfg-actividades" value={form.actividad_economica ?? ""} onChange={(e) => set("actividad_economica", e.target.value)} placeholder="Seleccione o escriba" />
          <datalist id="cfg-actividades">
            {ACTIVIDADES_ECONOMICAS.map((a) => (
              <option key={a} value={a} />
            ))}
          </datalist>
        </div>
        <div className="fld cfg-full">
          <label className="lbl" htmlFor="cfg-dir">Dirección fiscal</label>
          <input id="cfg-dir" className="inp" value={form.direccion ?? ""} onChange={(e) => set("direccion", e.target.value)} placeholder="Calle, número, sector" />
        </div>
        <div className="fld">
          <label className="lbl" htmlFor="cfg-prov">Provincia</label>
          <select
            id="cfg-prov"
            className="inp"
            value={form.provincia ?? ""}
            onChange={(e) => setForm((f) => ({ ...f, provincia: e.target.value, municipio: PROVINCIAS[e.target.value]?.[0] ?? "" }))}
          >
            <option value="">Seleccione…</option>
            {Object.keys(PROVINCIAS).map((p) => (
              <option key={p}>{p}</option>
            ))}
          </select>
        </div>
        <div className="fld">
          <label className="lbl" htmlFor="cfg-mun">Municipio</label>
          <input id="cfg-mun" className="inp" list="cfg-municipios" value={form.municipio ?? ""} onChange={(e) => set("municipio", e.target.value)} />
          <datalist id="cfg-municipios">
            {municipios.map((m) => (
              <option key={m} value={m} />
            ))}
          </datalist>
        </div>
        <div className="fld">
          <label className="lbl" htmlFor="cfg-tel">Teléfono</label>
          <input id="cfg-tel" className="inp" type="tel" value={form.telefono ?? ""} onChange={(e) => set("telefono", e.target.value)} placeholder="809-555-0110" />
        </div>
        <div className="fld">
          <label className="lbl" htmlFor="cfg-em">Correo de facturación</label>
          <input id="cfg-em" className="inp" type="email" value={form.correo ?? ""} onChange={(e) => set("correo", e.target.value)} />
        </div>
        <div className="fld cfg-full">
          <span className="lbl">Logo (opcional, sale en la representación impresa)</span>
          <div className="cfg-acciones">
            {form.logo && <img src={form.logo} alt="Logo de la empresa" style={{ height: 40, maxWidth: 160, objectFit: "contain", border: "1px solid #E5E7EB", borderRadius: 8, padding: 4 }} />}
            <label className="btn btn-o btn-sm">
              {form.logo ? "Cambiar logo" : "Subir logo"}
              <input type="file" accept="image/png,image/jpeg,image/webp,image/svg+xml" hidden onChange={(e) => elegirLogo(e.target.files?.[0])} />
            </label>
            {form.logo && (
              <button type="button" className="btn btn-g btn-sm" onClick={() => set("logo", null)}>
                Quitar
              </button>
            )}
            {errLogo && <span className="err-t">{errLogo}</span>}
          </div>
        </div>
      </fieldset>
    </Tarjeta>
  );
}

// --- Certificado digital ---------------------------------------------------------

function badgeCertificado(c: Certificado): { cls: string; txt: string } {
  if (c.estado === "vigente") return { cls: "bd b-acep", txt: `Vigente · vence en ${numero(c.dias_restantes)} días` };
  if (c.estado === "por_vencer") return { cls: "bd b-proc", txt: `Vence en ${numero(c.dias_restantes)} días` };
  if (c.estado === "vencido") return { cls: "bd b-rech", txt: "Vencido" };
  if (c.estado === "error") return { cls: "bd b-rech", txt: "No se pudo abrir" };
  return { cls: "bd b-borr", txt: "Sin certificado" };
}

export function SeccionCertificado({ refSeccion, avisar }: { refSeccion: RefSeccion; avisar: Avisar }) {
  const [cert, setCert] = useState<Certificado | null>(null);
  const [archivo, setArchivo] = useState<File | null>(null);
  const [clave, setClave] = useState("");
  const [arrastrando, setArrastrando] = useState(false);
  const [validando, setValidando] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);

  useEffect(() => {
    configuracionApi.certificado().then(setCert).catch((err) => setError(mensajeError(err)));
  }, []);

  function tomar(f: File | undefined | null) {
    setOk(null);
    if (!f) return;
    if (!/\.(p12|pfx)$/i.test(f.name)) {
      setArchivo(null);
      setError(`El archivo «${f.name}» no es un certificado. Seleccione un archivo .p12 o .pfx.`);
      return;
    }
    setError(null);
    setArchivo(f);
  }

  async function cargar() {
    if (!archivo) return;
    if (clave.length < 4) return setError("Escriba la contraseña del certificado (mínimo 4 caracteres).");
    setValidando(true);
    setError(null);
    try {
      const nuevo = await configuracionApi.cargarCertificado(archivo, clave);
      setCert(nuevo);
      setArchivo(null);
      setClave("");
      setOk(`Certificado validado: titular ${nuevo.titular ?? "—"}${nuevo.rnc ? ` (${nuevo.rnc})` : ""}. Ya se usa para firmar los nuevos e-CF.`);
      avisar("Nuevo certificado cargado y activo");
    } catch (err) {
      setError(mensajeError(err));
    } finally {
      setValidando(false);
    }
  }

  const b = cert ? badgeCertificado(cert) : null;
  const pct = cert?.vigencia_consumida_pct ?? 0;
  const tieneDatos = cert && (cert.estado === "vigente" || cert.estado === "por_vencer" || cert.estado === "vencido");

  return (
    <Tarjeta id="certificado" refSeccion={refSeccion} titulo="Certificado digital para firma" extra={b && <span className={b.cls}>{b.txt}</span>}>
      <div className="card-b cfg-row">
        <div className="cfg-box">
          <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
            <span className="cfg-ic">
              <IconDocumento />
            </span>
            <div style={{ minWidth: 0 }}>
              <div className="mono" style={{ fontWeight: 600, color: "#111827", overflowWrap: "anywhere" }}>{cert?.archivo ?? "—"}</div>
              <div className="hint">
                Certificado de persona jurídica para e-CF
                {cert?.origen === "cargado" ? " · cargado desde aquí" : cert?.origen === "variables" ? " · certs/emisor.p12 del servidor" : ""}
              </div>
            </div>
          </div>
          {tieneDatos ? (
            <>
              <dl className="kv">
                <dt>Titular</dt>
                <dd>{cert.titular ?? "—"}</dd>
                <dt>Firmante</dt>
                <dd>{[cert.firmante, cert.documento_firmante].filter(Boolean).join(" · ") || "—"}</dd>
                <dt>Emitido por</dt>
                <dd>{cert.emisor ?? "—"}</dd>
                <dt>Válido desde</dt>
                <dd>{fechaCorta(cert.valido_desde)}</dd>
                <dt>Vence</dt>
                <dd style={{ color: cert.estado === "vigente" ? "#166534" : cert.estado === "por_vencer" ? "#92400E" : "#B91C1C", fontWeight: 650 }}>{fechaCorta(cert.vence)}</dd>
              </dl>
              <div className={pct >= 90 ? "pb bad" : pct >= 75 ? "pb warn" : "pb"}>
                <span style={{ width: `${pct}%` }} />
              </div>
              <span className="hint">{pct}% de la vigencia consumida · Recordatorios activos a 60, 30 y 7 días</span>
              {cert.coincide_rnc_emisor === false && (
                <div className="al al-w">
                  <span>El certificado no parece ser del RNC del emisor. La DGII rechazará los e-CF firmados con él.</span>
                </div>
              )}
            </>
          ) : (
            <div className={cert?.estado === "error" ? "al al-d" : "al al-i"}>
              <span>{cert?.error ?? "Consultando el certificado…"}</span>
            </div>
          )}
        </div>

        <div style={{ flex: "1 1 280px", display: "flex", flexDirection: "column", gap: 12, minWidth: 0 }}>
          <label
            className={`cfg-drop${arrastrando ? " drag" : archivo ? " lleno" : ""}`}
            onDragOver={(e) => {
              e.preventDefault();
              setArrastrando(true);
            }}
            onDragLeave={() => setArrastrando(false)}
            onDrop={(e) => {
              e.preventDefault();
              setArrastrando(false);
              tomar(e.dataTransfer.files?.[0]);
            }}
          >
            <input type="file" accept=".p12,.pfx" onChange={(e) => tomar(e.target.files?.[0])} aria-label="Seleccionar certificado .p12" />
            <span className="cfg-ic">
              <IconSubir />
            </span>
            <span style={{ fontWeight: 600, color: "#111827" }}>{archivo ? archivo.name : "Cargar nuevo certificado .p12"}</span>
            <span className="hint">{archivo ? `${(archivo.size / 1024).toFixed(1)} KB · listo para validar` : "Arrastre el archivo aquí o haga clic para seleccionarlo"}</span>
          </label>
          <div className="fld">
            <label className="lbl" htmlFor="cfg-pw">Contraseña del certificado</label>
            <input id="cfg-pw" type="password" className="inp" placeholder="••••••••" value={clave} autoComplete="off" onChange={(e) => { setClave(e.target.value); setError(null); }} />
          </div>
          {error && (
            <div className="al al-d" role="alert">
              <span>{error}</span>
            </div>
          )}
          {ok && (
            <div className="al al-s" role="status">
              <span>{ok}</span>
            </div>
          )}
          {cert && !cert.carga_disponible && (
            <div className="al al-w">
              <span>
                Para cargar certificados desde aquí falta configurar <code>CERTIFICADOS_CLAVE</code> (32 caracteres o más) en Dokploy (ver <code>docs/AUTH.md</code>).
              </span>
            </div>
          )}
          <div className="row">
            <button type="button" className="btn btn-p" onClick={() => void cargar()} disabled={!archivo || validando || !cert?.carga_disponible}>
              {validando ? "Validando…" : "Validar y cargar certificado"}
            </button>
            {archivo && (
              <button type="button" className="btn btn-g" onClick={() => setArchivo(null)}>
                Quitar archivo
              </button>
            )}
          </div>
          <span className="hint">La contraseña se cifra y nunca se muestra. El certificado anterior seguirá activo hasta que el nuevo sea validado.</span>
        </div>
      </div>
    </Tarjeta>
  );
}

// --- Ambiente DGII ---------------------------------------------------------

export const AMBIENTES = [
  { label: "Pruebas", app: "test", desc: "TesteCF · simulación sin validez fiscal" },
  { label: "Certificación", app: "qa", desc: "CerteCF · proceso para ser emisor electrónico" },
  { label: "Producción", app: "production", desc: "eCF · comprobantes con validez fiscal" },
] as const satisfies readonly { label: string; app: AmbienteApp; desc: string }[];

type OpcionAmbiente = (typeof AMBIENTES)[number];

const etiquetaAmbiente = (app: string | null | undefined) => AMBIENTES.find((a) => a.app === app)?.label ?? app ?? "—";

function fechaHora(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString("es-DO", { dateStyle: "short", timeStyle: "short" });
}

/**
 * Configuración → Ambiente DGII (PUT /configuracion/ambiente, solo administradores). El cambio llega a
 * facturas-api, recepcion y las consultas en unos segundos; a producción pide confirmar que los
 * comprobantes pasan a tener validez fiscal y exige un certificado vigente. Bloqueado mientras haya
 * facturas en contingencia o lotes enviándose.
 */
export function SeccionAmbiente({ refSeccion, avisar, alCambiar }: { refSeccion: RefSeccion; avisar: Avisar; alCambiar: () => void }) {
  const [estado, setEstado] = useState<EstadoAmbiente | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pedido, setPedido] = useState<OpcionAmbiente | null>(null);
  const [confirmado, setConfirmado] = useState(false);
  const [errorCambio, setErrorCambio] = useState<string | null>(null);
  const [cambiando, setCambiando] = useState(false);

  const cargar = () =>
    configuracionApi
      .ambiente()
      .then((e) => {
        setEstado(e);
        setError(null);
      })
      .catch((err) => setError(mensajeError(err)));
  useEffect(() => {
    void cargar();
  }, []);

  const actual = estado?.ambiente;
  const bloqueado = Boolean(estado && estado.bloqueos.length > 0);
  const sinPermiso = estado?.motivo_sin_permiso ?? null;

  function abrir(a: OpcionAmbiente) {
    if (!estado || a.app === actual) return;
    setPedido(a);
    setConfirmado(false);
    setErrorCambio(null);
    void cargar(); // bloqueos y certificado al momento de decidir
  }

  async function cambiar() {
    if (!pedido) return;
    setCambiando(true);
    setErrorCambio(null);
    try {
      const nuevo = await configuracionApi.cambiarAmbiente(pedido.app, confirmado);
      setEstado(nuevo);
      setPedido(null);
      alCambiar();
      avisar(`Ambiente cambiado a ${pedido.label}`, { detalle: nuevo.avisos.join(" ") || undefined });
    } catch (err) {
      setErrorCambio(mensajeError(err));
    } finally {
      setCambiando(false);
    }
  }

  const aProduccion = pedido?.app === "production";
  const certificadoInvalido = aProduccion && estado && !estado.certificado.valido;
  const sinSecuencias = pedido && estado && !estado.secuencias[pedido.app];
  const puedeConfirmar = Boolean(pedido && estado && !bloqueado && !sinPermiso && !certificadoInvalido && (!aProduccion || confirmado));

  return (
    <Tarjeta id="ambiente" refSeccion={refSeccion} titulo="Ambiente DGII" extra={<span className="hint">Define a qué servicio web se envían los e-CF</span>}>
      <div className="card-b stack">
        {error && (
          <div className="al al-d" role="alert">
            <span>{error}</span>
          </div>
        )}
        <div className="amb" role="radiogroup" aria-label="Ambiente DGII">
          {AMBIENTES.map((a) => (
            <button
              key={a.app}
              type="button"
              role="radio"
              aria-checked={a.app === actual}
              className={a.app === actual ? "on" : ""}
              disabled={!estado || (a.app !== actual && Boolean(sinPermiso))}
              onClick={() => abrir(a)}
            >
              <span style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <b>{a.label}</b>
                <span className="dot" />
              </span>
              <small>{a.desc}</small>
            </button>
          ))}
        </div>
        {estado && actual !== "production" && (
          <div className="al al-w">
            <span>
              <b>Los comprobantes emitidos en {etiquetaAmbiente(actual)} no tienen validez fiscal.</b> Use este ambiente solo para pruebas o para el
              proceso de certificación como emisor electrónico.
            </span>
          </div>
        )}
        {estado && estado.bloqueos.length > 0 && (
          <div className="al al-d" role="status">
            <span>
              <b>No se puede cambiar el ambiente ahora.</b> {estado.bloqueos.join(" ")}
            </span>
          </div>
        )}
        {sinPermiso && <span className="hint">{sinPermiso}</span>}
        {estado && (
          <span className="hint">
            {estado.origen === "configuracion" ? (
              <>
                Elegido aquí{estado.cambiado_por ? ` por ${estado.cambiado_por}` : ""} el {fechaHora(estado.cambiado_en)} (variable del despliegue:{" "}
                <code>APP_ENV={estado.app_env}</code>).
              </>
            ) : (
              <>
                Tomado de la variable del despliegue: <code>APP_ENV={estado.app_env}</code> ({estado.ambiente_dgii}). Al cambiarlo aquí, esta pantalla manda.
              </>
            )}{" "}
            Las secuencias e-NCF son distintas en cada ambiente; las facturas conservan el ambiente en que se crearon.
          </span>
        )}
        {estado && estado.cambios.length > 0 && (
          <div className="tw">
            <table className="tbl" aria-label="Cambios de ambiente">
              <thead>
                <tr>
                  <th>Fecha</th>
                  <th>Cambio</th>
                  <th>Usuario</th>
                </tr>
              </thead>
              <tbody>
                {estado.cambios.map((c) => (
                  <tr key={c.id}>
                    <td className="mut">{fechaHora(c.creado_en)}</td>
                    <td>
                      {etiquetaAmbiente(c.anterior)} → <b>{etiquetaAmbiente(c.nuevo)}</b>
                    </td>
                    <td className="mut">{c.usuario_correo ?? "Sin inicio de sesión"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
      {pedido && estado && (
        <div className="scrim" role="presentation" onClick={() => !cambiando && setPedido(null)}>
          <div className="modal" role="dialog" aria-modal="true" aria-labelledby="amb-t" onClick={(e) => e.stopPropagation()}>
            <div className="modal-h">
              <h3 id="amb-t">Cambiar a {pedido.label}</h3>
            </div>
            <div className="modal-b">
              <p style={{ margin: 0 }}>
                Los e-CF se enviarán a <b>{pedido.desc.split(" · ")[0]}</b> en lugar de {AMBIENTES.find((a) => a.app === actual)?.desc.split(" · ")[0] ?? estado.ambiente_dgii}. Las
                facturas que ya existen conservan su ambiente: las enviadas se siguen consultando donde se enviaron y las que no se han enviado no
                podrán enviarse (su e-NCF es de la secuencia del ambiente anterior).
              </p>
              {aProduccion && (
                <div className="al al-d">
                  <span>
                    <b>Comprobantes con validez fiscal.</b> {estado.aviso_produccion}
                  </span>
                </div>
              )}
              {certificadoInvalido && (
                <div className="al al-d" role="alert">
                  <span>{estado.certificado.motivo}</span>
                </div>
              )}
              {sinSecuencias && (
                <div className="al al-w">
                  <span>
                    {pedido.label} no tiene secuencias e-NCF registradas: regístrelas en <b>Secuencias autorizadas</b> (eligiendo ese ambiente) antes de
                    facturar.
                  </span>
                </div>
              )}
              {estado.bloqueos.length > 0 && (
                <div className="al al-d" role="alert">
                  <span>{estado.bloqueos.join(" ")}</span>
                </div>
              )}
              {aProduccion && !certificadoInvalido && !bloqueado && (
                <label style={{ display: "flex", gap: 10, alignItems: "flex-start", cursor: "pointer" }}>
                  <input type="checkbox" checked={confirmado} onChange={(e) => setConfirmado(e.target.checked)} style={{ accentColor: "#0F766E", marginTop: 3 }} />
                  <span>Entiendo que desde ahora los comprobantes que emita son fiscalmente válidos ante la DGII.</span>
                </label>
              )}
              {errorCambio && (
                <div className="al al-d" role="alert">
                  <span>{errorCambio}</span>
                </div>
              )}
            </div>
            <div className="modal-f">
              <button type="button" className="btn btn-g" onClick={() => setPedido(null)} disabled={cambiando}>
                Cancelar
              </button>
              <button type="button" className={aProduccion ? "btn btn-d" : "btn btn-p"} onClick={() => void cambiar()} disabled={!puedeConfirmar || cambiando}>
                {cambiando ? "Cambiando…" : `Cambiar a ${pedido.label}`}
              </button>
            </div>
          </div>
        </div>
      )}
    </Tarjeta>
  );
}

// --- Secuencias autorizadas ---------------------------------------------------------

interface FormSecuencia {
  desde: string;
  hasta: string;
  proximo: string;
  vence: string;
  activa: boolean;
}

function formDe(s: Secuencia): FormSecuencia {
  return {
    desde: String(s.desde ?? 1),
    hasta: String(s.hasta ?? ""),
    proximo: s.configurada && s.proximo != null && s.proximo !== s.desde ? String(s.proximo) : "",
    vence: s.fecha_vencimiento ?? "",
    activa: s.configurada ? s.activa : true,
  };
}

export function SeccionSecuencias({ refSeccion, avisar, ambienteActivo }: { refSeccion: RefSeccion; avisar: Avisar; ambienteActivo?: AmbienteApp }) {
  const [seqs, setSeqs] = useState<Secuencia[]>([]);
  /** Las secuencias son por ambiente: por defecto las del activo; se pueden registrar las de otro (p. ej. producción antes de cambiar). */
  const [ambiente, setAmbiente] = useState<AmbienteApp | undefined>(ambienteActivo);
  const [error, setError] = useState<string | null>(null);
  const [editando, setEditando] = useState<number | null>(null);
  const [form, setForm] = useState<FormSecuencia | null>(null);
  const [solicitar, setSolicitar] = useState(false);

  useEffect(() => {
    setAmbiente(ambienteActivo);
  }, [ambienteActivo]);
  const cargar = () => configuracionApi.secuencias(ambiente).then(setSeqs).catch((err) => setError(mensajeError(err)));
  useEffect(() => {
    setEditando(null);
    void cargar();
  }, [ambiente]);

  async function guardar(tipo: number) {
    if (!form) return;
    try {
      const s = await configuracionApi.guardarSecuencia(tipo, {
        desde: Number(form.desde),
        hasta: Number(form.hasta),
        proximo: form.proximo ? Number(form.proximo) : null,
        fecha_vencimiento: form.vence || null,
        activa: form.activa,
      }, ambiente);
      setSeqs((l) => l.map((x) => (x.tipo_ecf === tipo ? s : x)));
      setEditando(null);
      avisar(`Secuencia E${tipo} guardada`, { detalle: s.siguiente_encf ? `Próximo e-NCF: ${s.siguiente_encf}` : undefined });
    } catch (err) {
      avisar("No se pudo guardar la secuencia", { detalle: mensajeError(err), error: true });
    }
  }

  return (
    <Tarjeta
      id="secuencias"
      refSeccion={refSeccion}
      titulo="Secuencias autorizadas"
      extra={
        <div className="row" style={{ gap: 8 }}>
          <select
            className="inp"
            style={{ width: "auto", height: 32, padding: "0 10px" }}
            aria-label="Ambiente de las secuencias"
            value={ambiente ?? seqs[0]?.ambiente ?? ""}
            onChange={(e) => setAmbiente(e.target.value as AmbienteApp)}
          >
            {AMBIENTES.map((a) => (
              <option key={a.app} value={a.app}>
                {a.label} ({a.desc.split(" · ")[0]}){a.app === ambienteActivo ? " · activo" : ""}
              </option>
            ))}
          </select>
          <button type="button" className="btn btn-o btn-sm" onClick={() => setSolicitar((v) => !v)}>
            {solicitar ? "Cerrar" : "Solicitar secuencias"}
          </button>
        </div>
      }
    >
      {solicitar && (
        <div className="cfg-seq-form" style={{ gridTemplateColumns: "1fr auto" }}>
          <span style={{ fontSize: 13.5, color: "#374151" }}>
            La DGII autoriza los rangos de e-NCF en la <b>Oficina Virtual</b> (Solicitud de autorización de secuencias de e-CF). Responde en
            24 a 48 horas; cuando la apruebe, registre aquí el rango y la fecha de vencimiento con <b>Editar</b>.
          </span>
          <a className="btn btn-p" href={OFICINA_VIRTUAL_DGII} target="_blank" rel="noopener noreferrer">
            Abrir Oficina Virtual
          </a>
        </div>
      )}
      {error && (
        <div className="al al-d" style={{ margin: 16 }}>
          <span>{error}</span>
        </div>
      )}
      <div className="tw">
        <table className="tbl" style={{ minWidth: 860 }}>
          <thead>
            <tr>
              <th>Tipo de e-CF</th>
              <th>Rango autorizado</th>
              <th className="num">Usados</th>
              <th className="num">Disponibles</th>
              <th style={{ width: "20%" }}>Consumo</th>
              <th>Vence</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {seqs.map((s) => {
              const pct = s.consumo_pct;
              const color = pct >= 90 ? "#B91C1C" : pct >= 75 ? "#B45309" : "#111827";
              const abierta = editando === s.tipo_ecf && form;
              return [
                <tr key={s.tipo_ecf}>
                  <td>
                    <span className="chip mono" style={{ marginRight: 8 }}>E{s.tipo_ecf}</span>
                    {s.nombre}
                    {s.avisos.length > 0 && s.configurada && (
                      <div className="row-motivo" style={{ color: s.estado === "por_vencer" || s.estado === "por_agotarse" ? "#B45309" : "#B91C1C" }}>
                        {s.avisos.join(" ")}
                      </div>
                    )}
                  </td>
                  <td className="mono mut">{s.configurada ? `${encf(s.tipo_ecf, s.desde ?? 1)} – ${encf(s.tipo_ecf, s.hasta ?? 0)}` : "Sin registrar"}</td>
                  <td className="num">{s.configurada ? numero(s.usados) : "—"}</td>
                  <td className="num" style={{ fontWeight: 600, color }}>{s.configurada ? numero(s.disponibles) : "—"}</td>
                  <td>
                    {s.configurada ? (
                      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                        <div className={pct >= 90 ? "pb bad" : pct >= 75 ? "pb warn" : "pb"} style={{ flex: 1 }}>
                          <span style={{ width: `${Math.min(pct, 100)}%` }} />
                        </div>
                        <span className="hint" style={{ width: 46, textAlign: "right" }}>{pct < 10 ? pct.toFixed(1) : Math.round(pct)}%</span>
                      </div>
                    ) : (
                      <span className="hint">—</span>
                    )}
                  </td>
                  <td className="mut">
                    {s.configurada ? fechaCorta(s.fecha_vencimiento) : "—"}
                    {s.configurada && !s.activa && <span className="bd b-borr nodot" style={{ marginLeft: 6 }}>Inactiva</span>}
                  </td>
                  <td className="right">
                    <button
                      type="button"
                      className="btn btn-g btn-sm"
                      onClick={() => {
                        setEditando(abierta ? null : s.tipo_ecf);
                        setForm(formDe(s));
                      }}
                    >
                      {s.configurada ? "Editar" : "Registrar"}
                    </button>
                  </td>
                </tr>,
                abierta && form ? (
                  <tr key={`${s.tipo_ecf}-f`}>
                    <td colSpan={7} style={{ padding: 0 }}>
                      <div className="cfg-seq-form">
                        <div className="fld">
                          <label className="lbl" htmlFor={`sd-${s.tipo_ecf}`}>Desde (número)</label>
                          <input id={`sd-${s.tipo_ecf}`} className="inp mono" inputMode="numeric" value={form.desde} onChange={(e) => setForm({ ...form, desde: e.target.value.replace(/\D/g, "") })} />
                        </div>
                        <div className="fld">
                          <label className="lbl" htmlFor={`sh-${s.tipo_ecf}`}>Hasta (número)</label>
                          <input id={`sh-${s.tipo_ecf}`} className="inp mono" inputMode="numeric" value={form.hasta} onChange={(e) => setForm({ ...form, hasta: e.target.value.replace(/\D/g, "") })} />
                        </div>
                        <div className="fld">
                          <label className="lbl" htmlFor={`sp-${s.tipo_ecf}`}>Seguir desde (opcional)</label>
                          <input id={`sp-${s.tipo_ecf}`} className="inp mono" inputMode="numeric" value={form.proximo} placeholder={String(s.siguiente ?? "")} onChange={(e) => setForm({ ...form, proximo: e.target.value.replace(/\D/g, "") })} />
                        </div>
                        <div className="fld">
                          <label className="lbl" htmlFor={`sv-${s.tipo_ecf}`}>Vence{s.tipo_ecf === 32 || s.tipo_ecf === 34 ? " (opcional)" : ""}</label>
                          <input id={`sv-${s.tipo_ecf}`} type="date" className="inp" value={form.vence} onChange={(e) => setForm({ ...form, vence: e.target.value })} />
                        </div>
                        <div className="fld" style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
                          <Interruptor on={form.activa} onChange={(v) => setForm({ ...form, activa: v })} label="Secuencia activa" />
                          <span>Activa</span>
                        </div>
                        <div className="cfg-acciones">
                          <button type="button" className="btn btn-p btn-sm" onClick={() => void guardar(s.tipo_ecf)} disabled={!form.desde || !form.hasta}>
                            Guardar
                          </button>
                          <button type="button" className="btn btn-g btn-sm" onClick={() => setEditando(null)}>
                            Cancelar
                          </button>
                        </div>
                        <span className="hint" style={{ gridColumn: "1 / -1" }}>
                          {form.desde && form.hasta
                            ? `Rango ${encf(s.tipo_ecf, Number(form.desde))} – ${encf(s.tipo_ecf, Number(form.hasta))}. `
                            : ""}
                          El formulario de factura toma de aquí el próximo e-NCF y la fecha de vencimiento de la secuencia.
                        </span>
                      </div>
                    </td>
                  </tr>
                ) : null,
              ];
            })}
          </tbody>
        </table>
      </div>
    </Tarjeta>
  );
}

// --- Método de inventario ---------------------------------------------------------

export function SeccionInventario({ refSeccion, metodo, setMetodo }: { refSeccion: RefSeccion; metodo: "promedio" | "peps"; setMetodo: (m: "promedio" | "peps") => void }) {
  return (
    <Tarjeta id="inventario" refSeccion={refSeccion} titulo="Método de valuación de inventario">
      <div className="card-b stack-sm">
        <div className="seg" role="group" aria-label="Método de inventario">
          <button type="button" className={metodo === "promedio" ? "on" : ""} onClick={() => setMetodo("promedio")}>
            Promedio ponderado
          </button>
          <button type="button" className={metodo === "peps" ? "on" : ""} onClick={() => setMetodo("peps")}>
            PEPS (primeras entradas, primeras salidas)
          </button>
        </div>
        <span className="hint">
          El método debe aplicarse de forma consistente en todo el ejercicio fiscal. Un cambio aplica a partir del próximo ejercicio y requiere
          autorización de la DGII.
        </span>
        {metodo === "peps" && (
          <span className="hint">
            Hoy el costo de ventas del inventario se calcula por promedio ponderado; PEPS queda guardado como preferencia de presentación del Kardex.
          </span>
        )}
      </div>
    </Tarjeta>
  );
}

// --- WhatsApp y correo ---------------------------------------------------------

export function SeccionEnvio({ refSeccion, avisar, empresa }: { refSeccion: RefSeccion; avisar: Avisar; empresa: Empresa | null }) {
  const [cfg, setCfg] = useState<EnvioConfig | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [correo, setCorreo] = useState({ host: "", puerto: 587, seguridad: "starttls" as "starttls" | "ssl" | "ninguna", usuario: "", contrasena: "", remitente: "", cco: "", adjuntar_xml: true, envio_automatico: false });
  const [wa, setWa] = useState({ phone_number_id: "", numero: "", token: "", plantilla: "hello_world", idioma: "en_US", envio_automatico: false, plantilla_mensajes: "", idioma_mensajes: "es" });
  const [pruebaWa, setPruebaWa] = useState("");
  const [ocupado, setOcupado] = useState("");

  function aplicar(c: EnvioConfig) {
    setCfg(c);
    setCorreo((f) => ({ ...f, ...c.correo, contrasena: "" }));
    setWa((f) => ({ ...f, ...c.whatsapp, token: "" }));
  }

  useEffect(() => {
    configuracionApi.envio().then(aplicar).catch((err) => setError(mensajeError(err)));
  }, []);

  async function accion(nombre: string, fn: () => Promise<unknown>, okTexto: string) {
    setOcupado(nombre);
    try {
      const r = await fn();
      if (r && typeof r === "object" && "correo" in r) aplicar(r as EnvioConfig);
      avisar(okTexto, r && typeof r === "object" && "mensaje" in r ? { detalle: String((r as { mensaje: string }).mensaje) } : undefined);
    } catch (err) {
      avisar("No se pudo completar", { detalle: mensajeError(err), error: true });
    } finally {
      setOcupado("");
    }
  }

  const guardarCorreo = (cambios: Partial<typeof correo> = {}) => {
    const datos = { ...correo, ...cambios };
    setCorreo(datos);
    return accion("correo", () => configuracionApi.guardarCorreo({ ...datos, contrasena: datos.contrasena || null }), "Correo guardado");
  };
  const guardarWa = (cambios: Partial<typeof wa> = {}) => {
    const datos = { ...wa, ...cambios };
    setWa(datos);
    return accion("wa", () => configuracionApi.guardarWhatsapp({ ...datos, token: datos.token || null }), datos.token ? "WhatsApp Business conectado" : "WhatsApp guardado");
  };

  const conectado = Boolean(cfg?.whatsapp.conectado);
  const nombre = empresa?.nombre_comercial || empresa?.razon_social || "su empresa";
  const destinoPrueba = sesionActual()?.usuario.email || correo.remitente;

  return (
    <Tarjeta id="envio" refSeccion={refSeccion} titulo="Envío automático al cliente" extra={<span className="hint">Solo se envían comprobantes Aceptados o Aceptados condicional</span>}>
      {error && (
        <div className="al al-d" style={{ margin: 16 }}>
          <span>{error}</span>
        </div>
      )}
      {cfg && !cfg.cifrado_disponible && (
        <div className="al al-w" style={{ margin: "16px 16px 0" }}>
          <span>
            Para guardar la contraseña SMTP y el token de WhatsApp configure <code>CERTIFICADOS_CLAVE</code> (32 caracteres o más) en Dokploy (se guardan cifrados).
          </span>
        </div>
      )}
      <div className="card-b cfg-grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr))" }}>
        <div className="cfg-box">
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
            <b style={{ color: "#111827" }}>WhatsApp Business</b>
            <span className={conectado ? "bd b-acep" : "bd b-borr"}>{conectado ? "Conectado" : "No conectado"}</span>
          </div>
          {conectado ? (
            <>
              <dl className="kv">
                <dt>Número</dt>
                <dd>{cfg?.whatsapp.numero || "—"}</dd>
                <dt>Enviados este mes</dt>
                <dd>{numero(cfg?.enviados_mes.whatsapp)}</dd>
              </dl>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12 }}>
                <span style={{ fontSize: 13.5 }}>Enviar al ser aceptada por la DGII</span>
                <Interruptor on={wa.envio_automatico} onChange={(v) => void guardarWa({ envio_automatico: v })} label="Envío automático por WhatsApp" />
              </div>
              {wa.envio_automatico && (
                <span className="hint">
                  El envío automático por WhatsApp necesita una plantilla con documento aprobada por Meta; mientras tanto solo se envía por correo.
                </span>
              )}
              <div className="fld">
                <label className="lbl" htmlFor="wa-plantilla">Plantilla para mensajes (Kontara Ventas)</label>
                <div style={{ display: "flex", gap: 8 }}>
                  <input
                    id="wa-plantilla"
                    className="inp mono"
                    value={wa.plantilla_mensajes}
                    onChange={(e) => setWa({ ...wa, plantilla_mensajes: e.target.value.toLowerCase().replace(/[^a-z0-9_]/g, "") })}
                    placeholder="recordatorio_kontara"
                  />
                  <button type="button" className="btn btn-o btn-sm" disabled={ocupado === "wa"} onClick={() => void guardarWa()}>
                    Guardar
                  </button>
                </div>
                <span className="hint">
                  Plantilla aprobada en Meta con un parámetro {"{{1}}"} en el cuerpo: lleva el texto de cada recordatorio. Sin ella, Meta solo entrega
                  los mensajes dentro de las 24 h de conversación con el cliente; los demás quedan «Requiere plantilla» para enviarlos a mano.
                </span>
              </div>
              <div className="wa-msg">
                Hola. Le compartimos su factura <b>E310000000131</b> por RD$ 1,284,560.00. PDF adjunto. — {nombre}
              </div>
              <div className="fld">
                <label className="lbl" htmlFor="wa-prueba">Enviar mensaje de prueba a</label>
                <div style={{ display: "flex", gap: 8 }}>
                  <input id="wa-prueba" className="inp" value={pruebaWa} onChange={(e) => setPruebaWa(e.target.value)} placeholder="809-555-0142" />
                  <button type="button" className="btn btn-o btn-sm" disabled={!pruebaWa || ocupado === "wa-p"} onClick={() => void accion("wa-p", () => configuracionApi.probarWhatsapp(pruebaWa), "Mensaje de prueba enviado")}>
                    Probar
                  </button>
                </div>
              </div>
              <button type="button" className="btn btn-o btn-sm" onClick={() => void accion("wa-d", () => configuracionApi.desconectarWhatsapp(), "WhatsApp Business desconectado")}>
                Desconectar
              </button>
            </>
          ) : (
            <>
              <span className="hint" style={{ lineHeight: 1.5 }}>
                Necesita una cuenta de WhatsApp Business Platform (Meta). En <b>developers.facebook.com → su app → WhatsApp → Configuración de la API</b>{" "}
                copie el <i>Phone number ID</i> y genere un <i>token de acceso permanente</i> (usuario del sistema).
              </span>
              <div className="fld">
                <label className="lbl" htmlFor="wa-id">Phone number ID</label>
                <input id="wa-id" className="inp mono" value={wa.phone_number_id} onChange={(e) => setWa({ ...wa, phone_number_id: e.target.value })} />
              </div>
              <div className="fld">
                <label className="lbl" htmlFor="wa-num">Número de WhatsApp</label>
                <input id="wa-num" className="inp" value={wa.numero} onChange={(e) => setWa({ ...wa, numero: e.target.value })} placeholder="+1 809-555-0142" />
              </div>
              <div className="fld">
                <label className="lbl" htmlFor="wa-tok">Token de acceso</label>
                <input id="wa-tok" type="password" className="inp" value={wa.token} autoComplete="off" onChange={(e) => setWa({ ...wa, token: e.target.value })} placeholder={cfg?.whatsapp.token_configurado ? "(guardado)" : ""} />
              </div>
              <button type="button" className="btn btn-p btn-sm" disabled={!wa.phone_number_id || !wa.token || ocupado === "wa"} onClick={() => void guardarWa()}>
                Conectar
              </button>
            </>
          )}
        </div>

        <div className="cfg-box">
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
            <b style={{ color: "#111827" }}>Correo electrónico</b>
            <span className={cfg?.correo.configurado ? "bd b-acep" : "bd b-borr"}>{cfg?.correo.configurado ? "Configurado" : "Sin configurar"}</span>
          </div>
          <div className="fld">
            <label className="lbl" htmlFor="ml-from">Remitente</label>
            <input id="ml-from" className="inp" value={correo.remitente} onChange={(e) => setCorreo({ ...correo, remitente: e.target.value })} placeholder="facturacion@empresa.do" />
          </div>
          <div className="fld">
            <label className="lbl" htmlFor="ml-cc">Copia oculta (CCO)</label>
            <input id="ml-cc" className="inp" value={correo.cco} onChange={(e) => setCorreo({ ...correo, cco: e.target.value })} />
          </div>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12 }}>
            <span style={{ fontSize: 13.5 }}>Adjuntar PDF y XML firmado</span>
            <Interruptor on={correo.adjuntar_xml} onChange={(v) => setCorreo({ ...correo, adjuntar_xml: v })} label="Adjuntar XML firmado" />
          </div>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12 }}>
            <span style={{ fontSize: 13.5 }}>Enviar al ser aceptada por la DGII</span>
            <Interruptor on={correo.envio_automatico} onChange={(v) => setCorreo({ ...correo, envio_automatico: v })} label="Envío automático por correo" />
          </div>
          <details>
            <summary style={{ cursor: "pointer", fontSize: 13.5, fontWeight: 600, color: "#0F766E" }}>Servidor de correo (SMTP)</summary>
            <div className="cfg-grid" style={{ marginTop: 12, gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))" }}>
              <div className="fld cfg-full">
                <label className="lbl" htmlFor="ml-host">Servidor</label>
                <input id="ml-host" className="inp" value={correo.host} onChange={(e) => setCorreo({ ...correo, host: e.target.value })} placeholder="smtp.gmail.com" />
              </div>
              <div className="fld">
                <label className="lbl" htmlFor="ml-port">Puerto</label>
                <input id="ml-port" className="inp" inputMode="numeric" value={correo.puerto} onChange={(e) => setCorreo({ ...correo, puerto: Number(e.target.value.replace(/\D/g, "")) || 587 })} />
              </div>
              <div className="fld">
                <label className="lbl" htmlFor="ml-sec">Seguridad</label>
                <select id="ml-sec" className="inp" value={correo.seguridad} onChange={(e) => setCorreo({ ...correo, seguridad: e.target.value as typeof correo.seguridad })}>
                  <option value="starttls">STARTTLS (587)</option>
                  <option value="ssl">SSL/TLS (465)</option>
                  <option value="ninguna">Ninguna</option>
                </select>
              </div>
              <div className="fld">
                <label className="lbl" htmlFor="ml-user">Usuario</label>
                <input id="ml-user" className="inp" value={correo.usuario} autoComplete="off" onChange={(e) => setCorreo({ ...correo, usuario: e.target.value })} />
              </div>
              <div className="fld">
                <label className="lbl" htmlFor="ml-pw">Contraseña</label>
                <input id="ml-pw" type="password" className="inp" value={correo.contrasena} autoComplete="new-password" onChange={(e) => setCorreo({ ...correo, contrasena: e.target.value })} placeholder={cfg?.correo.contrasena_configurada ? "(guardada)" : ""} />
              </div>
            </div>
          </details>
          <div className="cfg-acciones">
            <button type="button" className="btn btn-p btn-sm" disabled={ocupado === "correo"} onClick={() => void guardarCorreo()}>
              Guardar correo
            </button>
            <button
              type="button"
              className="btn btn-o btn-sm"
              disabled={!cfg?.correo.configurado || !destinoPrueba || ocupado === "correo-p"}
              onClick={() => void accion("correo-p", () => configuracionApi.probarCorreo(destinoPrueba), "Correo de prueba enviado")}
            >
              Enviar correo de prueba
            </button>
          </div>
          <span className="hint">Enviados este mes: {numero(cfg?.enviados_mes.correo)}</span>
        </div>
      </div>
    </Tarjeta>
  );
}

// --- Vendedores ---------------------------------------------------------

const VENDEDOR_VACIO = { nombre: "", correo: "", comision_pct: "0", activo: true };

export function SeccionVendedores({ refSeccion, avisar }: { refSeccion: RefSeccion; avisar: Avisar }) {
  const [lista, setLista] = useState<Vendedor[]>([]);
  const [editando, setEditando] = useState<string | "nuevo" | null>(null);
  const [form, setForm] = useState(VENDEDOR_VACIO);
  const [error, setError] = useState<string | null>(null);

  const cargar = () => configuracionApi.vendedores().then(setLista).catch((err) => setError(mensajeError(err)));
  useEffect(() => {
    void cargar();
  }, []);

  async function guardar() {
    const payload = { nombre: form.nombre, correo: form.correo || null, comision_pct: form.comision_pct || "0", activo: form.activo };
    try {
      if (editando === "nuevo") await configuracionApi.crearVendedor(payload);
      else if (editando) await configuracionApi.actualizarVendedor(editando, payload);
      setEditando(null);
      await cargar();
      avisar("Vendedor guardado");
    } catch (err) {
      avisar("No se pudo guardar el vendedor", { detalle: mensajeError(err), error: true });
    }
  }

  async function eliminar(v: Vendedor) {
    try {
      await configuracionApi.eliminarVendedor(v.id);
      await cargar();
      avisar(`${v.nombre} eliminado`);
    } catch (err) {
      avisar("No se pudo eliminar", { detalle: mensajeError(err), error: true });
    }
  }

  const formulario = (
    <div className="cfg-seq-form">
      <div className="fld">
        <label className="lbl" htmlFor="vd-n">Nombre <span className="req">*</span></label>
        <input id="vd-n" className="inp" value={form.nombre} onChange={(e) => setForm({ ...form, nombre: e.target.value })} />
      </div>
      <div className="fld">
        <label className="lbl" htmlFor="vd-c">Correo</label>
        <input id="vd-c" className="inp" type="email" value={form.correo} onChange={(e) => setForm({ ...form, correo: e.target.value })} />
      </div>
      <div className="fld">
        <label className="lbl" htmlFor="vd-p">Comisión %</label>
        <input id="vd-p" className="inp" inputMode="decimal" value={form.comision_pct} onChange={(e) => setForm({ ...form, comision_pct: e.target.value.replace(/[^0-9.]/g, "") })} />
      </div>
      <div className="fld" style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
        <Interruptor on={form.activo} onChange={(v) => setForm({ ...form, activo: v })} label="Vendedor activo" />
        <span>Activo</span>
      </div>
      <div className="cfg-acciones">
        <button type="button" className="btn btn-p btn-sm" disabled={!form.nombre.trim()} onClick={() => void guardar()}>
          Guardar
        </button>
        <button type="button" className="btn btn-g btn-sm" onClick={() => setEditando(null)}>
          Cancelar
        </button>
      </div>
    </div>
  );

  return (
    <Tarjeta
      id="vendedores"
      refSeccion={refSeccion}
      titulo="Vendedores"
      extra={
        <button
          type="button"
          className="btn btn-o btn-sm"
          onClick={() => {
            setEditando("nuevo");
            setForm(VENDEDOR_VACIO);
          }}
        >
          Nuevo vendedor
        </button>
      }
    >
      {editando === "nuevo" && formulario}
      {error && (
        <div className="al al-d" style={{ margin: 16 }}>
          <span>{error}</span>
        </div>
      )}
      {lista.length === 0 && !error ? (
        <p className="pad mut">Aún no hay vendedores. Agréguelos para elegirlos al emitir una factura.</p>
      ) : (
        <div className="tw">
          <table className="tbl" style={{ minWidth: 560 }}>
            <thead>
              <tr>
                <th>Nombre</th>
                <th>Correo</th>
                <th className="num">Comisión</th>
                <th>Estado</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {lista.map((v) => [
                <tr key={v.id}>
                  <td className="strong">{v.nombre}</td>
                  <td className="mut">{v.correo ?? "—"}</td>
                  <td className="num">{Number(v.comision_pct).toFixed(2)} %</td>
                  <td>
                    <span className={v.activo ? "bd b-acep nodot" : "bd b-borr nodot"}>{v.activo ? "Activo" : "Inactivo"}</span>
                  </td>
                  <td className="right">
                    <button
                      type="button"
                      className="btn btn-g btn-sm"
                      onClick={() => {
                        setEditando(v.id);
                        setForm({ nombre: v.nombre, correo: v.correo ?? "", comision_pct: v.comision_pct, activo: v.activo });
                      }}
                    >
                      Editar
                    </button>
                    <button type="button" className="btn btn-g btn-sm" onClick={() => void eliminar(v)}>
                      Eliminar
                    </button>
                  </td>
                </tr>,
                editando === v.id ? (
                  <tr key={`${v.id}-f`}>
                    <td colSpan={5} style={{ padding: 0 }}>
                      {formulario}
                    </td>
                  </tr>
                ) : null,
              ])}
            </tbody>
          </table>
        </div>
      )}
    </Tarjeta>
  );
}

// --- Usuarios ---------------------------------------------------------

export function SeccionUsuarios({ refSeccion, avisar }: { refSeccion: RefSeccion; avisar: Avisar }) {
  const [datos, setDatos] = useState<Usuarios | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [nuevo, setNuevo] = useState(false);
  const [correo, setCorreo] = useState("");
  const [clave, setClave] = useState("");
  const [rol, setRol] = useState<"admin" | "usuario">("usuario");
  const yo = sesionActual()?.usuario.id;
  const nombreDeLaEmpresa = useDatosEmpresa().nombre;

  const cargar = () => configuracionApi.usuarios().then(setDatos).catch((err) => setError(mensajeError(err)));
  useEffect(() => {
    void cargar();
  }, []);

  async function crear() {
    try {
      const u = await configuracionApi.crearUsuario(correo, clave || null, rol);
      setNuevo(false);
      setCorreo("");
      setClave("");
      setRol("usuario");
      await cargar();
      avisar(u.invitado ? `Invitación enviada a ${u.correo}` : `${u.correo} agregado a ${nombreDeLaEmpresa}`);
    } catch (err) {
      avisar("No se pudo crear el usuario", { detalle: mensajeError(err), error: true });
    }
  }

  async function cambiar(id: string, activo: boolean) {
    try {
      await (activo ? configuracionApi.activarUsuario(id) : configuracionApi.desactivarUsuario(id));
      await cargar();
      avisar(activo ? "Usuario activado" : "Usuario desactivado");
    } catch (err) {
      avisar("No se pudo cambiar el usuario", { detalle: mensajeError(err), error: true });
    }
  }

  async function cambiarRol(u: Usuario, nuevoRol: "admin" | "usuario") {
    try {
      await configuracionApi.cambiarRolUsuario(u.id, nuevoRol);
      await cargar();
      avisar(`${u.correo} ahora es ${nuevoRol === "admin" ? "administrador" : "usuario"} de ${nombreDeLaEmpresa}`);
    } catch (err) {
      avisar("No se pudo cambiar el rol", { detalle: mensajeError(err), error: true });
    }
  }

  async function quitar(u: Usuario) {
    if (!window.confirm(`¿Quitar a ${u.correo} de ${nombreDeLaEmpresa}? Sigue en sus otras empresas.`)) return;
    try {
      await configuracionApi.quitarDeEmpresa(u.id);
      await cargar();
      avisar(`${u.correo} ya no es miembro de ${nombreDeLaEmpresa}`);
    } catch (err) {
      avisar("No se pudo quitar de la empresa", { detalle: mensajeError(err), error: true });
    }
  }

  async function darAcceso(u: Usuario) {
    try {
      await configuracionApi.darAccesoUsuario(u.id);
      await cargar();
      avisar(`${u.correo} ya tiene acceso`, { detalle: "Entra desde su próximo inicio de sesión." });
    } catch (err) {
      avisar("No se pudo dar acceso", { detalle: mensajeError(err), error: true });
    }
  }

  return (
    <Tarjeta
      id="usuarios"
      refSeccion={refSeccion}
      titulo={`Usuarios de ${nombreDeLaEmpresa}`}
      extra={
        datos?.configurado ? (
          <button type="button" className="btn btn-o btn-sm" onClick={() => setNuevo((v) => !v)}>
            {nuevo ? "Cerrar" : "Agregar usuario"}
          </button>
        ) : undefined
      }
    >
      {error && (
        <div className="al al-d" style={{ margin: 16 }}>
          <span>{error}</span>
        </div>
      )}
      {datos && !datos.configurado && (
        <div className="card-b stack-sm">
          <div className="al al-i">
            <span>{datos.mensaje}</span>
          </div>
          <ol style={{ margin: 0, paddingLeft: 20, fontSize: 13.5, color: "#374151", display: "flex", flexDirection: "column", gap: 4 }}>
            <li>Abra Supabase Studio (por ejemplo <code>https://supa.acentosdeco.lat</code>).</li>
            <li>
              <b>Authentication → Users → Add user → Create new user</b>: correo y contraseña, con <i>Auto Confirm User</i> marcado.
            </li>
            <li>
              Dele acceso a Kontara: en el usuario → <b>Edit raw app_metadata</b>, agregue <code>"kontara": true</code> (o ponga su correo en{" "}
              <code>KONTARA_ADMIN_EMAILS</code>, ver <code>docs/AUTH.md</code>).
            </li>
            <li>Para quitar el acceso: en la misma lista, menú del usuario → <b>Ban user</b> (o <b>Delete user</b>).</li>
          </ol>
        </div>
      )}
      {nuevo && (
        <div className="cfg-seq-form">
          <div className="fld">
            <label className="lbl" htmlFor="us-c">Correo <span className="req">*</span></label>
            <input id="us-c" className="inp" type="email" value={correo} onChange={(e) => setCorreo(e.target.value)} />
          </div>
          <div className="fld">
            <label className="lbl" htmlFor="us-p">Contraseña inicial</label>
            <input id="us-p" className="inp" type="password" autoComplete="new-password" value={clave} onChange={(e) => setClave(e.target.value)} placeholder="Vacía = enviar invitación" />
          </div>
          <div className="fld">
            <label className="lbl" htmlFor="us-r">Rol en {nombreDeLaEmpresa}</label>
            <select id="us-r" className="inp" value={rol} onChange={(e) => setRol(e.target.value as "admin" | "usuario")}>
              <option value="usuario">Usuario</option>
              <option value="admin">Administrador</option>
            </select>
          </div>
          <div className="cfg-acciones">
            <button type="button" className="btn btn-p btn-sm" disabled={!correo.includes("@") || (clave !== "" && clave.length < 8)} onClick={() => void crear()}>
              {clave ? "Crear usuario" : "Enviar invitación"}
            </button>
          </div>
          <span className="hint" style={{ gridColumn: "1 / -1" }}>
            Sin contraseña, Supabase envía un correo de invitación (requiere SMTP configurado en el recurso de Supabase). Con contraseña, el usuario
            queda confirmado y puede entrar de inmediato; pídale que la cambie en Seguridad. Si el correo ya es de un usuario (de otra de sus
            empresas), se agrega a esta sin crearlo de nuevo.
          </span>
        </div>
      )}
      {datos?.configurado && (
        <div className="tw">
          <table className="tbl" style={{ minWidth: 620 }}>
            <thead>
              <tr>
                <th>Correo</th>
                <th>Rol</th>
                <th>Creado</th>
                <th>Último acceso</th>
                <th>Estado</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {datos.usuarios.map((u) => (
                <tr key={u.id}>
                  <td className="strong">
                    {u.correo}
                    {u.id === yo && <span className="chip chip-sm" style={{ marginLeft: 6 }}>Usted</span>}
                  </td>
                  <td>
                    {u.id === yo ? (
                      <span className="mut">{u.rol_empresa === "admin" ? "Administrador" : "Usuario"}</span>
                    ) : (
                      <select
                        className="inp"
                        style={{ height: 32, width: "auto" }}
                        aria-label={`Rol de ${u.correo}`}
                        value={u.rol_empresa ?? "usuario"}
                        onChange={(e) => void cambiarRol(u, e.target.value as "admin" | "usuario")}
                      >
                        <option value="usuario">Usuario</option>
                        <option value="admin">Administrador</option>
                      </select>
                    )}
                  </td>
                  <td className="mut">{fechaCorta(u.creado_en)}</td>
                  <td className="mut">{u.ultimo_acceso ? fechaCorta(u.ultimo_acceso) : "Nunca"}</td>
                  <td>
                    <span className={u.activo ? (u.invitado ? "bd b-proc nodot" : "bd b-acep nodot") : "bd b-rech nodot"}>
                      {!u.activo ? "Desactivado" : u.invitado ? "Invitado" : "Activo"}
                    </span>
                  </td>
                  <td className="right">
                    {u.id !== yo && (
                      <span style={{ display: "inline-flex", gap: 4 }}>
                        <button type="button" className="btn btn-g btn-sm" onClick={() => void cambiar(u.id, !u.activo)}>
                          {u.activo ? "Desactivar" : "Activar"}
                        </button>
                        <button type="button" className="btn btn-g btn-sm" title={`Quitar de ${nombreDeLaEmpresa} (sigue en sus otras empresas)`} onClick={() => void quitar(u)}>
                          Quitar
                        </button>
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {datos?.configurado && datos.usuarios.length === 0 && <p className="pad mut">No hay usuarios.</p>}
      {datos?.configurado && datos.sin_acceso.length > 0 && (
        <div className="card-b stack-sm" style={{ borderTop: "1px solid #E5E7EB" }}>
          <span className="hint">
            <b>Sin acceso a Kontara</b> ({datos.sin_acceso.length}): existen en Supabase Auth pero no pueden entrar a la aplicación hasta que les dé
            acceso.
          </span>
          <div className="tw">
            <table className="tbl" style={{ minWidth: 520 }}>
              <tbody>
                {datos.sin_acceso.map((u) => (
                  <tr key={u.id}>
                    <td className="mut">{u.correo}</td>
                    <td>
                      <span className="bd b-borr nodot">Sin acceso</span>
                    </td>
                    <td className="right">
                      <button type="button" className="btn btn-g btn-sm" onClick={() => void darAcceso(u)}>
                        Dar acceso
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </Tarjeta>
  );
}
