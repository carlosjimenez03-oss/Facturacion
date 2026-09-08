import { Link, Route, Routes } from "react-router-dom";
import { CompraForm } from "./components/CompraForm";
import { CompraList } from "./components/CompraList";
import { FacturaDetailPage } from "./components/FacturaDetailPage";
import { FacturaForm } from "./components/FacturaForm";
import { FacturaList } from "./components/FacturaList";
import { MovimientosList } from "./components/MovimientosList";
import { ProductoForm } from "./components/ProductoForm";
import { ProductoList } from "./components/ProductoList";
import { ProveedorForm } from "./components/ProveedorForm";
import { ProveedorList } from "./components/ProveedorList";

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
            <Link to="/inventario/productos">Productos</Link>
            <Link to="/inventario/compras">Compras</Link>
            <Link to="/inventario/movimientos">Movimientos</Link>
            <Link to="/inventario/proveedores">Proveedores</Link>
          </nav>
        </div>
      </header>
      <main>
        <Routes>
          <Route path="/" element={<FacturaList />} />
          <Route path="/facturas/nueva" element={<FacturaForm />} />
          <Route path="/facturas/:id/editar" element={<FacturaForm />} />
          <Route path="/facturas/:id" element={<FacturaDetailPage />} />

          <Route path="/inventario/productos" element={<ProductoList />} />
          <Route path="/inventario/productos/nuevo" element={<ProductoForm />} />
          <Route path="/inventario/productos/:id/editar" element={<ProductoForm />} />

          <Route path="/inventario/proveedores" element={<ProveedorList />} />
          <Route path="/inventario/proveedores/nuevo" element={<ProveedorForm />} />
          <Route path="/inventario/proveedores/:id/editar" element={<ProveedorForm />} />

          <Route path="/inventario/compras" element={<CompraList />} />
          <Route path="/inventario/compras/nueva" element={<CompraForm />} />
          <Route path="/inventario/compras/:id" element={<CompraForm />} />

          <Route path="/inventario/movimientos" element={<MovimientosList />} />
        </Routes>
      </main>
    </div>
  );
}
