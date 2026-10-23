import { Route, Routes, useLocation } from "react-router-dom";
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
import { AppShell } from "./layout/AppShell";
import { ContactoForm } from "./pages/contactos/ContactoForm";
import { ContactoList } from "./pages/contactos/ContactoList";
import { CobrosPage } from "./pages/ingresos/Cobros";
import { CotizacionForm } from "./pages/ingresos/CotizacionForm";
import { CotizacionList } from "./pages/ingresos/CotizacionList";
import { NotasCreditoPage, NotasDebitoPage } from "./pages/ingresos/Notas";
import { Inicio } from "./pages/Inicio";
import { Proximamente } from "./pages/Proximamente";

/** /facturas/nueva?cotizacion=…, ?copiar=…, ?tipo=… comparten ruta: la clave
 * reinicia el formulario cuando solo cambia la consulta (p. ej. del menú "+"). */
function FacturaNueva() {
  const { search } = useLocation();
  return <FacturaForm key={search} />;
}

export default function App() {
  return (
    <AppShell>
      <Routes>
        <Route path="/" element={<Inicio />} />
        <Route path="/facturas" element={<FacturaList />} />
        <Route path="/facturas/nueva" element={<FacturaNueva />} />
        <Route path="/facturas/:id/editar" element={<FacturaForm />} />
        <Route path="/facturas/:id" element={<FacturaDetailPage />} />

        <Route path="/notas-credito" element={<NotasCreditoPage />} />
        <Route path="/notas-debito" element={<NotasDebitoPage />} />
        <Route path="/cotizaciones" element={<CotizacionList />} />
        <Route path="/cotizaciones/nueva" element={<CotizacionForm />} />
        <Route path="/cotizaciones/:id" element={<CotizacionForm />} />
        <Route path="/cobros" element={<CobrosPage />} />

        <Route path="/contactos" element={<ContactoList />} />
        <Route path="/contactos/nuevo" element={<ContactoForm />} />
        <Route path="/contactos/:id" element={<ContactoForm />} />

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

        <Route path="/modulo/:slug" element={<Proximamente />} />
        <Route path="*" element={<Proximamente />} />
      </Routes>
    </AppShell>
  );
}
