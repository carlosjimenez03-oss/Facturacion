import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { cargarEmpresa, nombreEmpresa, useEmpresa } from "../lib/configuracionApi";
import { empresaEnUso } from "../lib/empresaActual";
import { cambiarEmpresa, cargarMisEmpresas, useMisEmpresas } from "../lib/empresasApi";
import { ERP_API_URL } from "../lib/erpApi";
import type { ResumenSincronizacion, ResumenTareas } from "../lib/operacionApi";
import { EMPRESA_NOMBRE, EMPRESA_RNC, formatoRnc, MARCA, nombreAmbiente } from "../lib/marca";
import { AUTH_CONFIGURADA, cerrarSesion, fetchConSesion } from "../lib/sesion";
import { motivoRechazo, useFacturas } from "../lib/useFacturas";
import { useSesion } from "../pages/plataforma/useSesion";
import { leerCola } from "../pages/pos/posAlmacen";
import { sinSincronizar } from "../pages/pos/posOffline";
import { AgregarEmpresa } from "./AgregarEmpresa";
import {
  IconActualizar,
  IconAlerta,
  IconApps,
  IconAyuda,
  IconBuscar,
  IconCampana,
  IconCerrar,
  IconCheck,
  IconChevron,
  IconConfig,
  IconEmpresa,
  IconEscudo,
  IconExterno,
  IconHoja,
  IconIngresos,
  IconMas,
  IconNomina,
  IconPlan,
  IconPos,
  IconRechazo,
  IconSalir,
  IconUsuario,
} from "./icons";
import { MENU_PRINCIPAL, MENU_SOLUCIONES } from "./menu";

// Sin inicio de sesión en el build, nginx no reenvía ninguna empresa (no hay
// quién verifique la membresía): todo va a la predeterminada, así que no se
// ofrece cambiar ni agregar. En desarrollo (Vite directo a las APIs, sin
// nginx) VITE_MULTIEMPRESA_SIN_SESION=true lo permite.
const SELECCION_DISPONIBLE = AUTH_CONFIGURADA || import.meta.env.VITE_MULTIEMPRESA_SIN_SESION === "true";

type Panel = "nuevo" | "ayuda" | "notif" | "eco" | "apps" | "empresa" | "usuario" | null;
type Modal = "pos" | "agregar-empresa" | null;

const AYUDA = [
  { t: "Emitir una factura electrónica", d: "Elija el tipo de e-CF, agregue los productos, genere el XML, firme y envíe a la DGII.", to: "/facturas/nueva" },
  { t: "Qué hacer si la DGII rechaza un e-CF", d: "Abra la factura en el listado: el detalle muestra el código y el mensaje devuelto por la DGII.", to: "/facturas" },
  { t: "Configurar la facturación electrónica", d: "Datos de la empresa, certificado digital, ambiente DGII y secuencias autorizadas.", to: "/configuracion" },
  { t: "Formato 606", d: "Registre las compras del período y genere el archivo para la Oficina Virtual.", to: "/formato-606" },
  { t: "Inventario y costos", d: "Productos, compras a proveedores y movimientos de existencia.", to: "/inventario/productos" },
];

/** Iniciales para el avatar: "María Rosario" -> "MR", "maria@x.do" -> "M". */
function iniciales(nombre: string, correo: string): string {
  const partes = nombre.trim().split(/\s+/).filter(Boolean);
  if (partes.length >= 2) return (partes[0][0] + partes[1][0]).toUpperCase();
  return (partes[0]?.[0] ?? correo[0] ?? "U").toUpperCase();
}

/**
 * Consulta opcional a endpoints de otros módulos (Mis tareas: GET
 * /tareas/resumen; Centro de sincronización: GET /sincronizacion/resumen).
 * Si fallan, devuelve null y la barra sigue como si no estuvieran.
 */
