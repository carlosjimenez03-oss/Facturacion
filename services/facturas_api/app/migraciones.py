"""Migración mínima de `facturacion.facturas` al arrancar facturas_api.

La tabla la crea `db/init/002_facturas.sql` (Postgres local) o la carga
inicial a Supabase (docs/SUPABASE.md), donde nadie corre db/init después:
por eso los cambios de esquema de esta tabla se aplican aquí, idempotentes,
en el lifespan de main.py. `db/init/013_configuracion.sql` repite
`DDL_FACTURAS`, `db/init/017_contingencia.sql` repite `DDL_CONTINGENCIA`,
`db/init/018_pos_sin_conexion.sql` repite `DDL_IDEMPOTENCIA` y
`db/init/020_fase7_fiscal.sql` repite `DDL_FASE7` para el Postgres local (lo
verifica tests/test_migraciones.py).

Para cambios futuros: agregar sentencias idempotentes al final del DDL que
corresponda (`ADD COLUMN IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS`) o un
DDL nuevo con su propio db/init.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

import asyncpg

from libs.ecf_common import multiempresa_ddl
from libs.ecf_common.empresa import modo_sistema

logger = logging.getLogger(__name__)

# Clave fija del advisory lock (distinta de la de erp_api/inventario_api).
_LOCK_MIGRACIONES = 7_002_001

# Igual que erp_api: espera máxima por cada lock antes de rendirse y
# reintentar con espera creciente.
_LOCK_TIMEOUT = "5s"
_REINTENTOS = 5
_ESPERA_INICIAL_SEGUNDOS = 1.0
_ESPERA_MAXIMA_SEGUNDOS = 30.0

DDL_FACTURAS = """
-- Vendedor de la factura (configuracion.vendedores de erp_api, sin FK):
-- metadata de la aplicación, fuera de `datos` (que refleja el XSD del e-CF).
-- Se consulta el catálogo antes: en una base ya migrada no se toma ningún
-- lock de facturacion.facturas.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'facturacion' AND table_name = 'facturas' AND column_name = 'vendedor_id'
    ) THEN
        ALTER TABLE facturacion.facturas ADD COLUMN IF NOT EXISTS vendedor_id uuid;
    END IF;
    IF to_regclass('facturacion.facturas_vendedor_idx') IS NULL THEN
        CREATE INDEX IF NOT EXISTS facturas_vendedor_idx ON facturacion.facturas (vendedor_id);
    END IF;
END;
$$;
"""

# Modo contingencia (ver app.contingencia y docs/dgii/CONTINGENCIA.md). El
# estado nuevo de la factura, 'contingencia' (firmada y entregada al
# comprador, pendiente de envío a la DGII), no necesita cambiar ninguna
# restricción: `facturacion.facturas.estado` es text sin CHECK (ver
# db/init/002_facturas.sql).
DDL_CONTINGENCIA = """
-- Cuándo se firmó el XML que se envía (y que lleva la representación
-- impresa); si se emitió en contingencia (la leyenda de la RI no depende
-- del estado: se conserva tras aceptarse), el lote de envío diferido y los
-- intentos de envío. Se consulta el catálogo antes de cada ALTER TABLE /
-- CREATE INDEX para no tomar locks de facturacion.facturas en cada arranque.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'facturacion' AND table_name = 'facturas' AND column_name = 'ultimo_intento_envio'
    ) THEN
        ALTER TABLE facturacion.facturas
            ADD COLUMN IF NOT EXISTS firmado_en timestamptz,
            ADD COLUMN IF NOT EXISTS emitida_en_contingencia boolean NOT NULL DEFAULT false,
            ADD COLUMN IF NOT EXISTS contingencia_en timestamptz,
            ADD COLUMN IF NOT EXISTS lote_id text,
            ADD COLUMN IF NOT EXISTS intentos_envio integer NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS ultimo_intento_envio timestamptz;
    END IF;
    IF to_regclass('facturacion.facturas_contingencia_idx') IS NULL THEN
        CREATE INDEX IF NOT EXISTS facturas_contingencia_idx ON facturacion.facturas (firmado_en, creado_en)
            WHERE estado = 'contingencia';
    END IF;
    IF to_regclass('facturacion.facturas_lote_idx') IS NULL THEN
        CREATE INDEX IF NOT EXISTS facturas_lote_idx ON facturacion.facturas (lote_id) WHERE lote_id IS NOT NULL;
    END IF;
