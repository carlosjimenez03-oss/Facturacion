"""Migraciones de los esquemas `ventas` (clientes, cotizaciones, cobros),
`impuestos` (anulaciones manuales del 608, ajustes de los pre-cálculos y
declaraciones presentadas), `compras` (facturas de compra y pagos a
proveedores) y la tabla `recepcion.ecf_recibidos` (bandeja de entrada).

En producción la base es Supabase y los archivos de `db/init` NO se
ejecutan solos (ver docs/SUPABASE.md) -- por eso `erp_api` aplica su propio
DDL al arrancar (lifespan de main.py), justo después de crear el pool. Todo
es idempotente (`IF NOT EXISTS` / `CREATE OR REPLACE`), así que correrlo en
cada arranque es seguro. `db/init/007_ventas.sql`, `008_impuestos.sql`,
`009_fecha_dgii_segura.sql` y `010_compras.sql` contienen exactamente el
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
            await conn.execute(DDL_SOLUCIONES)
    logger.info("Esquemas 'ventas', 'impuestos', 'compras', 'recepcion' y 'soluciones' verificados/creados")
