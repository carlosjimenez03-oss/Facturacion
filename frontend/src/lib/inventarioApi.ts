// Cliente del servicio `inventario-api`. Este archivo es el CONTRATO entre el
// backend (services/inventario_api: schemas.py y main.py) y las pantallas de
// Inventario (Productos, Almacenes, Lotes, Ajustes, Kardex, Compras). Ver
// docs/INVENTARIO.md. Cantidades, costos y montos viajan como string decimal
// ("545.0000"), fechas como "YYYY-MM-DD" y marcas de tiempo como ISO 8601.
//
// Errores: 404 = no existe (o un almacén/lote referenciado no existe), 409 =
// la operación no aplica en el estado actual (ajuste ya aplicado, almacén
// inactivo, lote de otro producto, sin existencia...), 422 = datos inválidos.
// Un 409 por falta de existencia trae además `faltante` (ver
// InventarioApiError). (La ruta interna de facturas-api
// POST /movimientos/validar-disponibilidad responde 422 en vez de 404 por un
// almacén/lote inexistente; no se usa desde el frontend.)
import type {
  Ajuste,
  AjusteItemPayload,
  AlertasLotes,
  Almacen,
  Compra,
  CompraItem,
  EstadoAjuste,
  EstadoLote,
  ExistenciaAlmacen,
  ExistenciasProducto,
  Kardex,
  Lote,
  MetodoValuacion,
  MotivoAjuste,
  Movimiento,
  Producto,
  Proveedor,
  SugerenciaFefo,
  TasaItbis,
  TipoProducto,
  Transferencia,
  Valuacion,
} from "./types";

export const INVENTARIO_API_URL =
  (import.meta.env.VITE_INVENTARIO_API_URL as string | undefined) ?? "http://localhost:8012";

/** Detalle de una salida que no tiene existencia (409 de transferencias,
 * ajustes y anulaciones). `lote_id` presente si lo que falta es en un lote. */
export interface FaltanteStock {
  producto_id: string;
  nombre: string;
  almacen_id: string;
  lote_id: string | null;
  solicitado: string;
  disponible: string;
}

class InventarioApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public faltante?: FaltanteStock,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${INVENTARIO_API_URL}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!response.ok) {
    let detail = response.statusText;
    let faltante: FaltanteStock | undefined;
    try {
      const body = await response.json();
      detail = body.detail ?? JSON.stringify(body);
      faltante = body.faltante;
    } catch {
      // sin cuerpo JSON, se mantiene statusText
    }
    throw new InventarioApiError(
      response.status,
      typeof detail === "string" ? detail : JSON.stringify(detail),
      faltante,
    );
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return response.json() as Promise<T>;
}

function consulta(params: Record<string, string | number | boolean | undefined | null>): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") q.set(k, String(v));
  }
  const s = q.toString();
  return s ? `?${s}` : "";
}

/** Campos de la Fase 4 del producto. Todos opcionales en el payload. */
export interface ProductoCamposFase4 {
  tipo?: TipoProducto;
  tasa_itbis?: TasaItbis;
  /** "" (o solo espacios) se guarda como null. */
  codigo_barras?: string | null;
  categoria?: string | null;
  stock_minimo?: string;
  maneja_lotes?: boolean;
}

export interface ProductoPayload extends ProductoCamposFase4 {
  sku: string;
  nombre: string;
  descripcion?: string | null;
  unidad_medida?: string | null;
  precio_venta: string;
  stock_inicial?: string;
  /** Costo del stock inicial (fija el costo promedio); por defecto 0. */
  costo_unitario_inicial?: string | null;
  /** Almacén donde entra el stock inicial; por defecto el principal. */
  almacen_id?: string | null;
}

/** PUT /productos/{id}: los campos de `ProductoCamposFase4` que NO se
 * envían se conservan (enviar `categoria: null` sí la borra). `tipo`,
 * `tasa_itbis`, `stock_minimo` y `maneja_lotes` no aceptan null. Pasar a
 * "servicio" exige existencia cero (409); `maneja_lotes: false` exige que
 * ningún lote tenga existencia (409). */
export interface ProductoActualizarPayload extends ProductoCamposFase4 {
  sku: string;
  nombre: string;
  descripcion?: string | null;
  unidad_medida?: string | null;
  precio_venta: string;
  activo: boolean;
}

