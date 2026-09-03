import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, ApiError } from "../lib/api";
import type { FacturaDetalle } from "../lib/types";

export function FacturaDetailPage() {
  const { id } = useParams<{ id: string }>();
  const [factura, setFactura] = useState<FacturaDetalle | null>(null);
  const [errores, setErrores] = useState<string[]>([]);
  const [mensaje, setMensaje] = useState<string | null>(null);
  const [cargando, setCargando] = useState(true);
  const [procesando, setProcesando] = useState(false);

  const cargar = useCallback(() => {
    if (!id) return;
    setCargando(true);
    api
      .obtener(id)
      .then(setFactura)
      .catch((err) => setMensaje(err instanceof ApiError ? err.message : String(err)))
      .finally(() => setCargando(false));
  }, [id]);

  useEffect(() => {
    cargar();
  }, [cargar]);

  async function generarXml() {
    if (!id) return;
    setProcesando(true);
    setErrores([]);
    setMensaje(null);
    try {
      const resultado = await api.generarXml(id);
      if (resultado.errores.length > 0) {
        setErrores(resultado.errores);
      } else {
        setMensaje("XML generado y validado contra el XSD oficial.");
        cargar();
      }
    } catch (err) {
      setMensaje(err instanceof ApiError ? err.message : String(err));
    } finally {
      setProcesando(false);
    }
  }

  async function firmar() {
    if (!id) return;
    setProcesando(true);
    setMensaje(null);
    try {
      await api.firmar(id);
      setMensaje("Vista previa firmada generada.");
      cargar();
    } catch (err) {
      setMensaje(err instanceof ApiError ? err.message : String(err));
    } finally {
      setProcesando(false);
    }
  }

  async function enviar() {
    if (!id) return;
    if (!confirm("¿Enviar este e-CF a la DGII? Esta acción no se puede deshacer.")) return;
    setProcesando(true);
    setMensaje(null);
    try {
      const resultado = await api.enviar(id);
      setMensaje(
        resultado.track_id
          ? `Enviado. trackId: ${resultado.track_id}`
          : `Error al enviar: ${resultado.error}`,
      );
      cargar();
    } catch (err) {
      setMensaje(err instanceof ApiError ? err.message : String(err));
    } finally {
      setProcesando(false);
    }
  }

  if (cargando) return <p>Cargando…</p>;
  if (!factura) return <p className="error">{mensaje ?? "Factura no encontrada"}</p>;

  return (
    <div className="factura-detalle">
      <h2>
        e-CF {factura.tipo_ecf} · {factura.encf || "(sin eNCF)"}
      </h2>
      <p>
        Estado: <span className={`estado estado-${factura.estado}`}>{factura.estado}</span>
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

      <div className="acciones">
        {factura.estado === "borrador" && (
          <Link to={`/facturas/${factura.id}/editar`} className="btn-secundario">
            Editar borrador
          </Link>
        )}
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
          disabled={procesando || !factura.xml_generado || factura.estado === "enviado" || factura.estado === "aceptado"}
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
      <details>
        <summary>Datos estructurados</summary>
        <pre>{JSON.stringify(factura.datos, null, 2)}</pre>
      </details>
    </div>
  );
}
