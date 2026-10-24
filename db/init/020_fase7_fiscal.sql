-- Fase 7 · fiscal: ambiente DGII elegido en Configuración, secuencias e-NCF
-- por ambiente y reverso de inventario de las facturas rechazadas.
--
-- 1. erp_api: tablas `configuracion.ambiente_dgii` (ambiente activo) y
--    `configuracion.ambiente_dgii_cambios` (auditoría), y la clave
--    (ambiente, tipo_ecf) de `configuracion.secuencias`. En producción
--    (Supabase) erp_api lo aplica al arrancar con exactamente este mismo DDL
--    (services/erp_api/app/migraciones_configuracion.py, DDL_AMBIENTE).
-- 2. facturas_api: columnas del reverso de inventario e índice por ambiente
--    de facturacion.facturas (services/facturas_api/app/migraciones.py,
--    DDL_FASE7). El índice único del e-NCF por (rnc_emisor, ambiente, encf)
--    lo crea facturas_api al arrancar (app/secuencias_encf.py).
--
-- Lo verifican los test_migraciones.py de ambos servicios.

-- erp_api: DDL_AMBIENTE
-- Ambiente DGII activo (una sola fila: 'test' = TesteCF, 'qa' = CerteCF,
-- 'production' = eCF). Sin fila, cada servicio usa su APP_ENV. Lo cambia
-- un administrador en Configuración → Ambiente DGII.
CREATE TABLE IF NOT EXISTS configuracion.ambiente_dgii (
    id               smallint PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    ambiente         text NOT NULL CHECK (ambiente IN ('test', 'qa', 'production')),
    cambiado_por_id  text,           -- usuario de Supabase Auth (NULL: sin inicio de sesión configurado)
    cambiado_por     text,           -- su correo
    cambiado_en      timestamptz NOT NULL DEFAULT now()
);

-- Auditoría: cada cambio de ambiente, quién y cuándo (nunca se borra).
CREATE TABLE IF NOT EXISTS configuracion.ambiente_dgii_cambios (
    id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    anterior         text NOT NULL,
    nuevo            text NOT NULL,
    usuario_id       text,
    usuario_correo   text,
    creado_en        timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ambiente_dgii_cambios_creado_idx ON configuracion.ambiente_dgii_cambios (creado_en DESC);

-- Secuencias e-NCF por ambiente: la DGII autoriza rangos distintos en
-- TesteCF, CerteCF y eCF. La clave pasa de (tipo_ecf) a (ambiente, tipo_ecf).
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'configuracion' AND table_name = 'secuencias' AND column_name = 'ambiente'
    ) THEN
        ALTER TABLE configuracion.secuencias ADD COLUMN IF NOT EXISTS ambiente text;
        UPDATE configuracion.secuencias
            SET ambiente = COALESCE(NULLIF(current_setting('kontara.ambiente_defecto', true), ''), 'test');
        ALTER TABLE configuracion.secuencias ALTER COLUMN ambiente SET NOT NULL;
        ALTER TABLE configuracion.secuencias ADD CONSTRAINT secuencias_ambiente_check
            CHECK (ambiente IN ('test', 'qa', 'production'));
        ALTER TABLE configuracion.secuencias DROP CONSTRAINT IF EXISTS secuencias_pkey;
        ALTER TABLE configuracion.secuencias ADD CONSTRAINT secuencias_pkey PRIMARY KEY (ambiente, tipo_ecf);
    END IF;
END;
$$;

-- facturas_api: DDL_FASE7
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