export interface ProveedorPayload {
  nombre: string;
  rnc?: string | null;
  telefono?: string | null;
  correo?: string | null;
  direccion?: string | null;
}

export interface ProveedorActualizarPayload extends ProveedorPayload {
  activo: boolean;
}

export interface CompraPayload {
  proveedor_id?: string | null;
  numero_documento?: string | null;
  notas?: string | null;
  /** Almacén que recibe la mercancía al confirmar; por defecto el principal. */
  almacen_id?: string | null;
  items: CompraItem[];
}

export interface AlmacenPayload {
  nombre: string;
  direccion?: string | null;
  permite_negativo?: boolean;
}

/** PUT /almacenes/{id}. `es_principal: true` lo vuelve el principal (el
 * anterior deja de serlo); no se puede quitar la marca sin designar otro, ni
 * inactivar el principal o un almacén con existencia (409). */
export interface AlmacenActualizarPayload extends AlmacenPayload {
  activo: boolean;
  es_principal?: boolean;
}

export interface TransferenciaPayload {
  almacen_origen_id: string;
  almacen_destino_id: string;
  notas?: string | null;
  /** Sin `lote_id`, un producto con lotes sale por FEFO y entra al destino
   * en los mismos lotes. */
  items: { producto_id: string; cantidad: string; lote_id?: string | null }[];
}

export interface LotePayload {
  producto_id: string;
  numero: string;
  fecha_vencimiento?: string | null;
  notas?: string | null;
  /** > 0 = registra además una entrada manual de esa cantidad al lote. */
  cantidad_inicial?: string;
  /** Almacén de la entrada manual; por defecto el principal. */
  almacen_id?: string | null;
  /** Costo de la entrada manual; por defecto el costo promedio vigente. */
  costo_unitario?: string | null;
}

export interface LoteActualizarPayload {
  numero: string;
  fecha_vencimiento?: string | null;
  /** true = sale de las sugerencias FEFO, de la venta automática y de las alertas. */
  bloqueado: boolean;
  notas?: string | null;
}

export interface AjustePayload {
  /** Por defecto el principal. */
  almacen_id?: string | null;
  motivo: MotivoAjuste;
  /** "YYYY-MM-DD"; por defecto hoy. */
  fecha?: string | null;
  notas?: string | null;
  items: AjusteItemPayload[];
}

export interface KardexParams {
  producto_id: string;
  /** Sin almacén = todos (sin mostrar las transferencias entre almacenes). */
  almacen_id?: string;
  desde?: string;
  hasta?: string;
  /** "promedio" (por defecto, el método con que se valúa) o "peps" (solo vista). */
  metodo?: MetodoValuacion;
  /** Filtran las filas; los saldos siempre cuentan todos los movimientos. */
  tipo?: "entrada" | "salida";
  referencia_tipo?: string;
}

