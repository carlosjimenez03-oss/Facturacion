// Kontara Ventas (Ventas.dc.html): promoción y activación del módulo (prueba
// gratis de 30 días) y, ya activo, las cuatro automatizaciones con su
// configuración y la cola de envíos que genera erp-api a diario. Un envío
// solo figura "Enviado" si lo entregó un canal configurado (SMTP / WhatsApp
// Cloud API); sin canal queda "Por enviar" con su enlace wa.me / correo.
import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { MARCA } from "../../lib/marca";
import { solucionesApi } from "../../lib/solucionesApi";
import type { Automatizacion, CanalEnvio, Envio, EstadoEnvio, EstadoVentas } from "../../lib/solucionesApi";
import { AvisoError, Cargando, Modal } from "../ingresos/IngresosShell";
import { useAviso } from "../ingresos/useAviso";
import { enlaceCorreo, fechaLegible, mensajeError } from "../ingresos/ingresosUtil";
import "./ventas.css";

const ESTADO_ENVIO: Record<EstadoEnvio, { label: string; badge: string }> = {
  pendiente_canal: { label: "Por enviar", badge: "bd b-proc" },
  enviado: { label: "Enviado", badge: "bd b-acep" },
  enviado_manual: { label: "Enviado a mano", badge: "bd b-teal" },
  error: { label: "Error", badge: "bd b-rech" },
  descartado: { label: "Descartado", badge: "bd b-borr" },
};

const CANALES: { value: CanalEnvio; label: string }[] = [
  { value: "whatsapp", label: "WhatsApp" },
  { value: "correo", label: "Correo" },
  { value: "ambos", label: "WhatsApp y correo" },
];

type Filtro = "por_enviar" | "enviados" | "todos";

function detalle(a: Automatizacion, diasReactivacion: number): string {
  switch (a.clave) {
    case "motivar":
      return `Una vez al mes, a los clientes que no compran hace ${a.dias} días o más (y hasta ${diasReactivacion}). Incluye las categorías de tu catálogo de productos.`;
    case "cobranza":
      return `Facturas a crédito pendientes que vencen dentro de ${a.dias} días. Una por factura; deja de aplicar al registrar el cobro.`;
    case "fidelizacion":
      return `e-CF aceptados por la DGII desde que activaste esta automatización (revisa los últimos ${a.dias} días). Por correo adjunta la representación impresa.`;
    case "reactivacion":
      return `Clientes sin comprar hace más de ${a.dias} días; un mensaje por cada período de inactividad.`;
  }
}

function IconoVentas() {
  return (
    <svg
      width="40"
      height="40"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.6}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M3 3v18h18" />
      <path d="m7 15 4-4 3 3 5-6" />
    </svg>
  );
}

