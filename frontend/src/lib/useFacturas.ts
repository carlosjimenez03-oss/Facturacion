import { useEffect, useState } from "react";
import { api, ApiError } from "./api";
import type { FacturaResumen } from "./types";

interface EstadoFacturas {
  facturas: FacturaResumen[];
  cargando: boolean;
  error: string | null;
  /** true si la API respondió en la última consulta. */
  enLinea: boolean;
}

let cache: EstadoFacturas | null = null;
let enCurso: Promise<EstadoFacturas> | null = null;
const suscriptores = new Set<(e: EstadoFacturas) => void>();

function cargar(): Promise<EstadoFacturas> {
  if (!enCurso) {
    enCurso = api
      .listar()
      .then((facturas) => ({ facturas, cargando: false, error: null, enLinea: true }))
      .catch((err) => ({
        facturas: cache?.facturas ?? [],
        cargando: false,
        error: err instanceof ApiError ? err.message : String(err),
        enLinea: err instanceof ApiError,
      }))
      .then((estado) => {
        cache = estado;
        enCurso = null;
        suscriptores.forEach((fn) => fn(estado));
        return estado;
      });
  }
  return enCurso;
}

/** Lista de facturas compartida entre la barra superior, el inicio y el listado (una sola consulta). */
export function useFacturas(): EstadoFacturas & { recargar: () => void } {
  const [estado, setEstado] = useState<EstadoFacturas>(
    cache ?? { facturas: [], cargando: true, error: null, enLinea: true },
  );

  useEffect(() => {
    suscriptores.add(setEstado);
    if (!cache) void cargar();
    return () => {
      suscriptores.delete(setEstado);
    };
  }, []);

  return { ...estado, recargar: () => void cargar() };
}

/** Motivo legible de un rechazo, a partir de la respuesta guardada de la DGII. */
export function motivoRechazo(f: FacturaResumen): string {
  const partes = [f.dgii_codigo ? `Código ${f.dgii_codigo}` : null, f.dgii_estado, f.error].filter(Boolean);
  return partes.length ? partes.join(" · ") : "La DGII no devolvió un motivo. Consulte el detalle de la factura.";
}