export const inventarioApi = {
  // --- Productos ---------------------------------------------------------

  /** `alerta_stock: true` = bienes sin existencia o bajo su mínimo. `q` busca
   * en nombre, SKU y código de barras. */
  listarProductos: (params?: {
    q?: string;
    activo?: boolean;
    categoria?: string;
    tipo?: TipoProducto;
    alerta_stock?: boolean;
    limit?: number;
    offset?: number;
  }) => request<Producto[]>(`/productos${consulta({ ...params })}`),

  /** Categorías usadas por algún producto (para el selector). */
  listarCategorias: () => request<string[]>("/productos/categorias"),

  obtenerProducto: (id: string) => request<Producto>(`/productos/${id}`),

  crearProducto: (payload: ProductoPayload) =>
    request<Producto>("/productos", { method: "POST", body: JSON.stringify(payload) }),

  actualizarProducto: (id: string, payload: ProductoActualizarPayload) =>
    request<Producto>(`/productos/${id}`, { method: "PUT", body: JSON.stringify(payload) }),

  /** Existencia del producto por almacén y por lote. */
  existenciasProducto: (id: string) => request<ExistenciasProducto>(`/productos/${id}/existencias`),

  // --- Proveedores ---------------------------------------------------------

  listarProveedores: (params?: { activo?: boolean }) => {
    const query = new URLSearchParams();
    if (params?.activo !== undefined) query.set("activo", String(params.activo));
    const qs = query.toString();
    return request<Proveedor[]>(`/proveedores${qs ? `?${qs}` : ""}`);
  },

  obtenerProveedor: (id: string) => request<Proveedor>(`/proveedores/${id}`),

  crearProveedor: (payload: ProveedorPayload) =>
    request<Proveedor>("/proveedores", { method: "POST", body: JSON.stringify(payload) }),

  actualizarProveedor: (id: string, payload: ProveedorActualizarPayload) =>
    request<Proveedor>(`/proveedores/${id}`, { method: "PUT", body: JSON.stringify(payload) }),

  // --- Compras ---------------------------------------------------------

  listarCompras: (params?: { estado?: string }) => {
    const query = new URLSearchParams();
    if (params?.estado) query.set("estado", params.estado);
    const qs = query.toString();
    return request<Compra[]>(`/compras${qs ? `?${qs}` : ""}`);
  },

  obtenerCompra: (id: string) => request<Compra>(`/compras/${id}`),

  crearCompra: (payload: CompraPayload) =>
    request<Compra>("/compras", { method: "POST", body: JSON.stringify(payload) }),

  actualizarCompra: (id: string, payload: CompraPayload) =>
    request<Compra>(`/compras/${id}`, { method: "PUT", body: JSON.stringify(payload) }),

  eliminarCompra: (id: string) => request<void>(`/compras/${id}`, { method: "DELETE" }),

  /** Ingresa cada línea al almacén de la compra (y a su lote), sube el stock y
   * recalcula el costo promedio. */
  confirmarCompra: (id: string) => request<Compra>(`/compras/${id}/confirmar`, { method: "POST" }),

  // --- Movimientos y Kardex ---------------------------------------------------------

  /** Movimientos más recientes primero. */
  listarMovimientos: (params?: {
    producto_id?: string;
    tipo?: string;
    almacen_id?: string;
    lote_id?: string;
    referencia_tipo?: string;
    referencia_id?: string;
    desde?: string;
    hasta?: string;
    limit?: number;
    offset?: number;
  }) => request<Movimiento[]>(`/movimientos${consulta({ ...params })}`),

  /** Kardex valorizado (pantalla Kardex): filas en orden cronológico con su
   * saldo acumulado, saldo inicial del período y totales. */
  kardex: (params: KardexParams) => request<Kardex>(`/kardex${consulta({ ...params })}`),

  /** URL del mismo Kardex como CSV para Excel (UTF-8 con BOM, separado por
   * comas). Sirve para un `<a href download>` o `window.open`. */
  kardexCsvUrl: (params: KardexParams) =>
    `${INVENTARIO_API_URL}/kardex${consulta({ ...params, formato: "csv" })}`,

  /** El CSV del Kardex como texto (para guardarlo con un Blob). */
  descargarKardexCsv: async (params: KardexParams): Promise<string> => {
    const response = await fetch(inventarioApi.kardexCsvUrl(params));
    if (!response.ok) throw new InventarioApiError(response.status, response.statusText);
    return response.text();
  },

  /** Valor del inventario total, por almacén y por categoría, y contadores
   * de alertas de stock (tarjetas de Productos y Almacenes). */
  valuacion: () => request<Valuacion>("/valuacion"),

  // --- Almacenes y transferencias ---------------------------------------------------------

  /** Principal primero; cada uno con productos, cantidad y valor. */
  listarAlmacenes: (params?: { activo?: boolean }) => request<Almacen[]>(`/almacenes${consulta({ ...params })}`),

  obtenerAlmacen: (id: string) => request<Almacen>(`/almacenes/${id}`),

  /** 409 si ya hay un almacén con ese nombre. */
  crearAlmacen: (payload: AlmacenPayload) =>
    request<Almacen>("/almacenes", { method: "POST", body: JSON.stringify(payload) }),

  actualizarAlmacen: (id: string, payload: AlmacenActualizarPayload) =>
    request<Almacen>(`/almacenes/${id}`, { method: "PUT", body: JSON.stringify(payload) }),

  /** Productos con existencia en el almacén (`incluir_cero` también los que quedaron en 0). */
  existenciasAlmacen: (id: string, params?: { q?: string; incluir_cero?: boolean }) =>
    request<ExistenciaAlmacen[]>(`/almacenes/${id}/existencias${consulta({ ...params })}`),

  /** Transfiere y aplica en el acto, todo o nada. 409 con `faltante` si una
   * línea no tiene existencia en el origen. */
  crearTransferencia: (payload: TransferenciaPayload) =>
    request<Transferencia>("/transferencias", { method: "POST", body: JSON.stringify(payload) }),

  /** Con `almacen_id`, las que salen de o llegan a ese almacén. */
  listarTransferencias: (params?: { almacen_id?: string; limit?: number; offset?: number }) =>
    request<Transferencia[]>(`/transferencias${consulta({ ...params })}`),

  obtenerTransferencia: (id: string) => request<Transferencia>(`/transferencias/${id}`),

  // --- Lotes ---------------------------------------------------------

  /** `dias` = días de alerta para "por_vencer" (por defecto los del servidor, 30). */
  listarLotes: (params?: {
    producto_id?: string;
    almacen_id?: string;
    estado?: EstadoLote;
    q?: string;
    con_existencia?: boolean;
    dias?: number;
    limit?: number;
    offset?: number;
  }) => request<Lote[]>(`/lotes${consulta({ ...params })}`),

  obtenerLote: (id: string) => request<Lote>(`/lotes/${id}`),

  /** Registra el lote (y activa `maneja_lotes` del producto); 409 si el
   * producto ya tiene un lote con ese número. */
  crearLote: (payload: LotePayload) =>
    request<Lote>("/lotes", { method: "POST", body: JSON.stringify(payload) }),

  /** Editar número/vencimiento/notas o bloquear ("Bloquear" de la pantalla). */
  actualizarLote: (id: string, payload: LoteActualizarPayload) =>
    request<Lote>(`/lotes/${id}`, { method: "PUT", body: JSON.stringify(payload) }),

  /** Lotes con existencia por vencer (dentro de `dias`) y vencidos. */
  alertasLotes: (params?: { dias?: number }) => request<AlertasLotes>(`/lotes/alertas${consulta({ ...params })}`),

  /** De qué lotes sacar `cantidad` (primero el que vence antes). Es lo mismo
   * que hará la venta si la línea no indica lote. */
  sugerirFefo: (params: { producto_id: string; cantidad: string; almacen_id?: string }) =>
    request<SugerenciaFefo>(`/lotes/fefo${consulta({ ...params })}`),

  // --- Ajustes ---------------------------------------------------------

  listarAjustes: (params?: {
    estado?: EstadoAjuste;
    almacen_id?: string;
    producto_id?: string;
    desde?: string;
    hasta?: string;
    limit?: number;
    offset?: number;
  }) => request<Ajuste[]>(`/ajustes${consulta({ ...params })}`),

  obtenerAjuste: (id: string) => request<Ajuste>(`/ajustes/${id}`),

  /** Crea el ajuste en "borrador" (sin efecto en el stock). Con `aplicar`
   * lo aplica en la misma llamada ("Aplicar ajuste" del prototipo): si no se
   * puede aplicar (409), tampoco se crea. */
  crearAjuste: (payload: AjustePayload, opciones?: { aplicar?: boolean }) =>
    request<Ajuste>(`/ajustes${consulta({ aplicar: opciones?.aplicar })}`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  /** Solo en "borrador" (409 si no). */
  actualizarAjuste: (id: string, payload: AjustePayload) =>
    request<Ajuste>(`/ajustes/${id}`, { method: "PUT", body: JSON.stringify(payload) }),

  /** Solo en "borrador" (409 si no). */
  eliminarAjuste: (id: string) => request<void>(`/ajustes/${id}`, { method: "DELETE" }),

  /** Genera los movimientos: salidas al costo promedio (validan existencia),
   * entradas recalculan el promedio. */
  aplicarAjuste: (id: string) => request<Ajuste>(`/ajustes/${id}/aplicar`, { method: "POST" }),

  /** "Revertir" del prototipo: movimientos inversos; 409 con `faltante` si
   * la mercancía que entró ya salió. */
  anularAjuste: (id: string) => request<Ajuste>(`/ajustes/${id}/anular`, { method: "POST" }),
};

export { InventarioApiError };
