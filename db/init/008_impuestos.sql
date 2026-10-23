-- Esquema de impuestos de erp-api: anulaciones manuales del Formato 608,
-- valores manuales de los pre-cálculos (IT-1, IR-17, IR-2) y el registro de
-- declaraciones presentadas. Los formatos 607/608 y los pre-cálculos NO se
-- guardan: se calculan leyendo facturacion.facturas, facturacion.envios_dgii,
-- compras606.registros e inventario.productos (todos de solo lectura para
-- erp-api).
--
-- Este archivo es para el Postgres local de desarrollo (se ejecuta una sola
-- vez, la primera vez que arranca el contenedor con un volumen vacío, igual
-- que los demás de db/init). En producción (Supabase) NO hace falta
-- aplicarlo a mano: services/erp_api crea el esquema por sí mismo al
-- arrancar, con exactamente este mismo DDL (services/erp_api/app/migraciones.py,
-- DDL_IMPUESTOS). Si cambia uno, cambie el otro -- lo verifica
-- services/erp_api/tests/test_migraciones.py.
--
-- Igual que ventas, este esquema NO se expone vía PostgREST: todo el acceso
-- pasa por los endpoints de erp_api.

CREATE SCHEMA IF NOT EXISTS impuestos;

-- Comprobantes anulados que se registran a mano para el Formato 608 (NCF
-- serie B/A/P/Q de 11 o 19 posiciones). Las anulaciones de e-NCF NO se
-- guardan aquí: se leen de facturacion.envios_dgii (modulo
-- 'anulacion_encf'). El período del 608 es el mes de `fecha_comprobante`.
CREATE TABLE IF NOT EXISTS impuestos.anulaciones_608 (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    ncf                text NOT NULL,
    fecha_comprobante  date NOT NULL,
    tipo_anulacion     text NOT NULL CHECK (tipo_anulacion ~ '^(0[1-9]|10)$'),
    creado_en          timestamptz NOT NULL DEFAULT now()
);

-- Un NCF no puede reportarse anulado dos veces ("NCF repetido" de la
-- plantilla oficial).
CREATE UNIQUE INDEX IF NOT EXISTS anulaciones_608_ncf_idx ON impuestos.anulaciones_608 (ncf);
CREATE INDEX IF NOT EXISTS anulaciones_608_fecha_idx ON impuestos.anulaciones_608 (fecha_comprobante);

-- Valores que el usuario ingresa en un pre-cálculo (saldo a favor
-- anterior, anticipos, inventario inicial...). `periodo` es AAAAMM para
-- it1/ir17 y AAAA para ir2.
CREATE TABLE IF NOT EXISTS impuestos.ajustes_precalculo (
    formulario      text NOT NULL CHECK (formulario IN ('it1', 'ir17', 'ir2')),
    periodo         text NOT NULL,
    clave           text NOT NULL,
    valor           numeric(18, 2) NOT NULL,
    actualizado_en  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (formulario, periodo, clave)
);

-- Marca de "presentada" de cada declaración/formato por período. Sin fila
-- = no presentada (pendiente o vencida, se calcula al leer).
CREATE TABLE IF NOT EXISTS impuestos.declaraciones_presentadas (
    formulario     text NOT NULL CHECK (formulario IN ('606', '607', '608', 'it1', 'ir17', 'ir2')),
    periodo        text NOT NULL,
    presentada_en  date NOT NULL,
    monto          numeric(18, 2),
    referencia     text,
    creado_en      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (formulario, periodo)
);
