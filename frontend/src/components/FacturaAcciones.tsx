import { useState } from "react";
import { api, ApiError } from "../lib/api";
import type { FacturaDetalle } from "../lib/types";

/** Generar XML / vista previa firmada / enviar a la DGII -- compartido entre
 * FacturaDetailPage y el modo edición de FacturaForm. Estas acciones operan
 * sobre los `datos` YA GUARDADOS de la factura, no sobre ediciones del
 * formulario aún sin guardar. */
export function FacturaAcciones({
  factura,
  onRecargar,
}: {
  factura: FacturaDetalle;
  onRecargar: () => void;
}) {
  const [errores, setErrores] = useState<string[]>([]);
  const [mensaje, setMensaje] = useState<string | null>(null);
  const [procesando, setProcesando] = useState(false);

  async function generarXml() {
    setProcesando(true);
    setErrores([]);
    setMensaje(null);
    try {
      const resultado = await api.generarXml(factura.id);
      if (resultado.errores.length > 0) {
        setErrores(resultado.errores);
      } else {
        setMensaje("XML generado y validado contra el XSD oficial.");
        onRecargar();
      }
    } catch (err) {
      setMensaje(err instanceof ApiError ? err.message : String(err));
    } finally {
      setProcesando(false);
    }
  }

  async function firmar() {
    setProcesando(true);
    setMensaje(null);
    try {
      await api.firmar(factura.id);
      setMensaje("Vista previa firmada generada.");
      onRecargar();
    } catch (err) {
      setMensaje(err instanceof ApiError ? err.message : String(err));
    } finally {
      setProcesando(false);
    }
  }

  async function enviar() {
    if (!confirm("¿Enviar este e-CF a la DGII? Esta acción no se puede deshacer.")) return;
    setProcesando(true);
    setMensaje(null);
    try {
      const resultado = await api.enviar(factura.id);
      setMensaje(
        resultado.track_id
          ? `Enviado. trackId: ${resultado.track_id}`
          : `Error al enviar: ${resultado.error}`,
      );
      onRecargar();
    } catch (err) {
      setMensaje(err instanceof ApiError ? err.message : String(err));
    } finally {
      setProcesando(false);
    }
  }

  return (
    <div className="factura-acciones-panel">
      <p className="nota-acciones">
        Estas acciones usan los datos ya guardados de la factura -- guarda tus cambios antes de
        generar el XML.
      </p>

      {errores.length > 0 && (
        <div className="errores-validacion">
          <strong>El XML no pasó la validación contra el XSD oficial:</strong>
          <ul>
            {errores.map((e, i) => (
              <li key={i}>{e}</li>
            ))}
          </ul>
        </div>
      )}
      {mensaje && <p className="mensaje">{mensaje}</p>}

      <div className="acciones acciones-secundarias">
        <button type="button" className="btn-secundario" onClick={generarXml} disabled={procesando}>
          Generar XML
        </button>
        <button
          type="button"
          className="btn-secundario"
          onClick={firmar}
          disabled={procesando || !factura.xml_generado}
        >
          Vista previa firmada
        </button>
        <button
          type="button"
          className="btn-primario"
          onClick={enviar}
          disabled={
            procesando ||
            !factura.xml_generado ||
            factura.estado === "enviado" ||
            factura.estado === "aceptado"
          }
        >
          Enviar a la DGII
        </button>
      </div>

      {factura.track_id && (
        <p>
          <strong>Track ID:</strong> {factura.track_id}
        </p>
      )}
      {factura.error && (
        <p className="error">
          <strong>Error:</strong> {factura.error}
        </p>
      )}

      {factura.xml_generado && (
        <details>
          <summary>XML generado (sin firmar)</summary>
          <pre>{factura.xml_generado}</pre>
        </details>
      )}
      {factura.xml_firmado && (
        <details>
          <summary>XML firmado (vista previa)</summary>
          <pre>{factura.xml_firmado}</pre>
        </details>
      )}
    </div>
  );
}
