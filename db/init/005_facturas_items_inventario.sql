-- Vincula cada línea de una factura con un producto del catálogo de
-- inventario (inventario.productos), SIN tocar `datos` (que refleja 1:1 la
-- estructura del XSD del e-CF y no debe llevar campos ajenos a la DGII,
-- ver xml_builder.py). `items_inventario` es un array aparte:
--   [{"numero_linea": "1", "producto_id": "<uuid>", "cantidad": "2.00"}, ...]
-- que facturas_api usa para pedirle a inventario_api que valide
-- disponibilidad y luego registre la salida de mercancía al enviar la
-- factura exitosamente a la DGII (ver services/facturas_api/app/main.py).
--
-- `inventario_sincronizado` / `inventario_error`: la salida de inventario
-- se registra DESPUÉS de que la DGII ya aceptó el envío (documento fiscal
-- irreversible) -- si esa llamada a inventario_api falla, no se puede
-- fallar la respuesta de /enviar por ello. Estas columnas dejan constancia
-- de si la sincronización de stock quedó pendiente, para poder detectarlo y
-- resolverlo manualmente.
--
-- Igual que los demás archivos de db/init: se ejecuta una sola vez, la
-- primera vez que arranca el contenedor de Postgres con un volumen de datos
-- vacío. No editar este archivo tras su primer despliegue -- para cambios
-- de esquema futuros, agregar un nuevo 00N_*.sql.

ALTER TABLE facturacion.facturas
    ADD COLUMN IF NOT EXISTS items_inventario jsonb,
    ADD COLUMN IF NOT EXISTS inventario_sincronizado boolean NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS inventario_error text;
