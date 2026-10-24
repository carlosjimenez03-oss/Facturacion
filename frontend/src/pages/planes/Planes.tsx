// Planes y suscripción (Planes.dc.html): elegir plan con periodicidad mensual
// o anual y consumo real del mes. No hay pasarela de pago: "Elegir plan"
// registra una solicitud en erp-api y explica cómo se completa el pago.
import { useCallback, useEffect, useState } from "react";
import { formatoMonto } from "../../lib/marca";
import { useDatosEmpresa } from "../../lib/configuracionApi";
import { solucionesApi } from "../../lib/solucionesApi";
import type { Consumo, Periodicidad, Plan, SolicitudPlan, Suscripcion } from "../../lib/solucionesApi";
import { AvisoError, Cargando, Modal } from "../ingresos/IngresosShell";
import { fechaLegible, mensajeError } from "../ingresos/ingresosUtil";

const NOMBRE_PLAN: Record<string, string> = { emprendedor: "Emprendedor", pyme: "Pyme", empresarial: "Empresarial" };

function IconoCheck() {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="#0F766E"
      strokeWidth={2.4}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      style={{ flex: "none", marginTop: 2 }}
    >
      <path d="M20 6 9 17l-5-5" />
    </svg>
  );
}

function mesLegible(iso: string): string {
  return new Date(`${iso}T12:00:00`).toLocaleDateString("es-DO", { month: "long", year: "numeric" }).replace(" de ", " ");
}

function subtitulo(s: Suscripcion | null): string {
  if (!s) return "";
  if (s.estado === "activa") {
    return `Plan ${s.plan_nombre} activo${s.proxima_factura ? ` · próxima factura el ${fechaLegible(s.proxima_factura)}` : ""}.`;
  }
  if (s.estado === "prueba_vencida") return "Tu prueba gratis terminó: elige un plan para seguir emitiendo.";
  return s.dias_restantes === 1 ? "Te queda 1 día de prueba gratis." : `Te quedan ${s.dias_restantes} días de prueba gratis.`;
}

function claseBarra(p: number | null): string {
  if (p == null) return "pb";
  if (p >= 100) return "pb bad";
  return p >= 80 ? "pb warn" : "pb";
}

interface FilaConsumo {
  k: string;
  v: string;
  p: number | null;
}

function filasConsumo(c: Consumo): FilaConsumo[] {
  const n = (x: number) => x.toLocaleString("en-US");
  return [
    { k: "e-CF emitidos", v: `${n(c.ecf_emitidos)} / ${c.ecf_limite == null ? "ilimitados" : n(c.ecf_limite)}`, p: c.ecf_porcentaje },
    {
      k: "Usuarios",
      v: c.usuarios == null ? "—" : `${n(c.usuarios)} / ${c.usuarios_limite == null ? "ilimitados" : n(c.usuarios_limite)}`,
      p: c.usuarios_porcentaje,
    },
    { k: "Almacenamiento de XML", v: `${c.almacenamiento_texto} / ${c.almacenamiento_limite_texto}`, p: c.almacenamiento_porcentaje },
  ];
}

