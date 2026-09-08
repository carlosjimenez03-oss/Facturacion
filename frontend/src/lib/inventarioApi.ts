import type { Compra, CompraItem, Movimiento, Producto, Proveedor } from "./types";

export const INVENTARIO_API_URL =
  (import.meta.env.VITE_INVENTARIO_API_URL as string | undefined) ?? "http://localhost:8012";

class InventarioApiError extends Error {
  constructor(
    public status: number,
    message: string,
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
    try {
      const body = await response.json();
      detail = body.detail ?? JSON.stringify(body);
    } catch {
      // sin cuerpo JSON, se mantiene statusText
    }
    throw new InventarioApiError(response.status, typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return response.json() as Promise<T>;
}

export interface ProductoPayload {
  sku: string;
  nombre: string;
  descripcion?: string | null;
  unidad_medida?: string | null;
  precio_venta: string;
  stock_inicial?: string;
}

export interface ProductoActualizarPayload {
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
  items: CompraItem[];
}

export const inventarioApi = {
  listarProductos: (params?: { q?: string; activo?: boolean }) => {
    const query = new URLSearchParams();
    if (params?.q) query.set("q", params.q);
    if (params?.activo !== undefined) query.set("activo", String(params.activo));
    const qs = query.toString();
    return request<Producto[]>(`/productos${qs ? `?${qs}` : ""}`);
  },

  obtenerProducto: (id: string) => request<Producto>(`/productos/${id}`),

  crearProducto: (payload: ProductoPayload) =>
    request<Producto>("/productos", { method: "POST", body: JSON.stringify(payload) }),

  actualizarProducto: (id: string, payload: ProductoActualizarPayload) =>
    request<Producto>(`/productos/${id}`, { method: "PUT", body: JSON.stringify(payload) }),

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

  confirmarCompra: (id: string) => request<Compra>(`/compras/${id}/confirmar`, { method: "POST" }),

  listarMovimientos: (params?: { producto_id?: string; tipo?: string }) => {
    const query = new URLSearchParams();
    if (params?.producto_id) query.set("producto_id", params.producto_id);
    if (params?.tipo) query.set("tipo", params.tipo);
    const qs = query.toString();
    return request<Movimiento[]>(`/movimientos${qs ? `?${qs}` : ""}`);
  },
};

export { InventarioApiError };
