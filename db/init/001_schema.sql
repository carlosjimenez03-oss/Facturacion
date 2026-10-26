-- Esquema de auditoría de envíos a la DGII.
--
-- Se ejecuta automáticamente una sola vez, la primera vez que arranca el
-- contenedor de Postgres con un volumen de datos vacío (mecanismo estándar
-- de la imagen oficial: todo lo que esté en /docker-entrypoint-initdb.d se
-- corre como el usuario definido en POSTGRES_USER). No se vuelve a ejecutar
-- en arranques posteriores — para cambios de esquema futuros, agregar un
-- nuevo archivo 002_*.sql (no editar este) o usar una migración manual.
--
-- Esta tabla se expone de solo lectura a través de PostgREST (rol
-- `web_anon`, ver docker-compose.dokploy.yml). Las escrituras SOLO ocurren
-- desde los módulos FastAPI vía asyncpg (libs/ecf_common/db.py), nunca a
-- través de la API REST de PostgREST.

CREATE SCHEMA IF NOT EXISTS facturacion;

CREATE TABLE IF NOT EXISTS facturacion.envios_dgii (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    modulo      text NOT NULL,        -- p.ej. 'recepcion', 'recepcion_rfce', 'aprobacion_comercial', 'anulacion_encf'
    ambiente    text NOT NULL,        -- 'test' | 'qa' | 'production'
    rnc_emisor  text NOT NULL,
    encf        text,                 -- e-NCF del comprobante (si aplica; anulación cubre rangos, puede ser NULL)
    track_id    text,                 -- trackId retornado por la DGII (si aplica)
    estado      text,                 -- estado/resultado informado por la DGII
    codigo      text,                 -- código de resultado informado por la DGII
    respuesta   jsonb,                -- cuerpo completo de la respuesta de la DGII
    error       text,                 -- mensaje de error si la llamada a la DGII falló
    creado_en   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS envios_dgii_encf_idx ON facturacion.envios_dgii (encf);
CREATE INDEX IF NOT EXISTS envios_dgii_track_id_idx ON facturacion.envios_dgii (track_id);
CREATE INDEX IF NOT EXISTS envios_dgii_creado_en_idx ON facturacion.envios_dgii (creado_en DESC);

-- Rol anónimo de PostgREST (sin login): las peticiones sin autenticar caen
-- en este rol. Solo tiene permiso de lectura — las escrituras nunca pasan
-- por PostgREST. El rol de conexión de PostgREST (POSTGRES_USER, ver
-- PGRST_DB_URI) necesita poder cambiarse a este rol por cada solicitud.
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'web_anon') THEN
        CREATE ROLE web_anon NOLOGIN;
    END IF;
END $$;

-- Con el nombre del rol, no CURRENT_USER: en la imagen de Supabase
-- (supautils) `GRANT ... TO CURRENT_USER` de un no superusuario tumba el servidor.
DO $$
BEGIN
    EXECUTE format('GRANT web_anon TO %I', current_user);
END $$;
GRANT USAGE ON SCHEMA facturacion TO web_anon;
GRANT SELECT ON facturacion.envios_dgii TO web_anon;
