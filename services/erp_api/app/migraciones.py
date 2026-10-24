"""Migraciones de los esquemas `ventas` (clientes, cotizaciones, cobros),
`impuestos` (anulaciones manuales del 608, ajustes de los pre-cálculos y
declaraciones presentadas), `compras` (facturas de compra y pagos a
proveedores), `nomina` (Kontara Nómina: empleados, parámetros de ley y
períodos de nómina) y la tabla `recepcion.ecf_recibidos` (bandeja de entrada).

En producción la base es Supabase y los archivos de `db/init` NO se
ejecutan solos (ver docs/SUPABASE.md) -- por eso `erp_api` aplica su propio
DDL al arrancar (lifespan de main.py), justo después de crear el pool. Todo
es idempotente (`IF NOT EXISTS` / `CREATE OR REPLACE`), así que correrlo en
cada arranque es seguro. `db/init/007_ventas.sql`, `008_impuestos.sql`,
`009_fecha_dgii_segura.sql`, `010_compras.sql`, `012_bancos_contabilidad.sql`,
`013_configuracion.sql`, `014_operacion.sql`, `015_nomina.sql`,
`016_soluciones.sql`, `017_contingencia.sql` y `018_pagos_en_linea.sql` contienen
exactamente el mismo DDL para el Postgres local de desarrollo (lo verifica
tests/test_migraciones.py).

`recepcion.ecf_recibidos` la escribe `receptor_recepcion` (que no tiene
migraciones propias) y la lee/actualiza este servicio: por eso se crea aquí.

Para cambios de esquema futuros: agregar sentencias idempotentes al final
de `DDL_VENTAS`/`DDL_IMPUESTOS` (p. ej. `ALTER TABLE ... ADD COLUMN IF NOT
EXISTS`) y copiarlas también en un nuevo `db/init/00N_*.sql`.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

import asyncpg

from .contabilidad_calculos import ddl_semilla_catalogo
from .migraciones_configuracion import DDL_CONFIGURACION

logger = logging.getLogger(__name__)

# Clave arbitraria (pero fija) del advisory lock que serializa las
# migraciones: si arrancan dos réplicas a la vez, `CREATE TABLE IF NOT
# EXISTS` concurrente puede fallar con una violación de unicidad en el
# catálogo de Postgres.
_LOCK_MIGRACIONES = 7_007_001

# Espera máxima por cada lock (advisory lock entre réplicas incluido) antes
# de rendirse y reintentar: una migración que espera un AccessExclusiveLock
# deja en cola detrás de ella a todas las escrituras de la tabla (cobros,
# pagos). Igual que inventario_api/app/migraciones.py.
_LOCK_TIMEOUT = "5s"
_REINTENTOS = 5
_ESPERA_INICIAL_SEGUNDOS = 1.0
_ESPERA_MAXIMA_SEGUNDOS = 30.0

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

DDL_IMPUESTOS = """
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
"""

DDL_FUNCIONES = """
-- Fecha DD-MM-AAAA de un e-CF (texto del JSON de la factura) -> date, o
-- NULL si falta, no tiene ese formato o no existe (31-02-2026, 00-13-2026):
-- `to_date` lanzaría un error y haría fallar la consulta entera (cuentas
-- por cobrar, formatos 607/608...) por una sola factura con una fecha
-- imposible. Ver `_fecha_dgii` en services/erp_api/app/repository.py.
CREATE OR REPLACE FUNCTION ventas.fecha_dgii_segura(valor text)
RETURNS date
LANGUAGE plpgsql
IMMUTABLE
PARALLEL SAFE
AS $$
BEGIN
    IF valor IS NULL OR valor !~ '^[0-9]{2}-[0-9]{2}-[0-9]{4}$' THEN
        RETURN NULL;
    END IF;
    RETURN make_date(substr(valor, 7, 4)::int, substr(valor, 4, 2)::int, substr(valor, 1, 2)::int);
EXCEPTION
    -- make_date: "date field value out of range" (día/mes/año imposibles).
    WHEN datetime_field_overflow OR invalid_datetime_format OR numeric_value_out_of_range THEN
        RETURN NULL;
END;
$$;
"""


DDL_COMPRAS = """
-- Bandeja de entrada: e-CF que los proveedores nos emiten, guardados
-- (best-effort) por services/receptor_recepcion al recibirlos -- ver
-- libs/ecf_common/ecf_recibidos.py. `xml` es el documento tal como llegó
-- (decodificado según su declaración); `firma_valida` es la verificación de
-- su firma digital al recibirlo (NULL = no verificada) y `firma_detalle` el
-- motivo. `estado_aprobacion` es la aprobación comercial (ACECF) que decide
-- el usuario y erp-api envía por services/aprobacion_comercial; mientras se
-- envía queda 'enviando' (`decision_en_envio`, desde `envio_iniciado_en`;
-- si se queda así más de unos minutos, se puede volver a intentar).
-- `aprobado_en` es la fecha de la decisión (aprobación o rechazo);
-- `acecf_xml` el ACECF firmado que aceptó la DGII y `enviado_al_emisor` /
-- `error_envio_emisor` su entrega (best-effort) al proveedor.
CREATE SCHEMA IF NOT EXISTS recepcion;

CREATE TABLE IF NOT EXISTS recepcion.ecf_recibidos (
    id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    rnc_emisor            text NOT NULL,
    razon_social_emisor   text,
    rnc_comprador         text,
    encf                  text NOT NULL,
    tipo_ecf              smallint,
    fecha_emision         date,
    fecha_limite_pago     date,
    monto_total           numeric(18, 2) NOT NULL DEFAULT 0,
    itbis_total           numeric(18, 2) NOT NULL DEFAULT 0,
    xml                   text NOT NULL,
    recibido_en           timestamptz NOT NULL DEFAULT now(),
    firma_valida          boolean,
    firma_detalle         text,
    estado_aprobacion     text NOT NULL DEFAULT 'pendiente'
                          CHECK (estado_aprobacion IN ('pendiente', 'enviando', 'aprobado', 'rechazado')),
    decision_en_envio     text CHECK (decision_en_envio IN ('aprobado', 'rechazado')),
    envio_iniciado_en     timestamptz,
    motivo_rechazo        text,
    aprobado_en           timestamptz,
    respuesta_aprobacion  jsonb,           -- respuesta de la DGII al ACECF
    error_aprobacion      text,            -- último intento fallido de enviar el ACECF
    acecf_xml             text,            -- ACECF firmado que aceptó la DGII
    enviado_al_emisor     boolean NOT NULL DEFAULT false,
    error_envio_emisor    text,
    compra606_id          uuid,            -- compras606.registros.id (sin FK: ese esquema es de formato_606)
    compra_inventario_id  uuid,            -- inventario.compras.id (sin FK: ese esquema es de inventario_api)
    actualizado_en        timestamptz NOT NULL DEFAULT now()
);

-- Idempotencia de receptor_recepcion: un reenvío del mismo e-CF no duplica la fila.
CREATE UNIQUE INDEX IF NOT EXISTS ecf_recibidos_rnc_encf_idx ON recepcion.ecf_recibidos (rnc_emisor, encf);
CREATE UNIQUE INDEX IF NOT EXISTS ecf_recibidos_compra606_idx
    ON recepcion.ecf_recibidos (compra606_id) WHERE compra606_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ecf_recibidos_recibido_en_idx ON recepcion.ecf_recibidos (recibido_en DESC);
