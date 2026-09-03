import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, ApiError } from "../lib/api";
import type { FacturaDetalle } from "../lib/types";
import { FacturaAcciones } from "./FacturaAcciones";

export function FacturaDetailPage() {
  const { id } = useParams<{ id: string }>();
  const [factura, setFactura] = useState<FacturaDetalle | null>(null);
  const [mensaje, setMensaje] = useState<string | null>(null);
  const [cargando, setCargando] = useState(true);

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

      {factura.estado === "borrador" && (
        <p>
          <Link to={`/facturas/${factura.id}/editar`} className="btn-secundario">
            Editar borrador
          </Link>
        </p>
      )}

      <FacturaAcciones factura={factura} onRecargar={cargar} />

      <details>
        <summary>Datos estructurados</summary>
        <pre>{JSON.stringify(factura.datos, null, 2)}</pre>
      </details>
    </div>
  );
}
