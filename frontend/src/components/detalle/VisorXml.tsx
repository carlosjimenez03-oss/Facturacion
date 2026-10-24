import { useState } from "react";
import type { FacturaDetalle } from "../../lib/types";
import { descargarArchivo } from "./formato";

/** XML generado (validado contra el XSD) y vista previa firmada, con descarga. */
export function VisorXml({ factura }: { factura: FacturaDetalle }) {
  const opciones = [
    factura.xml_firmado ? { id: "firmado" as const, label: "XML firmado", xml: factura.xml_firmado } : null,
    factura.xml_generado ? { id: "generado" as const, label: "XML sin firmar", xml: factura.xml_generado } : null,
  ].filter((o): o is NonNullable<typeof o> => o !== null);
  const [vista, setVista] = useState<"firmado" | "generado">(opciones[0]?.id ?? "generado");

  if (opciones.length === 0) return null;
  const actual = opciones.find((o) => o.id === vista) ?? opciones[0];
  const base = factura.encf || factura.id;

  return (
    <section className="card">
      <div className="card-h">
        <h2>XML del comprobante</h2>
        <div className="row">
          {opciones.length > 1 && (
            <div className="seg" role="group" aria-label="Versión del XML">
              {opciones.map((o) => (
                <button key={o.id} type="button" className={o.id === actual.id ? "on" : ""} onClick={() => setVista(o.id)} aria-pressed={o.id === actual.id}>
                  {o.label}
                </button>
              ))}
            </div>
          )}
          <button
            type="button"
            className="btn btn-o btn-sm"
            onClick={() => descargarArchivo(actual.id === "firmado" ? `${base}.xml` : `${base}-sin-firmar.xml`, actual.xml)}
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M12 3v12M7 10l5 5 5-5M5 21h14" />
            </svg>
            Descargar XML
          </button>
        </div>
      </div>
      {actual.id === "firmado" && (
        <p className="hint" style={{ margin: 0, padding: "10px 20px 0" }}>
          {factura.estado === "borrador" || factura.estado === "generado" || factura.estado === "firmado"
            ? "Firmado con el certificado del emisor. Es el XML que se envía a la DGII (y el del código de seguridad de la representación impresa), salvo que regenere el XML."
            : "Firmado con el certificado del emisor: es, byte a byte, el XML que recibe la DGII y el del código de seguridad de la representación impresa."}
        </p>
      )}
      <pre className="xml-pre mono" tabIndex={0} aria-label={actual.label}>
        {actual.xml}
      </pre>
    </section>
  );
}