CREATE INDEX IF NOT EXISTS ecf_recibidos_estado_idx ON recepcion.ecf_recibidos (estado_aprobacion);

CREATE SCHEMA IF NOT EXISTS compras;

-- Una factura de compra ES un renglón de compras606.registros (los datos
-- fiscales: proveedor, NCF, montos, retenciones, fecha y forma de pago).
-- Esta tabla guarda lo que el 606 no tiene: el nombre del proveedor, el
-- contacto y la compra de inventario vinculados, el concepto y el
-- vencimiento; y los valores de pago del 606 ANTES del primer pago
-- (`base_*`), para restaurarlos si se anulan todos los pagos.
CREATE TABLE IF NOT EXISTS compras.facturas (
    registro606_id              uuid PRIMARY KEY,  -- compras606.registros.id (sin FK)
    proveedor_nombre            text,
    contacto_id                 uuid REFERENCES ventas.clientes (id) ON DELETE SET NULL,
    compra_inventario_id        uuid,              -- inventario.compras.id (sin FK)
    concepto                    text,
    vence                       date,
    base_guardada               boolean NOT NULL DEFAULT false,
    base_fecha_pago             text,
    base_forma_pago             smallint,
    base_itbis_retenido         numeric(18, 2) NOT NULL DEFAULT 0,
    base_tipo_retencion_isr     smallint,
    base_monto_retencion_renta  numeric(18, 2) NOT NULL DEFAULT 0,
    creado_en                   timestamptz NOT NULL DEFAULT now(),
    actualizado_en              timestamptz NOT NULL DEFAULT now()
);

