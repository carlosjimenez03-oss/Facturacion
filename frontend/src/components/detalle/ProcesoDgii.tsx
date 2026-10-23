import { ESTADO_BADGE, ESTADO_LABEL } from "../../lib/estados";
import type { FacturaDetalle } from "../../lib/types";
import { fechaHora } from "./formato";
import { mensajesDgii } from "./rechazo";

type Clase = "ok" | "mal" | "esp" | "pend";

interface Paso {
  titulo: string;
  hora?: string;
  detalle?: string;
  clase: Clase;
}

/** Pasos del envío a partir de lo que la factura tiene guardado. Solo se
 * muestran horas que realmente existen (creación y última actualización). */
function pasos(f: FacturaDetalle): Paso[] {
  const enviada = Boolean(f.track_id);
  const fallo = f.estado === "error" || f.estado === "rechazado";
  const lista: Paso[] = [
    { titulo: "Creada", hora: fechaHora(f.creado_en), detalle: "Comprobante guardado en Kontara", clase: "ok" },
    f.xml_generado
      ? { titulo: "XML generado", detalle: `Validado contra el XSD oficial del e-CF ${f.tipo_ecf}`, clase: "ok" }
      : { titulo: "XML generado", detalle: "Se genera y valida al emitir", clase: "pend" },
    enviada
      ? { titulo: "Firmada", detalle: "Firma digital XAdES con el certificado del emisor", clase: "ok" }
      : f.xml_firmado
        ? { titulo: "Firmada (vista previa)", detalle: "La firma definitiva se aplica al enviar", clase: "ok" }
        : { titulo: "Firmada", detalle: "Se firma digitalmente al enviar", clase: "pend" },
  ];

  if (enviada) {
    lista.push({ titulo: "Enviada a la DGII", detalle: `Track ID ${f.track_id}`, clase: "ok" });
  } else if (f.estado === "error") {
    const motivo = mensajesDgii(f)[0]?.valor;
    lista.push({ titulo: "Envío fallido", hora: fechaHora(f.actualizado_en), detalle: motivo, clase: "mal" });
    return lista;
  } else {
    lista.push({ titulo: "Enviada a la DGII", detalle: "Pendiente de emitir", clase: "pend" });
  }

  const codigo = f.dgii_codigo ? `Código ${f.dgii_codigo}` : null;
  if (f.estado === "aceptado") {
    lista.push({
      titulo: f.dgii_estado && /condicional/i.test(f.dgii_estado) ? "Aceptada condicional" : "Aceptada",
      hora: fechaHora(f.actualizado_en),
      detalle: [codigo, f.dgii_estado].filter(Boolean).join(" · ") || "Comprobante válido ante la DGII",
      clase: "ok",
    });
  } else if (fallo) {
    const m = mensajesDgii(f)[0];
    lista.push({
      titulo: f.estado === "rechazado" ? "Rechazada" : "No procesada",
      hora: fechaHora(f.actualizado_en),
      detalle: [m?.codigo ? `Código ${m.codigo}` : codigo, m?.valor ?? f.dgii_estado].filter(Boolean).join(" · "),
      clase: "mal",
    });
  } else if (enviada) {
    lista.push({
      titulo: "En proceso en la DGII",
      detalle: "La DGII está validando la estructura, la firma y los datos del comprador. El estado se actualiza solo cada pocos minutos.",
      clase: "esp",
    });
  } else {
    lista.push({ titulo: "Respuesta de la DGII", clase: "pend" });
  }
  return lista;
}

export function ProcesoDgii({ factura }: { factura: FacturaDetalle }) {
  const lista = pasos(factura);
  return (
    <section className="card">
      <div className="card-h">
        <h2>Proceso DGII</h2>
        <span className={ESTADO_BADGE[factura.estado] ?? "bd b-borr"}>{ESTADO_LABEL[factura.estado] ?? factura.estado}</span>
      </div>
      <ol className="proc">
        {lista.map((p, i) => {
          const ultimo = i === lista.length - 1;
          return (
            <li key={p.titulo}>
              <div className="proc-eje">
                <span className={`proc-pt ${p.clase}`} aria-hidden="true">
                  {p.clase === "ok" && (
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={3} strokeLinecap="round" strokeLinejoin="round">
                      <path d="M20 6 9 17l-5-5" />
                    </svg>
                  )}
                  {p.clase === "mal" && (
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={3} strokeLinecap="round">
                      <path d="M18 6 6 18M6 6l12 12" />
                    </svg>
                  )}
                </span>
                {!ultimo && <span className={lista[i + 1].clase === "pend" ? "proc-ln pend" : "proc-ln"} />}
              </div>
              <div className={p.clase === "pend" ? "proc-tx pend" : "proc-tx"}>
                <b>{p.titulo}</b>
                {p.hora && <div className="hint">{p.hora}</div>}
                {p.detalle && <div className="proc-det">{p.detalle}</div>}
              </div>
            </li>
          );
        })}
      </ol>
    </section>
  );
}