export function PlanesPage() {
  const miEmpresa = useDatosEmpresa();
  const [planes, setPlanes] = useState<Plan[]>([]);
  const [suscripcion, setSuscripcion] = useState<Suscripcion | null>(null);
  const [consumo, setConsumo] = useState<Consumo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [errorConsumo, setErrorConsumo] = useState<string | null>(null);
  const [cargando, setCargando] = useState(true);
  const [periodicidad, setPeriodicidad] = useState<Periodicidad>("mensual");
  const [elegido, setElegido] = useState<Plan | null>(null);
  const [formaPago, setFormaPago] = useState<"transferencia" | "tarjeta">("transferencia");
  const [contacto, setContacto] = useState({ nombre: "", correo: "", telefono: "" });
  const [enviando, setEnviando] = useState(false);
  const [errorModal, setErrorModal] = useState<string | null>(null);
  const [resultado, setResultado] = useState<{ solicitud: SolicitudPlan; instrucciones: string[] } | null>(null);

  const cargar = useCallback(async () => {
    setCargando(true);
    const [p, s, c] = await Promise.allSettled([solucionesApi.listarPlanes(), solucionesApi.suscripcion(), solucionesApi.consumo()]);
    if (p.status === "fulfilled" && s.status === "fulfilled") {
      setPlanes(p.value);
      setSuscripcion(s.value);
      setError(null);
    } else {
      setError(mensajeError(p.status === "rejected" ? p.reason : (s as PromiseRejectedResult).reason));
    }
    if (c.status === "fulfilled") {
      setConsumo(c.value);
      setErrorConsumo(null);
    } else {
      setErrorConsumo(mensajeError(c.reason));
    }
    setCargando(false);
  }, []);

  useEffect(() => {
    void cargar();
  }, [cargar]);

  const anual = periodicidad === "anual";
  const precio = (p: Plan) => Number(anual ? p.precio_anual : p.precio_mensual);

  function abrir(p: Plan) {
    setElegido(p);
    setErrorModal(null);
    setFormaPago("transferencia");
  }

  async function confirmar() {
    if (!elegido) return;
    if (!contacto.correo.trim() && !contacto.telefono.trim()) {
      setErrorModal("Indique un correo o un teléfono para que el asesor le contacte.");
      return;
    }
    setEnviando(true);
    setErrorModal(null);
    try {
      const r = await solucionesApi.solicitarPlan({
        plan: elegido.clave,
        periodicidad,
        forma_pago: formaPago,
        contacto_nombre: contacto.nombre.trim() || miEmpresa.nombre,
        contacto_correo: contacto.correo.trim() || null,
        contacto_telefono: contacto.telefono.trim() || null,
      });
      setResultado(r);
      setElegido(null);
      void cargar();
    } catch (err) {
      setErrorModal(mensajeError(err));
    } finally {
      setEnviando(false);
    }
  }

  const planActual = suscripcion?.plan ?? null;
  const pendiente = resultado?.solicitud ?? suscripcion?.solicitud_pendiente ?? null;
  const sel = elegido ? precio(elegido) : 0;

  return (
    <div className="content" style={{ maxWidth: 1180 }}>
      <div className="ph">
        <div>
          <h1>Elige tu plan</h1>
          <p>{subtitulo(suscripcion)} Todos los planes incluyen facturación electrónica e-CF certificada por la DGII.</p>
        </div>
        <div className="seg" role="group" aria-label="Periodicidad">
          <button type="button" className={anual ? "" : "on"} onClick={() => setPeriodicidad("mensual")}>
            Mensual
          </button>
          <button type="button" className={anual ? "on" : ""} onClick={() => setPeriodicidad("anual")}>
            Anual · 2 meses gratis
          </button>
        </div>
      </div>

      {error && <AvisoError titulo="No se pudieron cargar los planes." mensaje={error} onReintentar={cargar} />}

      {resultado && (
        <div className="al al-s" role="status">
          <span>
            <b>Solicitud del plan {NOMBRE_PLAN[resultado.solicitud.plan]} recibida.</b> Un asesor le contactará · Total RD${" "}
            {formatoMonto(resultado.solicitud.total)} ({resultado.solicitud.periodicidad}, ITBIS incluido).
            <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>
              {resultado.instrucciones.map((i) => (
                <li key={i}>{i}</li>
              ))}
            </ul>
          </span>
        </div>
      )}
      {!resultado && pendiente && (
        <div className="al al-i">
          <span>
            <b>Tiene una solicitud del plan {NOMBRE_PLAN[pendiente.plan]} en espera</b> desde el {fechaLegible(pendiente.creado_en)}: un asesor le
            contactará para completar el pago por transferencia. El plan se activa al confirmar el depósito.
          </span>
        </div>
      )}

      {cargando && planes.length === 0 ? (
        <Cargando />
      ) : (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))", gap: 16, alignItems: "stretch" }}>
          {planes.map((p) => {
            const esActual = planActual === p.clave;
            return (
              <section
                key={p.clave}
                className="card"
                data-plan={p.clave}
                style={{
                  padding: 24,
                  display: "flex",
                  flexDirection: "column",
                  gap: 14,
                  border: p.popular ? "1.5px solid #0F766E" : "1px solid #E5E7EB",
                }}
              >
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <b style={{ fontSize: 17, color: "#111827" }}>{p.nombre}</b>
                  {p.popular && <span className="bd b-teal nodot">Más elegido</span>}
                </div>
                <span className="hint" style={{ fontSize: 13.5 }}>
                  {p.descripcion}
                </span>
                <div>
                  <span style={{ fontSize: 30, fontWeight: 700, color: "#111827" }}>RD$ {formatoMonto(precio(p))}</span>
                  <span className="mut"> / {anual ? "año" : "mes"}</span>
                </div>
                <span className="hint">+ ITBIS 18%</span>
                <ul
                  style={{
                    margin: 0,
                    padding: 0,
                    listStyle: "none",
                    display: "flex",
                    flexDirection: "column",
                    gap: 8,
                    fontSize: 13.5,
                    color: "#374151",
                    flex: 1,
                  }}
                >
                  {p.caracteristicas.map((f) => (
                    <li key={f} style={{ display: "flex", gap: 8 }}>
                      <IconoCheck />
                      {f}
                    </li>
                  ))}
                </ul>
                <button type="button" className={p.popular ? "btn btn-p" : "btn btn-o"} onClick={() => abrir(p)} disabled={esActual}>
                  {esActual ? "Plan actual" : `Elegir ${p.nombre}`}
                </button>
              </section>
            );
          })}
        </div>
      )}

      <section className="card">
        <div className="card-h">
          <h2>Consumo del plan · {consumo ? mesLegible(consumo.mes) : mesLegible(new Date().toISOString().slice(0, 10))}</h2>
          {consumo && !planActual && <span className="hint">Límites del plan {NOMBRE_PLAN[consumo.plan_limites]} durante la prueba</span>}
        </div>
        <div className="card-b" style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 20 }}>
          {errorConsumo && <AvisoError mensaje={errorConsumo} onReintentar={cargar} />}
          {consumo &&
            filasConsumo(consumo).map((u) => (
              <div key={u.k} style={{ display: "flex", flexDirection: "column", gap: 6 }} data-consumo={u.k}>
                <div style={{ display: "flex", justifyContent: "space-between", fontSize: 13.5 }}>
                  <span>{u.k}</span>
                  <b>{u.v}</b>
                </div>
                <div className={claseBarra(u.p)}>
                  <span style={{ width: `${u.p ?? 0}%` }} />
                </div>
                {u.k === "Usuarios" && consumo.usuarios == null && (
                  <span className="hint">Disponible cuando se activen los usuarios de la empresa.</span>
                )}
              </div>
            ))}
          {consumo && consumo.ecf_por_empresa.length > 1 && (
            <div style={{ gridColumn: "1 / -1", display: "flex", flexDirection: "column", gap: 6 }} data-consumo="por-empresa">
              <span className="hint">e-CF emitidos este mes por empresa (el plan cuenta el total de todas las empresas de la cuenta):</span>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                {consumo.ecf_por_empresa.map((e) => (
                  <span key={e.empresa_id} className="chip">
                    {e.nombre}: <b style={{ marginLeft: 4 }}>{e.ecf_emitidos.toLocaleString("en-US")}</b>
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>
      </section>

      {elegido && (
        <Modal
          titulo={`Confirmar plan ${elegido.nombre}`}
          onCerrar={() => setElegido(null)}
          pie={
            <>
              <button type="button" className="btn btn-o" onClick={() => setElegido(null)}>
                Cancelar
              </button>
              <button type="button" className="btn btn-p" onClick={confirmar} disabled={enviando}>
                {enviando ? "Enviando…" : "Solicitar plan"}
              </button>
            </>
          }
        >
          <dl className="kv" style={{ gridTemplateColumns: "1fr auto" }}>
            <dt>
              Plan {elegido.nombre} ({anual ? "año" : "mes"})
            </dt>
            <dd className="num">RD$ {formatoMonto(sel)}</dd>
            <dt>ITBIS 18%</dt>
            <dd className="num">RD$ {formatoMonto(Math.round(sel * 18) / 100)}</dd>
            <dt style={{ fontWeight: 650, color: "#111827" }}>Total</dt>
            <dd className="num" style={{ fontWeight: 700 }}>
              RD$ {formatoMonto(sel + Math.round(sel * 18) / 100)}
            </dd>
          </dl>
          <div className="fld">
            <span className="lbl">Forma de pago</span>
            <div className="seg" role="group">
              <button type="button" className={formaPago === "tarjeta" ? "on" : ""} onClick={() => setFormaPago("tarjeta")}>
                Tarjeta
              </button>
              <button type="button" className={formaPago === "transferencia" ? "on" : ""} onClick={() => setFormaPago("transferencia")}>
                Transferencia
              </button>
            </div>
            <span className="hint">
              {formaPago === "tarjeta"
                ? "El pago con tarjeta en línea aún no está disponible: un asesor le enviará un enlace de pago o los datos para transferir."
                : "Un asesor le enviará los datos bancarios. El plan se activa al confirmar el depósito."}
            </span>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: 12 }}>
            <label className="fld">
              <span className="lbl">Nombre de contacto</span>
              <input
                className="inp"
                value={contacto.nombre}
                onChange={(e) => setContacto({ ...contacto, nombre: e.target.value })}
                placeholder={miEmpresa.nombre}
              />
            </label>
            <label className="fld">
              <span className="lbl">Correo</span>
              <input className="inp" type="email" value={contacto.correo} onChange={(e) => setContacto({ ...contacto, correo: e.target.value })} />
            </label>
            <label className="fld">
              <span className="lbl">Teléfono</span>
              <input className="inp" type="tel" value={contacto.telefono} onChange={(e) => setContacto({ ...contacto, telefono: e.target.value })} />
            </label>
          </div>
          <span className="hint">No se realiza ningún cargo ahora: se registra su solicitud.</span>
          {errorModal && <AvisoError mensaje={errorModal} />}
        </Modal>
      )}
    </div>
  );
}
