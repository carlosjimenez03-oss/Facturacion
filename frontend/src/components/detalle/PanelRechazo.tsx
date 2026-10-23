import { Link } from "react-router-dom";
import type { FacturaDetalle } from "../../lib/types";
import { fechaHora } from "./formato";
import { comoCorregir, mensajesDgii, secuenciaUtilizada } from "./rechazo";

/** Panel rojo de una factura rechazada por la DGII o cuyo envío falló:
 * motivo con su código, cómo corregirlo y qué pasó con la secuencia. */
export function PanelRechazo({
  factura,
  proximoEncf,
  procesando,
  onReintentar,
}: {
  factura: FacturaDetalle;
  proximoEncf: string | null;
  procesando: boolean;
  onReintentar: () => void;
}) {
  const mensajes = mensajesDgii(factura);
  const rechazada = factura.estado === "rechazado";
  const informada = secuenciaUtilizada(factura);
  // La DGII da por utilizado el e-NCF de un comprobante rechazado; si el envío
  // ni siquiera llegó (sin trackId) la secuencia sigue disponible.
  const consumida = informada ?? (rechazada || Boolean(factura.track_id));
  const [fecha, hora] = fechaHora(factura.actualizado_en).split(" · ");
  const encf = factura.encf ?? "";

  return (
    <section className="card rech">
      <div className="rech-in">
        <span className="rech-ic">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" aria-hidden="true">
            <circle cx="12" cy="12" r="9" />
            <path d="M15 9l-6 6M9 9l6 6" />
          </svg>
        </span>
        <div className="rech-main">
          <div>
            <h2>
              {rechazada
                ? "La DGII rechazó este comprobante"
                : factura.track_id
                  ? "La DGII no pudo procesar este comprobante"
                  : "No se pudo enviar el comprobante a la DGII"}
            </h2>
            <p>
              {fecha ? `Última actualización el ${fecha} a las ${hora}` : "Sin fecha de respuesta"}
              {factura.dgii_estado ? ` · Estado: ${factura.dgii_estado}` : ""}
            </p>
          </div>
          {mensajes.length === 0 && (
            <div className="rech-msg">
              <span className="rech-fix">La DGII no devolvió un motivo. Consulte el estado con el Track ID en la Oficina Virtual o emita una nueva factura.</span>
            </div>
          )}
          {mensajes.map((m, i) => {
            const consejo = comoCorregir(m.valor);
            return (
              <div key={i} className="rech-msg">
                <div className="row" style={{ gap: 10, alignItems: "flex-start", flexWrap: "nowrap" }}>
                  {m.codigo && <span className="mono rech-cod nowrap">Código {m.codigo}</span>}
                  <span className="rech-txt">{m.valor}</span>
                </div>
                <span className="rech-fix">
                  <b>Cómo corregirlo: </b>
                  {consejo ?? "Revise el dato que señala la DGII, corrija el comprobante y vuelva a emitirlo."}
                </span>
              </div>
            );
          })}
          {encf && (
            <div className="row rech-sec">
              {consumida ? (
                <>
                  <span className="bd b-rech nodot">Secuencia utilizada</span>
                  <span>
                    La DGII registró <span className="mono">{encf}</span> como consumido: no puede reutilizarse.
                  </span>
                </>
              ) : (
                <>
                  <span className="bd b-acep nodot">Secuencia disponible</span>
                  <span>
                    La DGII no registró <span className="mono">{encf}</span>: puede reintentar el envío con la misma secuencia.
                  </span>
                </>
              )}
            </div>
          )}
        </div>
        <div className="rech-acc">
          <Link to={`/facturas/nueva?copiar=${factura.id}`} className="btn btn-p">
            Corregir y emitir nueva factura
          </Link>
          <span className="hint" style={{ textAlign: "center" }}>
            Se copiarán los datos y se asignará {proximoEncf ? <>el e-NCF <span className="mono">{proximoEncf}</span></> : "el siguiente e-NCF disponible"}
          </span>
          {consumida ? (
            <span
              className="tip"
              data-tip="Disponible solo cuando la DGII no registró la secuencia, por ejemplo por un error de comunicación. En este caso la secuencia ya se consumió."
              style={{ display: "flex" }}
            >
              <button type="button" className="btn btn-o" disabled style={{ width: "100%" }}>
                Reintentar con la misma secuencia
              </button>
            </span>
          ) : (
            <button type="button" className="btn btn-o" onClick={onReintentar} disabled={procesando}>
              {procesando ? "Reintentando…" : "Reintentar con la misma secuencia"}
            </button>
          )}
        </div>
      </div>
    </section>
  );
}
