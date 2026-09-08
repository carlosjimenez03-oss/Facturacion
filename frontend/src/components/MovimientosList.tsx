import { useEffect, useState } from "react";
import { InventarioApiError, inventarioApi } from "../lib/inventarioApi";
import type { Movimiento, Producto } from "../lib/types";

const TIPO_LABEL: Record<string, string> = { entrada: "Entrada", salida: "Salida" };

export function MovimientosList() {
  const [movimientos, setMovimientos] = useState<Movimiento[]>([]);
  const [productos, setProductos] = useState<Producto[]>([]);
  const [productoId, setProductoId] = useState("");
  const [tipo, setTipo] = useState("");
  const [cargando, setCargando] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    inventarioApi.listarProductos().then(setProductos).catch(() => {});
  }, []);

  useEffect(() => {
    setCargando(true);
    inventarioApi
      .listarMovimientos({ producto_id: productoId || undefined, tipo: tipo || undefined })
      .then(setMovimientos)
      .catch((err) => setError(err instanceof InventarioApiError ? err.message : String(err)))
      .finally(() => setCargando(false));
  }, [productoId, tipo]);

  const nombreProducto = (id: string) => productos.find((p) => p.id === id)?.nombre ?? id;

  return (
    <div>
      <div className="acciones-secundarias">
        <select value={productoId} onChange={(e) => setProductoId(e.target.value)}>
          <option value="">Todos los productos</option>
          {productos.map((p) => (
            <option key={p.id} value={p.id}>
              {p.sku} - {p.nombre}
            </option>
          ))}
        </select>
        <select value={tipo} onChange={(e) => setTipo(e.target.value)}>
          <option value="">Entradas y salidas</option>
          <option value="entrada">Solo entradas</option>
          <option value="salida">Solo salidas</option>
        </select>
      </div>

      {error && <p className="error">{error}</p>}
      {cargando ? (
        <p>Cargando…</p>
      ) : movimientos.length === 0 ? (
        <div className="lista-vacia">
          <p>No hay movimientos de inventario con estos filtros.</p>
        </div>
      ) : (
        <table className="lista-facturas">
          <thead>
            <tr>
              <th>Fecha</th>
              <th>Producto</th>
              <th>Tipo</th>
              <th>Cantidad</th>
              <th>Motivo</th>
              <th>Referencia</th>
            </tr>
          </thead>
          <tbody>
            {movimientos.map((m) => (
              <tr key={m.id}>
                <td>{new Date(m.creado_en).toLocaleString("es-DO")}</td>
                <td>{nombreProducto(m.producto_id)}</td>
                <td>
                  <span className={`estado estado-${m.tipo === "entrada" ? "enviado" : "rechazado"}`}>
                    {TIPO_LABEL[m.tipo] ?? m.tipo}
                  </span>
                </td>
                <td>{m.cantidad}</td>
                <td>{m.motivo ?? "—"}</td>
                <td>{m.referencia_tipo ? `${m.referencia_tipo}: ${m.referencia_id}` : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
