import { useEffect, useState } from "react";
import { erpApi } from "../../lib/erpApi";
import type { Cliente, CotizacionResumen } from "../../lib/erpApi";
import { formatoMonto } from "../../lib/marca";
import { useDatosEmpresa } from "../../lib/configuracionApi";
import { Modal } from "./IngresosShell";
import { enlaceCorreo, enlaceWhatsApp, fechaLegible } from "./ingresosUtil";

/** Prepara el mensaje de la cotización para WhatsApp o correo. Kontara no lo envía:
 * abre la aplicación del usuario con el texto listo para que él lo mande. */
export function EnviarCotizacion({ cotizacion, onCerrar }: { cotizacion: CotizacionResumen; onCerrar: () => void }) {
  const miEmpresa = useDatosEmpresa();
  const [cliente, setCliente] = useState<Cliente | null>(null);
  const [copiado, setCopiado] = useState(false);

  useEffect(() => {
    if (!cotizacion.cliente_id) return;
    erpApi
      .obtenerCliente(cotizacion.cliente_id)
      .then(setCliente)
      .catch(() => setCliente(null));
  }, [cotizacion.cliente_id]);

  const [texto, setTexto] = useState(
    () =>
      `Saludos, ${cotizacion.cliente_nombre}.\n\n` +
      `Le compartimos la cotización ${cotizacion.numero} por un total de RD$ ${formatoMonto(cotizacion.total)} (ITBIS incluido), ` +
      `válida hasta el ${fechaLegible(cotizacion.vence)}.\n\n` +
      `Quedamos a su disposición para cualquier consulta.\n${miEmpresa.nombre}`,
  );
  const asunto = `Cotización ${cotizacion.numero} · ${miEmpresa.nombre}`;

  async function copiar() {
    try {
      await navigator.clipboard.writeText(texto);
      setCopiado(true);
    } catch {
      setCopiado(false);
    }
  }

  return (
    <Modal
      titulo={`Enviar ${cotizacion.numero}`}
      onCerrar={onCerrar}
      ancho={560}
      pie={
        <>
          <button type="button" className="btn btn-g" onClick={copiar}>
            {copiado ? "Copiado" : "Copiar texto"}
          </button>
          <a className="btn btn-o" href={enlaceCorreo(asunto, texto, cliente?.correo)} onClick={onCerrar}>
            Abrir correo
          </a>
          <a className="btn btn-p" href={enlaceWhatsApp(texto, cliente?.telefono)} target="_blank" rel="noreferrer" onClick={onCerrar}>
            Abrir WhatsApp
          </a>
        </>
      }
    >
      <span>
        Se abrirá WhatsApp o su programa de correo con este mensaje listo; usted elige el contacto y lo envía.
        {cliente?.correo && (
          <>
            {" "}
            Correo del cliente: <b style={{ color: "#111827" }}>{cliente.correo}</b>.
          </>
        )}
      </span>
      <textarea className="inp" rows={7} value={texto} onChange={(e) => setTexto(e.target.value)} aria-label="Mensaje" />
      <span className="hint">Para adjuntar el documento, use «Imprimir» en la cotización y guárdela como PDF.</span>
    </Modal>
  );
}