END;
$$;

-- Estado compartido del modo contingencia (una sola fila, id = 1): lo
-- actualizan los envíos que fallan por conectividad, los que salen bien y
-- las sondas de la tarea de fondo. `activa` hace que los envíos siguientes
-- vayan directo a contingencia tras una sonda corta, sin esperar el timeout.
CREATE TABLE IF NOT EXISTS facturacion.contingencia (
    id                    smallint PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    activa                boolean NOT NULL DEFAULT false,
    desde                 timestamptz,
    errores_consecutivos  integer NOT NULL DEFAULT 0,
    ultimo_intento        timestamptz,
    ultimo_error          text,
    ultimo_exito          timestamptz,
    actualizado_en        timestamptz NOT NULL DEFAULT now()
);
-- Sin columnas en ON CONFLICT: desde la multiempresa la clave es (empresa_id, id).
INSERT INTO facturacion.contingencia (id) VALUES (1) ON CONFLICT DO NOTHING;

-- Lotes de envío de las facturas en contingencia (A-0001, A-0002...), las
-- más antiguas primero. `estado`: en_cola -> enviando -> completado, o
-- interrumpido (se cayó la conexión a mitad: se retoma en la próxima
-- vuelta). Los contadores los recalcula app.contingencia de las facturas
-- del lote.
CREATE SEQUENCE IF NOT EXISTS facturacion.lotes_contingencia_seq;
CREATE TABLE IF NOT EXISTS facturacion.lotes_contingencia (
    id             text PRIMARY KEY,
    documentos     integer NOT NULL DEFAULT 0,
    enviados       integer NOT NULL DEFAULT 0,
    aceptados      integer NOT NULL DEFAULT 0,
    rechazados     integer NOT NULL DEFAULT 0,
    estado         text NOT NULL DEFAULT 'en_cola'
                   CHECK (estado IN ('en_cola', 'enviando', 'interrumpido', 'completado')),
    ultimo_error   text,
    creado_en      timestamptz NOT NULL DEFAULT now(),
    iniciado_en    timestamptz,
    completado_en  timestamptz,
    actualizado_en timestamptz NOT NULL DEFAULT now()
);
DO $$
BEGIN
    IF to_regclass('facturacion.lotes_contingencia_creado_idx') IS NULL THEN
        CREATE INDEX IF NOT EXISTS lotes_contingencia_creado_idx ON facturacion.lotes_contingencia (creado_en DESC);
    END IF;
END;
$$;
"""

# Clave de idempotencia de la creación (POST /facturas): la pone el POS en
# cada venta hecha sin conexión con el servidor (un UUID generado en el
# navegador al cobrar). Al sincronizar, un reintento con la misma clave
# devuelve la factura ya creada en vez de crear otra (ver
# secuencias_encf.crear_factura y docs/dgii/CONTINGENCIA.md, "POS sin
# conexión"). Igual que DDL_CONTINGENCIA: se consulta el catálogo antes de
# tocar la tabla.
DDL_IDEMPOTENCIA = """
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'facturacion' AND table_name = 'facturas' AND column_name = 'clave_idempotencia'
    ) THEN
        ALTER TABLE facturacion.facturas ADD COLUMN IF NOT EXISTS clave_idempotencia uuid;
    END IF;
    IF to_regclass('facturacion.facturas_clave_idempotencia_idx') IS NULL THEN
        CREATE UNIQUE INDEX IF NOT EXISTS facturas_clave_idempotencia_idx ON facturacion.facturas (clave_idempotencia)
            WHERE clave_idempotencia IS NOT NULL;
    END IF;