-- Pagos a proveedores. `monto` es lo que sale del banco/caja (neto de
-- retenciones); las retenciones del pago se copian a su renglón del 606
-- (fecha_pago, itbis_retenido, tipo_retencion_isr, monto_retencion_renta),
-- de donde las leen el IT-1 y el IR-17 del mes del pago. Un pago no se
-- borra: se anula, y el 606 se recalcula con los pagos que quedan.
CREATE TABLE IF NOT EXISTS compras.pagos (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    registro606_id      uuid NOT NULL,  -- compras606.registros.id (sin FK)
    fecha               date NOT NULL,
    monto               numeric(14, 2) NOT NULL CHECK (monto >= 0),
    metodo              text NOT NULL CHECK (metodo IN ('efectivo', 'transferencia', 'tarjeta', 'cheque')),
    cuenta_origen       text,
    referencia          text,
    notas               text,
    tipo_retencion_isr  smallint CHECK (tipo_retencion_isr BETWEEN 1 AND 9),
    retencion_isr       numeric(14, 2) NOT NULL DEFAULT 0 CHECK (retencion_isr >= 0),
    retencion_itbis     numeric(14, 2) NOT NULL DEFAULT 0 CHECK (retencion_itbis >= 0),
    anulado             boolean NOT NULL DEFAULT false,
    anulado_en          timestamptz,
    creado_en           timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS pagos_registro606_idx ON compras.pagos (registro606_id);
CREATE INDEX IF NOT EXISTS pagos_fecha_idx ON compras.pagos (fecha DESC);
"""

DDL_CONTABILIDAD = """
CREATE SCHEMA IF NOT EXISTS contabilidad;

-- Catálogo de cuentas (árbol por `padre_id`; el código refleja la
-- jerarquía: "1.1.01" es hija de "1.1"). `tipo` = grupo del estado
-- financiero; `naturaleza` = lado en que aumenta el saldo (las cuentas
-- "contra", como la depreciación acumulada, tienen la contraria a su
-- grupo). Solo las cuentas que `acepta_movimientos` (hojas) llevan líneas
-- de asiento. `sistema` = sembrada con el catálogo sugerido.
CREATE TABLE IF NOT EXISTS contabilidad.cuentas (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    codigo              text NOT NULL CHECK (codigo ~ '^[0-9]+([.][0-9]+)*$'),
    nombre              text NOT NULL,
    tipo                text NOT NULL
                        CHECK (tipo IN ('activo', 'pasivo', 'patrimonio', 'ingreso', 'costo', 'gasto')),
    naturaleza          text NOT NULL CHECK (naturaleza IN ('deudora', 'acreedora')),
    acepta_movimientos  boolean NOT NULL DEFAULT true,
    padre_id            uuid REFERENCES contabilidad.cuentas (id) ON DELETE RESTRICT,
    activa              boolean NOT NULL DEFAULT true,
    sistema             boolean NOT NULL DEFAULT false,
    descripcion         text,
    creado_en           timestamptz NOT NULL DEFAULT now(),
    actualizado_en      timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS cuentas_codigo_idx ON contabilidad.cuentas (codigo);
CREATE INDEX IF NOT EXISTS cuentas_padre_idx ON contabilidad.cuentas (padre_id);

-- Cuenta del catálogo que usa cada concepto de los asientos automáticos
-- (ver contabilidad_calculos.CONCEPTOS). Sin fila = el concepto no tiene
-- cuenta y los documentos que la necesitan quedan "sin contabilizar".
CREATE TABLE IF NOT EXISTS contabilidad.cuentas_por_defecto (
    concepto        text PRIMARY KEY,
    cuenta_id       uuid NOT NULL REFERENCES contabilidad.cuentas (id) ON DELETE RESTRICT,
    actualizado_en  timestamptz NOT NULL DEFAULT now()
);

CREATE SEQUENCE IF NOT EXISTS contabilidad.asientos_numero_seq;

-- Asientos (libro diario). `origen` = 'manual' o el documento que lo generó
-- (`origen_id`: facturacion.facturas.id, ventas.cobros.id,
-- compras606.registros.id, compras.pagos.id, bancos.movimientos.id,
-- bancos.transferencias.id o el período AAAAMM de la depreciación / del
-- ajuste por proporcionalidad del ITBIS). Un
-- documento tiene a lo sumo un asiento vigente (índice único parcial):
-- generar es idempotente. `huella` = resumen de las líneas generadas (si el
-- documento o las cuentas por defecto cambian, el asiento se anula y se
-- genera de nuevo). Solo los 'contabilizado' cuentan en los reportes.
CREATE TABLE IF NOT EXISTS contabilidad.asientos (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    numero             bigint NOT NULL DEFAULT nextval('contabilidad.asientos_numero_seq'),
    fecha              date NOT NULL,
    concepto           text NOT NULL,
    referencia         text,
    estado             text NOT NULL DEFAULT 'borrador'
                       CHECK (estado IN ('borrador', 'contabilizado', 'anulado')),
    origen             text NOT NULL DEFAULT 'manual'
                       CHECK (origen IN ('manual', 'venta', 'nota_credito', 'nota_debito', 'cobro', 'compra', 'pago',
                                         'movimiento', 'transferencia', 'proporcionalidad', 'depreciacion')),
    origen_id          text,
    huella             text,
    total              numeric(18, 2) NOT NULL DEFAULT 0,
    contabilizado_en   timestamptz,
    anulado_en         timestamptz,
    motivo_anulacion   text,
    creado_en          timestamptz NOT NULL DEFAULT now(),
    actualizado_en     timestamptz NOT NULL DEFAULT now(),
    CHECK ((origen = 'manual') = (origen_id IS NULL))
);

CREATE UNIQUE INDEX IF NOT EXISTS asientos_numero_idx ON contabilidad.asientos (numero);
CREATE UNIQUE INDEX IF NOT EXISTS asientos_origen_idx
    ON contabilidad.asientos (origen, origen_id) WHERE estado <> 'anulado' AND origen <> 'manual';
CREATE INDEX IF NOT EXISTS asientos_fecha_idx ON contabilidad.asientos (fecha);

CREATE TABLE IF NOT EXISTS contabilidad.asiento_lineas (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    asiento_id   uuid NOT NULL REFERENCES contabilidad.asientos (id) ON DELETE CASCADE,
    linea        integer NOT NULL,
    cuenta_id    uuid NOT NULL REFERENCES contabilidad.cuentas (id) ON DELETE RESTRICT,
    descripcion  text,
    debito       numeric(18, 2) NOT NULL DEFAULT 0 CHECK (debito >= 0),
    credito      numeric(18, 2) NOT NULL DEFAULT 0 CHECK (credito >= 0),
    CHECK ((debito = 0) <> (credito = 0))
);

CREATE INDEX IF NOT EXISTS asiento_lineas_asiento_idx ON contabilidad.asiento_lineas (asiento_id);
CREATE INDEX IF NOT EXISTS asiento_lineas_cuenta_idx ON contabilidad.asiento_lineas (cuenta_id);

-- Activos fijos. `categoria` = categoría del Art. 287 literal e) del
-- Código Tributario (1 = 5 %, 2 = 25 %, 3 = 15 % anual). `metodo` es el de
-- los asientos de depreciación ('linea_recta' con `vida_util_meses`, o
-- 'dgii' = el método fiscal); la depreciación fiscal del IR-2 siempre usa
-- el método 'dgii'. `depreciar_desde` = primer mes (AAAAMM) que se
-- deprecia; `depreciacion_acumulada_inicial` = la que ya tenía al
-- registrarlo (activos comprados antes de usar el sistema). `valor_venta` =
-- lo cobrado al venderlo (con `fecha_baja`): rebaja la cuenta fiscal de su
-- categoría (IR-2).
CREATE TABLE IF NOT EXISTS contabilidad.activos_fijos (
    id                              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    descripcion                     text NOT NULL,
    categoria                       smallint NOT NULL CHECK (categoria IN (1, 2, 3)),
    fecha_adquisicion               date NOT NULL,
    costo                           numeric(18, 2) NOT NULL CHECK (costo > 0),
    valor_residual                  numeric(18, 2) NOT NULL DEFAULT 0 CHECK (valor_residual >= 0),
    metodo                          text NOT NULL DEFAULT 'dgii' CHECK (metodo IN ('linea_recta', 'dgii')),
    vida_util_meses                 integer CHECK (vida_util_meses > 0),
    depreciar_desde                 text NOT NULL CHECK (depreciar_desde ~ '^[0-9]{4}(0[1-9]|1[0-2])$'),
    depreciacion_acumulada_inicial  numeric(18, 2) NOT NULL DEFAULT 0 CHECK (depreciacion_acumulada_inicial >= 0),
    cuenta_activo_id                uuid REFERENCES contabilidad.cuentas (id) ON DELETE RESTRICT,
    registro606_id                  uuid,  -- compras606.registros.id (sin FK: ese esquema es de formato_606)
    numero_serie                    text,
    ubicacion                       text,
    fecha_baja                      date,
    valor_venta                     numeric(18, 2) CHECK (valor_venta >= 0),
    notas                           text,
    creado_en                       timestamptz NOT NULL DEFAULT now(),
    actualizado_en                  timestamptz NOT NULL DEFAULT now(),
    CHECK (metodo <> 'linea_recta' OR vida_util_meses IS NOT NULL)
);

-- Depreciación registrada por activo y mes (la del asiento del mes).
CREATE TABLE IF NOT EXISTS contabilidad.depreciaciones (
    activo_id   uuid NOT NULL REFERENCES contabilidad.activos_fijos (id) ON DELETE RESTRICT,
    periodo     text NOT NULL,
    monto       numeric(18, 2) NOT NULL CHECK (monto > 0),
    asiento_id  uuid NOT NULL REFERENCES contabilidad.asientos (id) ON DELETE CASCADE,
    PRIMARY KEY (activo_id, periodo)
);

CREATE INDEX IF NOT EXISTS depreciaciones_periodo_idx ON contabilidad.depreciaciones (periodo);

-- Bases creadas con una versión anterior de este DDL: el origen
-- 'proporcionalidad' y la columna `valor_venta`. Se consulta el catálogo
-- antes de cada ALTER TABLE para no tomar su AccessExclusiveLock en cada
-- arranque.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'contabilidad.asientos'::regclass AND conname = 'asientos_origen_check'
          AND pg_get_constraintdef(oid) NOT LIKE '%proporcionalidad%'
    ) THEN
        ALTER TABLE contabilidad.asientos DROP CONSTRAINT asientos_origen_check;
        ALTER TABLE contabilidad.asientos ADD CONSTRAINT asientos_origen_check
            CHECK (origen IN ('manual', 'venta', 'nota_credito', 'nota_debito', 'cobro', 'compra', 'pago',
                              'movimiento', 'transferencia', 'proporcionalidad', 'depreciacion'));
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'contabilidad' AND table_name = 'activos_fijos' AND column_name = 'valor_venta'
    ) THEN
        ALTER TABLE contabilidad.activos_fijos
            ADD COLUMN IF NOT EXISTS valor_venta numeric(18, 2) CHECK (valor_venta >= 0);
    END IF;
END;
$$;

-- Catálogo sugerido y cuentas por defecto, solo si el catálogo está vacío.
""" + ddl_semilla_catalogo() + "\n"

DDL_BANCOS = """
CREATE SCHEMA IF NOT EXISTS bancos;

-- Cuentas bancarias, cajas chicas y tarjetas de crédito de la empresa.
-- `numero` se guarda completo, pero la API solo devuelve los últimos 4
-- caracteres. `saldo_inicial` = saldo al empezar el día
-- `fecha_saldo_inicial`; el saldo actual = saldo inicial + movimientos
-- desde esa fecha. `cuenta_contable_id` = su cuenta del catálogo (sin ella,
-- los asientos usan la cuenta por defecto 'banco', 'caja_chica' o
-- 'tarjeta_credito').
CREATE TABLE IF NOT EXISTS bancos.cuentas (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    nombre               text NOT NULL,
    banco                text NOT NULL,
    tipo                 text NOT NULL CHECK (tipo IN ('corriente', 'ahorro', 'caja_chica', 'tarjeta')),
    numero               text,
    moneda               text NOT NULL DEFAULT 'DOP' CHECK (moneda IN ('DOP', 'USD')),
    saldo_inicial        numeric(18, 2) NOT NULL DEFAULT 0,
    fecha_saldo_inicial  date NOT NULL,
    cuenta_contable_id   uuid REFERENCES contabilidad.cuentas (id) ON DELETE RESTRICT,
    activa               boolean NOT NULL DEFAULT true,
    notas                text,
    creado_en            timestamptz NOT NULL DEFAULT now(),
    actualizado_en       timestamptz NOT NULL DEFAULT now()
);

