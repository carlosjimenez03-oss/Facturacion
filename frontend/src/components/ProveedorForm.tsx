import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { InventarioApiError, inventarioApi } from "../lib/inventarioApi";

export function ProveedorForm() {
  const navigate = useNavigate();
  const { id } = useParams<{ id: string }>();
  const editando = Boolean(id);

  const [nombre, setNombre] = useState("");
  const [rnc, setRnc] = useState("");
  const [telefono, setTelefono] = useState("");
  const [correo, setCorreo] = useState("");
  const [direccion, setDireccion] = useState("");
  const [activo, setActivo] = useState(true);

  const [cargando, setCargando] = useState(editando);
  const [guardando, setGuardando] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!id) return;
    inventarioApi
      .obtenerProveedor(id)
      .then((p) => {
        setNombre(p.nombre);
        setRnc(p.rnc ?? "");
        setTelefono(p.telefono ?? "");
        setCorreo(p.correo ?? "");
        setDireccion(p.direccion ?? "");
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
      const payload = {
        nombre,
        rnc: rnc || null,
        telefono: telefono || null,
        correo: correo || null,
        direccion: direccion || null,
      };
      if (editando && id) {
        await inventarioApi.actualizarProveedor(id, { ...payload, activo });
      } else {
        await inventarioApi.crearProveedor(payload);
      }
      navigate("/inventario/proveedores");
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
        <h2>{editando ? "Editar proveedor" : "Nuevo proveedor"}</h2>
        <label>
          Nombre
          <input value={nombre} onChange={(e) => setNombre(e.target.value)} required />
        </label>
        <label>
          RNC (opcional)
          <input value={rnc} onChange={(e) => setRnc(e.target.value)} />
        </label>
        <label>
          Teléfono (opcional)
          <input value={telefono} onChange={(e) => setTelefono(e.target.value)} />
        </label>
        <label>
          Correo (opcional)
          <input type="email" value={correo} onChange={(e) => setCorreo(e.target.value)} />
        </label>
        <label>
          Dirección (opcional)
          <input value={direccion} onChange={(e) => setDireccion(e.target.value)} />
        </label>
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
