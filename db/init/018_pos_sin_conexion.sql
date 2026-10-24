-- POS sin conexión: clave de idempotencia de la creación de facturas.
--
-- El POS guarda en el navegador las ventas hechas sin conexión con el
-- servidor, cada una con un UUID generado al cobrar; al reconectar las crea
-- en facturas_api con ese UUID (`clave_idempotencia`). Un reintento con la
-- misma clave devuelve la factura ya creada: nunca dos e-CF por una venta.
-- En producción (Supabase) facturas_api lo aplica al arrancar con
-- exactamente este mismo DDL (services/facturas_api/app/migraciones.py,
-- DDL_IDEMPOTENCIA); si cambia uno, cambie el otro (lo verifica
-- services/facturas_api/tests/test_migraciones.py). Ver
-- docs/dgii/CONTINGENCIA.md, "POS sin conexión".

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'facturacion' AND table_name = 'facturas' AND column_name = 'clave_idempotencia'
    ) THEN
        ALTER TABLE facturacion.facturas ADD COLUMN IF NOT EXISTS clave_idempotencia uuid;
    END IF;
    IF to_regclass('facturacion.facturas_clave_idempotencia_idx') IS NULL THEN
        CREATE UNIQUE INDEX IF NOT EXISTS facturas_clave_idempotencia_idx ON facturacion.facturas (clave_idempotencia)
            WHERE clave_idempotencia IS NOT NULL;
    END IF;
END;
$$;
