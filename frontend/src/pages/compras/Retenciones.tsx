import { useCallback, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { comprasApi } from "../../lib/comprasApi";
import type { CertificadoRetencion, RetencionesMes, RetencionesProveedor } from "../../lib/comprasApi";
import { EMPRESA_NOMBRE, EMPRESA_RNC, formatoMonto, formatoRnc } from "../../lib/marca";
import { AvisoError, Cargando, Estadistica, Vacio } from "../ingresos/IngresosShell";
import { useAviso } from "../ingresos/useAviso";
import { descargarCsv, enlaceCorreo, enlaceWhatsApp, fechaLegible, mensajeError } from "../ingresos/ingresosUtil";
import { nombrePeriodo, periodosRecientes, periodoValido } from "../impuestos/impuestosUtil";
import { SelectorPeriodo } from "../impuestos/ImpuestosShell";
import { ComprasEncabezado } from "./ComprasShell";
import { mensajeCompras, periodoActual } from "./comprasUtil";

/** "IR-17 / IT-1 oct." como en el prototipo. */
function textoDeclaracion(declaraciones: ("ir17" | "it1")[], periodo: string): string {
  const mes = nombrePeriodo(periodo).slice(0, 3);
  const nombres = declaraciones.map((d) => (d === "ir17" ? "IR-17" : "IT-1"));
  return nombres.length ? `${nombres.join(" / ")} ${mes}.` : "—";
}

export function RetencionesPage() {
  const [params, setParams] = useSearchParams();
  const valor = params.get("periodo");
  const periodo = periodoValido(valor) ? valor : periodoActual();
  const [datos, setDatos] = useState<RetencionesMes | null>(null);
  const [cargando, setCargando] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [cert, setCert] = useState<CertificadoRetencion | null>(null);
  const [preparando, setPreparando] = useState<string | null>(null);
  const [toast, mostrarToast] = useAviso();

  const cargar = useCallback(() => {
    setCargando(true);
    setError(null);
    comprasApi
      .retencionesDelMes(periodo)
      .then(setDatos)
      .catch((err) => {
        setDatos(null);
        setError(mensajeError(err));
      })
      .finally(() => setCargando(false));
  }, [periodo]);

  useEffect(() => {
    cargar();
  }, [cargar]);

  // Imprime el certificado en cuanto está en la página (la versión impresa reemplaza la pantalla).
  useEffect(() => {
    if (!cert) return;
    const t = window.setTimeout(() => window.print(), 50);
    const despues = () => setCert(null);
    window.addEventListener("afterprint", despues);
    return () => {
      window.clearTimeout(t);
      window.removeEventListener("afterprint", despues);
    };
  }, [cert]);

  async function certificado(rnc: string, nombre: string) {
    setPreparando(rnc);
    try {
      const c = await comprasApi.certificadoRetencion(periodo, rnc);
      setCert(c);
      mostrarToast("Certificado de retención listo", { detalle: `${nombre} · ${c.periodo_nombre}. Imprímalo o guárdelo como PDF.` });
    } catch (err) {
      mostrarToast("No se pudo generar el certificado", { detalle: mensajeCompras(err), error: true });
    } finally {
      setPreparando(null);
    }
  }

  function exportar() {
    if (!datos) return;
    descargarCsv(`Retenciones_${periodo}.csv`, [
      ["Proveedor", "RNC/Cédula", "Comprobante", "Fecha del comprobante", "Fecha de pago", "Monto facturado (RD$)", "ITBIS facturado (RD$)", "Tipo retención ISR", "ISR retenido (RD$)", "ITBIS retenido (RD$)", "Declaración", "Estado"],
      ...datos.filas.map((f) => [
        f.proveedor_nombre,
        f.proveedor_rnc,
        f.ncf,
        fechaLegible(f.fecha_comprobante),
        fechaLegible(f.fecha_pago),
        Number(f.monto_facturado).toFixed(2),
        Number(f.itbis_facturado).toFixed(2),
        f.tipo_retencion_isr_label,
        Number(f.isr_retenido).toFixed(2),
        Number(f.itbis_retenido).toFixed(2),
        textoDeclaracion(f.declaraciones, periodo),
        f.estado === "declarada" ? "Declarada" : "Por declarar",
      ]),
    ]);
  }

  const opciones = periodosRecientes(24).map((p) => ({ valor: p, label: nombrePeriodo(p, true) }));
  const isr = Number(datos?.totales.isr_retenido ?? 0);
  const itbis = Number(datos?.totales.itbis_retenido ?? 0);
  const filas = datos?.filas ?? [];
  const proveedores = datos?.proveedores ?? [];

  return (
    <div className="content">
      <div className={cert ? "no-imprimir" : undefined} style={{ display: "contents" }}>
        <ComprasEncabezado
          titulo="Retenciones"
          sub="ISR e ITBIS retenidos a proveedores; se declaran en el IR-17 y el IT-1 del mes del pago"
          acciones={
            <>
              <button type="button" className="btn btn-o" onClick={exportar} disabled={filas.length === 0}>
                Exportar
              </button>
              <Link className="btn btn-o" to={`/impuestos/precalculo/it1?periodo=${periodo}`}>
                Pre-cálculo IT-1
              </Link>
              <Link className="btn btn-p" to={`/impuestos/precalculo/ir17?periodo=${periodo}`}>
                Pre-cálculo IR-17
              </Link>
            </>
          }
        />

        <div className="row imp-acciones no-imprimir" style={{ flexWrap: "wrap" }}>
          <SelectorPeriodo
            valor={periodo}
            opciones={opciones.some((o) => o.valor === periodo) ? opciones : [{ valor: periodo, label: nombrePeriodo(periodo, true) }, ...opciones]}
            onCambiar={(p) => setParams({ periodo: p }, { replace: true })}
            etiqueta="Mes del pago"
            hint={
              datos && (
                <>
                  <span className={datos.ir17_presentado ? "bd b-acep" : "bd b-proc"}>IR-17 {datos.ir17_presentado ? "presentado" : "por presentar"}</span>
                  <span className={datos.it1_presentado ? "bd b-acep" : "bd b-proc"}>IT-1 {datos.it1_presentado ? "presentado" : "por presentar"}</span>
                </>
              )
            }
          />
        </div>

        {error && <AvisoError titulo="No se pudieron cargar las retenciones del mes." mensaje={error} onReintentar={cargar} />}

        <div className="grid-stats">
          <Estadistica k="ISR retenido" v={cargando ? "—" : `RD$ ${formatoMonto(isr)}`} s={`IR-17 de ${nombrePeriodo(periodo)}`} />
          <Estadistica k="ITBIS retenido" v={cargando ? "—" : `RD$ ${formatoMonto(itbis)}`} s={`IT-1 de ${nombrePeriodo(periodo)}`} />
          <Estadistica
            k="Total retenido"
            v={cargando ? "—" : `RD$ ${formatoMonto(isr + itbis)}`}
            s={`${proveedores.length} ${proveedores.length === 1 ? "proveedor" : "proveedores"} · ${filas.length} ${filas.length === 1 ? "comprobante" : "comprobantes"}`}
          />
        </div>

        <section className="card">
          <div className="card-h">
            <h2>Retenciones de {nombrePeriodo(periodo)}</h2>
            <span className="hint">Por fecha de pago, igual que el IT-1 y el IR-17</span>
          </div>
          {cargando && <Cargando />}
          {!cargando && !error && filas.length === 0 && (
            <Vacio
              titulo={`Sin retenciones en ${nombrePeriodo(periodo)}`}
              texto="Las retenciones aparecen aquí cuando registra el pago de una factura de compra con ISR o ITBIS retenido."
            >
              <Link className="btn btn-o btn-sm" to="/compras/pagos">
                Ir a Pagos a proveedores
              </Link>
            </Vacio>
          )}
          {!cargando && filas.length > 0 && (
            <div className="tw">
              <table className="tbl" style={{ minWidth: 980 }}>
                <thead>
                  <tr>
                    <th>Proveedor</th>
                    <th>Comprobante</th>
                    <th>Fecha de pago</th>
                    <th className="num">ISR retenido</th>
                    <th className="num">ITBIS retenido</th>
                    <th>Declaración</th>
                    <th>Estado</th>
                    <th style={{ textAlign: "right" }}>Acciones</th>
                  </tr>
                </thead>
                <tbody>
                  {filas.map((f) => (
                    <tr key={f.registro606_id}>
                      <td>
                        <div className="strong">{f.proveedor_nombre}</div>
                        <div className="hint mono">{formatoRnc(f.proveedor_rnc)}</div>
                      </td>
                      <td className="mono nowrap">
                        <Link className="link mono" to={`/compras/facturas/${f.registro606_id}`}>
                          {f.ncf}
                        </Link>
                      </td>
                      <td className="nowrap">{fechaLegible(f.fecha_pago)}</td>
                      <td className="num">
                        {formatoMonto(f.isr_retenido)}
                        {f.tipo_retencion_isr_label && Number(f.isr_retenido) > 0 && <div className="hint cp-sub">{f.tipo_retencion_isr_label}</div>}
                      </td>
                      <td className="num">{formatoMonto(f.itbis_retenido)}</td>
                      <td className="nowrap">{textoDeclaracion(f.declaraciones, periodo)}</td>
                      <td>{f.estado === "declarada" ? <span className="bd b-acep">Declarada</span> : <span className="bd b-proc">Por declarar</span>}</td>
                      <td>
                        <div className="fila-acc cp-acc">
                          {f.declaraciones.includes("ir17") && (
                            <Link className="btn btn-g btn-sm" to={`/impuestos/precalculo/ir17?periodo=${periodo}`}>
                              IR-17
                            </Link>
                          )}
                          <button type="button" className="btn btn-g btn-sm" onClick={() => certificado(f.proveedor_rnc, f.proveedor_nombre)} disabled={preparando !== null}>
                            Certificado
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        {proveedores.length > 0 && (
          <section className="card">
            <div className="card-h">
              <h2>Certificados por proveedor</h2>
              <span className="hint">Para enviar a cada proveedor el detalle de lo retenido</span>
            </div>
            <div className="tw">
              <table className="tbl" style={{ minWidth: 820 }}>
                <thead>
                  <tr>
                    <th>Proveedor</th>
                    <th className="num">Comprobantes</th>
                    <th className="num">ISR retenido</th>
                    <th className="num">ITBIS retenido</th>
                    <th className="num">Total retenido</th>
                    <th style={{ textAlign: "right" }}>Acciones</th>
                  </tr>
                </thead>
                <tbody>
                  {proveedores.map((p) => (
                    <FilaProveedor key={p.proveedor_rnc} p={p} periodo={periodo} ocupado={preparando !== null} onCertificado={() => certificado(p.proveedor_rnc, p.proveedor_nombre)} />
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        )}
      </div>

      {cert && <CertificadoImpreso cert={cert} />}
      {toast}
    </div>
  );
}

function FilaProveedor({ p, periodo, ocupado, onCertificado }: { p: RetencionesProveedor; periodo: string; ocupado: boolean; onCertificado: () => void }) {
  const total = Number(p.isr_retenido) + Number(p.itbis_retenido);
  const texto =
    `Saludos, ${p.proveedor_nombre}.\n\n` +
    `Le informamos que en ${nombrePeriodo(periodo)} le retuvimos RD$ ${formatoMonto(total)} ` +
    `(ISR RD$ ${formatoMonto(p.isr_retenido)} · ITBIS RD$ ${formatoMonto(p.itbis_retenido)}) en ${p.comprobantes} ${p.comprobantes === 1 ? "comprobante" : "comprobantes"}. ` +
    `Le enviamos el certificado de retención correspondiente.\n\n${EMPRESA_NOMBRE}`;
  return (
    <tr>
      <td>
        <div className="strong">{p.proveedor_nombre}</div>
        <div className="hint mono">{formatoRnc(p.proveedor_rnc)}</div>
      </td>
      <td className="num">{p.comprobantes}</td>
      <td className="num">{formatoMonto(p.isr_retenido)}</td>
      <td className="num">{formatoMonto(p.itbis_retenido)}</td>
      <td className="num" style={{ fontWeight: 600, color: "#111827" }}>
        {formatoMonto(total)}
      </td>
      <td>
        <div className="fila-acc cp-acc">
          <button type="button" className="btn btn-g btn-sm" onClick={onCertificado} disabled={ocupado}>
            Certificado
          </button>
          {p.proveedor_correo && (
            <a className="btn btn-g btn-sm" href={enlaceCorreo(`Certificado de retención · ${nombrePeriodo(periodo)}`, texto, p.proveedor_correo)}>
              Correo
            </a>
          )}
          <a className="btn btn-g btn-sm" href={enlaceWhatsApp(texto, p.proveedor_telefono)} target="_blank" rel="noreferrer">
            WhatsApp
          </a>
        </div>
      </td>
    </tr>
  );
}

/** Certificado de retención para imprimir o guardar como PDF (oculto en pantalla). */
function CertificadoImpreso({ cert }: { cert: CertificadoRetencion }) {
  const agente = cert.agente.nombre || EMPRESA_NOMBRE;
  const agenteRnc = cert.agente.rnc || EMPRESA_RNC;
  return (
    <div className="solo-impresion">
      <div className="imp-h">
        <div>
          <h1>{agente}</h1>
          {agenteRnc && <div>RNC {formatoRnc(agenteRnc)} · Agente de retención</div>}
        </div>
        <div style={{ textAlign: "right" }}>
          <h1>Certificado de retención</h1>
          <div>Período: {cert.periodo_nombre}</div>
          <div>Emitido el {fechaLegible(cert.fecha_emision)}</div>
        </div>
      </div>
      <p>
        Certificamos que a <b>{cert.proveedor.nombre}</b>, {cert.proveedor.tipo_identificacion === "rnc" ? "RNC" : "cédula"} {formatoRnc(cert.proveedor.rnc)}, se le
        retuvieron los impuestos que se detallan por los pagos realizados en {cert.periodo_nombre}. Las retenciones de ISR se declaran en el IR-17 y las de ITBIS en el IT-1
        de ese mes.
      </p>
      <table className="imp-tbl">
        <thead>
          <tr>
            <th>NCF</th>
            <th>Fecha</th>
            <th>Fecha de pago</th>
            <th style={{ textAlign: "right" }}>Monto facturado</th>
            <th style={{ textAlign: "right" }}>ITBIS facturado</th>
            <th>Retención ISR</th>
            <th style={{ textAlign: "right" }}>ISR retenido</th>
            <th style={{ textAlign: "right" }}>ITBIS retenido</th>
          </tr>
        </thead>
        <tbody>
          {cert.comprobantes.map((c) => (
            <tr key={c.registro606_id}>
              <td>{c.ncf}</td>
              <td>{fechaLegible(c.fecha_comprobante)}</td>
              <td>{fechaLegible(c.fecha_pago)}</td>
              <td style={{ textAlign: "right" }}>{formatoMonto(c.monto_facturado)}</td>
              <td style={{ textAlign: "right" }}>{formatoMonto(c.itbis_facturado)}</td>
              <td>{c.tipo_retencion_isr_label ?? "—"}</td>
              <td style={{ textAlign: "right" }}>{formatoMonto(c.isr_retenido)}</td>
              <td style={{ textAlign: "right" }}>{formatoMonto(c.itbis_retenido)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="imp-tot">
        <span>Monto facturado</span>
        <span>{formatoMonto(cert.totales.monto_facturado)}</span>
        <span>ITBIS facturado</span>
        <span>{formatoMonto(cert.totales.itbis_facturado)}</span>
        <span>ISR retenido</span>
        <span>{formatoMonto(cert.totales.isr_retenido)}</span>
        <span>ITBIS retenido</span>
        <span>{formatoMonto(cert.totales.itbis_retenido)}</span>
        <span className="t">Total retenido</span>
        <span className="t">RD$ {formatoMonto(cert.totales.total_retenido)}</span>
      </div>
      <div className="cp-cert-firma">
        <div>Firma y sello del agente de retención</div>
        <div>Recibido por el proveedor</div>
      </div>
    </div>
  );
}
