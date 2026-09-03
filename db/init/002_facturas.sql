-- Facturas generadas desde el frontend (borrador -> XML -> firmado -> enviado).
--
-- Igual que 001_schema.sql, este archivo se ejecuta una sola vez, la primera
-- vez que arranca el contenedor de Postgres con un volumen de datos vacío
-- (docker-entrypoint-initdb.d corre todos los *.sql en orden alfabético).
-- No editar 001_schema.sql ni este archivo tras su primer despliegue — para
-- cambios de esquema futuros, agregar un nuevo 003_*.sql.

CREATE SCHEMA IF NOT EXISTS facturacion;

CREATE TABLE IF NOT EXISTS facturacion.facturas (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tipo_ecf     smallint NOT NULL,       -- 31,32,33,34,41,43,44,45,46,47
    ambiente     text NOT NULL,           -- 'test' | 'qa' | 'production'
    rnc_emisor   text NOT NULL,
    encf         text,                    -- e-NCF asignado por el usuario (rango autorizado por la DGII)
    estado       text NOT NULL DEFAULT 'borrador',
                 -- 'borrador' | 'generado' | 'firmado' | 'enviado' | 'aceptado' | 'rechazado' | 'error'
    datos        jsonb NOT NULL,          -- estructura editable (Encabezado/Emisor/Comprador/DetallesItems/...)
    xml_generado text,                    -- XML sin firmar, generado desde `datos`
    xml_firmado  text,                    -- XML firmado (XML-DSig), lo que se envía a la DGII
    track_id     text,                    -- trackId devuelto por la DGII al enviar
    dgii_estado  text,
    dgii_codigo  text,
    dgii_respuesta jsonb,
    error        text,
    creado_en    timestamptz NOT NULL DEFAULT now(),
    actualizado_en timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS facturas_tipo_ecf_idx ON facturacion.facturas (tipo_ecf);
CREATE INDEX IF NOT EXISTS facturas_estado_idx ON facturacion.facturas (estado);
CREATE INDEX IF NOT EXISTS facturas_encf_idx ON facturacion.facturas (encf);
CREATE INDEX IF NOT EXISTS facturas_creado_en_idx ON facturacion.facturas (creado_en DESC);

-- El rol web_anon ya existe (creado en 001_schema.sql). Se le da lectura de
-- facturas también, para exponerlas vía PostgREST igual que envios_dgii —
-- ver la nota de seguridad en docs/DOKPLOY.md antes de exponer un dominio
-- público a PostgREST (contiene datos de facturación, no solo auditoría).
GRANT SELECT ON facturacion.facturas TO web_anon;
