import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { comprasApi } from "../../lib/comprasApi";
import type { EcfRecibido } from "../../lib/comprasApi";
import { INDICADORES_FACTURACION } from "../../lib/catalogos";
import { formatoMonto, formatoRnc } from "../../lib/marca";
import { AvisoError } from "../ingresos/IngresosShell";
import { useAviso } from "../ingresos/useAviso";
import { fechaLegible, mensajeError } from "../ingresos/ingresosUtil";
import { copiarTexto } from "../impuestos/impuestosUtil";
import { IconoDescargar } from "../impuestos/ImpuestosShell";
import { IconoOk } from "./ComprasShell";
import { DecisionEcf } from "./DecisionEcf";
import { ESTADO_APROBACION, tipoEcfTexto } from "./comprasUtil";
import "../ingresos/ingresos.css";
import "./compras.css";

function itbisLinea(indicador: string | null): string {
  if (!indicador) return "—";
  return INDICADORES_FACTURACION.find((i) => i.value === indicador)?.label ?? indicador;
}

function fechaHora(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("es-DO", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function EcfRecibidoDetalle() {
  const { id = "" } = useParams<{ id: string }>();
  const [ecf, setEcf] = useState<EcfRecibido | null>(null);
  const [cargando, setCargando] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [toast, mostrarToast] = useAviso();

  const cargar = useCallback(() => {
    setCargando(true);
    setError(null);
    comprasApi
      .obtenerEcfRecibido(id)
      .then(setEcf)
      .catch((err) => setError(mensajeError(err)))
      .finally(() => setCargando(false));
  }, [id]);

  useEffect(() => {
    cargar();
  }, [cargar]);

  const migas = (
    <div className="crumb">
      <Link to="/bandeja">Bandeja de entrada</Link>
      <span>/</span>
      <span>{ecf?.encf ?? "e-CF recibido"}</span>
    </div>
  );

  if (cargando && !ecf)
    return (
      <div className="content">
        <p className="mut">Cargando…</p>
      </div>
    );
  if (!ecf)
    return (
      <div className="content">
        <div className="ph">
          <div>
            {migas}
            <h1>e-CF recibido</h1>
          </div>
        </div>
        <AvisoError titulo="No se pudo abrir el e-CF recibido." mensaje={error ?? "No encontrado"} onReintentar={cargar} />
        <div>
          <Link to="/bandeja" className="btn btn-o">
            Volver a la bandeja
          </Link>
        </div>
      </div>
    );

  const est = ESTADO_APROBACION[ecf.estado_aprobacion] ?? { label: ecf.estado_aprobacion, badge: "bd b-borr" };
  const respuesta = ecf.respuesta_dgii as { mensaje?: unknown; estado?: unknown; codigo?: unknown } | null;

  async function copiarXml() {
    if (!ecf) return;
    const ok = await copiarTexto(ecf.xml);
    mostrarToast(ok ? "XML copiado al portapapeles" : "El navegador bloqueó el portapapeles", ok ? undefined : { detalle: "Use Descargar XML.", error: true });
  }

  return (
    <div className="content">
      <div className="ph">
        <div>
          {migas}
          <h1 className="row" style={{ gap: 12 }}>
            {ecf.razon_social_emisor || formatoRnc(ecf.rnc_emisor)}
            <span className={est.badge}>{est.label}</span>
          </h1>
          <p>
            <span className="mono">{ecf.encf}</span> · {tipoEcfTexto(ecf.tipo_ecf)} · recibido el {fechaHora(ecf.recibido_en)}
          </p>
        </div>
        <div className="row">
          <a className="btn btn-o" href={comprasApi.urlXmlEcfRecibido(ecf.id)} download>
            <IconoDescargar />
            Descargar XML
          </a>
        </div>
      </div>

      <div className="cp-split">
        <div className="cp-main cp-col">
          <section className="card">
            <div className="card-h">
              <h2>Emisor y comprobante</h2>
              <span className="ok-t">
                <IconoOk size={14} />
                Acuse de recibo enviado automáticamente
              </span>
            </div>
            <div className="card-b">
              <dl className="kv">
                <dt>Proveedor</dt>
                <dd>{ecf.razon_social_emisor || "—"}</dd>
                <dt>RNC del emisor</dt>
                <dd className="mono">{formatoRnc(ecf.rnc_emisor)}</dd>
                <dt>e-NCF</dt>
                <dd className="mono">{ecf.encf}</dd>
                <dt>Tipo</dt>
                <dd>{tipoEcfTexto(ecf.tipo_ecf)}</dd>
                <dt>Fecha de emisión</dt>
                <dd>{fechaLegible(ecf.fecha_emision)}</dd>
                <dt>Fecha límite de pago</dt>
                <dd>{fechaLegible(ecf.fecha_limite_pago)}</dd>
                <dt>RNC comprador</dt>
                <dd className="mono">{ecf.rnc_comprador ? formatoRnc(ecf.rnc_comprador) : "—"}</dd>
                <dt>Recibido</dt>
                <dd>{fechaHora(ecf.recibido_en)}</dd>
              </dl>
            </div>
          </section>

          <section className="card">
            <div className="card-h">
              <h2>Detalle</h2>
              <span className="hint">
                {ecf.items.length} {ecf.items.length === 1 ? "línea" : "líneas"}
              </span>
            </div>
            <div className="tw">
              <table className="tbl" style={{ minWidth: 640 }}>
                <thead>
                  <tr>
                    <th>#</th>
                    <th>Descripción</th>
                    <th className="num">Cantidad</th>
                    <th className="num">Precio (RD$)</th>
                    <th>ITBIS</th>
                    <th className="num">Importe (RD$)</th>
                  </tr>
                </thead>
                <tbody>
                  {ecf.items.map((it) => (
                    <tr key={it.linea}>
                      <td className="mut">{it.linea}</td>
                      <td>
                        <div className="strong">{it.descripcion}</div>
                        <div className="hint">{it.bien_o_servicio === "bien" ? "Bien" : "Servicio"}</div>
                      </td>
                      <td className="num">{Number(it.cantidad).toLocaleString("en-US")}</td>
                      <td className="num">{formatoMonto(it.precio_unitario)}</td>
                      <td className="nowrap">{itbisLinea(it.indicador_facturacion)}</td>
                      <td className="num">{formatoMonto(it.monto)}</td>
                    </tr>
                  ))}
                  {ecf.items.length === 0 && (
                    <tr>
                      <td colSpan={6} className="mut">
                        El XML no trae líneas de detalle.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </section>

          <section className="card">
            <div className="card-h">
              <h2>XML recibido</h2>
              <div className="row">
                <button type="button" className="btn btn-g btn-sm" onClick={copiarXml}>
                  Copiar
                </button>
                <a className="btn btn-o btn-sm" href={comprasApi.urlXmlEcfRecibido(ecf.id)} download>
                  Descargar
                </a>
              </div>
            </div>
            <pre className="cp-xml" aria-label="XML del e-CF recibido" tabIndex={0}>
              {ecf.xml}
            </pre>
          </section>
        </div>

        <div className="cp-side cp-col">
          <section className="card">
            <div className="card-h">
              <h2>Aprobación comercial</h2>
              <span className={est.badge}>{est.label}</span>
            </div>
            <div className="card-b cp-col">
              <dl className="kv cp-tot">
                <dt>Subtotal</dt>
                <dd className="num">{formatoMonto(ecf.subtotal)}</dd>
                <dt>ITBIS</dt>
                <dd className="num">{formatoMonto(ecf.itbis_total)}</dd>
                <dt className="t">Total</dt>
                <dd className="num t">RD$ {formatoMonto(ecf.monto_total)}</dd>
              </dl>
              <DecisionEcf ecf={ecf} onCambio={(e) => (e ? setEcf(e) : cargar())} avisar={mostrarToast} />
            </div>
          </section>
          {respuesta && (
            <section className="card">
              <div className="card-h">
                <h2>Respuesta de la DGII</h2>
              </div>
              <div className="card-b">
                <dl className="kv">
                  {respuesta.estado != null && (
                    <>
                      <dt>Estado</dt>
                      <dd>{String(respuesta.estado)}</dd>
                    </>
                  )}
                  {respuesta.codigo != null && (
                    <>
                      <dt>Código</dt>
                      <dd className="mono">{String(respuesta.codigo)}</dd>
                    </>
                  )}
                  {respuesta.mensaje != null && (
                    <>
                      <dt>Mensaje</dt>
                      <dd>{typeof respuesta.mensaje === "string" ? respuesta.mensaje : JSON.stringify(respuesta.mensaje)}</dd>
                    </>
                  )}
                  <dt>Decidido</dt>
                  <dd>{fechaHora(ecf.aprobado_en)}</dd>
                </dl>
              </div>
            </section>
          )}
        </div>
      </div>
      {toast}
    </div>
  );
}
