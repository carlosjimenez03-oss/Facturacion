-- Modo contingencia (facturación electrónica sin conexión con la DGII).
--
-- 1. facturas_api: estado compartido de la contingencia, lotes de envío y
--    columnas nuevas de facturacion.facturas (firma, contingencia, lote). En
--    producción (Supabase) facturas_api lo aplica al arrancar con
--    exactamente este mismo DDL (services/facturas_api/app/migraciones.py,
--    DDL_CONTINGENCIA). El estado nuevo de la factura, 'contingencia', no
--    toca ninguna restricción: facturacion.facturas.estado no tiene CHECK.
-- 2. erp_api: el origen 'factura_papel' de los asientos automáticos de las
--    facturas en papel (services/erp_api/app/migraciones.py,
--    DDL_CONTINGENCIA).
--
-- Si cambia uno, cambie el otro -- lo verifican los tests de migraciones de
-- ambos servicios. Ver docs/dgii/CONTINGENCIA.md.

-- facturas_api: DDL_CONTINGENCIA
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

-- El rol web_anon (001_schema.sql) lee las tablas de facturacion vía PostgREST.
GRANT SELECT ON facturacion.contingencia, facturacion.lotes_contingencia TO web_anon;

-- erp_api: DDL_CONTINGENCIA
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'contabilidad.asientos'::regclass AND conname = 'asientos_origen_check'
          AND pg_get_constraintdef(oid) NOT LIKE '%factura_papel%'
    ) THEN
        ALTER TABLE contabilidad.asientos DROP CONSTRAINT asientos_origen_check;
        ALTER TABLE contabilidad.asientos ADD CONSTRAINT asientos_origen_check
            CHECK (origen IN ('manual', 'venta', 'nota_credito', 'nota_debito', 'cobro', 'compra', 'pago',
                              'movimiento', 'transferencia', 'proporcionalidad', 'depreciacion',
                              'factura_papel'));
    END IF;
END;
$$;
