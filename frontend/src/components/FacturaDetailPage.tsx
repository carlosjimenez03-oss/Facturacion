import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import { IconAlerta } from "../layout/icons";
import { api, ApiError } from "../lib/api";
import { desglose, siguienteEncf } from "../lib/buildDatos";
import { CODIGOS_MODIFICACION, FORMAS_PAGO, TIPOS_INGRESOS } from "../lib/catalogos";
import { erpApi } from "../lib/erpApi";
import type { Cliente } from "../lib/erpApi";
import { ESTADO_BADGE, ESTADO_LABEL, TIPO_ECF_LABEL } from "../lib/estados";
import { configuracionApi } from "../lib/configuracionApi";
import { calcularMontoItem } from "../lib/format";
import { formatoMonto, formatoRnc, nombreAmbiente } from "../lib/marca";
import type { FacturaDetalle, FormaPago, ItemFactura } from "../lib/types";
import { useFacturas } from "../lib/useFacturas";
import { FacturaAcciones } from "./FacturaAcciones";
import { diasEntre, descargarArchivo, fechaDgiiIso, fechaDgiiLegible, fechaHora, valorXml } from "./detalle/formato";
import { PanelRechazo } from "./detalle/PanelRechazo";
import { ProcesoDgii } from "./detalle/ProcesoDgii";
import { Toast } from "./detalle/Toast";
import type { Aviso } from "./detalle/Toast";
import { VisorXml } from "./detalle/VisorXml";
import "./detalle/detalle.css";

const TASA_LABEL: Record<string, string> = { "1": "18%", "2": "16%", "3": "0%", "4": "Exento", "0": "No facturable" };

/** Tipos de venta a los que se les puede emitir una nota de crédito o de débito. */
const TIPOS_CON_NOTAS = [31, 32, 44, 45, 46];

const AMBIENTE_CLASE = { Producción: "env env-prod", Certificación: "env env-cert", Pruebas: "env env-test" } as const;

function texto(v: unknown): string {
  return typeof v === "string" || typeof v === "number" ? String(v) : "";
}

function cantidad(valor: string): string {
  const n = Number(valor) || 0;
  return n.toLocaleString("en-US", { maximumFractionDigits: 2 });
}

function porcentajeDescuento(item: ItemFactura): string {
  const bruto = (Number(item.CantidadItem) || 0) * (Number(item.PrecioUnitarioItem) || 0);
  const desc = Number(item.DescuentoMonto) || 0;
  if (!bruto || !desc) return "—";
  return `${Math.round((desc / bruto) * 10000) / 100}%`;
}

function mensajeError(err: unknown): string {
  return err instanceof ApiError ? err.message : "No se pudo conectar con el servicio de facturación.";
}

