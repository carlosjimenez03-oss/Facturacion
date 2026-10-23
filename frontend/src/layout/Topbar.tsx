import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { EMPRESA_NOMBRE, EMPRESA_RNC, formatoRnc, MARCA, nombreAmbiente } from "../lib/marca";
import { motivoRechazo, useFacturas } from "../lib/useFacturas";
import { IconAlerta, IconApps, IconAyuda, IconBuscar, IconCampana, IconCerrar, IconCheck, IconChevron, IconHoja, IconMas, IconRechazo, IconSalir } from "./icons";
import { MENU_PRINCIPAL, MENU_SOLUCIONES } from "./menu";

type Panel = "nuevo" | "ayuda" | "notif" | "eco" | "apps" | "empresa" | "usuario" | null;

const AYUDA = [
  { t: "Emitir una factura electrónica", d: "Elija el tipo de e-CF, agregue los productos, genere el XML, firme y envíe a la DGII.", to: "/facturas/nueva" },
  { t: "Qué hacer si la DGII rechaza un e-CF", d: "Abra la factura en el listado: el detalle muestra el código y el mensaje devuelto por la DGII.", to: "/facturas" },
  { t: "Formato 606", d: "Registre las compras del período y genere el archivo para la Oficina Virtual.", to: "/formato-606" },
  { t: "Inventario y costos", d: "Productos, compras a proveedores y movimientos de existencia.", to: "/inventario/productos" },
];

