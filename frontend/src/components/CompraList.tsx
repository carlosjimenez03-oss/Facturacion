import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { InventarioApiError, inventarioApi } from "../lib/inventarioApi";
import type { Compra } from "../lib/types";

const ESTADO_LABEL: Record<string, string> = {
  borrador: "Borrador",
  confirmada: "Confirmada",
};

export function CompraList() {
  const [compras, setCompras] = useState<Compra[]>([]);
  const [cargando, setCargando] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    inventarioApi
      .listarCompras()
      .then(setCompras)
      .catch((err) => setError(err instanceof InventarioApiError ? err.message : String(err)))
      .finally(() => setCargando(false));
  }, []);

  if (cargando) return <p>Cargando…</p>;
  if (error) return <p className="error">{error}</p>;

  return (
    <div>
      <div className="acciones-secundarias">
        <Link className="btn-primario" to="/inventario/compras/nueva">
          + Nueva compra
        </Link>
      </div>

      {compras.length === 0 ? (
        <div className="lista-vacia">
          <p>No hay compras registradas todavía.</p>
          <Link className="btn-primario" to="/inventario/compras/nueva">
            Registrar la primera
          </Link>
        </div>
      ) : (
        <table className="lista-facturas">
          <thead>
            <tr>
              <th>Documento</th>
              <th>Estado</th>
              <th>Creada</th>
            </tr>
          </thead>
          <tbody>
            {compras.map((c) => (
              <tr key={c.id}>
                <td>
                  <Link to={`/inventario/compras/${c.id}`}>{c.numero_documento || "(sin número)"}</Link>
                </td>
                <td>
                  <span className={`estado estado-${c.estado === "confirmada" ? "enviado" : "borrador"}`}>
                    {ESTADO_LABEL[c.estado] ?? c.estado}
                  </span>
                </td>
                <td>{new Date(c.creado_en).toLocaleString("es-DO")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
