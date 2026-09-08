import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { InventarioApiError, inventarioApi } from "../lib/inventarioApi";
import type { Producto } from "../lib/types";

export function ProductoList() {
  const [productos, setProductos] = useState<Producto[]>([]);
  const [q, setQ] = useState("");
  const [cargando, setCargando] = useState(true);
  const [error, setError] = useState<string | null>(null);

  function cargar() {
    setCargando(true);
    inventarioApi
      .listarProductos(q ? { q } : undefined)
      .then(setProductos)
      .catch((err) => setError(err instanceof InventarioApiError ? err.message : String(err)))
      .finally(() => setCargando(false));
  }

  useEffect(() => {
    const timeout = setTimeout(cargar, 250);
    return () => clearTimeout(timeout);
  }, [q]);

  if (error) return <p className="error">{error}</p>;

  return (
    <div>
      <div className="acciones-secundarias">
        <input
          placeholder="Buscar por SKU o nombre..."
          value={q}
          onChange={(e) => setQ(e.target.value)}
          style={{ maxWidth: "320px" }}
        />
        <Link className="btn-primario" to="/inventario/productos/nuevo">
          + Nuevo producto
        </Link>
      </div>

      {cargando ? (
        <p>Cargando…</p>
      ) : productos.length === 0 ? (
        <div className="lista-vacia">
          <p>No hay productos en el catálogo todavía.</p>
          <Link className="btn-primario" to="/inventario/productos/nuevo">
            Crear el primero
          </Link>
        </div>
      ) : (
        <table className="lista-facturas">
          <thead>
            <tr>
              <th>SKU</th>
              <th>Nombre</th>
              <th>Precio venta</th>
              <th>Costo promedio</th>
              <th>Stock</th>
              <th>Estado</th>
            </tr>
          </thead>
          <tbody>
            {productos.map((p) => (
              <tr key={p.id}>
                <td>{p.sku}</td>
                <td>
                  <Link to={`/inventario/productos/${p.id}/editar`}>{p.nombre}</Link>
                </td>
                <td>{p.precio_venta}</td>
                <td>{p.costo_promedio}</td>
                <td>{p.stock_actual}</td>
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
