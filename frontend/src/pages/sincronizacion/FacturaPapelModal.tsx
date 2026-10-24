// Registro de una factura emitida en papel durante una caída total (modal de Sincronizacion.dc.html del prototipo).
import { useState } from "react";
import { sincronizacionApi } from "../../lib/operacionApi";
import type { FacturaPapel } from "../../lib/operacionApi";
import { formatoMonto } from "../../lib/marca";
import { AvisoError, Modal } from "../ingresos/IngresosShell";
import { centavos, hoyIso, mensajeError } from "../ingresos/ingresosUtil";
import { FORMAS_PAGO_607 } from "./sincronizacionUtil";

const numero = (v: string) => Number(v.replace(/,/g, "")) || 0;

export function FacturaPapelModal({
  factura,
  onCerrar,
  onGuardada,
  onEliminada,
}: {
  factura: FacturaPapel | null;
  onCerrar: () => void;
  onGuardada: (f: FacturaPapel, nueva: boolean) => void;
  onEliminada: (f: FacturaPapel) => void;
}) {
  const [ncf, setNcf] = useState(factura?.ncf ?? "");
  const [fecha, setFecha] = useState(factura?.fecha ?? hoyIso());
  const [rnc, setRnc] = useState(factura?.cliente_rnc ?? "");
  const [nombre, setNombre] = useState(factura?.cliente_nombre ?? "");
  const [g18, setG18] = useState(factura ? factura.gravado_18 : "");
  const [i18, setI18] = useState(factura ? factura.itbis_18 : "");
  const [g16, setG16] = useState(factura && Number(factura.gravado_16) ? factura.gravado_16 : "");
  const [exento, setExento] = useState(factura && Number(factura.exento) ? factura.exento : "");
  const [forma, setForma] = useState(factura?.forma_pago ?? 1);
  const [reemplazo, setReemplazo] = useState(factura?.encf_reemplazo ?? "");
  const [notas, setNotas] = useState(factura?.notas ?? "");
  const [error, setError] = useState<string | null>(null);
  const [guardando, setGuardando] = useState(false);

  const itbis18 = i18 === "" ? centavos(numero(g18) * 0.18) : numero(i18);
  const itbis16 = centavos(numero(g16) * 0.16);
  const total = centavos(numero(g18) + numero(g16) + numero(exento) + itbis18 + itbis16);
  const esCreditoFiscal = /^B01/i.test(ncf.trim());

  async function guardar() {
    setGuardando(true);
    setError(null);
    const payload = {
      ncf: ncf.trim(),
      fecha,
      cliente_rnc: rnc.trim() || null,
      cliente_nombre: nombre.trim() || null,
      gravado_18: String(numero(g18)),
      gravado_16: String(numero(g16)),
      exento: String(numero(exento)),
      itbis_18: i18 === "" ? null : String(numero(i18)),
      forma_pago: forma,
      notas: notas.trim() || null,
      encf_reemplazo: reemplazo.trim() || null,
    };
    try {
      const f = factura ? await sincronizacionApi.actualizarFacturaPapel(factura.id, payload) : await sincronizacionApi.crearFacturaPapel(payload);
      onGuardada(f, !factura);
    } catch (err) {
      setError(mensajeError(err));
      setGuardando(false);
    }
  }

  async function eliminar() {
    if (!factura || !window.confirm(`¿Eliminar la factura en papel ${factura.ncf}?`)) return;
    try {
      await sincronizacionApi.eliminarFacturaPapel(factura.id);
      onEliminada(factura);
    } catch (err) {
      setError(mensajeError(err));
    }
  }

  return (
    <Modal
      titulo={factura ? `Factura en papel ${factura.ncf}` : "Registrar factura emitida en papel"}
      onCerrar={onCerrar}
      ancho={600}
      pie={
        <>
          {factura && (
            <button type="button" className="btn btn-do" onClick={eliminar} style={{ marginRight: "auto" }}>
              Eliminar
            </button>
          )}
          <button type="button" className="btn btn-o" onClick={onCerrar}>
            Cancelar
          </button>
          <button type="button" className="btn btn-p" onClick={guardar} disabled={guardando || !ncf.trim() || total <= 0}>
            {guardando ? "Guardando…" : factura ? "Guardar" : "Registrar comprobante"}
          </button>
        </>
      }
    >
      <div className="sy-papel-grid">
        <div className="fld">
          <label className="lbl" htmlFor="pn">
            Número de comprobante (NCF) <span className="req">*</span>
          </label>
          <input id="pn" className="inp mono" placeholder="B0100004521" value={ncf} onChange={(e) => setNcf(e.target.value.toUpperCase())} maxLength={19} />
          <span className="hint">{esCreditoFiscal ? "Crédito fiscal (B01): requiere el RNC del cliente" : "Serie B: B01 crédito fiscal, B02 consumo…"}</span>
        </div>
        <div className="fld">
          <label className="lbl" htmlFor="pf">
            Fecha de emisión
          </label>
          <input id="pf" className="inp" type="date" max={hoyIso()} value={fecha} onChange={(e) => setFecha(e.target.value)} />
        </div>
        <div className="fld">
          <label className="lbl" htmlFor="pr">
            RNC o cédula del cliente {esCreditoFiscal && <span className="req">*</span>}
          </label>
          <input id="pr" className="inp mono" placeholder="1-01-78452-3" value={rnc} onChange={(e) => setRnc(e.target.value)} />
        </div>
        <div className="fld">
          <label className="lbl" htmlFor="pc">
            Nombre del cliente
          </label>
          <input id="pc" className="inp" value={nombre} onChange={(e) => setNombre(e.target.value)} />
        </div>
        <div className="fld">
          <label className="lbl" htmlFor="pm">
            Monto gravado 18 % (RD$)
          </label>
          <input id="pm" className="inp" inputMode="decimal" placeholder="0.00" value={g18} onChange={(e) => setG18(e.target.value)} />
        </div>
        <div className="fld">
          <label className="lbl" htmlFor="pi">
            ITBIS 18 % (RD$)
          </label>
          <input id="pi" className="inp" inputMode="decimal" placeholder={formatoMonto(centavos(numero(g18) * 0.18))} value={i18} onChange={(e) => setI18(e.target.value)} />
        </div>
        <div className="fld">
          <label className="lbl" htmlFor="p16">
            Monto gravado 16 % (RD$)
          </label>
          <input id="p16" className="inp" inputMode="decimal" placeholder="0.00" value={g16} onChange={(e) => setG16(e.target.value)} />
        </div>
        <div className="fld">
          <label className="lbl" htmlFor="pe">
            Monto exento (RD$)
          </label>
          <input id="pe" className="inp" inputMode="decimal" placeholder="0.00" value={exento} onChange={(e) => setExento(e.target.value)} />
        </div>
        <div className="fld">
          <label className="lbl" htmlFor="pfp">
            Forma de pago
          </label>
          <select id="pfp" className="inp" value={forma} onChange={(e) => setForma(Number(e.target.value))}>
            {FORMAS_PAGO_607.map((f) => (
              <option key={f.value} value={f.value}>
                {f.label}
              </option>
            ))}
          </select>
        </div>
        <div className="fld">
          <label className="lbl" htmlFor="per">
            e-CF de reemplazo
          </label>
          <input id="per" className="inp mono" placeholder="E310000000150" value={reemplazo} onChange={(e) => setReemplazo(e.target.value.toUpperCase())} maxLength={13} />
        </div>
      </div>
      <div className="fld">
        <label className="lbl" htmlFor="pno">
          Notas
        </label>
        <input id="pno" className="inp" value={notas} onChange={(e) => setNotas(e.target.value)} />
      </div>
      <div className="sy-papel-total">
        <span>ITBIS RD$ {formatoMonto(itbis18 + itbis16)}</span>
        <b>Total RD$ {formatoMonto(total)}</b>
      </div>
      <div className="al al-i">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <circle cx="12" cy="12" r="9" />
          <path d="M12 11v5M12 8h.01" />
        </svg>
        <span>Al restablecerse el servicio, emita el e-CF de reemplazo (código de modificación 4) que referencia este comprobante y anótelo aquí.</span>
      </div>
      {error && <AvisoError mensaje={error} />}
    </Modal>
  );
}
