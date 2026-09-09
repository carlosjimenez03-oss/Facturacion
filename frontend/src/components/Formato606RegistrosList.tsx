import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Formato606ApiError, formato606Api } from "../lib/formato606Api";
import type { ErrorValidacion606, Registro606 } from "../lib/types";

const ORIGEN_LABEL: Record<string, string> = {
  receptor_recepcion: "e-CF recibido",
  auto_emitido: "Autoemitido",
  manual: "Manual",
};

function formatearFecha(aaaammdd: string | null): string {
  if (!aaaammdd || !/^\d{8}$/.test(aaaammdd)) return aaaammdd ?? "—";
  return `${aaaammdd.slice(6, 8)}/${aaaammdd.slice(4, 6)}/${aaaammdd.slice(0, 4)}`;
}

function estaClasificado(r: Registro606): boolean {
  return r.tipo_bienes_servicios != null && r.forma_pago != null;
}

export function Formato606RegistrosList() {
  const { periodo = "" } = useParams<{ periodo: string }>();
  const [registros, setRegistros] = useState<Registro606[]>([]);
  const [cargando, setCargando] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [mensaje, setMensaje] = useState<string | null>(null);
  const [validando, setValidando] = useState(false);
  const [generando, setGenerando] = useState(false);
  const [errores, setErrores] = useState<ErrorValidacion606[] | null>(null);

  const cargar = useCallback(() => {
    setCargando(true);
    formato606Api
      .listarRegistros(periodo)
      .then(setRegistros)
      .catch((err) => setError(err instanceof Formato606ApiError ? err.message : String(err)))
      .finally(() => setCargando(false));
  }, [periodo]);

  useEffect(() => {
    cargar();
  }, [cargar]);

  async function eliminar(id: string) {
    if (!confirm("¿Eliminar este registro manual?")) return;
    try {
      await formato606Api.eliminarRegistro(id);
      cargar();
    } catch (err) {
      setError(err instanceof Formato606ApiError ? err.message : String(err));
    }
  }

  async function validar() {
    setValidando(true);
    setError(null);
    setMensaje(null);
    try {
      const resultado = await formato606Api.validarPeriodo(periodo);
      setErrores(resultado.errores);
      if (resultado.valido) {
        setMensaje(`Periodo válido: ${resultado.total_registros} registro(s), sin errores.`);
      }
    } catch (err) {
      setError(err instanceof Formato606ApiError ? err.message : String(err));
    } finally {
      setValidando(false);
    }
  }

  async function generar() {
    setGenerando(true);
    setError(null);
    setMensaje(null);
    try {
      const { nombreArchivo, contenido } = await formato606Api.generarArchivo(periodo);
      const url = URL.createObjectURL(contenido);
      const a = document.createElement("a");
      a.href = url;
      a.download = nombreArchivo;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      setMensaje(`Archivo ${nombreArchivo} generado.`);
      setErrores([]);
    } catch (err) {
      setError(err instanceof Formato606ApiError ? err.message : String(err));
    } finally {
      setGenerando(false);
    }
  }

  if (cargando) return <p>Cargando…</p>;

  return (
    <div>
      <h2 style={{ fontSize: "1.05rem", marginBottom: "0.6rem" }}>Formato 606 · Periodo {periodo}</h2>

      <div className="acciones-secundarias">
        <Link className="btn-primario" to={`/formato-606/periodos/${periodo}/registros/nuevo`}>
          + Registro manual
        </Link>
        <button type="button" className="btn-secundario" onClick={validar} disabled={validando}>
          {validando ? "Validando…" : "Validar periodo"}
        </button>
        <button type="button" className="btn-secundario" onClick={generar} disabled={generando}>
          {generando ? "Generando…" : "Generar archivo TXT"}
        </button>
        <Link className="btn-secundario" to="/formato-606">
          Volver a periodos
        </Link>
      </div>

      {error && <p className="error">{error}</p>}
      {mensaje && <p className="mensaje">{mensaje}</p>}

      {errores && errores.length > 0 && (
        <div className="errores-validacion">
          <strong>{errores.length} error(es) de validación:</strong>
          <ul>
            {errores.map((e, i) => (
              <li key={`${e.registro_id}-${i}`}>
                NCF {e.ncf}: {e.mensaje}
              </li>
            ))}
          </ul>
        </div>
      )}

      {registros.length === 0 ? (
        <div className="lista-vacia">
          <p>No hay renglones para el periodo {periodo}.</p>
          <Link className="btn-primario" to={`/formato-606/periodos/${periodo}/registros/nuevo`}>
            Agregar el primero
          </Link>
        </div>
      ) : (
        <table className="lista-facturas">
          <thead>
            <tr>
              <th>NCF</th>
              <th>RNC/Cédula</th>
              <th>Origen</th>
              <th>Fecha</th>
              <th>Monto facturado</th>
              <th>ITBIS facturado</th>
              <th>Clasificación</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {registros.map((r) => (
              <tr key={r.id}>
                <td>{r.ncf}</td>
                <td>{r.rnc_cedula}</td>
                <td>{ORIGEN_LABEL[r.origen] ?? r.origen}</td>
                <td>{formatearFecha(r.fecha_comprobante)}</td>
                <td className="monto-item">{r.monto_facturado}</td>
                <td className="monto-item">{r.itbis_facturado}</td>
                <td>
                  {estaClasificado(r) ? (
                    <span className="estado estado-aceptado">Completo</span>
                  ) : (
                    <span className="estado estado-rechazado">Pendiente</span>
                  )}
                </td>
                <td>
                  <Link to={`/formato-606/registros/${r.id}/editar`}>Clasificar</Link>
                  {r.origen === "manual" && (
                    <>
                      {" · "}
                      <button
                        type="button"
                        onClick={() => eliminar(r.id)}
                        style={{
                          background: "none",
                          border: "none",
                          padding: 0,
                          font: "inherit",
                          fontSize: "0.86rem",
                          color: "var(--chip-bad-fg)",
                          cursor: "pointer",
                          textDecoration: "underline",
                        }}
                      >
                        Eliminar
                      </button>
                    </>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
