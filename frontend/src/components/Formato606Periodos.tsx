import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Formato606ApiError, formato606Api } from "../lib/formato606Api";
import type { PeriodoResumen606 } from "../lib/types";

function formatearPeriodo(periodo: string): string {
  if (!/^\d{6}$/.test(periodo)) return periodo;
  const meses = [
    "Enero",
    "Febrero",
    "Marzo",
    "Abril",
    "Mayo",
    "Junio",
    "Julio",
    "Agosto",
    "Septiembre",
    "Octubre",
    "Noviembre",
    "Diciembre",
  ];
  const mes = Number(periodo.slice(4, 6));
  return `${meses[mes - 1] ?? periodo.slice(4, 6)} ${periodo.slice(0, 4)}`;
}

export function Formato606Periodos() {
  const navigate = useNavigate();
  const [periodos, setPeriodos] = useState<PeriodoResumen606[]>([]);
  const [cargando, setCargando] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [periodoBuscado, setPeriodoBuscado] = useState("");

  useEffect(() => {
    formato606Api
      .listarPeriodos()
      .then(setPeriodos)
      .catch((err) => setError(err instanceof Formato606ApiError ? err.message : String(err)))
      .finally(() => setCargando(false));
  }, []);

  function irAPeriodo(e: React.FormEvent) {
    e.preventDefault();
    if (/^\d{6}$/.test(periodoBuscado)) {
      navigate(`/formato-606/periodos/${periodoBuscado}`);
    }
  }

  if (cargando) return <p>Cargando…</p>;
  if (error) return <p className="error">{error}</p>;

  return (
    <div>
      <div className="acciones-secundarias">
        <form onSubmit={irAPeriodo} style={{ display: "flex", gap: "0.5rem" }}>
          <input
            placeholder="AAAAMM, ej. 202601"
            pattern="\d{6}"
            value={periodoBuscado}
            onChange={(e) => setPeriodoBuscado(e.target.value)}
          />
          <button type="submit" className="btn-primario">
            Ir al periodo
          </button>
        </form>
      </div>

      {periodos.length === 0 ? (
        <div className="lista-vacia">
          <p>Todavía no hay renglones de compras registrados en ningún periodo.</p>
          <p className="nota-acciones">
            Los renglones se auto-completan al recibir e-CF de proveedores y al emitir comprobantes de
            compras/gastos menores/pagos al exterior. También puedes ir directo a un periodo (arriba) y
            agregar registros manuales.
          </p>
        </div>
      ) : (
        <table className="lista-facturas">
          <thead>
            <tr>
              <th>Periodo</th>
              <th>Registros</th>
              <th>Pendientes de clasificar</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {periodos.map((p) => (
              <tr key={p.periodo}>
                <td>{formatearPeriodo(p.periodo)}</td>
                <td>{p.total}</td>
                <td>
                  {p.pendientes > 0 ? (
                    <span className="estado estado-rechazado">{p.pendientes} pendiente(s)</span>
                  ) : (
                    <span className="estado estado-aceptado">Completo</span>
                  )}
                </td>
                <td>
                  <Link to={`/formato-606/periodos/${p.periodo}`}>Ver renglones</Link>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