-- Transferencias entre cuentas propias: un par de movimientos
-- (transferencia_salida en la de origen, transferencia_entrada en la de
-- destino) creados y anulados en la misma transacción. `monto_destino` =
-- lo que llega (distinto de `monto` solo entre monedas distintas);
-- `tasa_cambio` = RD$ por US$ cuando alguna de las cuentas es en dólares.
CREATE TABLE IF NOT EXISTS bancos.transferencias (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    cuenta_origen_id   uuid NOT NULL REFERENCES bancos.cuentas (id) ON DELETE RESTRICT,
    cuenta_destino_id  uuid NOT NULL REFERENCES bancos.cuentas (id) ON DELETE RESTRICT,
    fecha              date NOT NULL,
    monto              numeric(18, 2) NOT NULL CHECK (monto > 0),
    monto_destino      numeric(18, 2) NOT NULL CHECK (monto_destino > 0),
    tasa_cambio        numeric(12, 4),
    concepto           text,
    referencia         text,
    anulada            boolean NOT NULL DEFAULT false,
    anulada_en         timestamptz,
    creado_en          timestamptz NOT NULL DEFAULT now(),
    CHECK (cuenta_origen_id <> cuenta_destino_id)
);

-- Movimientos registrados en Bancos (depósitos, retiros, cargos, intereses,
-- comisiones y las dos patas de cada transferencia). Los cobros
-- (ventas.cobros) y pagos a proveedores (compras.pagos) con
-- `cuenta_bancaria_id` también son movimientos de la cuenta, pero se leen
-- de sus tablas (no se copian aquí). `monto` siempre positivo: el tipo dice
-- si entra o sale. `cuenta_contable_id` = contrapartida del asiento (sin
-- ella, la del tipo: caja, gastos bancarios, ingresos financieros).
CREATE TABLE IF NOT EXISTS bancos.movimientos (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    cuenta_id           uuid NOT NULL REFERENCES bancos.cuentas (id) ON DELETE RESTRICT,
    fecha               date NOT NULL,
    tipo                text NOT NULL
                        CHECK (tipo IN ('deposito', 'retiro', 'cargo_bancario', 'interes', 'comision',
                                        'transferencia_entrada', 'transferencia_salida')),
    monto               numeric(18, 2) NOT NULL CHECK (monto > 0),
    descripcion         text,
    referencia          text,
    tasa_cambio         numeric(12, 4),
    cuenta_contable_id  uuid REFERENCES contabilidad.cuentas (id) ON DELETE RESTRICT,
    transferencia_id    uuid REFERENCES bancos.transferencias (id) ON DELETE RESTRICT,
    anulado             boolean NOT NULL DEFAULT false,
    anulado_en          timestamptz,
    creado_en           timestamptz NOT NULL DEFAULT now(),
    CHECK ((transferencia_id IS NULL) = (tipo NOT IN ('transferencia_entrada', 'transferencia_salida')))
);

CREATE INDEX IF NOT EXISTS movimientos_cuenta_fecha_idx ON bancos.movimientos (cuenta_id, fecha);
CREATE INDEX IF NOT EXISTS movimientos_transferencia_idx ON bancos.movimientos (transferencia_id);

-- Extractos (estados de cuenta) importados y sus líneas. `monto` con signo:
-- + crédito del banco (entra), − débito (sale). Una línea conciliada apunta
-- a su movimiento (`mov_origen` 'manual' | 'cobro' | 'pago' + `mov_id`); un
-- movimiento se concilia con a lo sumo una línea. `huella` evita duplicar
-- líneas al volver a importar el mismo archivo.
CREATE TABLE IF NOT EXISTS bancos.extractos (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    cuenta_id       uuid NOT NULL REFERENCES bancos.cuentas (id) ON DELETE RESTRICT,
    nombre_archivo  text,
    desde           date,
    hasta           date,
    saldo_final     numeric(18, 2),
    columnas        jsonb,
    importado_en    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS bancos.extracto_lineas (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    extracto_id    uuid NOT NULL REFERENCES bancos.extractos (id) ON DELETE CASCADE,
    cuenta_id      uuid NOT NULL REFERENCES bancos.cuentas (id) ON DELETE RESTRICT,
    fila           integer,
    fecha          date NOT NULL,
    descripcion    text NOT NULL,
    referencia     text,
    monto          numeric(18, 2) NOT NULL CHECK (monto <> 0),
    saldo          numeric(18, 2),
    huella         text NOT NULL,
    mov_origen     text CHECK (mov_origen IN ('manual', 'cobro', 'pago')),
    mov_id         uuid,
    conciliada_en  timestamptz,
    CHECK ((mov_origen IS NULL) = (mov_id IS NULL))
);

CREATE UNIQUE INDEX IF NOT EXISTS extracto_lineas_huella_idx ON bancos.extracto_lineas (cuenta_id, huella);
CREATE UNIQUE INDEX IF NOT EXISTS extracto_lineas_mov_idx
    ON bancos.extracto_lineas (mov_origen, mov_id) WHERE mov_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS extracto_lineas_cuenta_fecha_idx ON bancos.extracto_lineas (cuenta_id, fecha);

-- Conciliaciones cerradas: al corte `hasta`, saldo según el banco y según
-- libros, con las partidas en tránsito (movimientos que el banco aún no
-- refleja). Cerrada, nada de la cuenta con fecha <= `hasta` se puede
-- cambiar en Bancos hasta reabrirla (borrar la última).
CREATE TABLE IF NOT EXISTS bancos.conciliaciones (
    id                       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    cuenta_id                uuid NOT NULL REFERENCES bancos.cuentas (id) ON DELETE RESTRICT,
    hasta                    date NOT NULL,
    saldo_banco              numeric(18, 2) NOT NULL,
    saldo_libros             numeric(18, 2) NOT NULL,
    transito_entradas        numeric(18, 2) NOT NULL DEFAULT 0,
    transito_salidas         numeric(18, 2) NOT NULL DEFAULT 0,
    diferencia               numeric(18, 2) NOT NULL DEFAULT 0,
    lineas_conciliadas       integer NOT NULL DEFAULT 0,
    movimientos_en_transito  integer NOT NULL DEFAULT 0,
    notas                    text,
    cerrada_en               timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS conciliaciones_cuenta_hasta_idx ON bancos.conciliaciones (cuenta_id, hasta);

-- Cuenta bancaria de los cobros y pagos a proveedores (opcional; el texto
-- libre `cuenta_destino` / `cuenta_origen` se conserva por compatibilidad).
-- ventas.cobros y compras.pagos reciben escrituras todo el día: la columna
-- (ALTER TABLE: AccessExclusiveLock, más la FK sobre bancos.cuentas) y su
-- índice (CREATE INDEX: ShareLock) se crean solo si faltan, consultando el
-- catálogo primero, así que los arranques siguientes no bloquean esas
-- tablas. La primera vez, `lock_timeout` hace que la migración se rinda y
-- reintente en lugar de dejar en cola a los cobros (ver aplicar_migraciones).
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'ventas' AND table_name = 'cobros' AND column_name = 'cuenta_bancaria_id'
    ) THEN
        ALTER TABLE ventas.cobros
            ADD COLUMN IF NOT EXISTS cuenta_bancaria_id uuid REFERENCES bancos.cuentas (id) ON DELETE RESTRICT;
    END IF;
    IF to_regclass('ventas.cobros_cuenta_bancaria_idx') IS NULL THEN
        CREATE INDEX IF NOT EXISTS cobros_cuenta_bancaria_idx ON ventas.cobros (cuenta_bancaria_id);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'compras' AND table_name = 'pagos' AND column_name = 'cuenta_bancaria_id'
    ) THEN
        ALTER TABLE compras.pagos
            ADD COLUMN IF NOT EXISTS cuenta_bancaria_id uuid REFERENCES bancos.cuentas (id) ON DELETE RESTRICT;
    END IF;
    IF to_regclass('compras.pagos_cuenta_bancaria_idx') IS NULL THEN
        CREATE INDEX IF NOT EXISTS pagos_cuenta_bancaria_idx ON compras.pagos (cuenta_bancaria_id);
    END IF;