function Promo({ onActivar, activando }: { onActivar: () => void; activando: boolean }) {
  const [comoFunciona, setComoFunciona] = useState(false);
  return (
    <section className="card ve-promo">
      <div className="ve-promo-txt">
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 16 }}>
          <span className="ve-ic">
            <IconoVentas />
          </span>
          <button type="button" className="ve-como" onClick={() => setComoFunciona(true)}>
            ¡Conoce cómo
            <br />
            funciona!
          </button>
        </div>
        <h1>{MARCA} Ventas</h1>
        <p style={{ fontSize: 20, color: "#111827" }}>¡Fideliza a tus clientes y vende más!</p>
        <p style={{ fontSize: 18, color: "#6B7280", lineHeight: 1.5 }}>
          Envía correos y mensajes de WhatsApp automáticos con promociones y recordatorios de pago, y permanece en la mente de tus clientes. ¡Actívalo
          hoy mismo!
        </p>
        <button type="button" className="btn ve-activar" onClick={onActivar} disabled={activando}>
          {activando ? "Activando…" : "¡Activar GRATIS!"}
        </button>
      </div>
      <div aria-hidden="true" className="ve-promo-ilus">
        <div className="ve-mini-menu">
          <b>{MARCA}</b>
          {["Inicio", "Ingresos", "Compras", "Contactos", "Inventario", "Bancos", "Contabilidad"].map((x) => (
            <span key={x}>{x}</span>
          ))}
          <span className="on">Ventas</span>
          <span>Portal de clientes</span>
        </div>
        <div className="ve-mini-pag">
          <span style={{ fontSize: 9, color: "#0F766E" }}>‹ Volver</span>
          <b style={{ fontSize: 13, color: "#111827" }}>Activar automatización</b>
          <span style={{ fontSize: 9, color: "#6B7280" }}>Elige una automatización y personalízala para empezar</span>
          <div className="ve-mini-card">
            <div className="ve-mini-video">▶</div>
            <span style={{ display: "flex", gap: 4 }}>
              <span className="chip" style={{ height: 14, fontSize: 7, background: "#EEF0FF", color: "#4338CA" }}>
                Motivar ventas
              </span>
              <span className="chip" style={{ height: 14, fontSize: 7 }}>
                Inactiva
              </span>
            </span>
            <b style={{ fontSize: 9, color: "#111827" }}>Presenta tus productos y servicios</b>
            <span style={{ fontSize: 8, color: "#6B7280" }}>Muestra tus categorías a clientes potenciales.</span>
            <span className="ve-mini-btn">Ver detalles ›</span>
          </div>
        </div>
      </div>
      {comoFunciona && (
        <Modal
          titulo={`Así funciona ${MARCA} Ventas`}
          onCerrar={() => setComoFunciona(false)}
          pie={
            <button
              type="button"
              className="btn btn-p"
              onClick={() => {
                setComoFunciona(false);
                onActivar();
              }}
            >
              Activar gratis
            </button>
          }
        >
          <ol style={{ margin: 0, paddingLeft: 18, display: "flex", flexDirection: "column", gap: 6 }}>
            <li>Activa el módulo: tienes 30 días de prueba gratis.</li>
            <li>Enciende las automatizaciones que quieras y personaliza el mensaje, los días y el canal.</li>
            <li>Cada día revisamos tus facturas, cuentas por cobrar y contactos y preparamos los mensajes que tocan.</li>
            <li>Con correo o WhatsApp configurados se envían solos; si no, los envías tú con un clic desde la cola.</li>
          </ol>
        </Modal>
      )}
    </section>
  );
}

