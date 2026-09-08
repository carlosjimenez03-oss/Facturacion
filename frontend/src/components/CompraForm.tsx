import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { InventarioApiError, inventarioApi } from "../lib/inventarioApi";
import type { Compra, CompraItem, Producto, Proveedor } from "../lib/types";

function itemVacio(): CompraItem {
  return { producto_id: "", cantidad: "1", costo_unitario: "0" };
}

export function CompraForm() {
  const navigate = useNavigate();
  const { id } = useParams<{ id: string }>();
  const editando = Boolean(id);

  const [compra, setCompra] = useState<Compra | null>(null);
  const [proveedores, setProveedores] = useState<Proveedor[]>([]);
  const [productos, setProductos] = useState<Producto[]>([]);

  const [proveedorId, setProveedorId] = useState("");
  const [numeroDocumento, setNumeroDocumento] = useState("");
  const [notas, setNotas] = useState("");
  const [items, setItems] = useState<CompraItem[]>([itemVacio()]);

  const [cargando, setCargando] = useState(editando);
  const [guardando, setGuardando] = useState(false);
  const [confirmando, setConfirmando] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    inventarioApi.listarProveedores({ activo: true }).then(setProveedores).catch(() => {});
    inventarioApi.listarProductos({ activo: true }).then(setProductos).catch(() => {});
  }, []);

  useEffect(() => {
    if (!id) return;
    inventarioApi
      .obtenerCompra(id)
      .then((c) => {
        setCompra(c);
        setProveedorId(c.proveedor_id ?? "");
        setNumeroDocumento(c.numero_documento ?? "");
        setNotas(c.notas ?? "");
        setItems(
          c.items.length > 0
            ? c.items.map((i) => ({ producto_id: i.producto_id, cantidad: i.cantidad, costo_unitario: i.costo_unitario }))
            : [itemVacio()],
        );
      })
      .catch((err) => setError(err instanceof InventarioApiError ? err.message : String(err)))
      .finally(() => setCargando(false));
  }, [id]);

  const soloLectura = compra?.estado === "confirmada";

  function actualizarItem(index: number, cambios: Partial<CompraItem>) {
    const copia = items.slice();
    copia[index] = { ...copia[index], ...cambios };
    setItems(copia);
  }

  function agregarItem() {
    setItems([...items, itemVacio()]);
  }

  function eliminarItem(index: number) {
    if (items.length <= 1) return;
    setItems(items.filter((_, i) => i !== index));
  }

  async function guardar(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setGuardando(true);
    try {
      const payload = {
        proveedor_id: proveedorId || null,
        numero_documento: numeroDocumento || null,
        notas: notas || null,
        items: items.filter((i) => i.producto_id),
      };
      if (editando && id) {
        await inventarioApi.actualizarCompra(id, payload);
        navigate(`/inventario/compras/${id}`);
      } else {
        const creada = await inventarioApi.crearCompra(payload);
        navigate(`/inventario/compras/${creada.id}`);
      }
    } catch (err) {
      setError(err instanceof InventarioApiError ? err.message : String(err));
    } finally {
      setGuardando(false);
    }
  }

  async function confirmar() {
    if (!id) return;
    setError(null);
    setConfirmando(true);
    try {
      const confirmada = await inventarioApi.confirmarCompra(id);
      setCompra(confirmada);
    } catch (err) {
      setError(err instanceof InventarioApiError ? err.message : String(err));
    } finally {
      setConfirmando(false);
    }
  }

  if (cargando) return <p>Cargando…</p>;

  const total = items.reduce((acc, i) => acc + (Number(i.cantidad) || 0) * (Number(i.costo_unitario) || 0), 0);

  return (
    <form className="factura-form" onSubmit={guardar}>
      <section>
        <h2>{editando ? "Compra" : "Nueva compra"}</h2>
        {compra && (
          <p>
            Estado:{" "}
            <span className={`estado estado-${compra.estado === "confirmada" ? "enviado" : "borrador"}`}>
              {compra.estado === "confirmada" ? "Confirmada" : "Borrador"}
            </span>
          </p>
        )}
        <label>
          Proveedor (opcional)
          <select value={proveedorId} onChange={(e) => setProveedorId(e.target.value)} disabled={soloLectura}>
            <option value="">-- Sin proveedor --</option>
            {proveedores.map((p) => (
              <option key={p.id} value={p.id}>
                {p.nombre}
              </option>
            ))}
          </select>
        </label>
        <label>
          Número de documento (opcional)
          <input
            value={numeroDocumento}
            onChange={(e) => setNumeroDocumento(e.target.value)}
            disabled={soloLectura}
          />
        </label>
        <label>
          Notas (opcional)
          <input value={notas} onChange={(e) => setNotas(e.target.value)} disabled={soloLectura} />
        </label>
      </section>

      <section>
        <h2>Productos comprados (entrada de mercancía)</h2>
        <div className="items-editor">
          <table className="items">
            <thead>
              <tr>
                <th>Producto</th>
                <th>Cantidad</th>
                <th>Costo unitario</th>
                <th>Subtotal</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {items.map((item, idx) => (
                <tr key={idx}>
                  <td>
                    <select
                      value={item.producto_id}
                      onChange={(e) => actualizarItem(idx, { producto_id: e.target.value })}
                      disabled={soloLectura}
                      required
                    >
                      <option value="" disabled>
                        Elegir producto...
                      </option>
                      {productos.map((p) => (
                        <option key={p.id} value={p.id}>
                          {p.sku} - {p.nombre}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td>
                    <input
                      type="number"
                      min="0"
                      step="0.01"
                      value={item.cantidad}
                      onChange={(e) => actualizarItem(idx, { cantidad: e.target.value })}
                      disabled={soloLectura}
                    />
                  </td>
                  <td>
                    <input
                      type="number"
                      min="0"
                      step="0.0001"
                      value={item.costo_unitario}
                      onChange={(e) => actualizarItem(idx, { costo_unitario: e.target.value })}
                      disabled={soloLectura}
                    />
                  </td>
                  <td className="monto-item">
                    {((Number(item.cantidad) || 0) * (Number(item.costo_unitario) || 0)).toFixed(2)}
                  </td>
                  <td>
                    {!soloLectura && (
                      <button
                        type="button"
                        className="btn-icono"
                        onClick={() => eliminarItem(idx)}
                        disabled={items.length <= 1}
                        aria-label="Eliminar línea"
                      >
                        ×
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {!soloLectura && (
            <button type="button" className="btn-secundario" onClick={agregarItem}>
              + Agregar línea
            </button>
          )}
          <p>
            <strong>Total: {total.toFixed(2)}</strong>
          </p>
        </div>
      </section>

      {error && <p className="error">{error}</p>}

      {!soloLectura && (
        <div className="acciones">
          <button type="submit" className="btn-primario" disabled={guardando}>
            {guardando ? "Guardando…" : editando ? "Guardar cambios" : "Guardar borrador"}
          </button>
          {editando && (
            <button type="button" className="btn-secundario" onClick={confirmar} disabled={confirmando}>
              {confirmando ? "Confirmando…" : "Confirmar compra (sube el stock)"}
            </button>
          )}
        </div>
      )}
    </form>
  );
}
