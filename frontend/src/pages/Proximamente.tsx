import { Link, useParams } from "react-router-dom";
import { MODULOS_PENDIENTES } from "../layout/menu";

/** Página para los módulos del diseño que todavía no tienen servicio en el backend. */
export function Proximamente() {
  const { slug = "" } = useParams();
  const modulo = MODULOS_PENDIENTES[slug] ?? { titulo: "Módulo", descripcion: "Esta sección está en desarrollo." };
  return (
    <div className="content">
      <div className="ph">
        <div>
          <h1>{modulo.titulo}</h1>
          <p>{modulo.descripcion}</p>
        </div>
      </div>
      <section className="card">
        <div className="empty">
          <span className="ei">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.7} strokeLinecap="round" aria-hidden="true">
              <path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M5.6 18.4l2.1-2.1M16.3 7.7l2.1-2.1" />
            </svg>
          </span>
          <h3>En construcción</h3>
          <p>
            El diseño de esta pantalla está listo (ver <code>docs/diseno/prototipo-kontara</code>), pero aún no tiene servicio en el
            backend. Se habilitará cuando exista su API.
          </p>
          <div className="row" style={{ justifyContent: "center" }}>
            <Link to="/" className="btn btn-o btn-sm">
              Volver al inicio
            </Link>
            <Link to="/facturas/nueva" className="btn btn-p btn-sm">
              Nueva factura
            </Link>
          </div>
        </div>
      </section>
    </div>
  );
}
