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
import { FormatosPage } from "./pages/impuestos/Formatos";
import { PreCalculoPage } from "./pages/impuestos/PreCalculo";
import { BandejaPage } from "./pages/compras/Bandeja";
import { ComprobantesE41Page, ComprobantesE43Page } from "./pages/compras/Comprobantes";
import { EcfRecibidoDetalle } from "./pages/compras/EcfRecibidoDetalle";
import { FacturaCompraForm } from "./pages/compras/FacturaCompraForm";
import { FacturasCompraPage } from "./pages/compras/FacturasCompra";
import { PagosProveedoresPage } from "./pages/compras/Pagos";
import { RetencionesPage } from "./pages/compras/Retenciones";
import { AjusteForm } from "./pages/inventario/AjusteForm";
import { AjustesPage } from "./pages/inventario/Ajustes";
import { AlmacenesPage } from "./pages/inventario/Almacenes";
import { KardexPage } from "./pages/inventario/Kardex";
import { LotesPage } from "./pages/inventario/Lotes";
import { Inicio } from "./pages/Inicio";
import { PlanesPage } from "./pages/planes/Planes";
import { PortalClientesPage } from "./pages/portal/PortalClientes";
import { PortalPublicoPage } from "./pages/portal/PortalPublico";
import { PosPage } from "./pages/pos/Pos";
import { VentasPage } from "./pages/ventas/Ventas";
import { Proximamente } from "./pages/Proximamente";

/** /facturas/nueva?cotizacion=…, ?copiar=…, ?tipo=… comparten ruta: la clave
 * reinicia el formulario cuando solo cambia la consulta (p. ej. del menú "+"). */
function FacturaNueva() {
  const { search } = useLocation();
  return <FacturaForm key={search} />;
}

export default function App() {
  const { pathname } = useLocation();
  // Portal de clientes público (/p/:token): sin menú ni sesión.
  if (pathname.startsWith("/p/")) {
    return (
      <Routes>
        <Route path="/p/:token" element={<PortalPublicoPage />} />
      </Routes>
    );
  }
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

        <Route path="/bandeja" element={<BandejaPage />} />
        <Route path="/bandeja/:id" element={<EcfRecibidoDetalle />} />
        <Route path="/compras/facturas" element={<FacturasCompraPage />} />
        <Route path="/compras/facturas/nueva" element={<FacturaCompraForm />} />
        <Route path="/compras/facturas/:id" element={<FacturaCompraForm />} />
        <Route path="/compras/e41" element={<ComprobantesE41Page />} />
        <Route path="/compras/e43" element={<ComprobantesE43Page />} />
        <Route path="/compras/pagos" element={<PagosProveedoresPage />} />
        <Route path="/compras/retenciones" element={<RetencionesPage />} />

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
        <Route path="/inventario/kardex" element={<KardexPage />} />
        <Route path="/inventario/almacenes" element={<AlmacenesPage />} />
        <Route path="/inventario/lotes" element={<LotesPage />} />
        <Route path="/inventario/ajustes" element={<AjustesPage />} />
        <Route path="/inventario/ajustes/nuevo" element={<AjusteForm />} />
        <Route path="/inventario/ajustes/:id" element={<AjusteForm />} />

        <Route path="/impuestos/formatos/:formato" element={<FormatosPage />} />
        <Route path="/impuestos/precalculo/:formulario" element={<PreCalculoPage />} />

        <Route path="/formato-606" element={<Formato606Periodos />} />
        <Route path="/formato-606/periodos/:periodo" element={<Formato606RegistrosList />} />
        <Route
          path="/formato-606/periodos/:periodo/registros/nuevo"
          element={<Formato606RegistroForm />}
        />
        <Route path="/formato-606/registros/:id/editar" element={<Formato606RegistroForm />} />

        <Route path="/pos" element={<PosPage />} />
        <Route path="/portal-clientes" element={<PortalClientesPage />} />
        <Route path="/planes" element={<PlanesPage />} />
        <Route path="/ventas" element={<VentasPage />} />

        <Route path="/modulo/:slug" element={<Proximamente />} />
        <Route path="*" element={<Proximamente />} />
      </Routes>
    </AppShell>
  );
}