END;
$$;
"""


# Fase 7: reverso de la salida de inventario de las facturas que la DGII
# rechaza (app.envio.revertir_inventario) y el filtro por ambiente DGII de la
# lista de facturas (el ambiente se elige en Configuración, ver app.ambiente).
# El índice único del e-NCF por ambiente lo crea secuencias_encf al arrancar.
DDL_FASE7 = """
-- Reverso de inventario pendiente (inventario-api no respondió: lo reintenta
-- la tarea de fondo) y cuándo quedó hecho. Se consulta el catálogo antes del
-- ALTER TABLE / CREATE INDEX para no tomar locks de facturacion.facturas en
-- cada arranque.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'facturacion' AND table_name = 'facturas' AND column_name = 'inventario_revertido_en'
    ) THEN
        ALTER TABLE facturacion.facturas
            ADD COLUMN IF NOT EXISTS inventario_reverso_pendiente boolean NOT NULL DEFAULT false,
            ADD COLUMN IF NOT EXISTS inventario_revertido_en timestamptz;
    END IF;
    IF to_regclass('facturacion.facturas_ambiente_idx') IS NULL THEN
        CREATE INDEX IF NOT EXISTS facturas_ambiente_idx ON facturacion.facturas (ambiente, creado_en DESC);
    END IF;
    IF to_regclass('facturacion.facturas_reverso_pendiente_idx') IS NULL THEN
        CREATE INDEX IF NOT EXISTS facturas_reverso_pendiente_idx ON facturacion.facturas (actualizado_en)
            WHERE inventario_reverso_pendiente;
    END IF;
END;
$$;
"""


# Multiempresa (docs/MULTIEMPRESA.md, libs/ecf_common/multiempresa_ddl.py):
# facturas, auditoría de envíos y contingencia por empresa. La unicidad del
# e-NCF ya es por emisor (rnc_emisor, ambiente, encf: ver secuencias_encf) y
# el RNC de cada empresa es único; la clave de idempotencia del POS sigue
# siendo global (un UUID). `facturacion.envios_dgii` solo existe si la creó
# db/init/001 (o la carga inicial a Supabase). Mismo DDL en
# db/init/021_multiempresa.sql.
DDL_MULTIEMPRESA = """
SELECT configuracion.kontara_aislar(t.tabla::regclass, t.unicos)
FROM (VALUES
    ('facturacion.facturas', ARRAY[]::text[]),
    ('facturacion.envios_dgii', ARRAY[]::text[]),
    ('facturacion.contingencia', ARRAY['contingencia_pkey']),
    ('facturacion.lotes_contingencia', ARRAY[]::text[])
) AS t (tabla, unicos)
WHERE to_regclass(t.tabla) IS NOT NULL;

SELECT configuracion.kontara_conceder(ARRAY['facturacion']);
"""


async def _migrar(conn, settings) -> None:
    async with conn.transaction():
        await conn.execute(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'")
        # El lock compartido de la multiempresa PRIMERO (antes de tocar tablas).
        await multiempresa_ddl.tomar_lock(conn)
        await conn.execute("SELECT pg_advisory_xact_lock($1)", _LOCK_MIGRACIONES)
        await conn.execute(DDL_FACTURAS)
        await conn.execute(DDL_CONTINGENCIA)
        await conn.execute(DDL_IDEMPOTENCIA)
        await conn.execute(DDL_FASE7)
        await multiempresa_ddl.aplicar(
            conn,
            DDL_MULTIEMPRESA,
            emisor_rnc=settings.emisor_rnc,
            razon_social=settings.emisor_razon_social,
            admin_emails=settings.kontara_admin_emails,
        )


async def aplicar_migraciones(
    pool: asyncpg.Pool,
    *,
    reintentos: int = _REINTENTOS,
    dormir: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Con `lock_timeout`: si una tabla está ocupada (o hay un deadlock) suelta
    todo y reintenta con espera creciente, igual que erp_api."""
    from .config import get_settings

    settings = get_settings()
    espera = _ESPERA_INICIAL_SEGUNDOS
    for intento in range(1, reintentos + 1):
        try:
            with modo_sistema():
                async with pool.acquire() as conn:
                    await _migrar(conn, settings)
            break
        except (asyncpg.LockNotAvailableError, asyncpg.DeadlockDetectedError) as exc:
            if intento == reintentos:
                raise
            logger.warning(
                "Migración de facturas-api en espera (intento %s/%s, %s): reintentando en %ss",
                intento,
                reintentos,
                exc.__class__.__name__,
                espera,
            )
            await dormir(espera)
            espera = min(espera * 2, _ESPERA_MAXIMA_SEGUNDOS)
    logger.info(
        "Columnas de 'facturacion.facturas', tablas de contingencia, idempotencia, fase 7 y multiempresa verificadas"
    )