END;
$$;
"""


# Fase 6 · Operación: Mis tareas y facturas en papel. Las tareas automáticas
# NO se guardan (se calculan al leer de los datos de cada módulo, ver
# tareas_repository.py); aquí solo viven las tareas manuales, lo que el
# usuario hizo con una automática (completarla o posponerla), por su clave
# estable, y las facturas en papel de una contingencia (sincronizacion_repository.py).
DDL_OPERACION = """
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
"""


# Kontara Nómina (docs/NOMINA.md): copia de db/init/015_nomina.sql.
DDL_NOMINA = """
-- Kontara Nómina: empresa empleadora, parámetros de ley por fecha de
-- vigencia (tasas TSS, topes en salarios mínimos cotizables y escala del
-- ISR de asalariados), empleados, períodos de nómina y sus líneas. Las
-- fuentes de cada valor están en docs/NOMINA.md; todo es editable en
-- Nómina › Configuración (los INSERT de abajo solo siembran lo que falte).
CREATE SCHEMA IF NOT EXISTS nomina;

-- Una sola fila (id = 1). `tasa_srl` = tasa del Seguro de Riesgos Laborales
-- de la empresa (1 % fijo + 0.1 % a 0.3 % según su riesgo).
CREATE TABLE IF NOT EXISTS nomina.empresa (
    id              smallint PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    rnc             text,
    razon_social    text,
    categoria       text,
    municipio       text,
    contacto_nombre text,
    tasa_srl        numeric(6, 4) NOT NULL DEFAULT 0.0110 CHECK (tasa_srl >= 0 AND tasa_srl < 0.1),
    frecuencia_pago text NOT NULL DEFAULT 'mensual' CHECK (frecuencia_pago IN ('mensual', 'quincenal')),
    dia_pago        smallint NOT NULL DEFAULT 30 CHECK (dia_pago BETWEEN 1 AND 31),
    banco           text,
    cuenta_origen   text,
    actualizado_en  timestamptz NOT NULL DEFAULT now()
);

-- Parámetros de ley vigentes desde `vigente_desde` (hasta la fila
-- siguiente). Tasas como fracción (0.0287 = 2.87 %); topes en cantidad de
-- salarios mínimos cotizables; `escala_isr` = tramos anuales
-- [{"desde", "tasa", "fijo"}]; `regalia_tope_exento` NULL = regalía
-- pascual exenta sin tope.
CREATE TABLE IF NOT EXISTS nomina.parametros (
    vigente_desde                 date PRIMARY KEY,
    salario_minimo_cotizable      numeric(14, 2) NOT NULL CHECK (salario_minimo_cotizable > 0),
    afp_empleado                  numeric(7, 5) NOT NULL CHECK (afp_empleado >= 0 AND afp_empleado < 1),
    afp_empleador                 numeric(7, 5) NOT NULL CHECK (afp_empleador >= 0 AND afp_empleador < 1),
    sfs_empleado                  numeric(7, 5) NOT NULL CHECK (sfs_empleado >= 0 AND sfs_empleado < 1),
    sfs_empleador                 numeric(7, 5) NOT NULL CHECK (sfs_empleador >= 0 AND sfs_empleador < 1),
    srl_empleador                 numeric(7, 5) NOT NULL CHECK (srl_empleador >= 0 AND srl_empleador < 1),
    infotep_empleador             numeric(7, 5) NOT NULL CHECK (infotep_empleador >= 0 AND infotep_empleador < 1),
    infotep_bonificacion_empleado numeric(7, 5) NOT NULL
                                  CHECK (infotep_bonificacion_empleado >= 0 AND infotep_bonificacion_empleado < 1),
    tope_afp_salarios             numeric(6, 2) NOT NULL CHECK (tope_afp_salarios > 0),
    tope_sfs_salarios             numeric(6, 2) NOT NULL CHECK (tope_sfs_salarios > 0),
    tope_srl_salarios             numeric(6, 2) NOT NULL CHECK (tope_srl_salarios > 0),
    escala_isr                    jsonb NOT NULL,
    regalia_tope_exento           numeric(14, 2) CHECK (regalia_tope_exento >= 0),
    divisor_salario_diario        numeric(6, 2) NOT NULL DEFAULT 23.83 CHECK (divisor_salario_diario > 0),
    horas_jornada                 numeric(4, 2) NOT NULL DEFAULT 8 CHECK (horas_jornada > 0),
    recargo_horas_extra           numeric(5, 4) NOT NULL DEFAULT 0.35 CHECK (recargo_horas_extra >= 0),
    recargo_horas_extra_exceso    numeric(5, 4) NOT NULL DEFAULT 1.00 CHECK (recargo_horas_extra_exceso >= 0),
    fuente                        text,
    actualizado_en                timestamptz NOT NULL DEFAULT now()
);

INSERT INTO nomina.parametros (
    vigente_desde, salario_minimo_cotizable, afp_empleado, afp_empleador, sfs_empleado, sfs_empleador,
    srl_empleador, infotep_empleador, infotep_bonificacion_empleado, tope_afp_salarios, tope_sfs_salarios,
    tope_srl_salarios, escala_isr, regalia_tope_exento, fuente
) VALUES
    ('2025-04-01', 21674.80, 0.0287, 0.0710, 0.0304, 0.0709, 0.0110, 0.0100, 0.0050, 20, 10, 4,
     '[{"desde": "0.00", "tasa": "0", "fijo": "0.00"}, {"desde": "416220.00", "tasa": "0.15", "fijo": "0.00"}, {"desde": "624329.00", "tasa": "0.20", "fijo": "31216.00"}, {"desde": "867123.00", "tasa": "0.25", "fijo": "79776.00"}]',
     NULL, 'TSS: salario mínimo cotizable RD$21,674.80 (abr. 2025 - ene. 2026). DGII: escala ISR de asalariados 2025'),
    ('2026-02-01', 23223.00, 0.0287, 0.0710, 0.0304, 0.0709, 0.0110, 0.0100, 0.0050, 20, 10, 4,
     '[{"desde": "0.00", "tasa": "0", "fijo": "0.00"}, {"desde": "416220.00", "tasa": "0.15", "fijo": "0.00"}, {"desde": "624329.00", "tasa": "0.20", "fijo": "31216.00"}, {"desde": "867123.00", "tasa": "0.25", "fijo": "79776.00"}]',
     NULL, 'TSS Res. 01-2025: salario mínimo cotizable RD$23,223.00 desde el 01/02/2026. DGII Res. DDG-AR1-2026-00001: escala ISR 2026')
