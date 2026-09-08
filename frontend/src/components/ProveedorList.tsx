import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { InventarioApiError, inventarioApi } from "../lib/inventarioApi";
import type { Proveedor } from "../lib/types";

export function ProveedorList() {
  const [proveedores, setProveedores] = useState<Proveedor[]>([]);
  const [cargando, setCargando] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    inventarioApi
      .listarProveedores()
      .then(setProveedores)
      .catch((err) => setError(err instanceof InventarioApiError ? err.message : String(err)))
      .finally(() => setCargando(false));
  }, []);

  if (cargando) return <p>Cargando…</p>;
  if (error) return <p className="error">{error}</p>;

  return (
    <div>
      <div className="acciones-secundarias">
        <Link className="btn-primario" to="/inventario/proveedores/nuevo">
          + Nuevo proveedor
        </Link>
      </div>

      {proveedores.length === 0 ? (
        <div className="lista-vacia">
          <p>No hay proveedores todavía.</p>
          <Link className="btn-primario" to="/inventario/proveedores/nuevo">
            Crear el primero
          </Link>
        </div>
      ) : (
        <table className="lista-facturas">
          <thead>
            <tr>
              <th>Nombre</th>
              <th>RNC</th>
              <th>Teléfono</th>
              <th>Correo</th>
              <th>Estado</th>
            </tr>
          </thead>
          <tbody>
            {proveedores.map((p) => (
              <tr key={p.id}>
                <td>
                  <Link to={`/inventario/proveedores/${p.id}/editar`}>{p.nombre}</Link>
                </td>
                <td>{p.rnc ?? "—"}</td>
                <td>{p.telefono ?? "—"}</td>
                <td>{p.correo ?? "—"}</td>
                <td>
                  <span className={`estado estado-${p.activo ? "enviado" : "rechazado"}`}>
                    {p.activo ? "Activo" : "Inactivo"}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
