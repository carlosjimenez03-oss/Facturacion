"""DDL del esquema `configuracion` (fase 6 · Plataforma): datos de la
empresa, secuencias e-NCF autorizadas por tipo de e-CF y vendedores.

Vive aparte de `migraciones.py` para no mezclarse con el DDL de otros
módulos; `migraciones.aplicar_migraciones` lo ejecuta en la misma
transacción. `db/init/013_configuracion.sql` contiene exactamente el mismo
DDL para el Postgres local (lo verifica tests/test_migraciones.py). Igual
que el resto: todo idempotente, para correrlo en cada arranque.
"""

DDL_CONFIGURACION = """
CREATE SCHEMA IF NOT EXISTS configuracion;

-- Datos de la empresa (una sola fila: la aplicación administra una
-- empresa por instalación). `rnc` solo dígitos. `logo` es una imagen
-- pequeña como data URL (data:image/png;base64,...), opcional.
CREATE TABLE IF NOT EXISTS configuracion.empresa (
    id                smallint PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    razon_social      text NOT NULL,
    nombre_comercial  text,
    rnc               text,
    actividad_economica text,
    direccion         text,
    provincia         text,
    municipio         text,
    telefono          text,
    correo            text,
    logo              text,
    actualizado_en    timestamptz NOT NULL DEFAULT now()
);

-- Secuencias e-NCF autorizadas por la DGII, una por tipo de e-CF. El e-NCF
-- es `prefijo` (E + tipo) + el número con 10 dígitos. `proximo` es el
-- número desde el que se sigue (para saltar números usados fuera de la
-- aplicación); el siguiente real también mira el mayor e-NCF ya guardado en
-- facturacion.facturas. `fecha_vencimiento` NULL = la DGII no la exige
-- (tipos 32 y 34).
CREATE TABLE IF NOT EXISTS configuracion.secuencias (
    tipo_ecf           smallint PRIMARY KEY
                       CHECK (tipo_ecf IN (31, 32, 33, 34, 41, 43, 44, 45, 46, 47)),
    prefijo            text NOT NULL,
    desde              bigint NOT NULL CHECK (desde >= 1),
    hasta              bigint NOT NULL CHECK (hasta <= 9999999999),
    proximo            bigint NOT NULL,
    fecha_vencimiento  date,
    activa             boolean NOT NULL DEFAULT true,
    creado_en          timestamptz NOT NULL DEFAULT now(),
    actualizado_en     timestamptz NOT NULL DEFAULT now(),
    CHECK (hasta >= desde),
    CHECK (proximo >= desde)
);

-- Vendedores (para la comisión y los reportes de ventas). La factura guarda
-- `facturacion.facturas.vendedor_id` (columna de facturas_api, sin FK).
CREATE TABLE IF NOT EXISTS configuracion.vendedores (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    nombre          text NOT NULL,
    correo          text,
    comision_pct    numeric(5, 2) NOT NULL DEFAULT 0 CHECK (comision_pct BETWEEN 0 AND 100),
    activo          boolean NOT NULL DEFAULT true,
    creado_en       timestamptz NOT NULL DEFAULT now(),
    actualizado_en  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS vendedores_nombre_idx ON configuracion.vendedores (nombre);

-- Preferencias de la empresa por clave: 'inventario' ({"metodo": ...}),
-- 'correo' (SMTP) y 'whatsapp' (Meta Cloud API). Las contraseñas y tokens
-- van cifrados con CERTIFICADOS_CLAVE (nunca en claro).
CREATE TABLE IF NOT EXISTS configuracion.preferencias (
    clave           text PRIMARY KEY,
    valor           jsonb NOT NULL DEFAULT '{}'::jsonb,
    actualizado_en  timestamptz NOT NULL DEFAULT now()
);

-- Envíos automáticos de comprobantes al cliente (uno por canal y factura).
CREATE TABLE IF NOT EXISTS configuracion.envios (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    canal       text NOT NULL CHECK (canal IN ('correo', 'whatsapp')),
    factura_id  uuid,              -- facturacion.facturas.id (sin FK); NULL = envío de prueba
    destino     text NOT NULL,
    estado      text NOT NULL CHECK (estado IN ('enviado', 'error')),
    error       text,
    enviado_en  timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS envios_canal_factura_idx ON configuracion.envios (canal, factura_id)
    WHERE factura_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS envios_enviado_en_idx ON configuracion.envios (enviado_en DESC);
"""