ON CONFLICT (vigente_desde) DO NOTHING;

-- Empleados. `cedula` solo dígitos (11). `tipo_pago` decide en qué
-- períodos entra (mensual o quincenal). Dar de baja = estado 'inactivo'.
CREATE TABLE IF NOT EXISTS nomina.empleados (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    cedula          text NOT NULL UNIQUE CHECK (cedula ~ '^[0-9]{11}$'),
    nombre          text NOT NULL CHECK (btrim(nombre) <> ''),
    cargo           text,
    departamento    text,
    fecha_ingreso   date NOT NULL,
    salario_mensual numeric(14, 2) NOT NULL CHECK (salario_mensual > 0),
    tipo_pago       text NOT NULL DEFAULT 'mensual' CHECK (tipo_pago IN ('mensual', 'quincenal')),
    banco           text,
    cuenta_bancaria text,
    tipo_cuenta     text CHECK (tipo_cuenta IN ('ahorro', 'corriente')),
    afp             text,
    ars             text,
    correo          text,
    estado          text NOT NULL DEFAULT 'activo' CHECK (estado IN ('activo', 'inactivo')),
    fecha_salida    date,
    creado_en       timestamptz NOT NULL DEFAULT now(),
    actualizado_en  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS empleados_nombre_idx ON nomina.empleados (lower(nombre));

-- Período de nómina: borrador -> calculada -> aprobada -> pagada -> cerrada.
-- `parametros` guarda la copia de los parámetros con que se calculó (así
-- editar la tabla de parámetros no cambia una nómina ya calculada).
CREATE TABLE IF NOT EXISTS nomina.periodos (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tipo         text NOT NULL CHECK (tipo IN ('mensual', 'quincenal')),
    anio         smallint NOT NULL CHECK (anio BETWEEN 2000 AND 2100),
    mes          smallint NOT NULL CHECK (mes BETWEEN 1 AND 12),
    quincena     smallint CHECK (quincena IN (1, 2)),
    fecha_inicio date NOT NULL,
    fecha_fin    date NOT NULL,
    fecha_pago   date NOT NULL,
    estado       text NOT NULL DEFAULT 'borrador'
                 CHECK (estado IN ('borrador', 'calculada', 'aprobada', 'pagada', 'cerrada')),
    parametros   jsonb,
    tasa_srl     numeric(6, 4),
    notas        text,
    calculado_en timestamptz,
    aprobado_en  timestamptz,
    pagado_en    timestamptz,
    cerrado_en   timestamptz,
    creado_en    timestamptz NOT NULL DEFAULT now(),
    CHECK ((tipo = 'mensual') = (quincena IS NULL))
);

CREATE UNIQUE INDEX IF NOT EXISTS periodos_unico_idx ON nomina.periodos (tipo, anio, mes, COALESCE(quincena, 0));

-- Una línea por empleado y período: copia de los datos del empleado al
-- calcular, novedades capturadas (horas extra, otros ingresos, descuentos)
-- y el resultado (NULL mientras no se calcule).
CREATE TABLE IF NOT EXISTS nomina.lineas (
    periodo_id              uuid NOT NULL REFERENCES nomina.periodos (id) ON DELETE CASCADE,
    empleado_id             uuid NOT NULL REFERENCES nomina.empleados (id),
    cedula                  text NOT NULL,
    nombre                  text NOT NULL,
    cargo                   text,
    departamento            text,
    salario_mensual         numeric(14, 2) NOT NULL,
    banco                   text,
    cuenta_bancaria         text,
    tipo_cuenta             text,
    horas_extra_35          numeric(8, 2) NOT NULL DEFAULT 0 CHECK (horas_extra_35 >= 0),
    horas_extra_100         numeric(8, 2) NOT NULL DEFAULT 0 CHECK (horas_extra_100 >= 0),
    otros_ingresos          numeric(14, 2) NOT NULL DEFAULT 0 CHECK (otros_ingresos >= 0),
    bonificacion            numeric(14, 2) NOT NULL DEFAULT 0 CHECK (bonificacion >= 0),
    ingresos_exentos        numeric(14, 2) NOT NULL DEFAULT 0 CHECK (ingresos_exentos >= 0),
    regalia                 numeric(14, 2) NOT NULL DEFAULT 0 CHECK (regalia >= 0),
    otros_descuentos        numeric(14, 2) NOT NULL DEFAULT 0 CHECK (otros_descuentos >= 0),
    salario                 numeric(14, 2),
    horas_extra             numeric(14, 2),
    total_ingresos          numeric(14, 2),
    salario_cotizable       numeric(14, 2),
    base_isr                numeric(14, 2),
    afp                     numeric(14, 2),
    sfs                     numeric(14, 2),
    isr                     numeric(14, 2),
    infotep_empleado        numeric(14, 2),
    total_descuentos        numeric(14, 2),
    neto                    numeric(14, 2),
    afp_empleador           numeric(14, 2),
    sfs_empleador           numeric(14, 2),
    srl                     numeric(14, 2),
    infotep                 numeric(14, 2),
    total_aportes_empleador numeric(14, 2),
    costo_total             numeric(14, 2),
    pagado                  boolean NOT NULL DEFAULT false,
    pagado_en               timestamptz,
    referencia_pago         text,
    PRIMARY KEY (periodo_id, empleado_id)
);

CREATE INDEX IF NOT EXISTS lineas_empleado_idx ON nomina.lineas (empleado_id);
"""



# Soluciones (fase 6): planes y suscripción, portal de clientes, Kontara
# Ventas (automatizaciones y su cola de envíos) y POS (sesiones de caja y
# ventas). Requiere `ventas.clientes` (DDL_VENTAS). Mismo DDL en
# db/init/016_soluciones.sql.
DDL_SOLUCIONES = """
CREATE SCHEMA IF NOT EXISTS soluciones;

-- Suscripción de la empresa (una sola fila, id = 1). La prueba gratis de 15
-- días empieza con el primer uso (la primera vez que se consulta
-- GET /suscripcion). `plan` NULL = en prueba; un plan se activa solo cuando
-- un asesor confirma el pago (no hay pasarela): elegir un plan en la
-- pantalla crea una solicitud en `solicitudes_plan`.
CREATE TABLE IF NOT EXISTS soluciones.suscripcion (
    id              smallint PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    plan            text CHECK (plan IN ('emprendedor', 'pyme', 'empresarial')),
    periodicidad    text CHECK (periodicidad IN ('mensual', 'anual')),
    prueba_inicio   date NOT NULL,
    prueba_fin      date NOT NULL,
    activo_desde    date,
    proxima_factura date,
    creado_en       timestamptz NOT NULL DEFAULT now(),
    actualizado_en  timestamptz NOT NULL DEFAULT now()
);

-- Solicitudes de plan ("Elegir plan"): montos calculados en el servidor con
-- el precio de lista del momento. Un asesor las atiende (pago por
-- transferencia) y activa el plan.
CREATE TABLE IF NOT EXISTS soluciones.solicitudes_plan (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    plan               text NOT NULL CHECK (plan IN ('emprendedor', 'pyme', 'empresarial')),
    periodicidad       text NOT NULL CHECK (periodicidad IN ('mensual', 'anual')),
    forma_pago         text NOT NULL CHECK (forma_pago IN ('transferencia', 'tarjeta')),
    subtotal           numeric(14, 2) NOT NULL,
    itbis              numeric(14, 2) NOT NULL,
    total              numeric(14, 2) NOT NULL,
    contacto_nombre    text,
    contacto_correo    text,
    contacto_telefono  text,
    notas              text,
    estado             text NOT NULL DEFAULT 'pendiente' CHECK (estado IN ('pendiente', 'atendida', 'cancelada')),
    creado_en          timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS solicitudes_plan_creado_idx ON soluciones.solicitudes_plan (creado_en DESC);

-- Portal de clientes: enlaces de solo lectura por cliente. Del token
-- (aleatorio, 32 bytes) solo se guarda su SHA-256: el enlace completo se
-- muestra una única vez, al crearlo. Un enlace revocado o vencido responde
-- 404 en el portal público.
CREATE TABLE IF NOT EXISTS soluciones.portal_enlaces (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    cliente_id        uuid NOT NULL REFERENCES ventas.clientes (id) ON DELETE CASCADE,
    token_hash        text NOT NULL UNIQUE,
    metodos_pago      text[] NOT NULL DEFAULT '{}',
    expira_en         timestamptz NOT NULL,
    revocado_en       timestamptz,
    ultimo_acceso_en  timestamptz,
    accesos           integer NOT NULL DEFAULT 0,
    creado_en         timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS portal_enlaces_cliente_idx ON soluciones.portal_enlaces (cliente_id);

-- RNC/cédula del cliente AL CREAR el enlace: el portal muestra las facturas
-- de esa identificación aunque luego se edite el RNC del contacto. Los
-- enlaces anteriores toman el RNC actual del cliente (una sola vez).
ALTER TABLE soluciones.portal_enlaces ADD COLUMN IF NOT EXISTS rnc text;
UPDATE soluciones.portal_enlaces e SET rnc = c.rnc
FROM ventas.clientes c
WHERE c.id = e.cliente_id AND e.rnc IS NULL AND c.rnc IS NOT NULL;

-- Datos para pagar por transferencia que ve el cliente en el portal (una
-- sola fila, id = 1).
CREATE TABLE IF NOT EXISTS soluciones.portal_config (
    id              smallint PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    banco           text,
    tipo_cuenta     text,
    numero_cuenta   text,
    titular         text,
    rnc_titular     text,
    instrucciones   text,
    actualizado_en  timestamptz NOT NULL DEFAULT now()
);

-- Kontara Ventas: activación del módulo (una sola fila, id = 1; prueba
-- gratis de 30 días) y sus cuatro automatizaciones.
CREATE TABLE IF NOT EXISTS soluciones.ventas_modulo (
    id                smallint PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    activo            boolean NOT NULL DEFAULT false,
    activado_en       timestamptz,
    prueba_hasta      date,
    ultima_ejecucion  timestamptz,
    ultimo_resultado  jsonb
);

CREATE TABLE IF NOT EXISTS soluciones.automatizaciones (
    clave           text PRIMARY KEY CHECK (clave IN ('motivar', 'cobranza', 'fidelizacion', 'reactivacion')),
    activa          boolean NOT NULL DEFAULT false,
    canal           text NOT NULL DEFAULT 'whatsapp' CHECK (canal IN ('whatsapp', 'correo', 'ambos')),
    dias            integer NOT NULL CHECK (dias BETWEEN 0 AND 365),
    asunto          text NOT NULL,
    plantilla       text NOT NULL,
    activada_en     timestamptz,
    actualizado_en  timestamptz NOT NULL DEFAULT now()
);

-- Cola de envíos que genera la tarea diaria. `clave_unica` evita repetir un
-- mismo envío (p. ej. 'cobranza:<factura>:<vence>'). Solo pasa a 'enviado'
-- cuando un canal configurado (SMTP / WhatsApp Cloud API) lo entregó; sin
-- canal queda 'pendiente_canal' y el usuario puede mandarlo a mano
-- (wa.me / correo) y marcarlo 'enviado_manual'.
CREATE TABLE IF NOT EXISTS soluciones.envios (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    automatizacion  text NOT NULL,
    clave_unica     text NOT NULL,
    canal           text NOT NULL CHECK (canal IN ('whatsapp', 'correo')),
    cliente_id      uuid,
    cliente_nombre  text NOT NULL,
    destino         text NOT NULL,
    factura_id      uuid,
    encf            text,
    asunto          text NOT NULL,
    mensaje         text NOT NULL,
    estado          text NOT NULL DEFAULT 'pendiente_canal'
                    CHECK (estado IN ('pendiente_canal', 'enviado', 'enviado_manual', 'error', 'descartado')),
    error           text,
    intentos        integer NOT NULL DEFAULT 0,
    enviado_en      timestamptz,
    creado_en       timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS envios_clave_canal_idx ON soluciones.envios (clave_unica, canal);
CREATE INDEX IF NOT EXISTS envios_creado_idx ON soluciones.envios (creado_en DESC);

-- POS: sesiones de caja (apertura con fondo, cierre con arqueo por método)
-- y las ventas cobradas en cada una. Una caja solo puede tener una sesión
-- abierta. `esperado`/`contado` = {"efectivo": "…", "tarjeta": "…",
-- "transferencia": "…"}.
CREATE TABLE IF NOT EXISTS soluciones.pos_sesiones (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    caja           text NOT NULL DEFAULT 'Caja principal',
    almacen_id     uuid,
    cajero         text,
    fondo_inicial  numeric(14, 2) NOT NULL DEFAULT 0 CHECK (fondo_inicial >= 0),
    abierta_en     timestamptz NOT NULL DEFAULT now(),
    cerrada_en     timestamptz,
    esperado       jsonb,
    contado        jsonb,
    notas_cierre   text,
    estado         text NOT NULL DEFAULT 'abierta' CHECK (estado IN ('abierta', 'cerrada'))
);

CREATE UNIQUE INDEX IF NOT EXISTS pos_sesiones_abierta_idx ON soluciones.pos_sesiones (caja) WHERE estado = 'abierta';

-- Cada venta apunta a su e-CF en facturacion.facturas (sin FK: es de
-- facturas_api). `pagos` = [{"metodo": "efectivo", "monto": "…",
-- "referencia": …}]; el `cambio` sale del efectivo.
CREATE TABLE IF NOT EXISTS soluciones.pos_ventas (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    sesion_id       uuid NOT NULL REFERENCES soluciones.pos_sesiones (id),
    factura_id      uuid NOT NULL UNIQUE,
    encf            text,
    tipo_ecf        smallint NOT NULL,
    cliente_rnc     text,
    cliente_nombre  text,
    total           numeric(14, 2) NOT NULL,
    pagos           jsonb NOT NULL,
    recibido        numeric(14, 2) NOT NULL,
    cambio          numeric(14, 2) NOT NULL DEFAULT 0 CHECK (cambio >= 0),
    creado_en       timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS pos_ventas_sesion_idx ON soluciones.pos_ventas (sesion_id);
"""

# Modo contingencia: los asientos automáticos de las facturas en papel
# (serie B) de `operacion.facturas_papel` (origen 'factura_papel', ver
# contabilidad_repository._fuente_facturas_papel). Se consulta el catálogo
# antes del ALTER TABLE para no tomar su lock en cada arranque. Mismo DDL en
# db/init/017_contingencia.sql.
DDL_CONTINGENCIA = """
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
"""


# Pagos con tarjeta en línea (fase 7): intentos de pago del portal de
# clientes y su bitácora (ver pagos_repository.py y docs/PAGOS.md). Requiere
# `soluciones.portal_enlaces` (DDL_SOLUCIONES) y `ventas.cobros`. Mismo DDL en
# db/init/018_pagos_en_linea.sql.
DDL_PAGOS = """
-- Pagos con tarjeta en línea desde el portal de clientes (fase 7, ver
-- docs/PAGOS.md). Cada intento es una fila: el portal la crea al pulsar
-- «Pagar con tarjeta» (estado 'pendiente', vence en `expira_en`) y el
-- retorno firmado de la pasarela la cierra (aprobado / declinado /
-- cancelado). Un aprobado registra UN cobro (`cobro_id`, índice único): las
-- respuestas repetidas no cobran dos veces. `orden` es el OrderNumber que
-- ve la pasarela; `referencia` (aleatoria, 32 bytes) identifica el intento
-- en las URL de retorno sin exponer el token del portal. Nunca se guardan
-- datos de la tarjeta.
CREATE TABLE IF NOT EXISTS soluciones.pagos_en_linea (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    referencia           text NOT NULL UNIQUE,
    orden                text NOT NULL UNIQUE,
    proveedor            text NOT NULL CHECK (proveedor IN ('azul', 'simulado')),
    ambiente             text NOT NULL CHECK (ambiente IN ('pruebas', 'produccion')),
    enlace_id            uuid REFERENCES soluciones.portal_enlaces (id) ON DELETE SET NULL,
    factura_id           uuid NOT NULL,  -- facturacion.facturas.id (sin FK, solo lectura)
    encf                 text,
    cliente_rnc          text,
    cliente_nombre       text,
    monto                numeric(14, 2) NOT NULL CHECK (monto > 0),
    itbis                numeric(14, 2) NOT NULL DEFAULT 0 CHECK (itbis >= 0),
    estado               text NOT NULL DEFAULT 'pendiente'
                         CHECK (estado IN ('pendiente', 'aprobado', 'declinado', 'cancelado', 'expirado')),
    expira_en            timestamptz NOT NULL,
    codigo_autorizacion  text,
    rrn                  text,
    orden_proveedor      text,
    iso_code             text,
    respuesta            text,
    error_descripcion    text,
    fecha_proveedor      text,
    cobro_id             uuid REFERENCES ventas.cobros (id),
    incidencia           text,
    respondido_en        timestamptz,
    creado_en            timestamptz NOT NULL DEFAULT now(),
    actualizado_en       timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS pagos_en_linea_factura_idx ON soluciones.pagos_en_linea (factura_id);
CREATE INDEX IF NOT EXISTS pagos_en_linea_creado_idx ON soluciones.pagos_en_linea (creado_en DESC);
CREATE INDEX IF NOT EXISTS pagos_en_linea_enlace_idx ON soluciones.pagos_en_linea (enlace_id, creado_en);
CREATE UNIQUE INDEX IF NOT EXISTS pagos_en_linea_cobro_idx
    ON soluciones.pagos_en_linea (cobro_id) WHERE cobro_id IS NOT NULL;

-- Bitácora de cada intento: creación, retornos (válidos o no: firma
-- inválida, monto que no coincide, respuesta repetida), cobro registrado o
-- fallido. `detalle` guarda los parámetros de la respuesta SIN el número
-- de tarjeta enmascarado ni los datos de bóveda (DataVault).
CREATE TABLE IF NOT EXISTS soluciones.pagos_en_linea_eventos (
    id         bigserial PRIMARY KEY,
    pago_id    uuid REFERENCES soluciones.pagos_en_linea (id) ON DELETE CASCADE,
    tipo       text NOT NULL,
    detalle    jsonb NOT NULL DEFAULT '{}'::jsonb,
    ip         text,
    creado_en  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS pagos_en_linea_eventos_pago_idx ON soluciones.pagos_en_linea_eventos (pago_id, creado_en);
"""


async def _migrar(conn: asyncpg.Connection) -> None:
    async with conn.transaction():
        await conn.execute(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'")
        await conn.execute("SELECT pg_advisory_xact_lock($1)", _LOCK_MIGRACIONES)
        await conn.execute(DDL_VENTAS)
        await conn.execute(DDL_IMPUESTOS)
        await conn.execute(DDL_FUNCIONES)
        await conn.execute(DDL_COMPRAS)
        await conn.execute(DDL_CONTABILIDAD)
        await conn.execute(DDL_BANCOS)
        await conn.execute(DDL_CONFIGURACION)
        await conn.execute(DDL_OPERACION)
        await conn.execute(DDL_NOMINA)
        await conn.execute(DDL_SOLUCIONES)
        await conn.execute(DDL_CONTINGENCIA)
        await conn.execute(DDL_PAGOS)


async def aplicar_migraciones(
    pool: asyncpg.Pool,
    *,
    reintentos: int = _REINTENTOS,
    dormir: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Crea (si no existen) los esquemas `ventas`, `impuestos`, `compras`,
    `recepcion`, `contabilidad`, `bancos`, `configuracion`, `operacion`,
    `nomina` y `soluciones`, sus tablas/índices y las
    funciones auxiliares de las consultas, y siembra el catálogo de cuentas
    sugerido si está vacío. Con `lock_timeout`: si una tabla está ocupada
    (o hay un deadlock) suelta todo y reintenta con espera creciente."""
    espera = _ESPERA_INICIAL_SEGUNDOS
    for intento in range(1, reintentos + 1):
        try:
            async with pool.acquire() as conn:
                await _migrar(conn)
            break
        except (asyncpg.LockNotAvailableError, asyncpg.DeadlockDetectedError) as exc:
            if intento == reintentos:
                raise
            logger.warning(
                "Migración de erp-api en espera (intento %s/%s, %s): reintentando en %ss",
                intento,
                reintentos,
                exc.__class__.__name__,
                espera,
            )
            await dormir(espera)
            espera = min(espera * 2, _ESPERA_MAXIMA_SEGUNDOS)
    logger.info(
        "Esquemas 'ventas', 'impuestos', 'compras', 'recepcion', 'contabilidad', 'bancos', "
        "'configuracion', 'operacion', 'nomina' y 'soluciones' verificados/creados"
    )
