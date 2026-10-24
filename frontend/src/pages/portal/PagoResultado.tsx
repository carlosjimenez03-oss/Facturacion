// Resultado de un pago con tarjeta en línea (/p/pago/:referencia): a esta
// página redirige erp-api cuando el banco devuelve al cliente, ya validada la
// respuesta y registrado el cobro. Sin sesión; solo muestra el estado del
// intento (la referencia es aleatoria y no da acceso a nada más).
import { useEffect, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { formatoMonto, MARCA } from "../../lib/marca";
import { pagosApi, portalDeVuelta } from "../../lib/pagosApi";
import type { PagoPublico } from "../../lib/pagosApi";
import { ErpApiError } from "../../lib/solucionesApi";
import { mensajeError } from "../ingresos/ingresosUtil";
import "./portal.css";

function textos(p: PagoPublico, aviso: string | null): { titulo: string; texto: string; badge: string; etiqueta: string } {
  if (p.estado === "aprobado") {
    return p.requiere_revision || !p.cobro_registrado
      ? {
          titulo: "Pago aprobado",
          texto: `El banco aprobó su pago. ${p.empresa} lo revisará y le confirmará cómo se aplicó a su factura.`,
          badge: "bd b-proc",
          etiqueta: "En revisión",
        }
      : {
          titulo: "¡Pago aprobado!",
          texto: `Gracias. Su pago quedó registrado en la factura y ${p.empresa} ya lo ve en su cuenta.`,
          badge: "bd b-acep",
          etiqueta: "Aprobado",
        };
  }
  if (p.estado === "declinado") {
    return {
      titulo: "El pago fue declinado",
      texto: `El banco no aprobó el pago${p.mensaje ? ` (${p.mensaje})` : ""}. No se le cobró nada: puede intentar con otra tarjeta.`,
      badge: "bd b-rech",
      etiqueta: "Declinado",
    };
  }
  if (p.estado === "cancelado") {
    return { titulo: "Pago cancelado", texto: "Canceló el pago en la página del banco. No se le cobró nada.", badge: "bd b-borr", etiqueta: "Cancelado" };
  }
  if (p.estado === "expirado") {
    return { titulo: "El intento de pago venció", texto: "Vuelva a su portal y pulse «Pagar con tarjeta» de nuevo.", badge: "bd b-borr", etiqueta: "Expirado" };
  }
  if (aviso) {
    return {
      titulo: "No pudimos confirmar el pago",
      texto: `No pudimos validar la respuesta del banco. Si ve el cargo en su tarjeta, ${p.empresa} lo verificará con el banco; no lo pague dos veces.`,
      badge: "bd b-rech",
      etiqueta: "Sin confirmar",
    };
  }
  return { titulo: "Pago en proceso", texto: "Todavía no tenemos la respuesta del banco.", badge: "bd b-proc", etiqueta: "Pendiente" };
}

export function PagoResultadoPage() {
  const { referencia = "" } = useParams();
  const [params] = useSearchParams();
  const [pago, setPago] = useState<PagoPublico | null>(null);
  const [error, setError] = useState<string | null>(null);
  const aviso = params.get("aviso");
  const portal = portalDeVuelta(referencia);

  useEffect(() => {
    document.title = `Pago con tarjeta · ${MARCA}`;
    pagosApi.estado(referencia).then(setPago, (err) =>
      setError(err instanceof ErpApiError && err.status === 404 ? "No encontramos este pago." : mensajeError(err)),
    );
  }, [referencia]);

  const t = pago ? textos(pago, aviso) : null;
  return (
    <div className="pp">
      <div className="pp-in" style={{ maxWidth: 560 }}>
        <section className="card">
          {error && (
            <div className="empty">
              <h3>No se pudo mostrar el pago</h3>
              <p>{error}</p>
            </div>
          )}
          {!error && !pago && <p className="pad mut">Cargando…</p>}
          {pago && t && (
            <div className="card-b" style={{ display: "flex", flexDirection: "column", gap: 14 }} data-estado-pago={pago.estado}>
              <span className="pp-marca">{pago.empresa}</span>
              <div className="row" style={{ justifyContent: "space-between" }}>
                <h1 style={{ margin: 0, fontSize: 22 }}>{t.titulo}</h1>
                <span className={t.badge}>{t.etiqueta}</span>
              </div>
              <p style={{ margin: 0, color: "#4B5563" }}>{t.texto}</p>
              <dl className="kv">
                <dt>Factura</dt>
                <dd className="mono">{pago.encf ?? "—"}</dd>
                <dt>Monto</dt>
                <dd>RD$ {formatoMonto(pago.monto)}</dd>
                {pago.codigo_autorizacion && (
                  <>
                    <dt>Autorización</dt>
                    <dd className="mono">{pago.codigo_autorizacion}</dd>
                  </>
                )}
                <dt>Procesado por</dt>
                <dd>{pago.proveedor}</dd>
              </dl>
              {pago.pruebas && <span className="bd b-proc nodot">Ambiente de pruebas: no se cobró dinero real</span>}
              {portal && (
                <Link className="btn btn-p" to={`/p/${portal}`} style={{ alignSelf: "flex-start" }}>
                  Volver a mis facturas
                </Link>
              )}
            </div>
          )}
        </section>
        <p className="pp-pie">{MARCA} no recibe ni guarda los datos de su tarjeta.</p>
      </div>
    </div>
  );
}
