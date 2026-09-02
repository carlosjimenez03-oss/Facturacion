import { Link, Route, Routes } from "react-router-dom";
import { FacturaDetailPage } from "./components/FacturaDetailPage";
import { FacturaForm } from "./components/FacturaForm";
import { FacturaList } from "./components/FacturaList";

export default function App() {
  return (
    <div className="app">
      <header className="app-header">
        <Link to="/" className="app-title">
          Facturación Electrónica RD
        </Link>
        <nav>
          <Link to="/">Facturas</Link>
          <Link to="/facturas/nueva">Nueva factura</Link>
        </nav>
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
