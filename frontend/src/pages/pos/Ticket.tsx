// Ticket de 80 mm de una venta del POS (se imprime con window.print; ver
// pos.css @media print). El QR y el código de seguridad salen del XML
// firmado de la factura (erp-api /pos/facturas/{id}/timbre), igual que en
// la representación impresa; si aún no existe, el ticket lo dice.
import { Fragment } from "react";
import { desglose } from "../../lib/buildDatos";
import { formatoMonto, formatoRnc } from "../../lib/marca";
import type { DatosEmisor } from "../../lib/preferencias";
import type { PagoPos, Timbre } from "../../lib/solucionesApi";
import type { ItemFactura } from "../../lib/types";

const METODO: Record<string, string> = { efectivo: "Efectivo", tarjeta: "Tarjeta", transferencia: "Transferencia" };

export interface DatosTicket {
  facturaId: string;
  tipoEcf: "31" | "32";
  encf: string;
  fecha: Date;
  emisor: DatosEmisor;
  cliente: { rnc: string; nombre: string };
  items: ItemFactura[];
  pagos: PagoPos[];
  recibido: number;
  cambio: number;
  estadoDgii: string;
  caja: string;
  cajero: string | null;
}

export function Ticket({ t, timbre }: { t: DatosTicket; timbre: Timbre | null }) {
  const tot = desglose(t.items);
  const itbis = tot.i18 + tot.i16;
  return (
    <div className="ticket" data-ticket>
      <div className="tk-c">
        <b className="tk-emp">{t.emisor.razonSocial}</b>
        <span>RNC {formatoRnc(t.emisor.rnc)}</span>
        {t.emisor.direccion && <span>{t.emisor.direccion}</span>}
      </div>
      <div className="tk-sep" />
      <div className="tk-c">
        <b>{t.tipoEcf === "31" ? "Factura de Crédito Fiscal Electrónica" : "Factura de Consumo Electrónica"}</b>
        <span className="tk-mono">e-NCF: {t.encf}</span>
        <span>
          {t.fecha.toLocaleDateString("es-DO")} {t.fecha.toLocaleTimeString("es-DO", { hour: "2-digit", minute: "2-digit" })} · {t.caja}
          {t.cajero ? ` · ${t.cajero}` : ""}
        </span>
      </div>
      {t.cliente.rnc && (
        <div className="tk-l">
          <span>Cliente: {t.cliente.nombre}</span>
          <span>RNC/Cédula: {formatoRnc(t.cliente.rnc)}</span>
        </div>
      )}
      <div className="tk-sep" />
      <table className="tk-items">
        <tbody>
          {t.items.map((i) => (
            <tr key={i.NumeroLinea}>
              <td>
                {i.NombreItem}
                <br />
                <span className="tk-mut">
                  {Number(i.CantidadItem)} × {formatoMonto(i.PrecioUnitarioItem)}
                  {i.IndicadorFacturacion === "4" ? " E" : ""}
                </span>
              </td>
              <td className="tk-num">{formatoMonto(Number(i.CantidadItem) * Number(i.PrecioUnitarioItem))}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="tk-sep" />
      <div className="tk-tot">
        <span>Subtotal</span>
        <span>{formatoMonto(tot.subtotal - tot.descuentos)}</span>
        <span>ITBIS</span>
        <span>{formatoMonto(itbis)}</span>
        <b>TOTAL</b>
        <b>RD$ {formatoMonto(tot.total)}</b>
        {t.pagos.map((p, idx) => (
          <Fragment key={idx}>
            <span>{METODO[p.metodo]}</span>
            <span>{formatoMonto(p.monto)}</span>
          </Fragment>
        ))}
        {t.cambio > 0 && (
          <>
            <span>Cambio</span>
            <span>{formatoMonto(t.cambio)}</span>
          </>
        )}
      </div>
      <div className="tk-sep" />
      <div className="tk-c">
        {timbre?.qr_svg ? (
          <>
            <span className="tk-qr" dangerouslySetInnerHTML={{ __html: timbre.qr_svg }} />
            <span>Código de seguridad: {timbre.codigo_seguridad}</span>
            <span>Fecha de firma: {timbre.fecha_firma}</span>
          </>
        ) : (
          <span className="tk-mut">Código de seguridad y QR: consúltelos en la representación impresa del e-CF.</span>
        )}
        <span>Estado DGII: {t.estadoDgii}</span>
        <span className="tk-mut">¡Gracias por su compra!</span>
      </div>
    </div>
  );
}
