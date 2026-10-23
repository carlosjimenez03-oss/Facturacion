// Piezas comunes de las pantallas de Impuestos (Formatos.dc.html y PreCalculo.dc.html del
// prototipo): selector de período, estado de la declaración, modal de "presentada" y el
// modal para copiar a mano cuando el navegador bloquea el portapapeles.
import { useState } from "react";
import type { ReactNode } from "react";
import type { Declaracion, MarcarPresentadaPayload } from "../../lib/impuestosApi";
import { IconAlerta } from "../../layout/icons";
import { Modal } from "../ingresos/IngresosShell";
import { hoyIso, mensajeError } from "../ingresos/ingresosUtil";
import { ESTADO_DECLARACION } from "./impuestosUtil";
import "./impuestos.css";

export function SelectorPeriodo({
  valor,
  opciones,
  onCambiar,
  etiqueta = "Período",
  hint,
}: {
  valor: string;
  opciones: { valor: string; label: string }[];
  onCambiar: (v: string) => void;
  etiqueta?: string;
  /** Vencimiento y estado de la declaración del período elegido. */
  hint?: ReactNode;
}) {
  return (
    <div className="fld">
      <label className="lbl" htmlFor="imp-per">
        {etiqueta}
      </label>
      <select id="imp-per" className="inp" style={{ minWidth: 220 }} value={valor} onChange={(e) => onCambiar(e.target.value)}>
        {opciones.map((o) => (
          <option key={o.valor} value={o.valor}>
            {o.label}
          </option>
        ))}
      </select>
      {hint && <span className="hint imp-per-hint">{hint}</span>}
    </div>
  );
}

export function EstadoDeclaracionBadge({ declaracion }: { declaracion: Declaracion | null }) {
  if (!declaracion) return null;
  const e = ESTADO_DECLARACION[declaracion.estado] ?? { label: declaracion.estado, badge: "bd b-borr" };
  return <span className={e.badge}>{e.label}</span>;
}

export function IconoValidar() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M12 3 4 6v6c0 5 3.5 8 8 9 4.5-1 8-4 8-9V6z" />
      <path d="m9 12 2 2 4-4" />
    </svg>
  );
}

export function IconoDescargar() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M12 3v12M7 10l5 5 5-5M5 21h14" />
    </svg>
  );
}

export function IconoOk() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M20 6 9 17l-5-5" />
    </svg>
  );
}

export function Advertencias({ lista }: { lista: string[] }) {
  if (lista.length === 0) return null;
  return (
    <div className="al al-w" role="status">
      <IconAlerta />
      {lista.length === 1 ? (
        <span>{lista[0]}</span>
      ) : (
        <div>
          <b>Revise {lista.length} advertencias antes de presentar:</b>
          <ul className="imp-lista">
            {lista.map((a, i) => (
              <li key={i}>{a}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

/** Registro de la declaración ya presentada en la Oficina Virtual (fecha, número de confirmación y monto pagado). */
export function ModalPresentada({
  titulo,
  montoSugerido,
  onCerrar,
  onConfirmar,
}: {
  titulo: string;
  montoSugerido?: string | null;
  onCerrar: () => void;
  onConfirmar: (payload: MarcarPresentadaPayload) => Promise<void>;
}) {
  const [fecha, setFecha] = useState(hoyIso());
  const [referencia, setReferencia] = useState("");
  const [monto, setMonto] = useState(montoSugerido && Number(montoSugerido) > 0 ? Number(montoSugerido).toFixed(2) : "");
  const [error, setError] = useState<string | null>(null);
  const [guardando, setGuardando] = useState(false);

  async function confirmar() {
    if (!fecha) return setError("Indique la fecha en que presentó la declaración.");
    if (monto && !(Number(monto) >= 0)) return setError("El monto pagado no es válido.");
    setGuardando(true);
    setError(null);
    try {
      await onConfirmar({ presentada_en: fecha, referencia: referencia.trim() || null, monto: monto ? Number(monto).toFixed(2) : null });
    } catch (err) {
      setError(mensajeError(err));
      setGuardando(false);
    }
  }

  return (
    <Modal
      titulo={titulo}
      onCerrar={onCerrar}
      ancho={520}
      pie={
        <>
          <button type="button" className="btn btn-o" onClick={onCerrar}>
            Cancelar
          </button>
          <button type="button" className="btn btn-p" onClick={confirmar} disabled={guardando}>
            {guardando ? "Guardando…" : "Marcar como presentada"}
          </button>
        </>
      }
    >
      <span>Anote los datos del acuse que le dio la Oficina Virtual de la DGII. Quedará registrada en el calendario fiscal.</span>
      <div className="fld">
        <label className="lbl" htmlFor="pr-fecha">
          Fecha de presentación <span className="req">*</span>
        </label>
        <input id="pr-fecha" type="date" className="inp" value={fecha} max={hoyIso()} onChange={(e) => setFecha(e.target.value)} />
      </div>
      <div className="fld">
        <label className="lbl" htmlFor="pr-ref">Número de confirmación</label>
        <input id="pr-ref" className="inp mono" value={referencia} onChange={(e) => setReferencia(e.target.value)} placeholder="N.º de acuse de recibo" />
      </div>
      <div className="fld">
        <label className="lbl" htmlFor="pr-monto">Monto pagado (RD$)</label>
        <input
          id="pr-monto"
          type="number"
          min="0"
          step="0.01"
          className="inp"
          style={{ textAlign: "right" }}
          value={monto}
          onChange={(e) => setMonto(e.target.value)}
          placeholder="Opcional"
        />
      </div>
      {error && (
        <div className="al al-d" role="alert">
          <IconAlerta />
          <span>
            <b>{error}</b>
          </span>
        </div>
      )}
    </Modal>
  );
}

/** Cuando el navegador no deja copiar, se muestra el texto para copiarlo a mano (como el prototipo). */
export function ModalCopiaManual({ texto, onCerrar }: { texto: string; onCerrar: () => void }) {
  return (
    <Modal
      titulo="Copie el contenido manualmente"
      onCerrar={onCerrar}
      ancho={640}
      pie={
        <button type="button" className="btn btn-p" onClick={onCerrar}>
          Cerrar
        </button>
      }
    >
      <span>El navegador bloqueó el portapapeles. Puede copiar el texto desde aquí.</span>
      <textarea className="inp mono" readOnly rows={10} style={{ fontSize: 12, whiteSpace: "pre" }} aria-label="Contenido para copiar" value={texto} onFocus={(e) => e.target.select()} />
      <span className="hint">Seleccione el texto (Ctrl+A) y cópielo (Ctrl+C).</span>
    </Modal>
  );
}
