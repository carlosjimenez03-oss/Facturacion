-- Bandeja de entrada (recepcion.ecf_recibidos) y facturas de compra / pagos
-- a proveedores (esquema `compras`) de erp-api -- Fase 3, Compras. Requiere
-- el esquema `ventas` (007_ventas.sql) por la FK de compras.facturas al
-- contacto.
--
-- Igual que 007/008/009: este archivo es para el Postgres local de
-- desarrollo. En producción (Supabase) services/erp_api lo crea por sí
-- mismo al arrancar, con exactamente este mismo DDL
-- (services/erp_api/app/migraciones.py, DDL_COMPRAS). Si cambia uno, cambie
-- el otro -- lo verifica services/erp_api/tests/test_migraciones.py.
-- services/receptor_recepcion escribe en recepcion.ecf_recibidos y tolera
-- que la tabla todavía no exista.

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
