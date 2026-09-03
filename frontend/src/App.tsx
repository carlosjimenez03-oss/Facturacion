import { Link, Route, Routes } from "react-router-dom";
import { FacturaDetailPage } from "./components/FacturaDetailPage";
import { FacturaForm } from "./components/FacturaForm";
import { FacturaList } from "./components/FacturaList";

export default function App() {
  return (
    <div className="app">
      <header className="app-header">
        <div className="app-bar">
          <Link to="/" className="brand">
            <span className="brand-mark">e·CF</span>
            <span className="brand-text">
              <b className="app-title">Facturación Electrónica RD</b>
              <span>Emisor electrónico certificado · DGII</span>
            </span>
          </Link>
          <nav className="tabs">
            <Link to="/facturas/nueva">Nueva factura</Link>
            <Link to="/">Historial</Link>
          </nav>
        </div>
      </header>
      <main>
        <Routes>
          <Route path="/" element={<FacturaList />} />
          <Route path="/facturas/nueva" element={<FacturaForm />} />
          <Route path="/facturas/:id" element={<FacturaDetailPage />} />
        </Routes>
      </main>
    </div>
  );
}
