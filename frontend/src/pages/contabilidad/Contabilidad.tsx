// Contabilidad (Contabilidad.dc.html del prototipo): pestañas Resumen, Catálogo de cuentas, Asientos,
// Activos fijos y Reportes en /contabilidad/:tab. El formulario de asiento manual está en AsientoForm.
import type { ReactNode } from "react";
import { Link, Navigate, useParams } from "react-router-dom";
import { useAviso } from "../ingresos/useAviso";
import "../ingresos/ingresos.css";
import "./contabilidad.css";
import { ActivosTab } from "./ActivosFijos";
import { AsientosTab } from "./Asientos";
import { CatalogoTab } from "./Catalogo";
import { ReportesTab } from "./Reportes";
import { ResumenTab } from "./Resumen";

type Pestana = "resumen" | "catalogo" | "asientos" | "activos" | "reportes";

const PESTANAS: { id: Pestana; label: string }[] = [
  { id: "resumen", label: "Resumen" },
  { id: "catalogo", label: "Catálogo de cuentas" },
  { id: "asientos", label: "Asientos" },
  { id: "activos", label: "Activos fijos" },
  { id: "reportes", label: "Reportes" },
];

export type MostrarAviso = (texto: string, opciones?: { detalle?: string; error?: boolean }) => void;

export function ContabilidadEncabezado({ titulo, acciones, pestana }: { titulo?: string; acciones?: ReactNode; pestana?: Pestana }) {
  return (
    <>
      <div className="ph no-imprimir">
        <div>
          <div className="crumb">
            <span>Contabilidad</span>
            {titulo && (
              <>
                <span>/</span>
                <span>{titulo}</span>
              </>
            )}
          </div>
          <h1>Contabilidad</h1>
          <p>Catálogo de cuentas, asientos contables y activos fijos</p>
        </div>
        {acciones && <div className="row">{acciones}</div>}
      </div>
      <nav className="tabs no-imprimir" aria-label="Contabilidad">
        {PESTANAS.map((p) => (
          <Link key={p.id} to={`/contabilidad/${p.id}`} className={p.id === pestana ? "tab on" : "tab"} aria-current={p.id === pestana ? "page" : undefined}>
            {p.label}
          </Link>
        ))}
      </nav>
    </>
  );
}

export function ContabilidadPage() {
  const { tab } = useParams<{ tab: string }>();
  const pestana = PESTANAS.find((p) => p.id === tab);
  const [toast, mostrarAviso] = useAviso();
  if (!pestana) return <Navigate to="/contabilidad/resumen" replace />;

  return (
    <div className="content">
      <ContabilidadEncabezado titulo={pestana.label} pestana={pestana.id} />
      {pestana.id === "resumen" && <ResumenTab />}
      {pestana.id === "catalogo" && <CatalogoTab mostrarAviso={mostrarAviso} />}
      {pestana.id === "asientos" && <AsientosTab mostrarAviso={mostrarAviso} />}
      {pestana.id === "activos" && <ActivosTab mostrarAviso={mostrarAviso} />}
      {pestana.id === "reportes" && <ReportesTab mostrarAviso={mostrarAviso} />}
      {toast}
    </div>
  );
}
