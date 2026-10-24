// Nómina › Suscripciones (vista "suscripciones" de Nomina.dc.html): por ahora solo informa del plan de
// prueba y lleva a /planes, donde se elige y paga el plan.
import { Link } from "react-router-dom";
import { Encabezado, NominaShell } from "./NominaShell";

export function SuscripcionesNominaPage() {
  return (
    <NominaShell>
      <Encabezado titulo="Suscripciones" sub="Tu plan de Nómina" />
      <section className="card card-b" style={{ display: "flex", flexWrap: "wrap", gap: 20, alignItems: "center" }}>
        <div style={{ flex: "1 1 300px" }}>
          <span className="bd b-proc">Prueba gratuita</span>
          <h2 style={{ margin: "10px 0 4px", fontSize: 20, color: "#111827" }}>Nómina Pyme · hasta 25 empleados</h2>
          <p className="mut" style={{ margin: 0 }}>Te quedan 15 días de prueba. Elige un plan para seguir calculando tu nómina.</p>
          <div className="pb" style={{ marginTop: 12, maxWidth: 360 }}>
            <span style={{ width: "50%" }} />
          </div>
        </div>
        <div className="row">
          <Link to="/planes" className="btn btn-p">Elegir plan</Link>
        </div>
      </section>
      <section className="card">
        <div className="card-h">
          <h2>Historial de facturación</h2>
        </div>
        <div className="empty">
          <h3>Sin facturas todavía</h3>
          <p>Recibirás un e-CF tipo 31 por cada pago de la suscripción.</p>
        </div>
      </section>
    </NominaShell>
  );
}
