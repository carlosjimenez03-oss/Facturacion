import { useCallback, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";

type Aviso = { texto: string; detalle?: string; error?: boolean };

/** Notificación flotante (toast) del prototipo; se cierra sola a los 5 s. */
export function useAviso(): [ReactNode, (texto: string, opciones?: { detalle?: string; error?: boolean }) => void] {
  const [aviso, setAviso] = useState<Aviso | null>(null);
  const timer = useRef<number>();
  const mostrar = useCallback((texto: string, opciones?: { detalle?: string; error?: boolean }) => {
    window.clearTimeout(timer.current);
    setAviso({ texto, ...opciones });
    timer.current = window.setTimeout(() => setAviso(null), 5000);
  }, []);
  useEffect(() => () => window.clearTimeout(timer.current), []);

  const nodo = aviso && (
    <div className="toasts" role="status" aria-live="polite">
      <div className="toast">
        <span className="ti" style={aviso.error ? { background: "#FEE2E2", color: "#B91C1C" } : { background: "#DCFCE7", color: "#166534" }}>
          {aviso.error ? (
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.2} strokeLinecap="round" aria-hidden="true">
              <path d="M12 8v5M12 16h.01" />
            </svg>
          ) : (
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.2} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M20 6 9 17l-5-5" />
            </svg>
          )}
        </span>
        <div style={{ flex: 1, minWidth: 0 }}>
          <b>{aviso.texto}</b>
          {aviso.detalle && <span className="mut">{aviso.detalle}</span>}
        </div>
        <button type="button" className="ib" style={{ width: 32, height: 32 }} onClick={() => setAviso(null)} aria-label="Cerrar notificación">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" aria-hidden="true">
            <path d="M18 6 6 18M6 6l12 12" />
          </svg>
        </button>
      </div>
    </div>
  );
  return [nodo, mostrar];
}
