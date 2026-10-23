// Utilidades compartidas por las pantallas de Impuestos (formatos 606/607/608 y pre-cálculos).
import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { impuestosApi } from "../../lib/impuestosApi";
import type { Declaracion, EstadoDeclaracion, FormularioDeclaracion } from "../../lib/impuestosApi";
import { formatoMonto } from "../../lib/marca";
import { hoyIso, mensajeError } from "../ingresos/ingresosUtil";

const MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"];

/** "202610" -> "octubre 2026" (en minúscula, para frases); `mayuscula` -> "Octubre 2026". */
export function nombrePeriodo(periodo: string, mayuscula = false): string {
  if (!/^\d{6}$/.test(periodo)) return periodo;
  const s = `${MESES[Number(periodo.slice(4, 6)) - 1] ?? periodo.slice(4, 6)} ${periodo.slice(0, 4)}`;
  return mayuscula ? s.charAt(0).toUpperCase() + s.slice(1) : s;
}

function aaaamm(d: Date): string {
  return `${d.getFullYear()}${String(d.getMonth() + 1).padStart(2, "0")}`;
}

/** Las declaraciones mensuales se presentan por el mes que acaba de cerrar. */
export function periodoAnterior(hoy = new Date()): string {
  return aaaamm(new Date(hoy.getFullYear(), hoy.getMonth() - 1, 1));
}

/** Meses que ofrece el selector: del mes en curso hacia atrás. */
export function periodosRecientes(cantidad = 24, hoy = new Date()): string[] {
  return Array.from({ length: cantidad }, (_, i) => aaaamm(new Date(hoy.getFullYear(), hoy.getMonth() - i, 1)));
}

export function periodoValido(p: string | null): p is string {
  return !!p && /^\d{6}$/.test(p) && Number(p.slice(4, 6)) >= 1 && Number(p.slice(4, 6)) <= 12;
}

/** Período mensual (?periodo=AAAAMM) guardado en la URL; por defecto, el mes anterior. */
export function usePeriodoMensual(): [string, (p: string) => void] {
  const [params, setParams] = useSearchParams();
  const valor = params.get("periodo");
  const periodo = periodoValido(valor) ? valor : periodoAnterior();
  const cambiar = useCallback((p: string) => setParams({ periodo: p }, { replace: true }), [setParams]);
  return [periodo, cambiar];
}

/** Ejercicio fiscal (?anio=AAAA) para el IR-2; por defecto, el año en curso. */
export function useAnio(): [string, (a: string) => void] {
  const [params, setParams] = useSearchParams();
  const valor = params.get("anio");
  const anio = valor && /^\d{4}$/.test(valor) ? valor : String(new Date().getFullYear());
  const cambiar = useCallback((a: string) => setParams({ anio: a }, { replace: true }), [setParams]);
  return [anio, cambiar];
}

/** Monto de una casilla: los negativos (restas) entre paréntesis, como en los formularios de la DGII. */
export function montoContable(valor: string | number | null | undefined): string {
  const n = Number(valor ?? 0);
  return n < 0 ? `(${formatoMonto(-n)})` : formatoMonto(n);
}

/** Fecha YYYY-MM-DD o AAAAMMDD (606) -> DD/MM/YYYY; `corta` -> DD/MM. */
export function fechaDgii(fecha: string | null | undefined, corta = false): string {
  if (!fecha) return "";
  const f = fecha.slice(0, 10);
  let d = "";
  let m = "";
  let a = "";
  if (/^\d{8}$/.test(f)) [a, m, d] = [f.slice(0, 4), f.slice(4, 6), f.slice(6, 8)];
  else if (/^\d{4}-\d{2}-\d{2}$/.test(f)) [a, m, d] = f.split("-");
  else return fecha;
  return corta ? `${d}/${m}` : `${d}/${m}/${a}`;
}

/** Días que faltan desde hoy hasta una fecha YYYY-MM-DD (negativo si ya pasó). */
export function diasHasta(fecha: string): number {
  const hoy = new Date(`${hoyIso()}T12:00:00`);
  return Math.round((new Date(`${fecha.slice(0, 10)}T12:00:00`).getTime() - hoy.getTime()) / 86_400_000);
}

