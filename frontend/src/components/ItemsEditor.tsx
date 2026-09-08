import { useEffect, useState } from "react";
import { inventarioApi } from "../lib/inventarioApi";
import { INDICADORES_FACTURACION, INDICADOR_BIEN_SERVICIO, UNIDADES_MEDIDA } from "../lib/catalogos";
import { calcularMontoItem, decimal2 } from "../lib/format";
import { nuevoItem } from "../lib/buildDatos";
import type { ItemFactura, Producto } from "../lib/types";

interface Props {
  items: ItemFactura[];
  onChange: (items: ItemFactura[]) => void;
}

export function ItemsEditor({ items, onChange }: Props) {
  const [productos, setProductos] = useState<Producto[]>([]);
  const [errorProductos, setErrorProductos] = useState<string | null>(null);

  useEffect(() => {
    inventarioApi
      .listarProductos({ activo: true })
      .then(setProductos)
      .catch(() => setErrorProductos("No se pudo cargar el catálogo de productos."));
  }, []);

  function actualizarItem(index: number, cambios: Partial<ItemFactura>) {
    const copia = items.slice();
    copia[index] = { ...copia[index], ...cambios };
    onChange(copia);
  }

  function elegirProducto(index: number, productoId: string) {
    const producto = productos.find((p) => p.id === productoId);
    if (!producto) {
      actualizarItem(index, { productoId: undefined });
      return;
    }
    actualizarItem(index, {
      productoId: producto.id,
      NombreItem: producto.nombre,
      PrecioUnitarioItem: producto.precio_venta,
    });
  }

  function agregarItem() {
    onChange([...items, nuevoItem(items.length + 1)]);
  }

  function eliminarItem(index: number) {
    if (items.length <= 1) return;
    onChange(items.filter((_, i) => i !== index));
  }

  return (
    <div className="items-editor">
      {errorProductos && <p className="error">{errorProductos}</p>}
      {!errorProductos && productos.length === 0 && (
        <p className="mensaje">
          No hay productos activos en el catálogo -- crea al menos uno en Inventario &gt; Productos antes
          de facturar.
        </p>
      )}
      <table className="items">
        <thead>
          <tr>
            <th>#</th>
            <th>Producto</th>
            <th>Bien/Servicio</th>
            <th>ITBIS</th>
            <th>Cantidad</th>
            <th>Unidad</th>
            <th>Precio unit.</th>
            <th>Descuento</th>
            <th>Monto</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {items.map((item, idx) => {
            const productoSeleccionado = productos.find((p) => p.id === item.productoId);
            const stockInsuficiente =
              productoSeleccionado != null &&
              Number(productoSeleccionado.stock_actual) < (Number(item.CantidadItem) || 0);
            return (
              <tr key={idx}>
                <td>{idx + 1}</td>
                <td>
                  <select
                    value={item.productoId ?? ""}
                    onChange={(e) => elegirProducto(idx, e.target.value)}
                    required
                  >
                    <option value="" disabled>
                      Elegir producto...
                    </option>
                    {productos.map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.sku} - {p.nombre} (stock: {p.stock_actual})
                      </option>
                    ))}
                  </select>
                  {stockInsuficiente && (
                    <div className="error" style={{ marginTop: "0.25rem" }}>
                      Stock insuficiente (disponible: {productoSeleccionado?.stock_actual})
                    </div>
                  )}
                </td>
                <td>
                  <select
                    value={item.IndicadorBienoServicio}
                    onChange={(e) => actualizarItem(idx, { IndicadorBienoServicio: e.target.value })}
                  >
                    {INDICADOR_BIEN_SERVICIO.map((o) => (
                      <option key={o.value} value={o.value}>
                        {o.label}
                      </option>
                    ))}
                  </select>
                </td>
                <td>
                  <select
                    value={item.IndicadorFacturacion}
                    onChange={(e) => actualizarItem(idx, { IndicadorFacturacion: e.target.value })}
                  >
                    {INDICADORES_FACTURACION.map((o) => (
                      <option key={o.value} value={o.value}>
                        {o.label}
                      </option>
                    ))}
                  </select>
                </td>
                <td>
                  <input
                    type="number"
                    min="0"
                    step="0.01"
                    value={item.CantidadItem}
                    onChange={(e) => actualizarItem(idx, { CantidadItem: e.target.value })}
                  />
                </td>
                <td>
                  <select
                    value={item.UnidadMedida ?? ""}
                    onChange={(e) => actualizarItem(idx, { UnidadMedida: e.target.value || undefined })}
                  >
                    <option value="">--</option>
                    {UNIDADES_MEDIDA.map((o) => (
                      <option key={o.value} value={o.value}>
                        {o.label}
                      </option>
                    ))}
                  </select>
                </td>
                <td>
                  <input
                    type="number"
                    min="0"
                    step="0.0001"
                    value={item.PrecioUnitarioItem}
                    onChange={(e) => actualizarItem(idx, { PrecioUnitarioItem: e.target.value })}
                  />
                </td>
                <td>
                  <input
                    type="number"
                    min="0"
                    step="0.01"
                    value={item.DescuentoMonto ?? ""}
                    onChange={(e) => actualizarItem(idx, { DescuentoMonto: e.target.value || undefined })}
                  />
                </td>
                <td className="monto-item">{decimal2(calcularMontoItem(item))}</td>
                <td>
                  <button
                    type="button"
                    className="btn-icono"
                    onClick={() => eliminarItem(idx)}
                    disabled={items.length <= 1}
                    aria-label="Eliminar línea"
                  >
                    ×
                  </button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <button type="button" className="btn-secundario" onClick={agregarItem}>
        + Agregar línea
      </button>
    </div>
  );
}
