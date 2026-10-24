"""Migración mínima de `facturacion.facturas` al arrancar facturas_api.

La tabla la crea `db/init/002_facturas.sql` (Postgres local) o la carga
inicial a Supabase (docs/SUPABASE.md), donde nadie corre db/init después:
por eso los cambios de esquema de esta tabla se aplican aquí, idempotentes,
en el lifespan de main.py. `db/init/013_configuracion.sql` repite el mismo
DDL para el Postgres local (lo verifica tests/test_migraciones.py).

Para cambios futuros: agregar sentencias idempotentes al final de
`DDL_FACTURAS` (`ADD COLUMN IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS`).
"""
from __future__ import annotations

import logging

import asyncpg

logger = logging.getLogger(__name__)

# Clave fija del advisory lock (distinta de la de erp_api/inventario_api).
_LOCK_MIGRACIONES = 7_002_001

DDL_FACTURAS = """
-- Vendedor de la factura (configuracion.vendedores de erp_api, sin FK):
-- metadata de la aplicación, fuera de `datos` (que refleja el XSD del e-CF).
ALTER TABLE facturacion.facturas ADD COLUMN IF NOT EXISTS vendedor_id uuid;
CREATE INDEX IF NOT EXISTS facturas_vendedor_idx ON facturacion.facturas (vendedor_id);
"""


async def aplicar_migraciones(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock($1)", _LOCK_MIGRACIONES)
            await conn.execute(DDL_FACTURAS)
    logger.info("Columnas de 'facturacion.facturas' verificadas")
