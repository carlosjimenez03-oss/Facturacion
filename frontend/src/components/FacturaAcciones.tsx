import { useState } from "react";
import { api, ApiError } from "../lib/api";
import { MENSAJE_CONTINGENCIA } from "../lib/estados";
import type { FacturaDetalle } from "../lib/types";

/** Generar XML / vista previa firmada / enviar a la DGII paso a paso --
 * compartido entre FacturaDetailPage ("Acciones avanzadas") y el modo edición
 * de FacturaForm. Estas acciones operan sobre los `datos` YA GUARDADOS de la
 * factura, no sobre ediciones del formulario aún sin guardar. */
export function FacturaAcciones({
  factura,
  onRecargar,
  mostrarXml = true,
}: {
  factura: FacturaDetalle;
  onRecargar: () => void;
  /** El detalle de la factura ya tiene su propio visor de XML. */
  mostrarXml?: boolean;
}) {
  const [errores, setErrores] = useState<string[]>([]);
  const [mensaje, setMensaje] = useState<{ texto: string; error: boolean } | null>(null);
  const [procesando, setProcesando] = useState(false);
  // Igual que facturas-api: el XML de una factura enviada no se regenera (solo si el envío falló sin trackId).
  const yaEnviada =
    !["borrador", "generado", "firmado", "error"].includes(factura.estado) || (factura.estado === "error" && Boolean(factura.track_id));
  const [confirmarEnvio, setConfirmarEnvio] = useState(false);

  const fallo = (err: unknown) => setMensaje({ texto: err instanceof ApiError ? err.message : String(err), error: true });

  async function generarXml() {
    setProcesando(true);
    setErrores([]);
    setMensaje(null);
    try {
      const resultado = await api.generarXml(factura.id);
      if (resultado.errores.length > 0) {
        setErrores(resultado.errores);
      } else {
        setMensaje({ texto: "XML generado y validado contra el XSD oficial.", error: false });
        onRecargar();
      }
    } catch (err) {
      fallo(err);
    } finally {
      setProcesando(false);
    }
  }

  async function firmar() {
    setProcesando(true);
    setMensaje(null);
    try {
      await api.firmar(factura.id);
      setMensaje({ texto: "Vista previa firmada generada.", error: false });
      onRecargar();
    } catch (err) {
      fallo(err);
    } finally {
      setProcesando(false);
    }
  }

  async function enviar() {
    setConfirmarEnvio(false);
    setProcesando(true);
    setMensaje(null);
    try {
      const resultado = await api.enviar(factura.id);
      setMensaje(
        resultado.track_id
          ? { texto: `Enviado. Track ID: ${resultado.track_id}`, error: false }
          : resultado.contingencia
            ? { texto: resultado.mensaje ?? MENSAJE_CONTINGENCIA, error: false }
            : { texto: `Error al enviar: ${resultado.error}`, error: true },
      );
      onRecargar();
    } catch (err) {
      fallo(err);
      onRecargar();
    } finally {
      setProcesando(false);
    }
  }

  return (
    <div className="stack">
      <span className="hint">
        Estas acciones usan los datos ya guardados de la factura: guarde sus cambios antes de generar el XML.
      </span>

      {errores.length > 0 && (
        <div className="al al-d" role="alert">
          <span>
            <b>El XML no pasó la validación contra el XSD oficial:</b>
            <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>
              {errores.map((e, i) => (
                <li key={i}>{e}</li>
              ))}
            </ul>
          </span>
        </div>
      )}
      {mensaje && (
        <div className={mensaje.error ? "al al-d" : "al al-s"} role="status">
          <span>{mensaje.texto}</span>
        </div>
      )}

      <div className="row">
        <button
          type="button"
          className="btn btn-o btn-sm"
          onClick={generarXml}
          disabled={procesando || yaEnviada}
          title={
            factura.estado === "contingencia"
              ? "Emitida en contingencia: su XML firmado ya se entregó y se enviará tal cual"
              : yaEnviada
                ? "La factura ya fue enviada a la DGII: su XML no se puede regenerar"
                : undefined
          }
        >
          Generar XML
        </button>
        <button type="button" className="btn btn-o btn-sm" onClick={firmar} disabled={procesando || !factura.xml_generado}>
          Vista previa firmada
        </button>
        <button
          type="button"
          className="btn btn-p btn-sm"
          onClick={() => setConfirmarEnvio(true)}
          disabled={procesando || !factura.xml_generado || factura.estado === "enviado" || factura.estado === "aceptado"}
          title={factura.estado === "contingencia" ? "Reintenta ya el envío de esta factura emitida en contingencia" : undefined}
        >
          {factura.estado === "contingencia" ? "Reintentar envío" : "Enviar a la DGII"}
        </button>
        {factura.xml_firmado && (
          <a className="btn btn-g btn-sm" href={api.representacionImpresaUrl(factura.id)} target="_blank" rel="noopener noreferrer">
            Ver representación impresa (PDF)
          </a>
        )}
      </div>

      {(factura.track_id || factura.error) && (
        <dl className="kv">
          {factura.track_id && (
            <>
              <dt>Track ID</dt>
              <dd className="mono">{factura.track_id}</dd>
            </>
          )}
          {factura.error && (
            <>
              <dt>Error</dt>
              <dd className="err-t" style={{ fontWeight: 500 }}>{factura.error}</dd>
            </>
          )}
        </dl>
      )}

      {mostrarXml && factura.xml_generado && (
        <details>
          <summary className="link" style={{ cursor: "pointer" }}>XML generado (sin firmar)</summary>
          <pre className="mono" style={{ maxHeight: 320, overflow: "auto", background: "#F8FAFC", padding: 12, borderRadius: 8 }}>
            {factura.xml_generado}
          </pre>
        </details>
      )}
      {mostrarXml && factura.xml_firmado && (
        <details>
          <summary className="link" style={{ cursor: "pointer" }}>
            {factura.firmado_en ? "XML firmado (el que se envía a la DGII)" : "XML firmado (vista previa)"}
          </summary>
          <pre className="mono" style={{ maxHeight: 320, overflow: "auto", background: "#F8FAFC", padding: 12, borderRadius: 8 }}>
            {factura.xml_firmado}
          </pre>
        </details>
      )}

      {confirmarEnvio && (
        <div className="scrim">
          <div className="modal" role="dialog" aria-modal="true" aria-labelledby="env-t">
            <div className="modal-h">
              <h3 id="env-t">¿Enviar este e-CF a la DGII?</h3>
            </div>
            <div className="modal-b">
              <span>
                Se enviará <b className="mono" style={{ color: "#111827" }}>{factura.encf}</b> tal como está guardado. Esta acción no se puede
                deshacer.
              </span>
            </div>
            <div className="modal-f">
              <button type="button" className="btn btn-o" onClick={() => setConfirmarEnvio(false)}>
                Cancelar
              </button>
              <button type="button" className="btn btn-p" onClick={enviar}>
                Sí, enviar
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
