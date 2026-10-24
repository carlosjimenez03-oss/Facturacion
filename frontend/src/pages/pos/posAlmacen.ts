// Almacenamiento del POS sin conexión en IndexedDB (base "kontara-pos"):
//
// - "cola": las ventas cobradas sin conexión con el servidor (VentaOffline,
//   clave = su UUID de idempotencia), hasta sincronizarse; las ya
//   sincronizadas se quedan para reimprimir el ticket hasta cerrar la caja.
// - "datos": la copia del catálogo que el POS necesita para vender sin red
//   (CatalogoPos): productos y precios (con su tasa de ITBIS), existencia del
//   almacén de la caja, almacenes, secuencias, la sesión de caja abierta (sin
//   el detalle de sus ventas) y los clientes con RNC/cédula (solo RNC y
//   nombre). Se renueva cada vez que el POS carga en línea y al reconectar.
//   El emisor y el vencimiento de la secuencia E31 ya los recuerda
//   lib/preferencias.ts (localStorage); las formas de pago son fijas.
//
// Nada de esto sale del navegador salvo al sincronizar. Si IndexedDB no está
// disponible (navegación privada en algunos navegadores) las funciones
// lanzan y el POS sigue funcionando solo en línea.
import type { Secuencia } from "../../lib/configuracionApi";
import type { SesionCaja } from "../../lib/solucionesApi";
import type { Almacen, Producto } from "../../lib/types";
import type { VentaOffline } from "./posOffline";

const BASE = "kontara-pos";
const VERSION = 1;
const COLA = "cola";
const DATOS = "datos";
const CLAVE_CATALOGO = "catalogo";

export interface CatalogoPos {
  guardadoEn: string;
  sesion: SesionCaja | null;
  productos: Producto[];
  almacenId: string;
  stock: Record<string, number>;
  almacenes: Almacen[];
  secuencias: Secuencia[];
  clientes: { rnc: string; nombre: string }[];
}

let abierta: Promise<IDBDatabase> | null = null;

function abrir(): Promise<IDBDatabase> {
  abierta ??= new Promise<IDBDatabase>((resolve, reject) => {
    if (typeof indexedDB === "undefined") {
      reject(new Error("Este navegador no permite guardar datos para trabajar sin conexión."));
      return;
    }
    const peticion = indexedDB.open(BASE, VERSION);
    peticion.onupgradeneeded = () => {
      const db = peticion.result;
      if (!db.objectStoreNames.contains(COLA)) db.createObjectStore(COLA, { keyPath: "clave" });
      if (!db.objectStoreNames.contains(DATOS)) db.createObjectStore(DATOS);
    };
    peticion.onsuccess = () => {
      const db = peticion.result;
      // Otra pestaña con una versión más nueva de la base: se cierra esta conexión.
      db.onversionchange = () => {
        db.close();
        abierta = null;
      };
      resolve(db);
    };
    peticion.onerror = () => {
      abierta = null;
      reject(peticion.error ?? new Error("No se pudo abrir el almacenamiento del POS."));
    };
  });
  return abierta;
}

async function operar<T>(almacen: string, modo: IDBTransactionMode, fn: (s: IDBObjectStore) => IDBRequest<T> | void): Promise<T> {
  const db = await abrir();
  return new Promise<T>((resolve, reject) => {
    const tx = db.transaction(almacen, modo);
    const peticion = fn(tx.objectStore(almacen));
    tx.oncomplete = () => resolve(peticion ? (peticion.result as T) : (undefined as T));
    tx.onerror = () => reject(tx.error ?? new Error("Error del almacenamiento del POS."));
    tx.onabort = () => reject(tx.error ?? new Error("Se canceló la escritura en el almacenamiento del POS."));
  });
}

export async function leerCola(): Promise<VentaOffline[]> {
  return operar<VentaOffline[]>(COLA, "readonly", (s) => s.getAll() as IDBRequest<VentaOffline[]>);
}

/** Inserta o actualiza (la transacción termina cuando el dato ya está escrito). */
export async function guardarVentaOffline(venta: VentaOffline): Promise<void> {
  await operar(COLA, "readwrite", (s) => {
    s.put(venta);
  });
}

export async function borrarVentasOffline(claves: string[]): Promise<void> {
  if (claves.length === 0) return;
  await operar(COLA, "readwrite", (s) => {
    for (const c of claves) s.delete(c);
  });
}

export async function leerCatalogo(): Promise<CatalogoPos | null> {
  const c = await operar<CatalogoPos | undefined>(DATOS, "readonly", (s) => s.get(CLAVE_CATALOGO) as IDBRequest<CatalogoPos | undefined>);
  return c ?? null;
}

export const CATALOGO_VACIO: CatalogoPos = {
  guardadoEn: "",
  sesion: null,
  productos: [],
  almacenId: "",
  stock: {},
  almacenes: [],
  secuencias: [],
  clientes: [],
};

let escritura: Promise<unknown> = Promise.resolve();

/**
 * Actualiza parte del catálogo guardado (cada carga en línea aporta lo suyo).
 * Las escrituras van en fila para que dos cargas a la vez no se pisen.
 */
export function actualizarCatalogo(parcial: Partial<CatalogoPos>): Promise<void> {
  const siguiente = escritura.then(async () => {
    const actual = (await leerCatalogo()) ?? CATALOGO_VACIO;
    const nuevo: CatalogoPos = { ...actual, ...parcial, guardadoEn: new Date().toISOString() };
    // La sesión sin el detalle de sus ventas (clientes, pagos): solo sus totales.
    if (nuevo.sesion) nuevo.sesion = { ...nuevo.sesion, ventas: [] };
    await operar(DATOS, "readwrite", (s) => {
      s.put(nuevo, CLAVE_CATALOGO);
    });
  });
  escritura = siguiente.catch(() => undefined);
  return siguiente;
}

/**
 * Serializa la sincronización entre pestañas del mismo navegador (Web Locks):
 * dos pestañas del POS no mandan la misma cola a la vez. Sin la API, corre
 * igual (el servidor es idempotente con la clave de cada venta).
 */
export async function conCandado<T>(fn: () => Promise<T>): Promise<T> {
  const locks = typeof navigator !== "undefined" ? navigator.locks : undefined;
  if (!locks) return fn();
  return locks.request("kontara-pos-sincronizar", fn) as Promise<T>;
}
