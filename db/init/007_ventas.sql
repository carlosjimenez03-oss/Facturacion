-- Esquema de ventas de erp-api: contactos (clientes/proveedores),
-- cotizaciones y cobros de facturas a crédito. Las cuentas por cobrar y el
-- saldo de cada cliente NO se guardan: se calculan leyendo
-- facturacion.facturas (solo lectura) más los cobros de aquí.
--
-- Este archivo es para el Postgres local de desarrollo (se ejecuta una sola
-- vez, la primera vez que arranca el contenedor con un volumen vacío, igual
-- que los demás de db/init). En producción (Supabase) NO hace falta
-- aplicarlo a mano: services/erp_api crea el esquema por sí mismo al
-- arrancar, con exactamente este mismo DDL (services/erp_api/app/migraciones.py).
-- Si cambia uno, cambie el otro -- lo verifica
-- services/erp_api/tests/test_migraciones.py.
--
-- Igual que inventario y compras606, este esquema NO se expone vía
-- PostgREST: todo el acceso pasa por los endpoints de erp_api.

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
