import { useEffect } from "react";

export interface Aviso {
  texto: string;
  detalle?: string;
  tono?: "ok" | "error" | "aviso";
}

const ICONO_FONDO: Record<NonNullable<Aviso["tono"]>, { background: string; color: string }> = {
  ok: { background: "#DCFCE7", color: "#166534" },
  error: { background: "#FEE2E2", color: "#B91C1C" },
  aviso: { background: "#FEF3C7", color: "#92400E" },
};

/** Notificación flotante del sistema de diseño (.toasts / .toast). Se cierra sola a los 6 s. */
export function Toast({ aviso, onCerrar }: { aviso: Aviso | null; onCerrar: () => void }) {
  useEffect(() => {
    if (!aviso) return;
    const t = window.setTimeout(onCerrar, aviso.tono === "error" ? 9000 : 6000);
    return () => window.clearTimeout(t);
  }, [aviso, onCerrar]);

  if (!aviso) return null;
  const tono = aviso.tono ?? "ok";
  return (
    <div className="toasts" role="status" aria-live="polite">
      <div className="toast">
        <span className="ti" style={ICONO_FONDO[tono]}>
          {tono === "ok" ? (
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.2} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M20 6 9 17l-5-5" />
            </svg>
          ) : (
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M12 8v5M12 16h.01" />
              <circle cx="12" cy="12" r="9" />
            </svg>
          )}
        </span>
        <div style={{ flex: 1, minWidth: 0 }}>
          <b>{aviso.texto}</b>
          {aviso.detalle && <span className="mut">{aviso.detalle}</span>}
        </div>
        <button type="button" className="ib" style={{ width: 32, height: 32 }} onClick={onCerrar} aria-label="Cerrar notificación">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" aria-hidden="true">
            <path d="M18 6 6 18M6 6l12 12" />
          </svg>
        </button>
      </div>
    </div>
  );
}
