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
`009_fecha_dgii_segura.sql`, `010_compras.sql` y `015_nomina.sql` contienen exactamente el
mismo DDL para el Postgres local de desarrollo (lo verifica
tests/test_migraciones.py).

`recepcion.ecf_recibidos` la escribe `receptor_recepcion` (que no tiene
migraciones propias) y la lee/actualiza este servicio: por eso se crea aquí.

Para cambios de esquema futuros: agregar sentencias idempotentes al final
de `DDL_VENTAS`/`DDL_IMPUESTOS` (p. ej. `ALTER TABLE ... ADD COLUMN IF NOT
EXISTS`) y copiarlas también en un nuevo `db/init/00N_*.sql`.
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


async def aplicar_migraciones(pool: asyncpg.Pool) -> None:
    """Crea (si no existen) los esquemas `ventas`, `impuestos`, `compras` y
    `recepcion`, sus tablas/índices y las funciones auxiliares de las
    consultas."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock($1)", _LOCK_MIGRACIONES)
            await conn.execute(DDL_VENTAS)
            await conn.execute(DDL_IMPUESTOS)
            await conn.execute(DDL_FUNCIONES)
            await conn.execute(DDL_COMPRAS)
            await conn.execute(DDL_NOMINA)
    logger.info("Esquemas 'ventas', 'impuestos', 'compras', 'recepcion' y 'nomina' verificados/creados")
