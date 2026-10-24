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
- `MIGRACIONES`: cada una (`DDL_FASE4` = exactamente
  `db/init/011_inventario_almacenes_lotes.sql`) se aplica UNA vez y queda
  anotada en `inventario.schema_version` (la última sentencia de su DDL).
  Los arranques siguientes solo leen esa tabla: ningún ALTER TABLE, así que
  no toman AccessExclusiveLock sobre `productos`/`movimientos` ni frenan
  (o bloquean en deadlock) las ventas que están entrando. El DDL sigue
  siendo idempotente (`IF NOT EXISTS`...): correrlo de nuevo -- p. ej. el
  archivo de db/init en el Postgres local -- no cambia nada.
  tests/test_migraciones.py verifica que los textos no se separen.

La vez que sí hay que migrar (despliegue de la versión nueva), la
transacción:
- usa `lock_timeout` de 5 s (advisory lock entre réplicas incluido);
- toma de entrada, con un solo `LOCK TABLE`, el AccessExclusiveLock de las
  tablas existentes que va a alterar (`tablas`), esperando por cada una
  MENOS que `deadlock_timeout` (`_SQL_ESPERA_LOCK_MS`). Si mientras la
  migración tiene unas tablas y espera otra, una operación en curso pide
  una de las suyas (p. ej. una salida por factura que ya leyó
  `movimientos` y bloquea `productos`), es la migración la que se rinde
  primero (lock_timeout) -- antes de que el detector de deadlocks de la
  otra sesión la elija a ella como víctima --, suelta todo y reintenta con
  espera creciente (`_REINTENTOS`). No se usa NOWAIT: con ventas entrando
  sin parar nunca encontraría las tablas libres; esperando, su pedido entra
  en la cola de locks y las transacciones nuevas esperan detrás de ella.

Para cambios de esquema futuros: un `DDL_FASE5` nuevo que termine
anotando su versión en `inventario.schema_version`, agregado a
`MIGRACIONES` (con las tablas existentes que altera), y copiado tal cual en
un nuevo `db/init/0NN_*.sql`.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import NamedTuple

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

-- Un código de barras vacío es "sin código" (NULL): si no, el índice único
-- de abajo solo admitiría un producto con ''.
UPDATE inventario.productos SET codigo_barras = NULL WHERE btrim(codigo_barras) = '';

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
-- (e-NCF, AJ-0001, TR-0001...) y un correlativo `secuencia`, que es el
-- orden real de los movimientos: se asigna al insertar, después de bloquear
-- el producto (stock.py), así que para un mismo producto sigue el orden en
-- que se aplicaron aunque una transacción que empezó antes confirme
-- después. `referencia_tipo` suma 'transferencia' y 'lote' (entrada manual
-- de un lote).
ALTER TABLE inventario.movimientos
    ADD COLUMN IF NOT EXISTS almacen_id uuid REFERENCES inventario.almacenes (id),
    ADD COLUMN IF NOT EXISTS lote_id uuid REFERENCES inventario.lotes (id),
    ADD COLUMN IF NOT EXISTS referencia_linea text,
    ADD COLUMN IF NOT EXISTS documento text,
    ADD COLUMN IF NOT EXISTS secuencia bigint GENERATED ALWAYS AS IDENTITY;

-- `creado_en` con la hora real del INSERT (no la del inicio de la
-- transacción, que es lo que da now()): la fecha del Kardex coincide con
-- el orden de `secuencia`.
ALTER TABLE inventario.movimientos ALTER COLUMN creado_en SET DEFAULT clock_timestamp();

CREATE INDEX IF NOT EXISTS movimientos_almacen_idx
    ON inventario.movimientos (almacen_id, producto_id, creado_en);
CREATE INDEX IF NOT EXISTS movimientos_lote_idx ON inventario.movimientos (lote_id) WHERE lote_id IS NOT NULL;