function TarjetaAutomatizacion({
  a,
  diasReactivacion,
  onCambio,
}: {
  a: Automatizacion;
  diasReactivacion: number;
  onCambio: (a: Automatizacion, texto?: string) => void;
}) {
  const [abierta, setAbierta] = useState(false);
  const [form, setForm] = useState({ canal: a.canal, dias: String(a.dias), asunto: a.asunto, plantilla: a.plantilla });
  const [guardando, setGuardando] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => setForm({ canal: a.canal, dias: String(a.dias), asunto: a.asunto, plantilla: a.plantilla }), [a]);

  async function alternar() {
    setError(null);
    try {
      const r = await solucionesApi.actualizarAutomatizacion(a.clave, { activa: !a.activa });
      onCambio(r, r.activa ? `«${a.titulo}» activada` : `«${a.titulo}» desactivada`);
    } catch (err) {
      setError(mensajeError(err));
    }
  }

  async function guardar() {
    setGuardando(true);
    setError(null);
    try {
      const r = await solucionesApi.actualizarAutomatizacion(a.clave, {
        canal: form.canal,
        dias: Number(form.dias),
        asunto: form.asunto,
        plantilla: form.plantilla,
      });
      onCambio(r, "Configuración guardada");
    } catch (err) {
      setError(mensajeError(err));
    } finally {
      setGuardando(false);
    }
  }

  const porEnviar = a.envios.pendiente_canal ?? 0;
  return (
    <section className="card ve-auto" data-auto={a.clave}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
        <span className="chip" style={{ background: "#EEF0FF", color: "#4338CA" }}>
          {a.categoria}
        </span>
        <span className={a.activa ? "bd b-acep" : "bd b-borr"}>{a.activa ? "Activa" : "Inactiva"}</span>
      </div>
      <b style={{ fontSize: 15, color: "#111827" }}>{a.titulo}</b>
      <span style={{ fontSize: 13.5, color: "#4B5563", lineHeight: 1.5 }}>{a.descripcion}</span>
      {abierta && (
        <div className="ve-det">
          <span>{detalle(a, diasReactivacion)}</span>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 110px", gap: 10 }}>
            <label className="fld">
              <span className="lbl">Canal</span>
              <select className="inp inp-sm" value={form.canal} onChange={(e) => setForm({ ...form, canal: e.target.value as CanalEnvio })}>
                {CANALES.map((c) => (
                  <option key={c.value} value={c.value}>
                    {c.label}
                  </option>
                ))}
              </select>
            </label>
            <label className="fld">
              <span className="lbl">Días</span>
              <input
                className="inp inp-sm"
                type="number"
                min={0}
                max={365}
                value={form.dias}
                onChange={(e) => setForm({ ...form, dias: e.target.value })}
              />
            </label>
          </div>
          <label className="fld">
            <span className="lbl">Asunto (correo)</span>
            <input className="inp inp-sm" value={form.asunto} onChange={(e) => setForm({ ...form, asunto: e.target.value })} />
          </label>
          <label className="fld">
            <span className="lbl">Mensaje</span>
            <textarea
              className="inp"
              rows={4}
              value={form.plantilla}
              onChange={(e) => setForm({ ...form, plantilla: e.target.value })}
              style={{ height: "auto", padding: 8, fontSize: 13 }}
            />
          </label>
          <span className="hint">Variables: {a.variables.map((v) => `{${v}}`).join(" ")}</span>
          {error && <AvisoError mensaje={error} />}
          <div className="row" style={{ justifyContent: "flex-end" }}>
            <button type="button" className="btn btn-p btn-sm" onClick={guardar} disabled={guardando}>
              {guardando ? "Guardando…" : "Guardar"}
            </button>
          </div>
        </div>
      )}
      {!abierta && error && <AvisoError mensaje={error} />}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: 4, gap: 8 }}>
        <button type="button" className="btn btn-g btn-sm" onClick={() => setAbierta(!abierta)}>
          {abierta ? "Ocultar detalles" : "Ver detalles ›"}
        </button>
        <span style={{ display: "flex", alignItems: "center", gap: 10 }}>
          {porEnviar > 0 && <span className="hint">{porEnviar} por enviar</span>}
          <button type="button" className={a.activa ? "sw on" : "sw"} onClick={alternar} aria-pressed={a.activa} aria-label={`Activar ${a.titulo}`} />
        </span>
      </div>
    </section>
  );
}

function enlaceEnvio(e: Envio): string {
  return e.canal === "whatsapp" ? `https://wa.me/${e.destino}?text=${encodeURIComponent(e.mensaje)}` : enlaceCorreo(e.asunto, e.mensaje, e.destino);
}

