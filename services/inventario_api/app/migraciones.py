"""Migraciones del esquema `inventario` (catálogo, compras, kardex y, desde
la Fase 4, almacenes, existencias por almacén, lotes, ajustes y
transferencias).

En producción la base es Supabase y los archivos de `db/init` NO se
ejecutan solos (ver docs/SUPABASE.md) -- por eso `inventario_api` aplica su
propio DDL al arrancar (lifespan de main.py), justo después de crear el
pool, igual que `erp_api` (ver services/erp_api/app/migraciones.py):

- `DDL_BASE` es exactamente `db/init/004_inventario.sql` (productos,
  proveedores, compras, movimientos). Solo se ejecuta si
  `inventario.movimientos` todavía no existe (Supabase recién creado): en
  una base que ya lo tiene NO se vuelve a correr, porque recrearía el
  índice único `movimientos_salida_factura_idx` que `DDL_FASE4` reemplaza
  (ver el comentario ahí).
- `DDL_FASE4` es exactamente `db/init/011_inventario_almacenes_lotes.sql`
  y se ejecuta en cada arranque: todo es idempotente (`IF NOT EXISTS`,
  `ADD COLUMN IF NOT EXISTS`, `INSERT ... WHERE NOT EXISTS` / `ON
  CONFLICT`). tests/test_migraciones.py verifica que los textos no se
  separen.

Para cambios de esquema futuros: agregar sentencias idempotentes al final
de `DDL_FASE4` (o un `DDL_FASE5` nuevo) y copiarlas también en un nuevo
`db/init/0NN_*.sql`.
"""
from __future__ import annotations

import logging

import asyncpg

logger = logging.getLogger(__name__)

# Clave arbitraria (pero fija) del advisory lock que serializa las
# migraciones si arrancan dos réplicas a la vez (ver erp_api: 7_007_001).
_LOCK_MIGRACIONES = 7_004_001

DDL_BASE = """
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
"""

