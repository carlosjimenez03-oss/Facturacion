"""Migraciones de los esquemas `ventas` (clientes, cotizaciones, cobros),
`impuestos` (anulaciones manuales del 608, ajustes de los pre-cálculos y
declaraciones presentadas), `compras` (facturas de compra y pagos a
proveedores) y la tabla `recepcion.ecf_recibidos` (bandeja de entrada).

En producción la base es Supabase y los archivos de `db/init` NO se
ejecutan solos (ver docs/SUPABASE.md) -- por eso `erp_api` aplica su propio
DDL al arrancar (lifespan de main.py), justo después de crear el pool. Todo
es idempotente (`IF NOT EXISTS` / `CREATE OR REPLACE`), así que correrlo en
cada arranque es seguro. `db/init/007_ventas.sql`, `008_impuestos.sql`,
`009_fecha_dgii_segura.sql`, `010_compras.sql`,
`012_bancos_contabilidad.sql` y `014_operacion.sql` contienen exactamente el mismo DDL para el Postgres local de desarrollo (lo verifica
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
        await conn.execute(DDL_OPERACION)


async def aplicar_migraciones(
    pool: asyncpg.Pool,
    *,
    reintentos: int = _REINTENTOS,
    dormir: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Crea (si no existen) los esquemas `ventas`, `impuestos`, `compras`,
    `recepcion`, `contabilidad`, `bancos` y `operacion`, sus tablas/índices y las
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
        "Esquemas 'ventas', 'impuestos', 'compras', 'recepcion', 'contabilidad', 'bancos' y 'operacion' verificados/creados"
    )
