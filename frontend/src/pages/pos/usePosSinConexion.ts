// Estado de conexión del POS y su cola de ventas sin conexión (ver
// posOffline.ts y posAlmacen.ts): detecta cuándo el navegador no llega al
// servidor, guarda las ventas en IndexedDB y las sincroniza en orden al
// reconectar (o con "Sincronizar ahora").
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../../lib/api";
import { siguienteEncf } from "../../lib/buildDatos";
import { configuracionApi } from "../../lib/configuracionApi";
import type { Secuencia } from "../../lib/configuracionApi";
import { ERP_API_URL } from "../../lib/erpApi";
import { prepararSesion } from "../../lib/sesion";
import { solucionesApi } from "../../lib/solucionesApi";
import { borrarVentasOffline, conCandado, guardarVentaOffline, leerCola } from "./posAlmacen";
import { ordenarCola, sincronizarCola } from "./posOffline";
import type { DependenciasSincronizacion, ResultadoSincronizacion, VentaOffline } from "./posOffline";

/** Cada cuánto se vuelve a probar el servidor mientras no hay conexión. */
const SONDEO_MS = 10_000;

/** Cualquier respuesta HTTP (incluido el 401 de nginx sin sesión) = el servidor está al alcance. */
async function servidorAlcanzable(): Promise<boolean> {
  if (typeof navigator !== "undefined" && navigator.onLine === false) return false;
  const control = new AbortController();
  const corte = window.setTimeout(() => control.abort(), 5_000);
  try {
    await fetch(`${ERP_API_URL}/health`, { cache: "no-store", credentials: "omit", signal: control.signal });
    return true;
  } catch {
    return false;
  } finally {
    window.clearTimeout(corte);
  }
}

function dependencias(alGuardar: (v: VentaOffline) => void): DependenciasSincronizacion {
  // Las secuencias se leen una vez por pasada: con secuencia registrada el
  // servidor asigna el e-NCF (asignar_encf); sin ella, igual que la venta en
  // línea, el mayor ya usado + 1.
  let secuencias: Promise<Secuencia[]> | null = null;
  return {
    prepararSesion,
    async crearFactura(v) {
      secuencias ??= configuracionApi.secuencias();
      const secuencia = (await secuencias).find((x) => String(x.tipo_ecf) === v.tipoEcf && x.configurada);
      const encf = secuencia ? null : siguienteEncf(await api.listar({ tipo_ecf: Number(v.tipoEcf) }), v.tipoEcf);
      return api.crear({
        tipo_ecf: Number(v.tipoEcf),
        encf,
        asignar_encf: Boolean(secuencia),
        datos: v.datos,
        items_inventario: v.itemsInventario,
        clave_idempotencia: v.clave,
      });
    },
    generarXml: (id) => api.generarXml(id),
    enviar: (id) => api.enviar(id),
    registrarVenta: (sesionId, facturaId, pagos) => solucionesApi.registrarVenta(sesionId, { factura_id: facturaId, pagos }),
    async guardar(v) {
      await guardarVentaOffline(v);
      alGuardar(v);
    },
  };
}

export interface PosSinConexion {
  /** El navegador llega al servidor. */
  enLinea: boolean;
  /** IndexedDB disponible (si no, el POS solo vende en línea). */
  almacenamiento: boolean;
  cola: VentaOffline[];
  sincronizando: boolean;
  /** La sesión venció y no se pudo renovar: hay que iniciar sesión (la cola se conserva). */
  sinSesion: boolean;
  ultimoResultado: ResultadoSincronizacion | null;
  encolar: (v: VentaOffline) => Promise<void>;
  sincronizar: () => Promise<ResultadoSincronizacion | null>;
  /** Una petición falló por red: pasa a "sin conexión" y empieza a sondear. */
  marcarSinConexion: () => void;
  /** Borra las ya sincronizadas de una caja (al cerrarla). */
  limpiarSincronizadas: (sesionId: string) => Promise<void>;
}