DDL_FASE4 = """
-- Productos: datos que pide la pantalla de Productos. `tipo` 'servicio' no
-- lleva existencia (no genera movimientos ni se valida stock al facturar).
-- `tasa_itbis` es la tasa con que se factura: '18', '16', '0' o 'exento'.
-- `stock_minimo` > 0 activa la alerta de "bajo mínimo". `maneja_lotes`
-- hace que las salidas sin lote indicado se tomen por FEFO de los lotes.
ALTER TABLE inventario.productos
    ADD COLUMN IF NOT EXISTS tipo text NOT NULL DEFAULT 'bien'
        CHECK (tipo IN ('bien', 'servicio')),
    ADD COLUMN IF NOT EXISTS tasa_itbis text NOT NULL DEFAULT '18'
        CHECK (tasa_itbis IN ('18', '16', '0', 'exento')),
    ADD COLUMN IF NOT EXISTS codigo_barras text,
    ADD COLUMN IF NOT EXISTS categoria text,
    ADD COLUMN IF NOT EXISTS stock_minimo numeric(14, 4) NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS maneja_lotes boolean NOT NULL DEFAULT false;

CREATE UNIQUE INDEX IF NOT EXISTS productos_codigo_barras_idx
    ON inventario.productos (codigo_barras) WHERE codigo_barras IS NOT NULL;
CREATE INDEX IF NOT EXISTS productos_categoria_idx ON inventario.productos (categoria);

-- Almacenes (bodegas/sucursales). Siempre hay exactamente uno
-- `es_principal`: el que se usa cuando una operación no indica almacén
-- (facturas, compras y productos de antes de la Fase 4). Con
-- `permite_negativo` = false (lo normal) una transferencia o un ajuste de
-- salida no puede dejar la existencia del almacén por debajo de cero; la
-- salida por factura SÍ puede (el e-CF ya se envió a la DGII).
CREATE TABLE IF NOT EXISTS inventario.almacenes (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    nombre            text NOT NULL,
    direccion         text,
    es_principal      boolean NOT NULL DEFAULT false,
    permite_negativo  boolean NOT NULL DEFAULT false,
    activo            boolean NOT NULL DEFAULT true,
    creado_en         timestamptz NOT NULL DEFAULT now(),
    actualizado_en    timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS almacenes_nombre_idx ON inventario.almacenes (lower(nombre));
CREATE UNIQUE INDEX IF NOT EXISTS almacenes_principal_idx
    ON inventario.almacenes (es_principal) WHERE es_principal;

INSERT INTO inventario.almacenes (nombre, es_principal)
SELECT 'Principal', true
WHERE NOT EXISTS (SELECT 1 FROM inventario.almacenes WHERE es_principal);

-- Existencia de cada producto en cada almacén. `productos.stock_actual`
-- sigue siendo el TOTAL (suma de esta tabla) para que los consumidores de
-- siempre (facturas_api, el IR-2 de erp_api, la pantalla de productos)
-- sigan funcionando; el repositorio mantiene ambos en la misma transacción.
CREATE TABLE IF NOT EXISTS inventario.existencias (
    producto_id     uuid NOT NULL REFERENCES inventario.productos (id),
    almacen_id      uuid NOT NULL REFERENCES inventario.almacenes (id),
    cantidad        numeric(14, 4) NOT NULL DEFAULT 0,
    actualizado_en  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (producto_id, almacen_id)
);

CREATE INDEX IF NOT EXISTS existencias_almacen_idx ON inventario.existencias (almacen_id);

-- Lotes de un producto (número + vencimiento). `bloqueado` lo saca de las
-- sugerencias FEFO y de la toma automática. Por vencer / vencido NO se
-- guarda: se calcula al leer con la fecha de hoy.
CREATE TABLE IF NOT EXISTS inventario.lotes (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    producto_id        uuid NOT NULL REFERENCES inventario.productos (id),
    numero             text NOT NULL,
    fecha_vencimiento  date,
    bloqueado          boolean NOT NULL DEFAULT false,
    notas              text,
    creado_en          timestamptz NOT NULL DEFAULT now(),
    actualizado_en     timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS lotes_producto_numero_idx ON inventario.lotes (producto_id, numero);
CREATE INDEX IF NOT EXISTS lotes_vencimiento_idx ON inventario.lotes (fecha_vencimiento);

-- Cantidad de cada lote en cada almacén. La existencia del almacén
-- (inventario.existencias) puede ser mayor que la suma de sus lotes: la
-- diferencia es mercancía "sin lote" (p. ej. la de antes de usar lotes).
CREATE TABLE IF NOT EXISTS inventario.existencias_lote (
    lote_id         uuid NOT NULL REFERENCES inventario.lotes (id),
    almacen_id      uuid NOT NULL REFERENCES inventario.almacenes (id),
    cantidad        numeric(14, 4) NOT NULL DEFAULT 0,
    actualizado_en  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (lote_id, almacen_id)
);

CREATE INDEX IF NOT EXISTS existencias_lote_almacen_idx ON inventario.existencias_lote (almacen_id);

-- Kardex: almacén y lote de cada movimiento, la línea del documento de
-- origen (NumeroLinea de la factura), el número legible del documento
-- (e-NCF, AJ-0001, TR-0001...) y un correlativo que ordena los movimientos
-- de una misma transacción (comparten `creado_en`). `referencia_tipo` suma
-- 'transferencia' y 'lote' (entrada manual de un lote).
ALTER TABLE inventario.movimientos
    ADD COLUMN IF NOT EXISTS almacen_id uuid REFERENCES inventario.almacenes (id),
    ADD COLUMN IF NOT EXISTS lote_id uuid REFERENCES inventario.lotes (id),
    ADD COLUMN IF NOT EXISTS referencia_linea text,
    ADD COLUMN IF NOT EXISTS documento text,
    ADD COLUMN IF NOT EXISTS secuencia bigint GENERATED ALWAYS AS IDENTITY;

CREATE INDEX IF NOT EXISTS movimientos_almacen_idx
    ON inventario.movimientos (almacen_id, producto_id, creado_en);
CREATE INDEX IF NOT EXISTS movimientos_lote_idx ON inventario.movimientos (lote_id) WHERE lote_id IS NOT NULL;

-- Con almacén y lote por línea, una misma factura puede sacar el mismo
-- producto en varios movimientos (dos líneas, o un lote que no alcanza y se
-- completa con el siguiente por FEFO): el índice único por (factura,
-- producto) de 004 ya no aplica. La idempotencia de la salida por factura
-- pasa a ser por factura completa (advisory lock + "¿ya hay salidas de
-- esta factura?", ver `repository.registrar_salida_por_factura`).
DROP INDEX IF EXISTS inventario.movimientos_salida_factura_idx;

-- Movimientos de antes de la Fase 4: eran del único almacén que había.
UPDATE inventario.movimientos
SET almacen_id = (SELECT id FROM inventario.almacenes WHERE es_principal)
WHERE almacen_id IS NULL;

-- `stock_actual` manda sobre el total: si no coincide con la suma de
-- `existencias` (productos de antes de la Fase 4, o escritos por una
-- versión anterior del servicio) la diferencia se asigna al almacén
-- principal. En una base al día no cambia nada.
INSERT INTO inventario.existencias (producto_id, almacen_id, cantidad)
SELECT p.id, a.id, p.stock_actual - COALESCE(e.total, 0)
FROM inventario.productos p
CROSS JOIN inventario.almacenes a
LEFT JOIN (
    SELECT producto_id, sum(cantidad) AS total FROM inventario.existencias GROUP BY producto_id
) e ON e.producto_id = p.id
WHERE a.es_principal AND p.stock_actual <> COALESCE(e.total, 0)
ON CONFLICT (producto_id, almacen_id)
DO UPDATE SET cantidad = inventario.existencias.cantidad + EXCLUDED.cantidad, actualizado_en = now();

-- Compras: almacén que recibe la mercancía (NULL = principal) y lote de
-- cada línea (se crea al confirmar si no existe).
ALTER TABLE inventario.compras
    ADD COLUMN IF NOT EXISTS almacen_id uuid REFERENCES inventario.almacenes (id);
ALTER TABLE inventario.compra_items
    ADD COLUMN IF NOT EXISTS lote_numero text,
    ADD COLUMN IF NOT EXISTS lote_vencimiento date;

-- Ajustes de inventario: 'borrador' (editable, sin efecto) -> 'aplicado'
-- (genera los movimientos) -> 'anulado' (genera los movimientos inversos).
CREATE SEQUENCE IF NOT EXISTS inventario.ajustes_numero_seq;

CREATE TABLE IF NOT EXISTS inventario.ajustes (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    numero          text NOT NULL,
    fecha           date NOT NULL DEFAULT current_date,
    almacen_id      uuid NOT NULL REFERENCES inventario.almacenes (id),
    motivo          text NOT NULL
                    CHECK (motivo IN ('conteo_fisico', 'merma', 'dano', 'vencimiento', 'otro')),
    notas           text,
    estado          text NOT NULL DEFAULT 'borrador'
                    CHECK (estado IN ('borrador', 'aplicado', 'anulado')),
    creado_en       timestamptz NOT NULL DEFAULT now(),
    actualizado_en  timestamptz NOT NULL DEFAULT now(),
    aplicado_en     timestamptz,
    anulado_en      timestamptz
);

CREATE UNIQUE INDEX IF NOT EXISTS ajustes_numero_idx ON inventario.ajustes (numero);
CREATE INDEX IF NOT EXISTS ajustes_estado_idx ON inventario.ajustes (estado);
CREATE INDEX IF NOT EXISTS ajustes_creado_en_idx ON inventario.ajustes (creado_en DESC);

CREATE TABLE IF NOT EXISTS inventario.ajuste_items (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    ajuste_id       uuid NOT NULL REFERENCES inventario.ajustes (id) ON DELETE CASCADE,
    linea           integer NOT NULL,  -- orden de las líneas (se aplican en este orden)
    producto_id     uuid NOT NULL REFERENCES inventario.productos (id),
    tipo            text NOT NULL CHECK (tipo IN ('entrada', 'salida')),
    cantidad        numeric(14, 4) NOT NULL CHECK (cantidad > 0),
    costo_unitario  numeric(14, 4),  -- solo entradas; NULL = costo promedio actual
    lote_id         uuid REFERENCES inventario.lotes (id)
);

CREATE INDEX IF NOT EXISTS ajuste_items_ajuste_idx ON inventario.ajuste_items (ajuste_id);

-- Transferencias entre almacenes: se aplican al crearse (un par de
-- movimientos salida/entrada por línea, en una sola transacción).
CREATE SEQUENCE IF NOT EXISTS inventario.transferencias_numero_seq;

CREATE TABLE IF NOT EXISTS inventario.transferencias (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    numero              text NOT NULL,
    almacen_origen_id   uuid NOT NULL REFERENCES inventario.almacenes (id),
    almacen_destino_id  uuid NOT NULL REFERENCES inventario.almacenes (id),
    notas               text,
    creado_en           timestamptz NOT NULL DEFAULT now(),
    CHECK (almacen_origen_id <> almacen_destino_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS transferencias_numero_idx ON inventario.transferencias (numero);
CREATE INDEX IF NOT EXISTS transferencias_creado_en_idx ON inventario.transferencias (creado_en DESC);

CREATE TABLE IF NOT EXISTS inventario.transferencia_items (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    transferencia_id  uuid NOT NULL REFERENCES inventario.transferencias (id) ON DELETE CASCADE,
    linea             integer NOT NULL,
    producto_id       uuid NOT NULL REFERENCES inventario.productos (id),
    cantidad          numeric(14, 4) NOT NULL CHECK (cantidad > 0),
    lote_id           uuid REFERENCES inventario.lotes (id)
);

CREATE INDEX IF NOT EXISTS transferencia_items_transferencia_idx
    ON inventario.transferencia_items (transferencia_id);
"""


async def aplicar_migraciones(pool: asyncpg.Pool) -> None:
    """Crea (si no existe) el esquema `inventario` y lo lleva a la Fase 4
    (almacenes, existencias, lotes, ajustes, transferencias)."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock($1)", _LOCK_MIGRACIONES)
            if await conn.fetchval("SELECT to_regclass('inventario.movimientos')") is None:
                await conn.execute(DDL_BASE)
            await conn.execute(DDL_FASE4)
    logger.info("Esquema 'inventario' verificado/migrado")
