"""Migraciones del esquema `ventas` (clientes, cotizaciones, cobros).

En producción la base es Supabase y los archivos de `db/init` NO se
ejecutan solos (ver docs/SUPABASE.md) -- por eso `erp_api` aplica su propio
DDL al arrancar (lifespan de main.py), justo después de crear el pool. Todo
es idempotente (`IF NOT EXISTS`), así que correrlo en cada arranque es
seguro. `db/init/007_ventas.sql` contiene exactamente el mismo DDL para el
Postgres local de desarrollo (lo verifica tests/test_migraciones.py).

Para cambios de esquema futuros: agregar sentencias idempotentes al final
de `DDL_VENTAS` (p. ej. `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`) y
copiarlas también en un nuevo `db/init/00N_*.sql`.
"""
from __future__ import annotations

import logging

import asyncpg

logger = logging.getLogger(__name__)

# Clave arbitraria (pero fija) del advisory lock que serializa las
# migraciones: si arrancan dos réplicas a la vez, `CREATE TABLE IF NOT
# EXISTS` concurrente puede fallar con una violación de unicidad en el
# catálogo de Postgres.
_LOCK_MIGRACIONES = 7_007_001

DDL_VENTAS = """
CREATE SCHEMA IF NOT EXISTS ventas;

-- Contactos (clientes y proveedores). `rnc` guarda solo dígitos (9 = RNC,
-- 11 = cédula), letras y dígitos si es pasaporte, o NULL para consumidores
-- sin identificar.
CREATE TABLE IF NOT EXISTS ventas.clientes (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tipo                 text NOT NULL DEFAULT 'cliente'
                         CHECK (tipo IN ('cliente', 'proveedor', 'ambos')),
    tipo_identificacion  text CHECK (tipo_identificacion IN ('rnc', 'cedula', 'pasaporte')),
    rnc                  text,
    nombre               text NOT NULL,
    correo               text,
    telefono             text,
    direccion            text,
    condicion_pago_dias  integer NOT NULL DEFAULT 0,
    limite_credito       numeric(14, 2),
    notas                text,
    activo               boolean NOT NULL DEFAULT true,
    creado_en            timestamptz NOT NULL DEFAULT now(),
    actualizado_en       timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS clientes_rnc_idx ON ventas.clientes (rnc) WHERE rnc IS NOT NULL;
CREATE INDEX IF NOT EXISTS clientes_nombre_idx ON ventas.clientes (nombre);

-- Correlativo legible de las cotizaciones ("COT-0001").
CREATE SEQUENCE IF NOT EXISTS ventas.cotizaciones_numero_seq;

-- Cotizaciones. `items` es un array jsonb con las líneas ya calculadas
-- (importe por línea) y los totales se guardan calculados por el backend.
-- `cliente_nombre`/`cliente_rnc` son una copia del cliente al momento de
-- cotizar. 'vencida' NO se guarda: se calcula al leer (abierta + vence
-- pasado).
CREATE TABLE IF NOT EXISTS ventas.cotizaciones (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    numero          text NOT NULL,
    cliente_id      uuid REFERENCES ventas.clientes (id) ON DELETE RESTRICT,
    cliente_nombre  text NOT NULL,
    cliente_rnc     text,
    fecha           date NOT NULL,
    vence           date NOT NULL,
    items           jsonb NOT NULL DEFAULT '[]'::jsonb,
    notas           text,
    subtotal        numeric(14, 2) NOT NULL DEFAULT 0,
    descuentos      numeric(14, 2) NOT NULL DEFAULT 0,
    itbis_total     numeric(14, 2) NOT NULL DEFAULT 0,
    total           numeric(14, 2) NOT NULL DEFAULT 0,
    estado          text NOT NULL DEFAULT 'abierta'
                    CHECK (estado IN ('abierta', 'aceptada', 'rechazada', 'facturada')),
    factura_id      uuid,  -- facturacion.facturas.id (sin FK: ese esquema es de facturas_api)
    creado_en       timestamptz NOT NULL DEFAULT now(),
    actualizado_en  timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS cotizaciones_numero_idx ON ventas.cotizaciones (numero);
CREATE INDEX IF NOT EXISTS cotizaciones_cliente_idx ON ventas.cotizaciones (cliente_id);
CREATE INDEX IF NOT EXISTS cotizaciones_estado_idx ON ventas.cotizaciones (estado);
CREATE INDEX IF NOT EXISTS cotizaciones_creado_en_idx ON ventas.cotizaciones (creado_en DESC);

-- Cobros (pagos recibidos) de facturas a crédito de facturacion.facturas.
-- Un cobro no se borra: se anula (`anulado`), y deja de contar en lo
-- cobrado de la factura.
CREATE TABLE IF NOT EXISTS ventas.cobros (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    factura_id      uuid NOT NULL,  -- facturacion.facturas.id (sin FK, solo lectura)
    fecha           date NOT NULL,
    monto           numeric(14, 2) NOT NULL CHECK (monto > 0),
    metodo          text NOT NULL
                    CHECK (metodo IN ('efectivo', 'transferencia', 'tarjeta', 'cheque')),
    cuenta_destino  text,
    referencia      text,
    notas           text,
    anulado         boolean NOT NULL DEFAULT false,
    anulado_en      timestamptz,
    creado_en       timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS cobros_factura_idx ON ventas.cobros (factura_id);
CREATE INDEX IF NOT EXISTS cobros_fecha_idx ON ventas.cobros (fecha DESC);
"""


async def aplicar_migraciones(pool: asyncpg.Pool) -> None:
    """Crea (si no existen) el esquema `ventas` y sus tablas/índices."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock($1)", _LOCK_MIGRACIONES)
            await conn.execute(DDL_VENTAS)
    logger.info("Esquema 'ventas' verificado/creado")