export function usePosSinConexion(opciones: { alReconectar?: () => void; alSincronizar?: (r: ResultadoSincronizacion) => void }): PosSinConexion {
  const [enLinea, setEnLinea] = useState(() => typeof navigator === "undefined" || navigator.onLine !== false);
  const [almacenamiento, setAlmacenamiento] = useState(true);
  const [cola, setCola] = useState<VentaOffline[]>([]);
  const [sincronizando, setSincronizando] = useState(false);
  const [sinSesion, setSinSesion] = useState(false);
  const [ultimoResultado, setUltimoResultado] = useState<ResultadoSincronizacion | null>(null);
  const enCurso = useRef<Promise<ResultadoSincronizacion | null> | null>(null);
  const opcionesRef = useRef(opciones);
  opcionesRef.current = opciones;

  const actualizarEnCola = useCallback((v: VentaOffline) => {
    setCola((prev) => ordenarCola([...prev.filter((x) => x.clave !== v.clave), v]));
  }, []);

  const recargar = useCallback(async () => {
    try {
      setCola(ordenarCola(await leerCola()));
      setAlmacenamiento(true);
    } catch {
      setAlmacenamiento(false);
    }
  }, []);

  const sincronizar = useCallback((): Promise<ResultadoSincronizacion | null> => {
    if (enCurso.current) return enCurso.current;
    const corrida = (async () => {
      setSincronizando(true);
      try {
        // Con candado entre pestañas y con la cola recién leída (otra pestaña pudo avanzarla).
        const resultado = await conCandado(async () => sincronizarCola(await leerCola(), dependencias(actualizarEnCola)));
        setUltimoResultado(resultado);
        setSinSesion(resultado.detenida === "sin_sesion");
        if (resultado.detenida === "sin_red") setEnLinea(false);
        opcionesRef.current.alSincronizar?.(resultado);
        return resultado;
      } catch {
        return null;
      } finally {
        await recargar();
        setSincronizando(false);
        enCurso.current = null;
      }
    })();
    enCurso.current = corrida;
    return corrida;
  }, [actualizarEnCola, recargar]);

  const encolar = useCallback(
    async (v: VentaOffline) => {
      await guardarVentaOffline(v);
      actualizarEnCola(v);
    },
    [actualizarEnCola],
  );

  const marcarSinConexion = useCallback(() => setEnLinea(false), []);

  const limpiarSincronizadas = useCallback(
    async (sesionId: string) => {
      const claves = (await leerCola()).filter((v) => v.sesionId === sesionId && v.estado === "sincronizada").map((v) => v.clave);
      await borrarVentasOffline(claves);
      await recargar();
    },
    [recargar],
  );

  useEffect(() => {
    void recargar();
  }, [recargar]);

  // Eventos del navegador: "offline" es fiable (sin red local); "online" no
  // garantiza llegar al servidor, así que se comprueba.
  useEffect(() => {
    const comprobar = () => void servidorAlcanzable().then(setEnLinea);
    const sinRed = () => setEnLinea(false);
    window.addEventListener("online", comprobar);
    window.addEventListener("offline", sinRed);
    return () => {
      window.removeEventListener("online", comprobar);
      window.removeEventListener("offline", sinRed);
    };
  }, []);

  // Sin conexión: sondeo hasta que el servidor responda.
  useEffect(() => {
    if (enLinea) return;
    const id = window.setInterval(() => void servidorAlcanzable().then((ok) => ok && setEnLinea(true)), SONDEO_MS);
    return () => window.clearInterval(id);
  }, [enLinea]);

  // Al volver la conexión (y al abrir el POS en línea): refrescar el
  // catálogo y sincronizar lo pendiente.
  const anterior = useRef<boolean | null>(null);
  useEffect(() => {
    const antes = anterior.current;
    anterior.current = enLinea;
    if (!enLinea) return;
    if (antes === false) opcionesRef.current.alReconectar?.();
    void sincronizar();
  }, [enLinea, sincronizar]);

  return { enLinea, almacenamiento, cola, sincronizando, sinSesion, ultimoResultado, encolar, sincronizar, marcarSinConexion, limpiarSincronizadas };
}
