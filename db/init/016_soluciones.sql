-- Soluciones de erp-api (fase 6): planes y suscripción, portal de clientes,
-- Kontara Ventas (automatizaciones y cola de envíos) y POS (sesiones de caja
-- y ventas). Requiere el esquema `ventas` (007_ventas.sql) por la FK de los
-- enlaces del portal al cliente.
--
-- Igual que 007/008/009/010: este archivo es para el Postgres local de
-- desarrollo. En producción (Supabase) services/erp_api lo crea por sí
-- mismo al arrancar, con exactamente este mismo DDL
-- (services/erp_api/app/migraciones.py, DDL_SOLUCIONES). Si cambia uno,
-- cambie el otro -- lo verifica services/erp_api/tests/test_migraciones.py.

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
