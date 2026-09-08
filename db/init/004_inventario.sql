-- Esquema de control de inventario: catálogo de productos, proveedores,
-- compras (entrada de mercancía) y el kardex de movimientos. La salida de
-- mercancía por venta la registra `services/inventario_api` cuando
-- `facturas_api` le notifica que una factura se envió exitosamente a la
-- DGII (ver `facturacion.facturas.items_inventario` en
-- 005_facturas_items_inventario.sql y docs/DOKPLOY.md).
--
-- Igual que los demás archivos de db/init: se ejecuta una sola vez, la
-- primera vez que arranca el contenedor de Postgres con un volumen de datos
-- vacío. No editar este archivo tras su primer despliegue -- para cambios
-- de esquema futuros, agregar un nuevo 00N_*.sql.
--
-- A diferencia de `facturacion.*`, este esquema NO se expone vía PostgREST
-- (PGRST_DB_SCHEMA sigue apuntando solo a `facturacion`) -- todo el acceso
-- pasa por los endpoints propios de `inventario_api`.

CREATE SCHEMA IF NOT EXISTS inventario;

CREATE TABLE IF NOT EXISTS inventario.productos (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    sku             text NOT NULL,
    nombre          text NOT NULL,
    descripcion     text,
    unidad_medida   text,
    precio_venta    numeric(14, 4) NOT NULL DEFAULT 0,
    costo_promedio  numeric(14, 4) NOT NULL DEFAULT 0,
    stock_actual    numeric(14, 4) NOT NULL DEFAULT 0,
    activo          boolean NOT NULL DEFAULT true,
    creado_en       timestamptz NOT NULL DEFAULT now(),
    actualizado_en  timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS productos_sku_idx ON inventario.productos (sku);
CREATE INDEX IF NOT EXISTS productos_nombre_idx ON inventario.productos (nombre);
CREATE INDEX IF NOT EXISTS productos_activo_idx ON inventario.productos (activo);

CREATE TABLE IF NOT EXISTS inventario.proveedores (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    nombre          text NOT NULL,
    rnc             text,
    telefono        text,
    correo          text,
    direccion       text,
    activo          boolean NOT NULL DEFAULT true,
    creado_en       timestamptz NOT NULL DEFAULT now(),
    actualizado_en  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS proveedores_nombre_idx ON inventario.proveedores (nombre);

-- Entrada de mercancía. Una compra nace en 'borrador' (editable, sin efecto
-- en el stock) y al confirmarse ('confirmada') genera un movimiento
-- 'entrada' por cada línea y sube `productos.stock_actual` +
-- `productos.costo_promedio` (costo promedio ponderado) -- ver
-- `repository.confirmar_compra`. Una compra confirmada ya no se edita ni se
-- borra (igual que una factura fuera de 'borrador' en facturacion.facturas).
CREATE TABLE IF NOT EXISTS inventario.compras (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    proveedor_id     uuid REFERENCES inventario.proveedores (id),
    numero_documento text,
    estado           text NOT NULL DEFAULT 'borrador',  -- 'borrador' | 'confirmada'
    notas            text,
    creado_en        timestamptz NOT NULL DEFAULT now(),
    actualizado_en   timestamptz NOT NULL DEFAULT now(),
    confirmada_en    timestamptz
);

CREATE INDEX IF NOT EXISTS compras_estado_idx ON inventario.compras (estado);
CREATE INDEX IF NOT EXISTS compras_proveedor_idx ON inventario.compras (proveedor_id);
CREATE INDEX IF NOT EXISTS compras_creado_en_idx ON inventario.compras (creado_en DESC);

CREATE TABLE IF NOT EXISTS inventario.compra_items (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    compra_id       uuid NOT NULL REFERENCES inventario.compras (id) ON DELETE CASCADE,
    producto_id     uuid NOT NULL REFERENCES inventario.productos (id),
    cantidad        numeric(14, 4) NOT NULL,
    costo_unitario  numeric(14, 4) NOT NULL
);

CREATE INDEX IF NOT EXISTS compra_items_compra_idx ON inventario.compra_items (compra_id);
CREATE INDEX IF NOT EXISTS compra_items_producto_idx ON inventario.compra_items (producto_id);

-- Kardex: un renglón por cada movimiento de stock, sea entrada (compra) o
-- salida (venta/factura) o un ajuste manual futuro. `referencia_tipo` +
-- `referencia_id` apuntan al documento de origen (p. ej. 'compra' + el id
-- de inventario.compras, o 'factura' + el id de facturacion.facturas).
--
-- El índice único parcial sobre (referencia_tipo, referencia_id,
-- producto_id) para movimientos de salida por factura es lo que hace
-- idempotente a `POST /movimientos/salida-por-factura`: si facturas_api
-- reintenta la llamada (timeout de red, reintento manual) para la MISMA
-- factura, el segundo intento no descuenta el stock dos veces.
CREATE TABLE IF NOT EXISTS inventario.movimientos (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    producto_id      uuid NOT NULL REFERENCES inventario.productos (id),
    tipo             text NOT NULL,  -- 'entrada' | 'salida'
    cantidad         numeric(14, 4) NOT NULL,
    costo_unitario   numeric(14, 4),
    motivo           text,
    referencia_tipo  text,           -- 'compra' | 'factura' | 'ajuste'
    referencia_id    text,
    creado_en        timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS movimientos_producto_idx ON inventario.movimientos (producto_id, creado_en DESC);
CREATE INDEX IF NOT EXISTS movimientos_referencia_idx ON inventario.movimientos (referencia_tipo, referencia_id);
CREATE UNIQUE INDEX IF NOT EXISTS movimientos_salida_factura_idx
    ON inventario.movimientos (referencia_id, producto_id)
    WHERE referencia_tipo = 'factura' AND tipo = 'salida';