-- Con almacén y lote por línea, una misma factura puede sacar el mismo
-- producto en varios movimientos (dos líneas, o un lote que no alcanza y se
-- completa con el siguiente por FEFO): el índice único por (factura,
-- producto) de 004 ya no aplica. La idempotencia de la salida por factura
-- pasa a ser por factura completa (advisory lock + "¿ya hay salidas de
-- esta factura?", ver `repository.registrar_salida_por_factura`).
--
-- OJO al desplegar: el código de antes de la Fase 4 hace
-- `ON CONFLICT (referencia_id, producto_id) WHERE ...` contra este índice;
-- sin él TODA salida por factura de una réplica vieja falla. No hay forma
-- de conservar un índice que esa sentencia acepte y que a la vez permita
-- varios movimientos por factura × producto, así que la versión vieja y la
-- nueva no deben atender a la vez (ver docs/INVENTARIO.md, "Despliegue").
DROP INDEX IF EXISTS inventario.movimientos_salida_factura_idx;

-- Movimientos de antes de la Fase 4: eran del único almacén que había.
UPDATE inventario.movimientos
SET almacen_id = (SELECT id FROM inventario.almacenes WHERE es_principal)
WHERE almacen_id IS NULL;

-- `stock_actual` manda sobre el total: los productos de antes de la Fase 4
-- no tienen `existencias`, así que su existencia completa se asigna al
-- almacén principal. Corre una sola vez (con la migración); una diferencia
-- que aparezca después NO se corrige sola: el arranque la reporta en el log
-- (`migraciones.verificar_existencias`) para revisarla a mano.
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

-- Migraciones ya aplicadas (el número es el del archivo de db/init). Con la
-- fila 11 presente, inventario-api ya no vuelve a correr este DDL al
-- arrancar (cada ALTER TABLE toma un AccessExclusiveLock sobre productos y
-- movimientos aunque no cambie nada).
CREATE TABLE IF NOT EXISTS inventario.schema_version (
    version      integer PRIMARY KEY,
    descripcion  text NOT NULL,
    aplicada_en  timestamptz NOT NULL DEFAULT now()
);

INSERT INTO inventario.schema_version (version, descripcion)
VALUES (11, '011_inventario_almacenes_lotes')
ON CONFLICT (version) DO NOTHING;
"""


class Migracion(NamedTuple):
    version: int
    ddl: str
    # Tablas que ya existían antes de esta migración y que su DDL altera:
    # se bloquean todas de entrada (ver el docstring del módulo).
    tablas: tuple[str, ...]


MIGRACIONES: tuple[Migracion, ...] = (
    Migracion(
        11,
        DDL_FASE4,
        (
            "inventario.productos",
            "inventario.movimientos",
            "inventario.compras",
            "inventario.compra_items",
        ),
    ),
)

VERSION_ACTUAL = MIGRACIONES[-1].version

_LOCK_TIMEOUT = "5s"
# Espera máxima por cada tabla del LOCK TABLE inicial: 80 % de
# `deadlock_timeout` (1 s por defecto), entre 50 y 900 ms.
_SQL_ESPERA_LOCK_MS = """
    SELECT GREATEST(LEAST(
        extract(epoch FROM current_setting('deadlock_timeout')::interval) * 1000 * 0.8, 900), 50)::int
"""
_REINTENTOS = 8
_ESPERA_INICIAL_SEGUNDOS = 0.5
_ESPERA_MAXIMA_SEGUNDOS = 15.0


async def version_aplicada(conn: asyncpg.Connection) -> int:
    """Última migración anotada en `inventario.schema_version` (0 si la
    tabla todavía no existe: base nueva o de antes de anotar versiones)."""
    if await conn.fetchval("SELECT to_regclass('inventario.schema_version')") is None:
        return 0
    return await conn.fetchval("SELECT COALESCE(max(version), 0) FROM inventario.schema_version")


async def _migrar(conn: asyncpg.Connection) -> list[int]:
    """Un intento: aplica las migraciones pendientes en una sola
    transacción. Devuelve las versiones aplicadas ([] si no había nada)."""
    # Camino normal (base ya al día): solo una lectura, sin locks de tabla.
    if await version_aplicada(conn) >= VERSION_ACTUAL:
        return []
    aplicadas: list[int] = []
    async with conn.transaction():
        await conn.execute(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'")
        await conn.execute("SELECT pg_advisory_xact_lock($1)", _LOCK_MIGRACIONES)
        # Otra réplica pudo migrar mientras esperábamos el advisory lock.
        actual = await version_aplicada(conn)
        if actual >= VERSION_ACTUAL:
            return []
        if await conn.fetchval("SELECT to_regclass('inventario.movimientos')") is None:
            await conn.execute(DDL_BASE)
        for migracion in MIGRACIONES:
            if migracion.version <= actual:
                continue
            espera_ms = await conn.fetchval(_SQL_ESPERA_LOCK_MS)
            await conn.execute(f"SET LOCAL lock_timeout = '{espera_ms}ms'")
            await conn.execute(f"LOCK TABLE {', '.join(migracion.tablas)} IN ACCESS EXCLUSIVE MODE")
            await conn.execute(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'")
            await conn.execute(migracion.ddl)
            aplicadas.append(migracion.version)
    return aplicadas


async def verificar_existencias(pool: asyncpg.Pool) -> int:
    """Reporta (log WARNING, sin corregir nada) los productos cuyo
    `stock_actual` no coincide con la suma de `existencias` por almacén --
    p. ej. salidas que escribió una réplica de antes de la Fase 4 durante
    un despliegue. Solo lee. Devuelve cuántos hay."""
    async with pool.acquire() as conn:
        filas = await conn.fetch(
            """
            SELECT p.sku, p.stock_actual, COALESCE(e.total, 0) AS en_almacenes
            FROM inventario.productos p
            LEFT JOIN (
                SELECT producto_id, sum(cantidad) AS total FROM inventario.existencias GROUP BY producto_id
            ) e ON e.producto_id = p.id
            WHERE p.stock_actual <> COALESCE(e.total, 0)
            ORDER BY p.sku
            """
        )
    if filas:
        detalle = ", ".join(
            f"{fila['sku']} (stock_actual {fila['stock_actual']}, almacenes {fila['en_almacenes']})"
            for fila in filas[:20]
        )
        logger.warning(
            "%s producto(s) con stock_actual distinto de la suma por almacén (no se corrige solo; "
            "ver docs/INVENTARIO.md, 'Despliegue'): %s%s",
            len(filas),
            detalle,
            "..." if len(filas) > 20 else "",
        )
    return len(filas)


async def aplicar_migraciones(
    pool: asyncpg.Pool,
    *,
    reintentos: int = _REINTENTOS,
    dormir: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> list[int]:
    """Crea (si no existe) el esquema `inventario` y le aplica las
    migraciones pendientes; reintenta con espera creciente si una tabla
    está ocupada (lock_timeout) o si cae en un deadlock. Al final
    verifica (solo log) que `stock_actual` cuadre con las existencias.
    Devuelve las versiones aplicadas en este arranque."""
    espera = _ESPERA_INICIAL_SEGUNDOS
    aplicadas: list[int] = []
    for intento in range(1, reintentos + 1):
        try:
            async with pool.acquire() as conn:
                aplicadas = await _migrar(conn)
            break
        except (asyncpg.LockNotAvailableError, asyncpg.DeadlockDetectedError) as exc:
            if intento == reintentos:
                raise
            logger.warning(
                "Migración de 'inventario' en espera (intento %s/%s, %s): reintentando en %ss",
                intento,
                reintentos,
                exc.__class__.__name__,
                espera,
            )
            await dormir(espera)
            espera = min(espera * 2, _ESPERA_MAXIMA_SEGUNDOS)
    if aplicadas:
        logger.info("Esquema 'inventario' migrado a la versión %s", aplicadas[-1])
    else:
        logger.info("Esquema 'inventario' al día (versión %s)", VERSION_ACTUAL)
    try:
        await verificar_existencias(pool)
    except asyncpg.PostgresError:
        logger.exception("No se pudo verificar la consistencia de existencias")
    return aplicadas