/** "Vence 20/11/2026 · en 28 días", "Venció hace 3 días" o "Presentada el 14/11/2026". */
export function textoVencimiento(vence: string | null | undefined, declaracion?: Declaracion | null): string {
  if (declaracion?.estado === "presentada") {
    return declaracion.presentada_en ? `Presentada el ${fechaDgii(declaracion.presentada_en)}` : "Presentada";
  }
  if (declaracion?.estado === "no_aplica") return "No aplica a emisores 100 % electrónicos";
  if (!vence) return "";
  const dias = diasHasta(vence);
  if (dias < 0) return `Venció el ${fechaDgii(vence)} · hace ${-dias} ${dias === -1 ? "día" : "días"}`;
  if (dias === 0) return `Vence hoy, ${fechaDgii(vence)}`;
  return `Vence ${fechaDgii(vence)} · en ${dias} ${dias === 1 ? "día" : "días"}`;
}

export const ESTADO_DECLARACION: Record<EstadoDeclaracion, { label: string; badge: string }> = {
  pendiente: { label: "Pendiente", badge: "bd b-proc" },
  presentada: { label: "Presentada", badge: "bd b-acep" },
  vencida: { label: "Vencida", badge: "bd b-rech" },
  no_aplica: { label: "No aplica", badge: "bd b-borr" },
};

/** Opción del selector de período: "Octubre 2026 · vence 16/11", "Septiembre 2026 · presentado". */
export function etiquetaPeriodo(nombre: string, declaracion: Declaracion | null): string {
  if (!declaracion) return nombre;
  if (declaracion.estado === "presentada") return `${nombre} · presentado`;
  if (declaracion.estado === "no_aplica") return `${nombre} · no aplica`;
  if (declaracion.estado === "vencida") return `${nombre} · vencido el ${fechaDgii(declaracion.vence, true)}`;
  return `${nombre} · vence ${fechaDgii(declaracion.vence, true)}`;
}

/** Calendario de declaraciones de uno o varios años; si el servicio no responde, la pantalla sigue sin estado. */
export function useDeclaraciones(anios: string[]) {
  const clave = [...new Set(anios)].sort().join(",");
  const [declaraciones, setDeclaraciones] = useState<Declaracion[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let vigente = true;
    void Promise.allSettled(clave.split(",").map((a) => impuestosApi.listarDeclaraciones(a))).then((res) => {
      if (!vigente) return;
      setDeclaraciones(res.flatMap((r) => (r.status === "fulfilled" ? r.value : [])));
      const fallo = res.find((r): r is PromiseRejectedResult => r.status === "rejected");
      setError(fallo ? mensajeError(fallo.reason) : null);
    });
    return () => {
      vigente = false;
    };
  }, [clave]);

  const buscar = useCallback(
    (formulario: FormularioDeclaracion, periodo: string) => declaraciones.find((d) => d.formulario === formulario && d.periodo === periodo) ?? null,
    [declaraciones],
  );
  const actualizar = useCallback((d: Declaracion) => {
    setDeclaraciones((prev) => [...prev.filter((x) => !(x.formulario === d.formulario && x.periodo === d.periodo)), d]);
  }, []);
  return { declaraciones, error, buscar, actualizar };
}

/** Guarda un archivo de texto con el nombre que propone el servidor (TXT de la DGII). */
export function descargarTexto(nombre: string, contenido: string | Blob) {
  const blob = contenido instanceof Blob ? contenido : new Blob([contenido], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = nombre;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 8000);
}

/** Copia al portapapeles; si el navegador lo bloquea, intenta con un textarea oculto. Devuelve si lo logró. */
export async function copiarTexto(texto: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(texto);
    return true;
  } catch {
    try {
      const t = document.createElement("textarea");
      t.value = texto;
      t.setAttribute("readonly", "");
      t.style.position = "fixed";
      t.style.top = "-1000px";
      document.body.appendChild(t);
      t.select();
      const ok = document.execCommand("copy");
      t.remove();
      return ok;
    } catch {
      return false;
    }
  }
}
