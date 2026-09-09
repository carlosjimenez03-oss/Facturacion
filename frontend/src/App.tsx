import { Link, Route, Routes } from "react-router-dom";
import { CompraForm } from "./components/CompraForm";
import { CompraList } from "./components/CompraList";
import { FacturaDetailPage } from "./components/FacturaDetailPage";
import { FacturaForm } from "./components/FacturaForm";
import { FacturaList } from "./components/FacturaList";
import { Formato606Periodos } from "./components/Formato606Periodos";
import { Formato606RegistroForm } from "./components/Formato606RegistroForm";
import { Formato606RegistrosList } from "./components/Formato606RegistrosList";
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
            <Link to="/formato-606">Formato 606</Link>
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

          <Route path="/formato-606" element={<Formato606Periodos />} />
          <Route path="/formato-606/periodos/:periodo" element={<Formato606RegistrosList />} />
          <Route
            path="/formato-606/periodos/:periodo/registros/nuevo"
            element={<Formato606RegistroForm />}
          />
          <Route path="/formato-606/registros/:id/editar" element={<Formato606RegistroForm />} />
        </Routes>
      </main>
    </div>
  );
}
