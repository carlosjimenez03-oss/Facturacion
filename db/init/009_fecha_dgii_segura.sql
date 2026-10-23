-- Función auxiliar de erp-api para leer las fechas DD-MM-AAAA de los e-CF
-- (facturacion.facturas.datos) sin que una fecha imposible haga fallar la
-- consulta entera. Requiere el esquema `ventas` (007_ventas.sql).
--
-- Igual que 007/008: este archivo es para el Postgres local de desarrollo.
-- En producción (Supabase) services/erp_api la crea por sí mismo al
-- arrancar, con exactamente este mismo DDL (services/erp_api/app/migraciones.py,
-- DDL_FUNCIONES). Si cambia uno, cambie el otro -- lo verifica
-- services/erp_api/tests/test_migraciones.py.

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
