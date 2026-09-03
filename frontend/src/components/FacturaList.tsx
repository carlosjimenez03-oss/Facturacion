import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, ApiError } from "../lib/api";
import type { FacturaResumen } from "../lib/types";

const ESTADO_LABEL: Record<string, string> = {
  borrador: "Borrador",
  generado: "XML generado",
  firmado: "Firmado (vista previa)",
  enviado: "Enviado a la DGII",
  aceptado: "Aceptado",
  rechazado: "Rechazado",
  error: "Error",
};

export function FacturaList() {
  const [facturas, setFacturas] = useState<FacturaResumen[]>([]);
  const [cargando, setCargando] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .listar()
      .then(setFacturas)
      .catch((err) => setError(err instanceof ApiError ? err.message : String(err)))
      .finally(() => setCargando(false));
  }, []);

  if (cargando) return <p>Cargando…</p>;
  if (error) return <p className="error">{error}</p>;

  if (facturas.length === 0) {
    return (
      <div className="lista-vacia">
        <p>No hay facturas todavía.</p>
        <Link className="btn-primario" to="/facturas/nueva">
          Crear la primera
        </Link>
      </div>
    );
  }

  return (
    <table className="lista-facturas">
      <thead>
        <tr>
          <th>Tipo</th>
          <th>e-NCF</th>
          <th>Estado</th>
          <th>Track ID</th>
          <th>Creada</th>
        </tr>
      </thead>
      <tbody>
        {facturas.map((f) => (
          <tr key={f.id}>
            <td>{f.tipo_ecf}</td>
            <td>
              <Link to={`/facturas/${f.id}`}>{f.encf || "(sin eNCF)"}</Link>
            </td>
            <td>
              <span className={`estado estado-${f.estado}`}>{ESTADO_LABEL[f.estado] ?? f.estado}</span>
            </td>
            <td>{f.track_id ?? "—"}</td>
            <td>{new Date(f.creado_en).toLocaleString("es-DO")}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
