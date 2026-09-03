-- jsonb NO preserva el orden de las claves de un objeto JSON (Postgres lo
-- reordena internamente) -- pero xml_builder.py (y construirDatos en el
-- frontend) dependen enteramente del orden de inserción de las claves para
-- producir los elementos del XML en la secuencia que exige cada XSD. Cambiar
-- `datos` a `json` (que sí preserva el texto original tal cual se insertó)
-- corrige eso. `dgii_respuesta` se deja como jsonb: es solo para mostrar la
-- respuesta cruda de la DGII, su orden no importa.
ALTER TABLE facturacion.facturas ALTER COLUMN datos TYPE json USING datos::json;
