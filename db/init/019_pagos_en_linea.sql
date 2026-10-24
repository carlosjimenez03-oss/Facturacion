-- Pagos con tarjeta en línea desde el portal de clientes (fase 7). En
-- producción (Supabase) erp_api aplica este mismo DDL al arrancar
-- (services/erp_api/app/migraciones.py, DDL_PAGOS). Requiere 016_soluciones.sql
-- (soluciones.portal_enlaces) y 007_ventas.sql (ventas.cobros). Ver docs/PAGOS.md.

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