function useOpcional<T>(ruta: string, cadaMs: number): T | null {
  const [valor, setValor] = useState<T | null>(null);
  useEffect(() => {
    let vivo = true;
    const leer = () =>
      fetchConSesion(`${ERP_API_URL}${ruta}`)
        .then((r) => (r.ok ? (r.json() as Promise<T>) : null))
        .catch(() => null)
        .then((v) => vivo && setValor(v));
    void leer();
    const t = window.setInterval(leer, cadaMs);
    return () => {
      vivo = false;
      window.clearInterval(t);
    };
  }, [ruta, cadaMs]);
  return valor;
}

export function Topbar() {
  const navigate = useNavigate();
  const sesion = useSesion();
  const empresa = useEmpresa();
  const misEmpresas = useMisEmpresas();
  const empresaId = misEmpresas?.actual ?? empresaEnUso();
  const [cambiando, setCambiando] = useState(false);
  const { facturas, enLinea, error } = useFacturas();
  const [panel, setPanel] = useState<Panel>(null);
  const [modal, setModal] = useState<Modal>(null);
  const [q, setQ] = useState("");
  const [leidas, setLeidas] = useState(false);
  const toggle = (p: Panel) => setPanel((actual) => (actual === p ? null : p));

  const resumenTareas = useOpcional<ResumenTareas>("/tareas/resumen", 120_000);
  const tareas = resumenTareas?.pendientes ?? null;
  // Modo contingencia del Centro de sincronización (el estado que guarda facturas-api).
  const sincronizacion = useOpcional<ResumenSincronizacion>("/sincronizacion/resumen", 60_000);
  const contingencia = Boolean(sincronizacion?.contingencia.activa);

  const ambiente = nombreAmbiente(empresa?.ambiente ?? facturas[0]?.ambiente);
  const nombre = nombreEmpresa(empresa, EMPRESA_NOMBRE);
  const razonSocial = empresa?.razon_social || nombre;
  const rnc = empresa?.rnc || EMPRESA_RNC || facturas[0]?.rnc_emisor || "";
  const usuarioNombre = sesion?.usuario.nombre || (AUTH_CONFIGURADA ? "" : "Usuario sin sesión");
  const usuarioCorreo = sesion?.usuario.email ?? "";

  const notificaciones = useMemo(
    () =>
      [...facturas]
        .filter((f) => f.estado === "rechazado" || f.estado === "aceptado" || f.estado === "error")
        .sort((a, b) => b.actualizado_en.localeCompare(a.actualizado_en))
        .slice(0, 8),
    [facturas],
  );
  const rechazosSinLeer = leidas ? 0 : notificaciones.filter((f) => f.estado !== "aceptado").length;
  // La campana cuenta las tareas pendientes (Mis tareas); si ese módulo aún no responde, los rechazos sin leer.
  const contador = tareas ?? rechazosSinLeer;

  const resultados = useMemo(() => {
    const texto = q.trim().toLowerCase();
    if (texto.length < 2) return [];
    const deFacturas = facturas
      .filter((f) => `${f.encf ?? ""} ${f.track_id ?? ""} ${f.tipo_ecf}`.toLowerCase().includes(texto))
      .slice(0, 5)
      .map((f) => ({ k: "e-CF", t: f.encf || "(sin e-NCF)", s: `Tipo ${f.tipo_ecf} · ${f.estado}`, to: `/facturas/${f.id}` }));
    const deMenu = [...MENU_PRINCIPAL, ...MENU_SOLUCIONES]
      .flatMap((m) => (m === "separador" ? [] : m.hijos ? m.hijos.map((h) => ({ label: h.label, to: h.to })) : [{ label: m.label, to: m.to ?? "/" }]))
      .filter((m) => m.label.toLowerCase().includes(texto))
      .slice(0, 5)
      .map((m) => ({ k: "Menú", t: m.label, s: "Ir a la pantalla", to: m.to }));
    return [...deFacturas, ...deMenu];
  }, [q, facturas]);

  const ir = (to: string) => {
    setPanel(null);
    setQ("");
    navigate(to);
  };

  /** Cambia de empresa (recarga la aplicación en la otra). Si el POS de esta
   * tiene ventas sin sincronizar, se avisa: se quedan en este navegador,
   * guardadas con su empresa, y se envían al volver a ella. */
  async function cambiarA(id: number, nombreOtra: string) {
    let pendientes = 0;
    try {
      pendientes = sinSincronizar(await leerCola()).length;
    } catch {
      // sin IndexedDB: no hay cola sin conexión
    }
    if (
      pendientes > 0 &&
      !window.confirm(
        `Hay ${pendientes} venta(s) del POS de ${nombre} sin sincronizar. Se quedan guardadas en este navegador y se enviarán a ${nombre} cuando vuelva a ella. ¿Cambiar a ${nombreOtra}?`,
      )
    )
      return;
    setCambiando(true);
    await cambiarEmpresa(id);
  }

  async function salir() {
    setPanel(null);
    await cerrarSesion();
    navigate("/login", { replace: true });
  }

  const conexion = contingencia
    ? { cls: "conn-cont", txt: "Modo contingencia", tip: "La DGII no responde: los e-CF se firman, se entregan con la leyenda de contingencia y se envían en lotes al restablecerse el servicio." }
    : enLinea
      ? { cls: "conn-on", txt: "En línea", tip: "La API de facturación responde." }
      : { cls: "conn-off", txt: "Sin conexión", tip: `Sin conexión con la API de facturación. ${error ?? ""}` };

  return (
    <header className="tb">
      {panel && panel !== "ayuda" && <button type="button" className="pop-scrim" onClick={() => setPanel(null)} aria-label="Cerrar menú" />}

      <div className="tb-c">
        <div className="tb-search">
          <IconBuscar />
          <input
            className="inp"
            type="search"
            placeholder="Buscar"
            aria-label="Búsqueda global: e-NCF, Track ID o pantalla"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && resultados[0]) ir(resultados[0].to);
              if (e.key === "Escape") setQ("");
            }}
          />
          {q.trim().length >= 2 && (
            <div className="pop" style={{ left: 0, right: 0, width: "auto" }}>
              <div className="pop-h">Resultados para “{q}”</div>
              <div className="pop-sec">
                {resultados.length === 0 && <div className="pop-empty">Sin resultados. Pruebe con un e-NCF o el nombre de una pantalla.</div>}
                {resultados.map((r) => (
                  <button key={r.k + r.to + r.t} type="button" className="pop-i" onClick={() => ir(r.to)}>
                    <span className="chip">{r.k}</span>
                    <span className="pop-txt">
                      <b>{r.t}</b>
                      <span className="hint">{r.s}</span>
                    </span>
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
        <div className="pw">
          <button type="button" className={`tb-plus${panel === "nuevo" ? " act" : ""}`} onClick={() => toggle("nuevo")} aria-expanded={panel === "nuevo"} aria-label="Crear nuevo">
            <IconMas />
          </button>
          {panel === "nuevo" && (
            <div className="pop pop-center" style={{ width: 260 }}>
              <div className="pop-h">Crear nuevo</div>
              <div className="pop-sec">
                <button type="button" className="pop-i" onClick={() => ir("/facturas/nueva")}>Factura de venta</button>
                <button type="button" className="pop-i" onClick={() => ir("/compras/facturas/nueva")}>Factura de compra</button>
                <button type="button" className="pop-i" onClick={() => ir("/contactos/nuevo")}>Cliente o proveedor</button>
                <button type="button" className="pop-i" onClick={() => ir("/inventario/productos/nuevo")}>Producto</button>
              </div>
            </div>
          )}
        </div>
      </div>

      <div className="tb-right">
        <span className="tip tb-env" data-tip="Ambiente de la DGII al que se envían los e-CF. Solo Producción tiene validez fiscal.">
          <span className={`env ${ambiente === "Producción" ? "env-prod" : ambiente === "Certificación" ? "env-cert" : "env-test"}`}>DGII · {ambiente}</span>
        </span>
        <span className="tip r tb-conn" data-tip={conexion.tip}>
          <span className={`conn ${conexion.cls}`}>
            <i />
            <span className="conn-txt">{conexion.txt}</span>
          </span>
        </span>

        <button type="button" className={`ib tb-opc${panel === "ayuda" ? " act" : ""}`} onClick={() => toggle("ayuda")} aria-label="Centro de ayuda">
          <IconAyuda />
        </button>

        <div className="pw">
          <button type="button" className={`ib${panel === "notif" ? " act" : ""}`} onClick={() => toggle("notif")} aria-expanded={panel === "notif"} aria-label={`Notificaciones, ${contador} pendientes`}>
            <IconCampana />
            {contador > 0 && <span className="dot-n">{contador > 99 ? "99+" : contador}</span>}
          </button>
          {panel === "notif" && (
            <div className="pop" style={{ width: 380 }}>
              <div className="pop-h">
                Notificaciones
                <button type="button" className="btn btn-g btn-sm" onClick={() => setLeidas(true)}>
                  Marcar todo como leído
                </button>
              </div>
              {tareas !== null && (
                <div className="pop-sec">
                  <button type="button" className="pop-i" onClick={() => ir("/tareas")}>
                    <span className="chip">{tareas}</span>
                    <span className="pop-txt">
                      <b>Tareas pendientes</b>
                      <span className="hint">
                        {resumenTareas && (resumenTareas.vencidas > 0 || resumenTareas.vencen_hoy > 0)
                          ? `${resumenTareas.vencidas} vencidas · ${resumenTareas.vencen_hoy} vencen hoy`
                          : "Vencimientos fiscales y pendientes de gestión"}
                      </span>
                    </span>
                  </button>
                </div>
              )}
              <div className="pop-sec">
                {notificaciones.length === 0 && <div className="pop-empty">No hay respuestas recientes de la DGII.</div>}
                {notificaciones.map((f) => {
                  const malo = f.estado !== "aceptado";
                  return (
                    <button key={f.id} type="button" className="pop-i pop-top" onClick={() => ir(`/facturas/${f.id}`)}>
                      <span className={`notif-ic ${malo ? "bad" : "good"}`}>{malo ? <IconRechazo /> : <IconCheck />}</span>
                      <span className="pop-txt">
                        <b>
                          {f.encf || "(sin e-NCF)"} {f.estado === "aceptado" ? "aceptada" : f.estado === "rechazado" ? "rechazada" : "con error"}
                        </b>
                        <span className={malo ? "notif-bad" : "hint"}>{malo ? motivoRechazo(f) : `Track ID ${f.track_id ?? "—"}`}</span>
                        <span className="hint">{new Date(f.actualizado_en).toLocaleString("es-DO")}</span>
                      </span>
                    </button>
                  );
                })}
              </div>
            </div>
          )}
        </div>

        <div className="pw tb-opc">
          <button type="button" className={`ib${panel === "eco" ? " act" : ""}`} onClick={() => toggle("eco")} aria-label="Compromiso ambiental">
            <span className="eco">
              <IconHoja />
            </span>
          </button>
          {panel === "eco" && (
            <div className="pop eco-pop" style={{ width: 320 }}>
              <span className="eco-big">
                <IconHoja />
              </span>
              <b>Compromiso ambiental</b>
              <div className="eco-stats">
                <div>
                  <strong>{facturas.length}</strong> <small>/500</small>
                  <span className="hint">Facturas creadas</span>
                </div>
                <div>
                  <strong>{Math.floor(facturas.length / 500)}</strong>
                  <span className="hint">Árboles plantados</span>
                </div>
              </div>
              <p>Cada 500 comprobantes electrónicos que emite evita imprimir papel y suma un árbol a la reforestación.</p>
            </div>
          )}
        </div>

        <div className="pw tb-opc">
          <button type="button" className={`ib${panel === "apps" ? " act" : ""}`} onClick={() => toggle("apps")} aria-expanded={panel === "apps"} aria-label="Mis soluciones">
            <IconApps />
          </button>
          {panel === "apps" && (
            <div className="pop">
              <div className="pop-h">Mis soluciones</div>
              <div className="pop-sec">
                <button type="button" className="pop-i" onClick={() => ir("/")}>
                  <span className="app-ic">
                    <IconIngresos />
                  </span>
                  <span className="pop-txt">
                    <b className="teal">{MARCA} Facturación</b>
                    <span className="hint">Factura electrónica, contabilidad e inventario.</span>
                  </span>
                  <IconCheck />
                </button>
                <button type="button" className="pop-i" onClick={() => ir("/nomina")}>
                  <span className="app-ic">
                    <IconNomina />
                  </span>
                  <span className="pop-txt">
                    <b className="teal">Nómina</b>
                    <span className="hint">Nómina con TSS, ISR e INFOTEP.</span>
                  </span>
                </button>
              </div>
              <div className="pop-sep" />
              <div className="pop-h" style={{ paddingTop: 8 }}>Descubre más</div>
              <div className="pop-sec">
                <button
                  type="button"
                  className="pop-i"
                  onClick={() => {
                    setPanel(null);
                    setModal("pos");
                  }}
                >
                  <span className="app-ic">
                    <IconPos />
                  </span>
                  <span className="pop-txt">
                    <b className="teal">POS</b>
                    <span className="hint">Vende y emite e-CF de consumo en caja.</span>
                  </span>
                  <span className="bd b-acep nodot" style={{ boxShadow: "inset 0 0 0 1px #BBF7D0" }}>
                    Probar gratis
                  </span>
                </button>
              </div>
              <div className="pop-f">
                <Link to="/configuracion" className="link-strong" onClick={() => setPanel(null)} style={{ display: "inline-flex", gap: 6, alignItems: "center" }}>
                  <IconConfig />
                  Administrar mis soluciones
                </Link>
              </div>
            </div>
          )}
        </div>

        <div className="pw">
          <button type="button" className={`co${panel === "empresa" ? " act" : ""}`} onClick={() => toggle("empresa")} aria-expanded={panel === "empresa"} aria-label="Cambiar empresa">
            <span className="co-av">{nombre.charAt(0).toUpperCase()}</span>
            <span className="co-txt">{nombre}</span>
            <IconChevron width={16} height={16} />
          </button>
          {panel === "empresa" && (
            <div className="pop" style={{ width: 360 }}>
              <div className="pop-h" style={{ fontSize: 14, color: "#111827" }}>
                Mis empresas
                <span style={{ display: "flex", gap: 2 }}>
                  <button
                    type="button"
                    className="ib"
                    style={{ width: 32, height: 32 }}
                    onClick={() => {
                      void cargarEmpresa(true);
                      void cargarMisEmpresas(true);
                    }}
                    aria-label="Actualizar lista"
                  >
                    <IconActualizar width={17} height={17} />
                  </button>
                  <button type="button" className="ib" style={{ width: 32, height: 32 }} onClick={() => setPanel(null)} aria-label="Cerrar">
                    <IconCerrar />
                  </button>
                </span>
              </div>
              <div className="pop-sec co-lista">
                {(misEmpresas?.empresas.length ? misEmpresas.empresas : [null]).map((e) => {
                  const actual = e === null || e.id === empresaId;
                  const titulo = e ? e.razon_social : razonSocial;
                  const nombreCorto = e ? e.nombre_comercial || e.razon_social : nombre;
                  const rncEmpresa = e ? e.rnc : rnc;
                  return (
                    <button
                      key={e?.id ?? "actual"}
                      type="button"
                      className={`pop-i${actual ? " act" : ""}`}
                      aria-current={actual ? "true" : undefined}
                      disabled={cambiando || (!actual && !SELECCION_DISPONIBLE)}
                      onClick={() => (actual || !e ? undefined : void cambiarA(e.id, nombreCorto))}
                    >
                      <span className="co-av">{nombreCorto.charAt(0).toUpperCase()}</span>
                      <span className="pop-txt">
                        <b>{titulo}</b>
                        <span className="hint">
                          {rncEmpresa ? `RNC ${formatoRnc(rncEmpresa)}` : "RNC sin configurar"} · Emisor electrónico
                        </span>
                      </span>
                      {e && misEmpresas && misEmpresas.empresas.length > 1 && e.rol === "admin" && <span className="co-rol">Admin</span>}
                      {actual && <IconCheck />}
                    </button>
                  );
                })}
              </div>
              {(misEmpresas?.empresas.length ?? 0) <= 1 && (
                <div className="co-empty">
                  <span className="co-empty-ic">
                    <IconEmpresa />
                    <b>+</b>
                  </span>
                  <b style={{ color: "#111827", fontSize: 14.5 }}>¡Agrega otras empresas a tu cuenta!</b>
                  <span className="hint" style={{ fontSize: 13, lineHeight: 1.5 }}>
                    Gestiona todas tus empresas en un mismo lugar y simplifica tu administración.
                  </span>
                </div>
              )}
              {misEmpresas && (!misEmpresas.puede_agregar || !SELECCION_DISPONIBLE) && (
                <div className="co-motivo">
                  {!SELECCION_DISPONIBLE
                    ? "Para trabajar con varias empresas active el inicio de sesión (ver docs/AUTH.md): sin él, todas las pantallas usan la empresa predeterminada."
                    : (misEmpresas.motivo ?? "Solo un administrador de la empresa puede agregar otras.")}
                </div>
              )}
              <div style={{ padding: "12px 16px 16px" }}>
                <button
                  type="button"
                  className="btn co-add"
                  disabled={!misEmpresas?.puede_agregar || !SELECCION_DISPONIBLE}
                  onClick={() => {
                    setPanel(null);
                    setModal("agregar-empresa");
                  }}
                >
                  + Agregar empresa
                </button>
              </div>
            </div>
          )}
        </div>

        <div className="pw">
          <button type="button" className={`av${panel === "usuario" ? " act" : ""}`} onClick={() => toggle("usuario")} aria-expanded={panel === "usuario"} aria-label="Menú de usuario">
            {iniciales(usuarioNombre, usuarioCorreo || nombre)}
          </button>
          {panel === "usuario" && (
            <div className="pop" style={{ width: 300 }}>
              <div className="pop-user">
                <b>{usuarioNombre || usuarioCorreo}</b>
                <span className="hint" style={{ fontSize: 13 }}>
                  {usuarioNombre ? usuarioCorreo : ""}
                  {!AUTH_CONFIGURADA && "Inicio de sesión no configurado"}
                </span>
              </div>
              <div className="pop-sep" />
              <div className="pop-row">
                <span style={{ color: "#111827" }}>Facturación electrónica</span>
                <span className={`bd ${enLinea ? "b-acep" : "b-borr"} nodot`}>{enLinea ? "Activada" : "Sin conexión"}</span>
              </div>
              <div className="pop-row solo-movil">
                <span style={{ color: "#111827" }}>Ambiente DGII</span>
                <span className="hint">{ambiente}{contingencia ? " · contingencia" : ""}</span>
              </div>
              <div className="pop-sep" />
              <div className="pop-sec">
                <button type="button" className="pop-i" onClick={() => ir("/planes")}>
                  <IconPlan />
                  <span style={{ flex: 1 }}>Consumo del plan</span>
                  <IconExterno className="pop-ext" />
                </button>
                <button type="button" className="pop-i" onClick={() => ir("/configuracion")}>
                  <IconConfig />
                  <span style={{ flex: 1 }}>Configuración</span>
                </button>
                <button type="button" className="pop-i" onClick={() => ir("/cuenta/perfil")}>
                  <IconUsuario />
                  <span style={{ flex: 1 }}>Mi perfil</span>
                  <IconExterno className="pop-ext" />
                </button>
                <button type="button" className="pop-i" onClick={() => ir("/cuenta/seguridad")}>
                  <IconEscudo />
                  <span style={{ flex: 1 }}>Seguridad</span>
                  <IconExterno className="pop-ext" />
                </button>
                <button type="button" className="pop-i" onClick={() => ir("/estado-soluciones")}>
                  <span className="pop-dot">
                    <i className={enLinea && !contingencia ? "" : "bad"} />
                  </span>
                  <span style={{ flex: 1 }}>Estado de las soluciones</span>
                  <IconExterno className="pop-ext" />
                </button>
                <button type="button" className="pop-i solo-movil" onClick={() => setPanel("ayuda")}>
                  <IconAyuda />
                  <span style={{ flex: 1 }}>Centro de ayuda</span>
                </button>
              </div>
              <div className="pop-sep" />
              <div className="pop-sec">
                <button
                  type="button"
                  className="pop-i"
                  onClick={() => void salir()}
                  disabled={!AUTH_CONFIGURADA}
                  title={AUTH_CONFIGURADA ? undefined : "El inicio de sesión no está configurado (ver docs/AUTH.md)"}
                >
                  <IconSalir />
                  <span style={{ flex: 1 }}>Cerrar sesión</span>
                </button>
              </div>
            </div>
          )}
        </div>
      </div>

      {panel === "ayuda" && (
        <aside className="help-drawer" role="dialog" aria-label="Centro de ayuda">
          <div className="help-h">
            <h2>Centro de ayuda</h2>
            <button type="button" className="ib" onClick={() => setPanel(null)} aria-label="Cerrar centro de ayuda">
              <IconCerrar />
            </button>
          </div>
          <div className="help-hero">
            <b>¿Cómo podemos ayudarte hoy?</b>
            <span>Guías rápidas de las tareas más comunes.</span>
          </div>
          <div className="help-list">
            {AYUDA.map((a) => (
              <Link key={a.t} to={a.to} className="help-item" onClick={() => setPanel(null)}>
                <b>{a.t}</b>
                <span>{a.d}</span>
              </Link>
            ))}
            <div className="al al-i">
              <IconAlerta />
              <span>
                Documentación técnica de los servicios DGII: <code>docs/dgii/SERVICIOS_DGII.md</code> en el repositorio.
              </span>
            </div>
          </div>
        </aside>
      )}

      {modal === "pos" && <ModalPos onCerrar={() => setModal(null)} onProbar={() => { setModal(null); navigate("/pos"); }} />}
      {modal === "agregar-empresa" && <AgregarEmpresa onCerrar={() => setModal(null)} />}
    </header>
  );
}

function ModalPos({ onCerrar, onProbar }: { onCerrar: () => void; onProbar: () => void }) {
  const punto = (texto: string) => (
    <div className="pos-li">
      <IconCheck />
      <span>{texto}</span>
    </div>
  );
  return (
    <div className="scrim" style={{ zIndex: 120 }} role="presentation" onClick={onCerrar}>
      <div className="pos-modal" role="dialog" aria-modal="true" aria-labelledby="pos-t" onClick={(e) => e.stopPropagation()}>
        <button type="button" className="ib" onClick={onCerrar} aria-label="Cerrar">
          <IconCerrar />
        </button>
        <div className="pos-txt">
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span className="app-ic" style={{ width: 32, height: 32, background: "#14B8A6", color: "#fff" }}>
              <IconPos />
            </span>
            <span style={{ lineHeight: 1 }}>
              <span style={{ display: "block", fontSize: 10, color: "#6B7280" }}>{MARCA.toLowerCase()}</span>
              <b style={{ fontSize: 17, color: "#111827" }}>POS</b>
            </span>
          </div>
          <h2 id="pos-t">
            Conecta tu <span>punto de venta</span> y automatiza ventas
          </h2>
          <p style={{ margin: 0, fontSize: 14, color: "#374151" }}>Al conectar {MARCA} POS con tu facturación, podrás:</p>
          <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
            {punto("Emitir e-CF de consumo desde caja y actualizar tu contabilidad al instante.")}
            {punto("Cerrar el mes sin tareas repetitivas.")}
            {punto("Sincronizar caja, ingresos y bancos en un solo lugar.")}
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 16, alignItems: "center", marginTop: 8 }}>
            <button type="button" className="btn" style={{ background: "#2BA89D", color: "#fff" }} onClick={onProbar}>
              Probar {MARCA} POS
            </button>
            <button type="button" className="btn btn-g" style={{ color: "#0F766E" }} onClick={onCerrar}>
              Seguir en Facturación
            </button>
          </div>
        </div>
        <div className="pos-art" aria-hidden="true">
          <div>
            {Array.from({ length: 6 }, (_, i) => (
              <i key={i} />
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
