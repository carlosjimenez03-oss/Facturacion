-- Padrón de RNC de la DGII (docs/PADRON_RNC.md). En producción (Supabase)
-- erp_api aplica este mismo DDL al arrancar (services/erp_api/app/migraciones.py,
-- DDL_PADRON). Requiere 021_multiempresa.sql (configuracion.kontara_conceder):
-- las tablas son globales (sin empresa_id ni RLS) y los roles kontara_app y
-- kontara_todas solo reciben permisos sobre ellas.

-- Padrón de RNC de la DGII (docs/PADRON_RNC.md): el listado público de
-- contribuyentes (DGII_RNC.zip) que se importa una vez al día para llenar
-- razón social, nombre comercial, actividad, estado y régimen al verificar
-- un RNC o una cédula. Es GLOBAL (de la instalación, igual para todas las
-- empresas): sin empresa_id ni RLS. Se reemplaza entero en una sola
-- transacción (COPY a una tabla temporal y luego altas, cambios y bajas):
-- quien lee ve el padrón anterior completo hasta el COMMIT. `rnc` solo
-- dígitos (9 = RNC, 11 = cédula); `actualizado_en` = cuándo cambió la fila.
CREATE TABLE IF NOT EXISTS configuracion.padron_rnc (
    rnc               text PRIMARY KEY,
    razon_social      text NOT NULL,
    nombre_comercial  text,
    actividad         text,
    estado            text,
    regimen           text,
    fecha_inicio      date,
    actualizado_en    timestamptz NOT NULL DEFAULT now()
);

-- Una sola fila: la última importación que terminó bien (importado_en,
-- filas, origen) y el último intento (intentado_en; error/error_en si
-- falló: el padrón anterior queda intacto).
CREATE TABLE IF NOT EXISTS configuracion.padron_rnc_estado (
    id                 smallint PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    importado_en       timestamptz,
    filas              integer,
    origen             text,
    nuevas             integer,
    cambiadas          integer,
    eliminadas         integer,
    duracion_segundos  numeric(10, 1),
    intentado_en       timestamptz,
    error              text,
    error_en           timestamptz
);

SELECT configuracion.kontara_conceder(ARRAY['configuracion']);
