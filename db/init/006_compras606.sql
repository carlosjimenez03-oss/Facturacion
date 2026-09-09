-- Renglones para el Formato 606 (Compras de Bienes y Servicios, Norma
-- General 07-2018/05-2019). Ver docs/dgii/FORMATO_606.md para el formato
-- completo del archivo de texto y las reglas de validación -- este esquema
-- solo persiste los 23 campos del detalle (más metadata de origen).
--
-- Poblado desde dos flujos, ambos best-effort vía
-- libs.ecf_common.compras606.registrar_compra_606 (mismo patrón no-blocking
-- que libs.ecf_common.db.registrar_envio -- un fallo al registrar un renglón
-- de compras606 NUNCA debe interrumpir la recepción de un e-CF ni el envío
-- de una factura, que ya sucedieron):
--   - services/receptor_recepcion, al recibir un e-CF de un proveedor.
--   - services/facturas_api, al enviar exitosamente una factura autoemitida
--     de tipo 41 (Compras), 43 (Gastos Menores) o 47 (Pagos al Exterior).
-- El servicio services/formato_606 es quien LEE y permite clasificar estos
-- renglones (completar los campos que ningún e-CF trae, como Tipo de Bienes
-- y Forma de Pago) y generar el archivo final.
--
-- Igual que los demás archivos de db/init: se ejecuta una sola vez, la
-- primera vez que arranca el contenedor de Postgres con un volumen de datos
-- vacío. No editar este archivo tras su primer despliegue -- para cambios
-- de esquema futuros, agregar un nuevo 00N_*.sql. En un servidor ya
-- desplegado, aplicar este archivo a mano (igual que se documenta para
-- 004_inventario.sql en docs/DOKPLOY.md).

CREATE SCHEMA IF NOT EXISTS compras606;

CREATE TABLE IF NOT EXISTS compras606.registros (
    id                          uuid PRIMARY KEY DEFAULT gen_random_uuid(),

    periodo                     text NOT NULL,       -- AAAAMM, derivado de fecha_comprobante
    origen                      text NOT NULL,        -- 'receptor_recepcion' | 'auto_emitido' | 'manual'
    referencia_tipo             text,                 -- 'envio_dgii' | 'factura' | NULL (manual)
    referencia_id               text,

    rnc_cedula                  text NOT NULL,
    tipo_id                     smallint NOT NULL,     -- 1=RNC 2=Cédula
    tipo_bienes_servicios       smallint,               -- 1-11, NULL hasta clasificar (bloquea generación)
    ncf                         text NOT NULL,
    ncf_modificado              text,
    fecha_comprobante           text NOT NULL,          -- AAAAMMDD
    fecha_pago                  text,                   -- AAAAMMDD

    monto_servicios             numeric(18, 2) NOT NULL DEFAULT 0,
    monto_bienes                numeric(18, 2) NOT NULL DEFAULT 0,
    itbis_facturado              numeric(18, 2) NOT NULL DEFAULT 0,
    itbis_retenido               numeric(18, 2) NOT NULL DEFAULT 0,
    itbis_proporcionalidad       numeric(18, 2) NOT NULL DEFAULT 0,
    itbis_costo                  numeric(18, 2) NOT NULL DEFAULT 0,
    itbis_percibido               numeric(18, 2) NOT NULL DEFAULT 0,
    tipo_retencion_isr           smallint,               -- 1-9, opcional
    monto_retencion_renta        numeric(18, 2) NOT NULL DEFAULT 0,
    isr_percibido                 numeric(18, 2) NOT NULL DEFAULT 0,
    impuesto_selectivo_consumo   numeric(18, 2) NOT NULL DEFAULT 0,
    otros_impuestos               numeric(18, 2) NOT NULL DEFAULT 0,
    monto_propina_legal           numeric(18, 2) NOT NULL DEFAULT 0,
    forma_pago                   smallint,               -- 1-7, NULL hasta clasificar (bloquea generación)

    creado_en                    timestamptz NOT NULL DEFAULT now(),
    actualizado_en                timestamptz NOT NULL DEFAULT now()
);

-- Idempotencia (un reintento de red no duplica el renglón) + réplica de la
-- regla "NCF Repetido" que la plantilla oficial valida dentro de un mismo
-- archivo -- aquí la hacemos global (un NCF de un proveedor no debe
-- reportarse dos veces en ningún periodo, igual que exige la alerta
-- "NCF ya se encuentra reportado" de la Oficina Virtual).
CREATE UNIQUE INDEX IF NOT EXISTS registros_rnc_ncf_idx ON compras606.registros (rnc_cedula, ncf);
CREATE INDEX IF NOT EXISTS registros_periodo_idx ON compras606.registros (periodo);