export function VentasPage() {
  const [estado, setEstado] = useState<EstadoVentas | null>(null);
  const [envios, setEnvios] = useState<Envio[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [cargando, setCargando] = useState(true);
  const [activando, setActivando] = useState(false);
  const [ejecutando, setEjecutando] = useState(false);
  const [filtro, setFiltro] = useState<Filtro>("por_enviar");
  const [confirmarDesactivar, setConfirmarDesactivar] = useState(false);
  const [toast, mostrarToast] = useAviso();

  const cargarEnvios = useCallback(async () => {
    try {
      setEnvios(await solucionesApi.listarEnvios({ limit: 300 }));
    } catch (err) {
      setError(mensajeError(err));
    }
  }, []);

  const cargar = useCallback(async () => {
    setCargando(true);
    try {
      const e = await solucionesApi.estadoVentas();
      setEstado(e);
      setError(null);
      if (e.activo) await cargarEnvios();
    } catch (err) {
      setError(mensajeError(err));
    } finally {
      setCargando(false);
    }
  }, [cargarEnvios]);

  useEffect(() => {
    void cargar();
  }, [cargar]);

  async function activar(activo: boolean) {
    setActivando(true);
    try {
      const e = await solucionesApi.activarVentas(activo);
      setEstado(e);
      setConfirmarDesactivar(false);
      if (activo) await cargarEnvios();
    } catch (err) {
      mostrarToast("No se pudo cambiar el módulo", { detalle: mensajeError(err), error: true });
    } finally {
      setActivando(false);
    }
  }

  async function ejecutar() {
    setEjecutando(true);
    try {
      const r = await solucionesApi.ejecutarAutomatizaciones();
      await cargar();
      const resumen = [
        r.omitidos_sin_contacto ? `${r.omitidos_sin_contacto} cliente(s) sin teléfono o correo` : "",
        r.entrega && r.entrega.enviados + r.entrega.errores > 0
          ? `Entregados por el canal: ${r.entrega.enviados}, con error: ${r.entrega.errores}`
          : "",
      ]
        .filter(Boolean)
        .join(" · ");
      mostrarToast(r.motivo ? "No se generaron envíos" : `${r.creados} envío(s) nuevo(s)`, { detalle: r.motivo ?? (resumen || undefined) });
    } catch (err) {
      mostrarToast("No se pudieron revisar las automatizaciones", { detalle: mensajeError(err), error: true });
    } finally {
      setEjecutando(false);
    }
  }

  async function marcar(e: Envio, nuevo: "enviado_manual" | "descartado") {
    try {
      const r = await solucionesApi.marcarEnvio(e.id, nuevo);
      setEnvios((prev) => prev.map((x) => (x.id === r.id ? r : x)));
    } catch (err) {
      mostrarToast("No se pudo actualizar el envío", { detalle: mensajeError(err), error: true });
    }
  }

  function cambioAutomatizacion(a: Automatizacion, texto?: string) {
    setEstado((prev) => (prev ? { ...prev, automatizaciones: prev.automatizaciones.map((x) => (x.clave === a.clave ? a : x)) } : prev));
    if (texto) mostrarToast(texto);
  }

  if (cargando && !estado)
    return (
      <div className="content">
        <Cargando />
      </div>
    );
  if (!estado) {
    return (
      <div className="content">
        <AvisoError titulo="No se pudo cargar Kontara Ventas." mensaje={error ?? ""} onReintentar={cargar} />
      </div>
    );
  }

  if (!estado.activo) {
    return (
      <div className="content" style={{ maxWidth: 1120, paddingTop: 16 }}>
        <Promo onActivar={() => activar(true)} activando={activando} />
        {toast}
      </div>
    );
  }

  const diasReactivacion = estado.automatizaciones.find((a) => a.clave === "reactivacion")?.dias ?? 90;
  const sinCanal = !estado.canales?.correo && !estado.canales?.whatsapp;
  const filas = envios.filter((e) =>
    filtro === "todos"
      ? true
      : filtro === "por_enviar"
        ? e.estado === "pendiente_canal" || e.estado === "error"
        : e.estado === "enviado" || e.estado === "enviado_manual",
  );
  const titulo = Object.fromEntries(estado.automatizaciones.map((a) => [a.clave, a.titulo]));

  return (
    <div className="content" style={{ maxWidth: 1120 }}>
      <div className="ph">
        <div>
          <h1>{MARCA} Ventas</h1>
          <p>Automatizaciones activas para fidelizar clientes. Se envían por correo y WhatsApp desde tus canales configurados.</p>
        </div>
        <button type="button" className="btn btn-o" onClick={() => setConfirmarDesactivar(true)}>
          Desactivar módulo
        </button>
      </div>
      {estado.habilitado ? (
        <div className="al al-s">
          <span>
            <b>¡Módulo activado!</b> Prueba gratis hasta el {fechaLegible(estado.prueba_hasta)}. Activa las automatizaciones que quieras usar.
          </span>
        </div>
      ) : (
        <div className="al al-w">
          <span>
            <b>
              Tu prueba gratis de {MARCA} Ventas terminó el {fechaLegible(estado.prueba_hasta)}.
            </b>{" "}
            Las automatizaciones están en pausa hasta que elijas el plan Pyme o Empresarial. <Link to="/planes">Ver planes</Link>
          </span>
        </div>
      )}
      {sinCanal && (
        <div className="al al-i">
          <span>
            <b>Sin canal de envío configurado.</b> Los mensajes quedan en la cola «Por enviar» y los envías tú con un clic por WhatsApp o correo. Con
            un servidor de correo (SMTP) o WhatsApp Business configurados se envían solos.
          </span>
        </div>
      )}
      {error && <AvisoError mensaje={error} onReintentar={cargar} />}

      <div className="ve-autos">
        {estado.automatizaciones.map((a) => (
          <TarjetaAutomatizacion key={a.clave} a={a} diasReactivacion={diasReactivacion} onCambio={cambioAutomatizacion} />
        ))}
      </div>

      <section className="card">
        <div className="card-h">
          <div>
            <h2>Envíos</h2>
            <span className="hint">
              {estado.ultima_ejecucion
                ? `Última revisión: ${new Date(estado.ultima_ejecucion).toLocaleString("es-DO")}`
                : "Se revisa automáticamente una vez al día."}
            </span>
          </div>
          <div className="row">
            <div className="seg" role="group" aria-label="Filtrar envíos">
              {(
                [
                  ["por_enviar", "Por enviar"],
                  ["enviados", "Enviados"],
                  ["todos", "Todos"],
                ] as const
              ).map(([id, label]) => (
                <button key={id} type="button" className={filtro === id ? "on" : ""} onClick={() => setFiltro(id)}>
                  {label}
                </button>
              ))}
            </div>
            <button type="button" className="btn btn-o btn-sm" onClick={ejecutar} disabled={ejecutando || !estado.habilitado}>
              {ejecutando ? "Revisando…" : "Revisar ahora"}
            </button>
          </div>
        </div>
        {filas.length === 0 ? (
          <p className="pad mut">
            {filtro === "por_enviar" ? "No hay mensajes por enviar. Activa una automatización y pulsa «Revisar ahora»." : "Sin envíos en esta vista."}
          </p>
        ) : (
          <div className="tw">
            <table className="tbl" style={{ minWidth: 760 }}>
              <thead>
                <tr>
                  <th>Fecha</th>
                  <th>Automatización</th>
                  <th>Cliente</th>
                  <th>Canal</th>
                  <th>Mensaje</th>
                  <th>Estado</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {filas.map((e) => (
                  <tr key={e.id} data-envio={e.automatizacion}>
                    <td>{fechaLegible(e.creado_en)}</td>
                    <td>{titulo[e.automatizacion]}</td>
                    <td>
                      {e.cliente_nombre}
                      {e.encf && <span className="hint mono"> {e.encf}</span>}
                    </td>
                    <td>
                      {e.canal === "whatsapp" ? "WhatsApp" : "Correo"}
                      <span className="hint"> {e.canal === "whatsapp" ? `+${e.destino}` : e.destino}</span>
                    </td>
                    <td className="ve-msg" title={e.mensaje}>
                      <span className="ve-msg-txt">{e.mensaje}</span>
                      {e.error && <span className="row-motivo">{e.error}</span>}
                    </td>
                    <td>
                      <span className={ESTADO_ENVIO[e.estado].badge}>{ESTADO_ENVIO[e.estado].label}</span>
                    </td>
                    <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                      {(e.estado === "pendiente_canal" || e.estado === "error") && (
                        <>
                          <a className="btn btn-g btn-sm" href={enlaceEnvio(e)} target="_blank" rel="noopener noreferrer">
                            {e.canal === "whatsapp" ? "Abrir WhatsApp" : "Abrir correo"}
                          </a>
                          <button type="button" className="btn btn-g btn-sm" onClick={() => marcar(e, "enviado_manual")}>
                            Marcar enviado
                          </button>
                          <button type="button" className="btn btn-g btn-sm" onClick={() => marcar(e, "descartado")}>
                            Descartar
                          </button>
                        </>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {confirmarDesactivar && (
        <Modal
          titulo={`Desactivar ${MARCA} Ventas`}
          onCerrar={() => setConfirmarDesactivar(false)}
          pie={
            <>
              <button type="button" className="btn btn-o" onClick={() => setConfirmarDesactivar(false)}>
                Cancelar
              </button>
              <button type="button" className="btn btn-d" onClick={() => activar(false)} disabled={activando}>
                Desactivar
              </button>
            </>
          }
        >
          <p style={{ margin: 0 }}>
            Se detienen todas las automatizaciones; la cola de envíos y tu configuración se conservan. Volver a activarlo no reinicia la prueba
            gratis.
          </p>
        </Modal>
      )}
      {toast}
    </div>
  );
}
