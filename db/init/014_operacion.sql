-- Fase 6 · Operación de erp-api: Mis tareas (tareas manuales y el estado
-- -- completada / pospuesta -- de las tareas automáticas) y las facturas en
-- papel del Centro de sincronización. Las tareas automáticas, el estado de
-- las sincronizaciones y los Reportes NO guardan nada: se calculan al leer,
-- de los datos de cada módulo.
--
-- Igual que 007-010: este archivo es para el Postgres local de desarrollo.
-- En producción (Supabase) services/erp_api lo crea por sí mismo al
-- arrancar, con exactamente este mismo DDL (services/erp_api/app/
-- migraciones.py, DDL_OPERACION). Si cambia uno, cambie el otro -- lo
-- verifica services/erp_api/tests/test_migraciones.py.

CREATE SCHEMA IF NOT EXISTS operacion;

-- Tareas que el usuario agrega a mano. `pospuesta_hasta` la saca de las
-- pendientes hasta esa fecha (inclusive).
CREATE TABLE IF NOT EXISTS operacion.tareas (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    titulo           text NOT NULL,
    descripcion      text,
    vence            date,
    prioridad        text NOT NULL DEFAULT 'media' CHECK (prioridad IN ('alta', 'media', 'baja')),
    completada       boolean NOT NULL DEFAULT false,
    completada_en    timestamptz,
    pospuesta_hasta  date,
    creado_en        timestamptz NOT NULL DEFAULT now(),
    actualizado_en   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS tareas_completada_idx ON operacion.tareas (completada, vence);

-- Estado de una tarea automática por su clave (p. ej. 'declaracion:it1:202609',
-- 'factura_rechazada:<id>'): completada o pospuesta. Cuando la condición
-- que la genera desaparece, la fila se borra al listar
-- (`tareas_repository.listar_tareas`): si vuelve a darse, la tarea reaparece.
CREATE TABLE IF NOT EXISTS operacion.tareas_automaticas (
    clave            text PRIMARY KEY,
    completada_en    timestamptz,
    pospuesta_hasta  date,
    actualizado_en   timestamptz NOT NULL DEFAULT now()
);

-- Facturas emitidas en papel (NCF serie B) durante una caída total del
-- sistema o de la DGII (Centro de sincronización). Los montos son los del
-- comprobante, por tasa de ITBIS; `forma_pago` es la del 607 (1 efectivo,
-- 2 cheque/transferencia, 3 tarjeta, 4 crédito, 5 bonos, 6 permuta, 7 otras).
-- `encf_reemplazo` es el e-CF que la sustituye al restablecerse el servicio
-- (código de modificación 4). Pensadas para alimentar luego el 607 y la
-- contabilidad.
CREATE TABLE IF NOT EXISTS operacion.facturas_papel (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    ncf              text NOT NULL,
    fecha            date NOT NULL,
    cliente_rnc      text,
    cliente_nombre   text,
    gravado_18       numeric(14, 2) NOT NULL DEFAULT 0,
    gravado_16       numeric(14, 2) NOT NULL DEFAULT 0,
    gravado_0        numeric(14, 2) NOT NULL DEFAULT 0,
    exento           numeric(14, 2) NOT NULL DEFAULT 0,
    itbis_18         numeric(14, 2) NOT NULL DEFAULT 0,
    itbis_16         numeric(14, 2) NOT NULL DEFAULT 0,
    total            numeric(14, 2) NOT NULL DEFAULT 0,
    forma_pago       smallint NOT NULL DEFAULT 1 CHECK (forma_pago BETWEEN 1 AND 7),
    notas            text,
    encf_reemplazo   text,
    creado_en        timestamptz NOT NULL DEFAULT now(),
    actualizado_en   timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS facturas_papel_ncf_idx ON operacion.facturas_papel (upper(ncf));
CREATE INDEX IF NOT EXISTS facturas_papel_fecha_idx ON operacion.facturas_papel (fecha DESC);
