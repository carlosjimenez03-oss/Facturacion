import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { InventarioApiError, inventarioApi } from "../lib/inventarioApi";

export function ProductoForm() {
  const navigate = useNavigate();
  const { id } = useParams<{ id: string }>();
  const editando = Boolean(id);

  const [sku, setSku] = useState("");
  const [nombre, setNombre] = useState("");
  const [descripcion, setDescripcion] = useState("");
  const [unidadMedida, setUnidadMedida] = useState("");
  const [precioVenta, setPrecioVenta] = useState("0");
  const [stockInicial, setStockInicial] = useState("0");
  const [activo, setActivo] = useState(true);

  const [cargando, setCargando] = useState(editando);
  const [guardando, setGuardando] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!id) return;
    inventarioApi
      .obtenerProducto(id)
      .then((p) => {
        setSku(p.sku);
        setNombre(p.nombre);
        setDescripcion(p.descripcion ?? "");
        setUnidadMedida(p.unidad_medida ?? "");
        setPrecioVenta(p.precio_venta);
        setActivo(p.activo);
      })
      .catch((err) => setError(err instanceof InventarioApiError ? err.message : String(err)))
      .finally(() => setCargando(false));
  }, [id]);

  async function guardar(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setGuardando(true);
    try {
      if (editando && id) {
        await inventarioApi.actualizarProducto(id, {
          sku,
          nombre,
          descripcion: descripcion || null,
          unidad_medida: unidadMedida || null,
          precio_venta: precioVenta,
          activo,
        });
      } else {
        await inventarioApi.crearProducto({
          sku,
          nombre,
          descripcion: descripcion || null,
          unidad_medida: unidadMedida || null,
          precio_venta: precioVenta,
          stock_inicial: stockInicial,
        });
      }
      navigate("/inventario/productos");
    } catch (err) {
      setError(err instanceof InventarioApiError ? err.message : String(err));
    } finally {
      setGuardando(false);
    }
  }

  if (cargando) return <p>Cargando…</p>;

  return (
    <form className="factura-form" onSubmit={guardar}>
      <section>
        <h2>{editando ? "Editar producto" : "Nuevo producto"}</h2>
        <label>
          SKU
          <input value={sku} onChange={(e) => setSku(e.target.value)} required />
        </label>
        <label>
          Nombre
          <input value={nombre} onChange={(e) => setNombre(e.target.value)} required />
        </label>
        <label>
          Descripción (opcional)
          <input value={descripcion} onChange={(e) => setDescripcion(e.target.value)} />
        </label>
        <label>
          Unidad de medida (opcional)
          <input
            value={unidadMedida}
            onChange={(e) => setUnidadMedida(e.target.value)}
            placeholder="Ej. UND, KG, CAJ"
          />
        </label>
        <label>
          Precio de venta
          <input
            type="number"
            min="0"
            step="0.0001"
            value={precioVenta}
            onChange={(e) => setPrecioVenta(e.target.value)}
            required
          />
        </label>
        {!editando && (
          <label>
            Stock inicial
            <input
              type="number"
              min="0"
              step="0.01"
              value={stockInicial}
              onChange={(e) => setStockInicial(e.target.value)}
            />
          </label>
        )}
        {editando && (
          <label>
            Estado
            <select value={activo ? "1" : "0"} onChange={(e) => setActivo(e.target.value === "1")}>
              <option value="1">Activo</option>
              <option value="0">Inactivo</option>
            </select>
          </label>
        )}
      </section>

      {error && <p className="error">{error}</p>}

      <div className="acciones">
        <button type="submit" className="btn-primario" disabled={guardando}>
          {guardando ? "Guardando…" : "Guardar"}
        </button>
      </div>
    </form>
  );
}