export function FacturaDetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const location = useLocation();
  const { facturas, recargar } = useFacturas();
  const [factura, setFactura] = useState<FacturaDetalle | null>(null);
  const [cargando, setCargando] = useState(true);
  const [errorCarga, setErrorCarga] = useState<string | null>(null);
  const [contacto, setContacto] = useState<Cliente | null>(null);
  const [procesando, setProcesando] = useState<"" | "emitir" | "pdf" | "eliminar" | "estado">("");
  const [confirmar, setConfirmar] = useState<"emitir" | "eliminar" | null>(null);
  const [erroresXml, setErroresXml] = useState<string[]>([]);
  const [errorAccion, setErrorAccion] = useState<string | null>(null);
  const [aviso, setAviso] = useState<Aviso | null>(null);
  const cerrarAviso = useCallback(() => setAviso(null), []);

  const cargar = useCallback(() => {
    if (!id) return;
    api
      .obtener(id)
      .then((f) => {
        setFactura(f);
        setErrorCarga(null);
      })
      .catch((err) =>
        setErrorCarga(err instanceof ApiError && err.status === 404 ? "Esta factura no existe o fue eliminada." : mensajeError(err)),
      )
      .finally(() => setCargando(false));
  }, [id]);

  useEffect(() => {
    setCargando(true);
    cargar();
  }, [cargar]);

  // Aviso que deja el formulario al llegar aquí (p. ej. si no se pudo marcar la cotización como facturada).
  useEffect(() => {
    const recibido = (location.state as { aviso?: Aviso } | null)?.aviso;
    if (recibido) setAviso(recibido);
  }, [location.state]);

  // Nombre del vendedor (Configuración → Vendedores); sin erp-api solo se omite.
  const [vendedor, setVendedor] = useState<string | null>(null);
  const vendedorId = factura?.vendedor_id ?? null;
  useEffect(() => {
    if (!vendedorId) return setVendedor(null);
    configuracionApi
      .vendedores()
      .then((l) => setVendedor(l.find((v) => v.id === vendedorId)?.nombre ?? "Vendedor eliminado"))
      .catch(() => setVendedor(null));
  }, [vendedorId]);

  const comprador = (factura?.datos.Encabezado.Comprador ?? {}) as Record<string, unknown>;
  const rncComprador = texto(comprador.RNCComprador).replace(/\D/g, "");

  // Teléfono y enlace al contacto, si el comprador está en Contactos (no bloquea si erp-api no responde).
  useEffect(() => {
    if (rncComprador.length !== 9 && rncComprador.length !== 11) return setContacto(null);
    erpApi
      .clientePorRnc(rncComprador)
      .then(setContacto)
      .catch(() => setContacto(null));
  }, [rncComprador]);

  const items = useMemo(() => factura?.datos.DetallesItems?.Item ?? [], [factura]);
  const totales = useMemo(() => desglose(items), [items]);

  if (cargando) return <div className="content"><p className="mut">Cargando…</p></div>;

  if (!factura) {
    return (
      <div className="content">
        <div className="ph">
          <div>
            <div className="crumb">
              <Link to="/facturas">Facturas de venta</Link>
            </div>
            <h1>Factura</h1>
          </div>
        </div>
        <div className="al al-d" role="alert">
          <IconAlerta />
          <span style={{ flex: 1 }}>
            <b>No se pudo abrir la factura.</b> {errorCarga}
          </span>
          <button type="button" className="btn btn-o btn-sm" onClick={() => { setCargando(true); cargar(); }}>
            Reintentar
          </button>
        </div>
      </div>
    );
  }

  const f = factura;
  const idDoc = f.datos.Encabezado.IdDoc ?? {};
  const emisor = f.datos.Encabezado.Emisor ?? {};
  const totalesDatos = f.datos.Encabezado.Totales ?? {};
  const referencia = (f.datos.InformacionReferencia ?? {}) as Record<string, unknown>;
  const formas = ((idDoc.TablaFormasPago as { FormaDePago?: FormaPago[] } | undefined)?.FormaDePago ?? []).filter(Boolean);

  const fechaEmisionIso = fechaDgiiIso(emisor.FechaEmision);
  const fechaEmision = fechaDgiiLegible(emisor.FechaEmision);
  const credito = texto(idDoc.TipoPago) === "2";
  const vence = fechaDgiiIso(idDoc.FechaLimitePago);
  const diasCredito = credito ? diasEntre(fechaEmisionIso, vence) : null;
  const condicion = credito
    ? `Crédito${diasCredito ? ` ${diasCredito} días` : ""}${vence ? ` · vence ${fechaDgiiLegible(idDoc.FechaLimitePago)}` : ""}`
    : texto(idDoc.TipoPago) === "3"
      ? "Gratuito"
      : `Contado${formas[0] ? ` · ${FORMAS_PAGO.find((p) => p.value === formas[0].FormaPago)?.label ?? ""}` : ""}`;

  const clienteNombre = texto(comprador.RazonSocialComprador) || (rncComprador ? formatoRnc(rncComprador) : "Consumidor final");
  const idLabel = rncComprador.length === 11 ? "Cédula" : rncComprador ? "RNC" : comprador.IdentificadorExtranjero ? "Identificador extranjero" : "";
  const idValor = rncComprador ? formatoRnc(rncComprador) : texto(comprador.IdentificadorExtranjero);
  const contactoLinea = [contacto?.telefono, texto(comprador.CorreoComprador) || contacto?.correo].filter(Boolean).join(" · ");

  const montoTotal = Number(totalesDatos.MontoTotal ?? NaN);
  const total = Number.isFinite(montoTotal) ? montoTotal : totales.total;

  const emitida = f.estado === "enviado" || f.estado === "aceptado";
  const fallida = f.estado === "rechazado" || f.estado === "error";
  const sinEnviar = f.estado === "borrador" || f.estado === "generado" || f.estado === "firmado";
  const admiteNotas = emitida && TIPOS_CON_NOTAS.includes(f.tipo_ecf) && Boolean(f.encf);
  const notaUrl = (tipo: 33 | 34) =>
    `/facturas/nueva?tipo=${tipo}&afecta=${encodeURIComponent(f.encf ?? "")}${fechaEmisionIso ? `&fecha=${fechaEmisionIso}` : ""}`;
  const proximo = fallida ? siguienteEncf(facturas, String(f.tipo_ecf)) : null;
  const afectada = referencia.NCFModificado ? facturas.find((x) => x.encf === referencia.NCFModificado) : undefined;
  const xml = f.xml_firmado ?? f.xml_generado;
  const ambiente = nombreAmbiente(f.ambiente);
  const ingresos = TIPOS_INGRESOS.find((t) => t.value === texto(idDoc.TipoIngresos))?.label.replace(" - ", " · ");
  const codigoSeguridad = valorXml(f.xml_firmado, "SignatureValue")?.slice(0, 6);
  const fechaFirma = valorXml(f.xml_firmado, "FechaHoraFirma");

  async function emitir() {
    setConfirmar(null);
    setErroresXml([]);
    setErrorAccion(null);
    setProcesando("emitir");
    try {
      const xmlGen = await api.generarXml(f.id);
      if (xmlGen.errores.length > 0) {
        setErroresXml(xmlGen.errores);
        setErrorAccion("La DGII no aceptaría este comprobante. Corrija lo siguiente en el borrador y vuelva a emitir.");
        return;
      }
      const envio = await api.enviar(f.id);
      // facturas-api consulta el resultado justo después de enviar: puede volver ya aceptada o rechazada.
      setAviso(
        !envio.track_id
          ? { texto: "La DGII no recibió el comprobante", detalle: envio.error ?? undefined, tono: "error" }
          : envio.estado === "aceptado"
            ? { texto: "Comprobante aceptado por la DGII", detalle: `Track ID ${envio.track_id}` }
            : envio.estado === "rechazado"
              ? { texto: "La DGII rechazó el comprobante", detalle: envio.error ?? undefined, tono: "error" }
              : { texto: "Comprobante enviado a la DGII", detalle: `En proceso de validación · Track ID ${envio.track_id}` },
      );
    } catch (err) {
      setErrorAccion(mensajeError(err));
    } finally {
      setProcesando("");
      recargar();
      cargar();
    }
  }

  /** Pregunta a la DGII (por trackId) si ya validó el comprobante. */
  async function actualizarEstado() {
    setErrorAccion(null);
    setProcesando("estado");
    try {
      const actualizada = await api.actualizarEstado(f.id);
      setFactura(actualizada);
      setAviso(
        actualizada.estado === "aceptado"
          ? { texto: "Comprobante aceptado por la DGII", detalle: actualizada.dgii_estado ?? undefined }
          : actualizada.estado === "rechazado"
            ? { texto: "La DGII rechazó el comprobante", detalle: actualizada.error ?? undefined, tono: "error" }
            : { texto: "Sigue en proceso en la DGII", detalle: "Vuelva a consultar en unos minutos; también se actualiza sola.", tono: "aviso" },
      );
      if (actualizada.estado !== f.estado) recargar();
    } catch (err) {
      setAviso({ texto: "No se pudo consultar el estado en la DGII", detalle: mensajeError(err), tono: "error" });
    } finally {
      setProcesando("");
    }
  }

  async function eliminar() {
    setConfirmar(null);
    setProcesando("eliminar");
    try {
      await api.eliminar(f.id);
      recargar();
      navigate("/facturas");
    } catch (err) {
      setErrorAccion(mensajeError(err));
      setProcesando("");
    }
  }

  /** La representación impresa se genera desde la vista previa firmada; si no existe, se firma primero. */
  async function verPdf() {
    const url = api.representacionImpresaUrl(f.id);
    if (f.xml_firmado) {
      window.open(url, "_blank", "noopener,noreferrer");
      return;
    }
    const ventana = window.open("", "_blank");
    setProcesando("pdf");
    try {
      await api.firmar(f.id);
      if (ventana) ventana.location.href = url;
      else window.open(url, "_blank", "noopener,noreferrer");
      cargar();
    } catch (err) {
      ventana?.close();
      setAviso({ texto: "No se pudo generar el PDF", detalle: mensajeError(err), tono: "error" });
    } finally {
      setProcesando("");
    }
  }

  function descargarXml() {
    if (!xml) return;
    descargarArchivo(f.xml_firmado ? `${f.encf || f.id}.xml` : `${f.encf || f.id}-sin-firmar.xml`, xml);
    setAviso({ texto: f.xml_firmado ? "XML firmado descargado" : "XML sin firmar descargado", detalle: `${f.encf || f.id}.xml` });
  }

  return (
    <div className="content">
      <div className="ph">
        <div style={{ minWidth: 0 }}>
          <div className="crumb">
            <Link to="/facturas">Facturas de venta</Link>
            <span>/</span>
            <span className="mono">{f.encf || "Borrador"}</span>
          </div>
          <div className="row" style={{ gap: 12 }}>
            <h1 className="mono" style={{ fontSize: 24, letterSpacing: "-.02em" }}>
              {f.encf || "Borrador sin e-NCF"}
            </h1>
            <span className={ESTADO_BADGE[f.estado] ?? "bd b-borr"}>{ESTADO_LABEL[f.estado] ?? f.estado}</span>
            <span className="chip">
              {f.tipo_ecf} · {TIPO_ECF_LABEL[f.tipo_ecf] ?? "e-CF"}
            </span>
          </div>
          <p>
            {clienteNombre}
            {fechaEmision && ` · ${sinEnviar ? "Fecha de emisión" : "Emitida el"} ${fechaEmision}`}
            {` · RD$ ${formatoMonto(total)}`}
          </p>
        </div>
        <div className="row">
          {sinEnviar ? (
            <>
              {f.estado === "borrador" && (
                <button type="button" className="btn btn-g btn-sm" style={{ color: "#B91C1C" }} onClick={() => setConfirmar("eliminar")} disabled={procesando !== ""}>
                  Eliminar
                </button>
              )}
              {f.estado === "borrador" && (
                <Link to={`/facturas/${f.id}/editar`} className="btn btn-o btn-sm">
                  Editar
                </Link>
              )}
              <button type="button" className="btn btn-p btn-sm" onClick={() => setConfirmar("emitir")} disabled={procesando !== "" || !f.encf}>
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <path d="m22 2-7 20-4-9-9-4z" />
                  <path d="M22 2 11 13" />
                </svg>
                {procesando === "emitir" ? "Emitiendo…" : "Emitir y enviar a DGII"}
              </button>
            </>
          ) : (
            <>
              {f.estado === "enviado" && f.track_id && (
                <button type="button" className="btn btn-o btn-sm" onClick={actualizarEstado} disabled={procesando !== ""}>
                  {procesando === "estado" ? "Consultando…" : "Actualizar estado DGII"}
                </button>
              )}
              <button type="button" className="btn btn-o btn-sm" onClick={verPdf} disabled={procesando !== "" || !f.xml_generado}>
                {procesando === "pdf" ? "Preparando PDF…" : "PDF"}
              </button>
              <button type="button" className="btn btn-o btn-sm" onClick={descargarXml} disabled={!xml}>
                XML
              </button>
              {admiteNotas && (
                <>
                  <Link to={notaUrl(33)} className="btn btn-o btn-sm">
                    Nota de débito
                  </Link>
                  <Link to={notaUrl(34)} className={credito ? "btn btn-o btn-sm" : "btn btn-p btn-sm"}>
                    Nota de crédito
                  </Link>
                </>
              )}
              {emitida && credito && (
                <Link to={`/cobros?factura=${f.id}`} className="btn btn-p btn-sm">
                  Registrar cobro
                </Link>
              )}
              {fallida && (
                <span className="tip r" data-tip="Una factura rechazada no tiene validez fiscal y no puede enviarse al cliente.">
                  <button type="button" className="btn btn-o btn-sm" disabled>
                    Enviar al cliente
                  </button>
                </span>
              )}
            </>
          )}
        </div>
      </div>

      {errorAccion && (
        <div className="al al-d" role="alert">
          <IconAlerta />
          <span>
            <b>{errorAccion}</b>
            {erroresXml.length > 0 && (
              <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>
                {erroresXml.map((e, i) => (
                  <li key={i}>{e}</li>
                ))}
              </ul>
            )}
          </span>
        </div>
      )}

      {fallida && <PanelRechazo factura={f} proximoEncf={proximo} procesando={procesando === "emitir"} onReintentar={() => setConfirmar("emitir")} />}

      {f.inventario_error && (
        <div className="al al-w">
          <IconAlerta />
          <span>
            <b>No se descontó el inventario de esta factura.</b> {f.inventario_error}
          </span>
        </div>
      )}

      <div style={{ display: "flex", flexWrap: "wrap", gap: 16, alignItems: "flex-start" }}>
        <div style={{ flex: "2 1 600px", minWidth: 0, display: "flex", flexDirection: "column", gap: 16 }}>
          <section className="card">
            <div className="partes">
              <div>
                <div className="partes-cap">Emisor</div>
                <div className="partes-nom">{texto(emisor.RazonSocialEmisor) || "—"}</div>
                <div className="partes-det">
                  RNC {formatoRnc(texto(emisor.RNCEmisor) || f.rnc_emisor)}
                  {texto(emisor.DireccionEmisor) && (
                    <>
                      <br />
                      {texto(emisor.DireccionEmisor)}
                    </>
                  )}
                  {texto(emisor.CorreoEmisor) && (
                    <>
                      <br />
                      {texto(emisor.CorreoEmisor)}
                    </>
                  )}
                </div>
              </div>
              <div>
                <div className="partes-cap">{f.tipo_ecf === 41 || f.tipo_ecf === 47 ? "Proveedor" : "Cliente"}</div>
                <div className="partes-nom">
                  {contacto ? (
                    <Link to={`/contactos/${contacto.id}`} className="link" style={{ color: "#111827", fontWeight: 650 }}>
                      {clienteNombre}
                    </Link>
                  ) : (
                    clienteNombre
                  )}
                </div>
                <div className="partes-det">
                  {idLabel ? `${idLabel} ${idValor}` : "Sin identificar"}
                  {texto(comprador.DireccionComprador) && (
                    <>
                      <br />
                      {texto(comprador.DireccionComprador)}
                    </>
                  )}
                  {contactoLinea && (
                    <>
                      <br />
                      {contactoLinea}
                    </>
                  )}
                </div>
              </div>
            </div>
          </section>

          <section className="card">
            <div className="card-h">
              <h2>Detalle</h2>
              <span className="hint">Condición: {condicion}</span>
            </div>
            <div className="tw">
              <table className="tbl" style={{ minWidth: 640 }}>
                <thead>
                  <tr>
                    <th>Descripción</th>
                    <th className="num">Cant.</th>
                    <th className="num">Precio</th>
                    <th className="num">Desc.</th>
                    <th>ITBIS</th>
                    <th className="num">Importe (RD$)</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((it, i) => (
                    <tr key={`${it.NumeroLinea}-${i}`}>
                      <td style={{ color: "#111827" }}>
                        {it.NombreItem}
                        {it.DescripcionItem && <div className="hint">{it.DescripcionItem}</div>}
                      </td>
                      <td className="num">{cantidad(it.CantidadItem)}</td>
                      <td className="num">{formatoMonto(it.PrecioUnitarioItem)}</td>
                      <td className="num mut">{porcentajeDescuento(it)}</td>
                      <td>
                        <span className="chip">{TASA_LABEL[it.IndicadorFacturacion] ?? it.IndicadorFacturacion}</span>
                      </td>
                      <td className="num" style={{ fontWeight: 600, color: "#111827" }}>
                        {formatoMonto(calcularMontoItem(it))}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div style={{ display: "flex", justifyContent: "flex-end", padding: "16px 20px", borderTop: "1px solid #EEF0F2" }}>
              <dl className="kv" style={{ minWidth: "min(300px, 100%)", gridTemplateColumns: "1fr auto" }}>
                <dt>Subtotal gravado</dt>
                <dd className="num">{formatoMonto(totales.g18 + totales.g16 + totales.g0)}</dd>
                <dt>Subtotal exento</dt>
                <dd className="num">{formatoMonto(totales.exento)}</dd>
                <dt>Descuentos</dt>
                <dd className="num">{totales.descuentos > 0 ? `-${formatoMonto(totales.descuentos)}` : "0.00"}</dd>
                <dt>ITBIS 18%</dt>
                <dd className="num">{formatoMonto(totales.i18)}</dd>
                {totales.i16 > 0 && (
                  <>
                    <dt>ITBIS 16%</dt>
                    <dd className="num">{formatoMonto(totales.i16)}</dd>
                  </>
                )}
                <dt style={{ fontWeight: 650, color: "#111827", fontSize: 15, paddingTop: 8, borderTop: "1px solid #E5E7EB" }}>Total</dt>
                <dd className="num" style={{ fontWeight: 700, fontSize: 17, paddingTop: 8, borderTop: "1px solid #E5E7EB" }}>
                  RD$ {formatoMonto(total)}
                </dd>
              </dl>
            </div>
          </section>

          <VisorXml factura={f} />
        </div>

        <div style={{ flex: "1 1 340px", minWidth: 0, display: "flex", flexDirection: "column", gap: 16 }}>
          <ProcesoDgii factura={f} />
          <section className="card">
            <div className="card-h">
              <h2>Datos fiscales</h2>
            </div>
            <dl className="kv card-b">
              <dt>Track ID</dt>
              <dd className="mono">{f.track_id ?? "—"}</dd>
              {codigoSeguridad && (
                <>
                  <dt>
                    <span className="tip" data-tip="Primeros 6 caracteres de la firma digital. Aparece en la representación impresa junto al QR.">
                      Código de seguridad ⓘ
                    </span>
                  </dt>
                  <dd className="mono">{codigoSeguridad}</dd>
                </>
              )}
              {fechaFirma && (
                <>
                  <dt>Fecha de firma</dt>
                  <dd>{fechaFirma.replace(/-/g, "/")}</dd>
                </>
              )}
              {texto(idDoc.FechaVencimientoSecuencia) && (
                <>
                  <dt>Venc. secuencia</dt>
                  <dd>{fechaDgiiLegible(idDoc.FechaVencimientoSecuencia)}</dd>
                </>
              )}
              {ingresos && (
                <>
                  <dt>Tipo de ingreso</dt>
                  <dd>{ingresos}</dd>
                </>
              )}
              {texto(referencia.NCFModificado) && (
                <>
                  <dt>Modifica</dt>
                  <dd>
                    {afectada ? (
                      <Link to={`/facturas/${afectada.id}`} className="mono link">
                        {texto(referencia.NCFModificado)}
                      </Link>
                    ) : (
                      <span className="mono">{texto(referencia.NCFModificado)}</span>
                    )}
                    {texto(referencia.FechaNCFModificado) && <span className="mut"> del {fechaDgiiLegible(referencia.FechaNCFModificado)}</span>}
                  </dd>
                  <dt>Motivo</dt>
                  <dd>{CODIGOS_MODIFICACION.find((c) => c.value === texto(referencia.CodigoModificacion))?.label ?? texto(referencia.CodigoModificacion)}</dd>
                </>
              )}
              {f.dgii_estado && (
                <>
                  <dt>Respuesta DGII</dt>
                  <dd>
                    {f.dgii_codigo && `Código ${f.dgii_codigo} · `}
                    {f.dgii_estado}
                  </dd>
                </>
              )}
              {vendedor && (
                <>
                  <dt>Vendedor</dt>
                  <dd>{vendedor}</dd>
                </>
              )}
              <dt>Ambiente</dt>
              <dd>
                <span className={AMBIENTE_CLASE[ambiente]} style={{ height: 22 }}>
                  {ambiente}
                </span>
              </dd>
              <dt>Última actualización</dt>
              <dd>{fechaHora(f.actualizado_en)}</dd>
            </dl>
          </section>
        </div>
      </div>

      <details className="card">
        <summary>
          <h2>Acciones avanzadas</h2>
          <span className="row hint">
            <span className="avz-txt">Generar XML, vista previa firmada y envío paso a paso</span>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="m6 9 6 6 6-6" />
            </svg>
          </span>
        </summary>
        <div className="card-b">
          <FacturaAcciones
            factura={f}
            mostrarXml={false}
            onRecargar={() => {
              recargar();
              cargar();
            }}
          />
        </div>
      </details>

      {confirmar === "emitir" && (
        <div className="scrim">
          <div className="modal" role="dialog" aria-modal="true" aria-labelledby="emitir-t">
            <div className="modal-h">
              <h3 id="emitir-t">{fallida ? "¿Reintentar el envío a la DGII?" : "¿Emitir y enviar a la DGII?"}</h3>
            </div>
            <div className="modal-b">
              <span>
                Se firmará digitalmente el comprobante{" "}
                <b className="mono" style={{ color: "#111827" }}>
                  {f.encf}
                </b>{" "}
                ({TIPO_ECF_LABEL[f.tipo_ecf] ?? "e-CF"}) por <b style={{ color: "#111827" }}>RD$ {formatoMonto(total)}</b> y se enviará a la DGII.
              </span>
              <div className="al al-w">
                <IconAlerta />
                <span>
                  Una vez enviado, el e-NCF queda utilizado y el comprobante no puede editarse. Para corregirlo deberá emitir una nota de crédito o
                  débito.
                </span>
              </div>
            </div>
            <div className="modal-f">
              <button type="button" className="btn btn-o" onClick={() => setConfirmar(null)}>
                Revisar
              </button>
              <button type="button" className="btn btn-p" onClick={emitir}>
                Sí, emitir
              </button>
            </div>
          </div>
        </div>
      )}

      {confirmar === "eliminar" && (
        <div className="scrim">
          <div className="modal" role="dialog" aria-modal="true" aria-labelledby="del-t">
            <div className="modal-h">
              <h3 id="del-t">¿Eliminar este borrador?</h3>
            </div>
            <div className="modal-b">
              <span>El borrador no se envió a la DGII, así que puede eliminarse sin consumir el e-NCF. Esta acción no se puede deshacer.</span>
            </div>
            <div className="modal-f">
              <button type="button" className="btn btn-o" onClick={() => setConfirmar(null)}>
                Cancelar
              </button>
              <button type="button" className="btn btn-d" onClick={eliminar}>
                Eliminar
              </button>
            </div>
          </div>
        </div>
      )}

      <Toast aviso={aviso} onCerrar={cerrarAviso} />
    </div>
  );
}