export function Topbar() {
  const navigate = useNavigate();
  const { facturas, enLinea, error } = useFacturas();
  const [panel, setPanel] = useState<Panel>(null);
  const [q, setQ] = useState("");
  const [leidas, setLeidas] = useState(false);
  const toggle = (p: Panel) => setPanel((actual) => (actual === p ? null : p));

  const ambiente = nombreAmbiente(facturas[0]?.ambiente);
  const rnc = EMPRESA_RNC || facturas[0]?.rnc_emisor || "";

  const notificaciones = useMemo(
    () =>
      [...facturas]
        .filter((f) => f.estado === "rechazado" || f.estado === "aceptado" || f.estado === "error")
        .sort((a, b) => b.actualizado_en.localeCompare(a.actualizado_en))
        .slice(0, 8),
    [facturas],
  );
  const sinLeer = leidas ? 0 : notificaciones.filter((f) => f.estado !== "aceptado").length;

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
                <button type="button" className="pop-i" onClick={() => ir("/facturas/nueva")}>Factura electrónica</button>
                <button type="button" className="pop-i" onClick={() => ir("/inventario/compras/nueva")}>Factura de compra</button>
                <button type="button" className="pop-i" onClick={() => ir("/inventario/productos/nuevo")}>Producto</button>
                <button type="button" className="pop-i" onClick={() => ir("/inventario/proveedores/nuevo")}>Proveedor</button>
              </div>
            </div>
          )}
        </div>
      </div>

      <div className="tb-right">
        <span className="tip" data-tip="Ambiente de la DGII al que se envían los e-CF. Solo Producción tiene validez fiscal.">
          <span className={`env ${ambiente === "Producción" ? "env-prod" : ambiente === "Certificación" ? "env-cert" : "env-test"}`}>DGII · {ambiente}</span>
        </span>
        <span className="tip r" data-tip={enLinea ? "La API de facturación responde." : `Sin conexión con la API de facturación. ${error ?? ""}`}>
          <span className={`conn ${enLinea ? "conn-on" : "conn-off"}`}>
            <i />
            {enLinea ? "En línea" : "Sin conexión"}
          </span>
        </span>

        <button type="button" className={`ib${panel === "ayuda" ? " act" : ""}`} onClick={() => toggle("ayuda")} aria-label="Centro de ayuda">
          <IconAyuda />
        </button>

        <div className="pw">
          <button type="button" className={`ib${panel === "notif" ? " act" : ""}`} onClick={() => toggle("notif")} aria-expanded={panel === "notif"} aria-label={`Notificaciones, ${sinLeer} sin leer`}>
            <IconCampana />
            {sinLeer > 0 && <span className="dot-n">{sinLeer}</span>}
          </button>
          {panel === "notif" && (
            <div className="pop" style={{ width: 380 }}>
              <div className="pop-h">
                Notificaciones
                <button type="button" className="btn btn-g btn-sm" onClick={() => setLeidas(true)}>
                  Marcar todo como leído
                </button>
              </div>
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

        <div className="pw">
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

        <div className="pw">
          <button type="button" className={`ib${panel === "apps" ? " act" : ""}`} onClick={() => toggle("apps")} aria-label="Mis soluciones">
            <IconApps />
          </button>
          {panel === "apps" && (
            <div className="pop">
              <div className="pop-h">Mis soluciones</div>
              <div className="pop-sec">
                <button type="button" className="pop-i" onClick={() => ir("/")}>
                  <span className="pop-txt">
                    <b className="teal">{MARCA} Facturación</b>
                    <span className="hint">Factura electrónica, inventario e impuestos.</span>
                  </span>
                  <IconCheck />
                </button>
              </div>
              <div className="pop-sep" />
              <div className="pop-h">Descubre más</div>
              <div className="pop-sec">
                {MENU_SOLUCIONES.map((s) => (
                  <button key={s.id} type="button" className="pop-i" onClick={() => ir(s.to ?? "/")}>
                    {s.icon}
                    <span className="pop-txt">
                      <b className="teal">{s.label}</b>
                    </span>
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>

        <div className="pw">
          <button type="button" className={`co${panel === "empresa" ? " act" : ""}`} onClick={() => toggle("empresa")} aria-label="Empresa">
            <span className="co-av">{EMPRESA_NOMBRE.charAt(0).toUpperCase()}</span>
            <span className="co-txt">{EMPRESA_NOMBRE}</span>
            <IconChevron width={16} height={16} />
          </button>
          {panel === "empresa" && (
            <div className="pop" style={{ width: 340 }}>
              <div className="pop-h">
                Mis empresas
                <button type="button" className="ib" onClick={() => setPanel(null)} aria-label="Cerrar">
                  <IconCerrar />
                </button>
              </div>
              <div className="pop-sec">
                <div className="pop-i pop-static">
                  <span className="co-av">{EMPRESA_NOMBRE.charAt(0).toUpperCase()}</span>
                  <span className="pop-txt">
                    <b>{EMPRESA_NOMBRE}</b>
                    <span className="hint">{rnc ? `RNC ${formatoRnc(rnc)}` : "Configure VITE_EMPRESA_RNC"} · {ambiente}</span>
                  </span>
                  <IconCheck />
                </div>
              </div>
            </div>
          )}
        </div>

        <div className="pw">
          <button type="button" className={`av${panel === "usuario" ? " act" : ""}`} onClick={() => toggle("usuario")} aria-label="Menú de usuario">
            {EMPRESA_NOMBRE.charAt(0).toUpperCase()}
          </button>
          {panel === "usuario" && (
            <div className="pop" style={{ width: 290 }}>
              <div className="pop-h">{EMPRESA_NOMBRE}</div>
              <div className="pop-row">
                <span>Facturación electrónica</span>
                <span className={`bd ${enLinea ? "b-acep" : "b-borr"} nodot`}>{enLinea ? "Activa" : "Sin conexión"}</span>
              </div>
              <div className="pop-sep" />
              <div className="pop-sec">
                <button type="button" className="pop-i" onClick={() => ir("/modulo/configuracion")}>Configuración</button>
                <button type="button" className="pop-i" onClick={() => ir("/modulo/sincronizacion")}>Estado de las soluciones</button>
              </div>
              <div className="pop-sep" />
              <div className="pop-sec">
                <button type="button" className="pop-i" onClick={() => setPanel(null)}>
                  <IconSalir /> Cerrar sesión
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
    </header>
  );
}
