-- Fase 5 · Bancos y contabilidad de erp-api: esquemas `contabilidad`
-- (catálogo de cuentas, cuentas por defecto de los asientos automáticos,
-- asientos y sus líneas, activos fijos y su depreciación) y `bancos`
-- (cuentas bancarias, movimientos, transferencias, extractos importados y
-- conciliaciones), más la columna `cuenta_bancaria_id` de ventas.cobros y
-- compras.pagos. Requiere 007_ventas.sql y 010_compras.sql.
--
-- Igual que 007-010: este archivo es para el Postgres local de desarrollo.
-- En producción (Supabase) services/erp_api lo crea por sí mismo al
-- arrancar, con exactamente este mismo DDL (services/erp_api/app/
-- migraciones.py, DDL_CONTABILIDAD y DDL_BANCOS; el bloque que siembra el
-- catálogo sale de contabilidad_calculos.ddl_semilla_catalogo). Si cambia
-- uno, cambie el otro -- lo verifica services/erp_api/tests/test_migraciones.py.

CREATE SCHEMA IF NOT EXISTS contabilidad;

-- Catálogo de cuentas (árbol por `padre_id`; el código refleja la
-- jerarquía: "1.1.01" es hija de "1.1"). `tipo` = grupo del estado
-- financiero; `naturaleza` = lado en que aumenta el saldo (las cuentas
-- "contra", como la depreciación acumulada, tienen la contraria a su
-- grupo). Solo las cuentas que `acepta_movimientos` (hojas) llevan líneas
-- de asiento. `sistema` = sembrada con el catálogo sugerido.
CREATE TABLE IF NOT EXISTS contabilidad.cuentas (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    codigo              text NOT NULL CHECK (codigo ~ '^[0-9]+([.][0-9]+)*$'),
    nombre              text NOT NULL,
    tipo                text NOT NULL
                        CHECK (tipo IN ('activo', 'pasivo', 'patrimonio', 'ingreso', 'costo', 'gasto')),
    naturaleza          text NOT NULL CHECK (naturaleza IN ('deudora', 'acreedora')),
    acepta_movimientos  boolean NOT NULL DEFAULT true,
    padre_id            uuid REFERENCES contabilidad.cuentas (id) ON DELETE RESTRICT,
    activa              boolean NOT NULL DEFAULT true,
    sistema             boolean NOT NULL DEFAULT false,
    descripcion         text,
    creado_en           timestamptz NOT NULL DEFAULT now(),
    actualizado_en      timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS cuentas_codigo_idx ON contabilidad.cuentas (codigo);
CREATE INDEX IF NOT EXISTS cuentas_padre_idx ON contabilidad.cuentas (padre_id);

-- Cuenta del catálogo que usa cada concepto de los asientos automáticos
-- (ver contabilidad_calculos.CONCEPTOS). Sin fila = el concepto no tiene
-- cuenta y los documentos que la necesitan quedan "sin contabilizar".
CREATE TABLE IF NOT EXISTS contabilidad.cuentas_por_defecto (
    concepto        text PRIMARY KEY,
    cuenta_id       uuid NOT NULL REFERENCES contabilidad.cuentas (id) ON DELETE RESTRICT,
    actualizado_en  timestamptz NOT NULL DEFAULT now()
);

CREATE SEQUENCE IF NOT EXISTS contabilidad.asientos_numero_seq;

-- Asientos (libro diario). `origen` = 'manual' o el documento que lo generó
-- (`origen_id`: facturacion.facturas.id, ventas.cobros.id,
-- compras606.registros.id, compras.pagos.id, bancos.movimientos.id,
-- bancos.transferencias.id o el período AAAAMM de la depreciación). Un
-- documento tiene a lo sumo un asiento vigente (índice único parcial):
-- generar es idempotente. `huella` = resumen de las líneas generadas (si el
-- documento o las cuentas por defecto cambian, el asiento se anula y se
-- genera de nuevo). Solo los 'contabilizado' cuentan en los reportes.
CREATE TABLE IF NOT EXISTS contabilidad.asientos (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    numero             bigint NOT NULL DEFAULT nextval('contabilidad.asientos_numero_seq'),
    fecha              date NOT NULL,
    concepto           text NOT NULL,
    referencia         text,
    estado             text NOT NULL DEFAULT 'borrador'
                       CHECK (estado IN ('borrador', 'contabilizado', 'anulado')),
    origen             text NOT NULL DEFAULT 'manual'
                       CHECK (origen IN ('manual', 'venta', 'nota_credito', 'nota_debito', 'cobro', 'compra', 'pago',
                                         'movimiento', 'transferencia', 'depreciacion')),
    origen_id          text,
    huella             text,
    total              numeric(18, 2) NOT NULL DEFAULT 0,
    contabilizado_en   timestamptz,
    anulado_en         timestamptz,
    motivo_anulacion   text,
    creado_en          timestamptz NOT NULL DEFAULT now(),
    actualizado_en     timestamptz NOT NULL DEFAULT now(),
    CHECK ((origen = 'manual') = (origen_id IS NULL))
);

CREATE UNIQUE INDEX IF NOT EXISTS asientos_numero_idx ON contabilidad.asientos (numero);
CREATE UNIQUE INDEX IF NOT EXISTS asientos_origen_idx
    ON contabilidad.asientos (origen, origen_id) WHERE estado <> 'anulado' AND origen <> 'manual';
CREATE INDEX IF NOT EXISTS asientos_fecha_idx ON contabilidad.asientos (fecha);

CREATE TABLE IF NOT EXISTS contabilidad.asiento_lineas (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    asiento_id   uuid NOT NULL REFERENCES contabilidad.asientos (id) ON DELETE CASCADE,
    linea        integer NOT NULL,
    cuenta_id    uuid NOT NULL REFERENCES contabilidad.cuentas (id) ON DELETE RESTRICT,
    descripcion  text,
    debito       numeric(18, 2) NOT NULL DEFAULT 0 CHECK (debito >= 0),
    credito      numeric(18, 2) NOT NULL DEFAULT 0 CHECK (credito >= 0),
    CHECK ((debito = 0) <> (credito = 0))
);

CREATE INDEX IF NOT EXISTS asiento_lineas_asiento_idx ON contabilidad.asiento_lineas (asiento_id);
CREATE INDEX IF NOT EXISTS asiento_lineas_cuenta_idx ON contabilidad.asiento_lineas (cuenta_id);

-- Activos fijos. `categoria` = categoría del Art. 287 literal e) del
-- Código Tributario (1 = 5 %, 2 = 25 %, 3 = 15 % anual). `metodo` es el de
-- los asientos de depreciación ('linea_recta' con `vida_util_meses`, o
-- 'dgii' = el método fiscal); la depreciación fiscal del IR-2 siempre usa
-- el método 'dgii'. `depreciar_desde` = primer mes (AAAAMM) que se
-- deprecia; `depreciacion_acumulada_inicial` = la que ya tenía al
-- registrarlo (activos comprados antes de usar el sistema).
CREATE TABLE IF NOT EXISTS contabilidad.activos_fijos (
    id                              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    descripcion                     text NOT NULL,
    categoria                       smallint NOT NULL CHECK (categoria IN (1, 2, 3)),
    fecha_adquisicion               date NOT NULL,
    costo                           numeric(18, 2) NOT NULL CHECK (costo > 0),
    valor_residual                  numeric(18, 2) NOT NULL DEFAULT 0 CHECK (valor_residual >= 0),
    metodo                          text NOT NULL DEFAULT 'dgii' CHECK (metodo IN ('linea_recta', 'dgii')),
    vida_util_meses                 integer CHECK (vida_util_meses > 0),
    depreciar_desde                 text NOT NULL CHECK (depreciar_desde ~ '^[0-9]{4}(0[1-9]|1[0-2])$'),
    depreciacion_acumulada_inicial  numeric(18, 2) NOT NULL DEFAULT 0 CHECK (depreciacion_acumulada_inicial >= 0),
    cuenta_activo_id                uuid REFERENCES contabilidad.cuentas (id) ON DELETE RESTRICT,
    registro606_id                  uuid,  -- compras606.registros.id (sin FK: ese esquema es de formato_606)
    numero_serie                    text,
    ubicacion                       text,
    fecha_baja                      date,
    notas                           text,
    creado_en                       timestamptz NOT NULL DEFAULT now(),
    actualizado_en                  timestamptz NOT NULL DEFAULT now(),
    CHECK (metodo <> 'linea_recta' OR vida_util_meses IS NOT NULL)
);

-- Depreciación registrada por activo y mes (la del asiento del mes).
CREATE TABLE IF NOT EXISTS contabilidad.depreciaciones (
    activo_id   uuid NOT NULL REFERENCES contabilidad.activos_fijos (id) ON DELETE RESTRICT,
    periodo     text NOT NULL,
    monto       numeric(18, 2) NOT NULL CHECK (monto > 0),
    asiento_id  uuid NOT NULL REFERENCES contabilidad.asientos (id) ON DELETE CASCADE,
    PRIMARY KEY (activo_id, periodo)
);

CREATE INDEX IF NOT EXISTS depreciaciones_periodo_idx ON contabilidad.depreciaciones (periodo);

-- Catálogo sugerido y cuentas por defecto, solo si el catálogo está vacío.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM contabilidad.cuentas) THEN
        INSERT INTO contabilidad.cuentas (codigo, nombre, tipo, naturaleza, acepta_movimientos, sistema)
        SELECT v.codigo, v.nombre, v.tipo, v.naturaleza, v.acepta, true
        FROM (VALUES
            ('1', 'Activos', 'activo', 'deudora', false),
            ('1.1', 'Activos corrientes', 'activo', 'deudora', false),
            ('1.1.01', 'Efectivo y equivalentes de efectivo', 'activo', 'deudora', false),
            ('1.1.01.01', 'Caja general', 'activo', 'deudora', true),
            ('1.1.01.02', 'Caja chica', 'activo', 'deudora', true),
            ('1.1.01.03', 'Bancos', 'activo', 'deudora', true),
            ('1.1.02', 'Cuentas por cobrar', 'activo', 'deudora', false),
            ('1.1.02.01', 'Cuentas por cobrar clientes', 'activo', 'deudora', true),
            ('1.1.02.02', 'Otras cuentas por cobrar', 'activo', 'deudora', true),
            ('1.1.03', 'Inventarios', 'activo', 'deudora', false),
            ('1.1.03.01', 'Inventario de mercancías', 'activo', 'deudora', true),
            ('1.1.04', 'Impuestos y anticipos', 'activo', 'deudora', false),
            ('1.1.04.01', 'ITBIS pagado por compensar (adelantado)', 'activo', 'deudora', true),
            ('1.1.04.02', 'Anticipos de ISR', 'activo', 'deudora', true),
            ('1.1.04.03', 'ISR retenido por terceros', 'activo', 'deudora', true),
            ('1.1.04.04', 'ITBIS retenido por terceros', 'activo', 'deudora', true),
            ('1.1.05', 'Gastos pagados por anticipado', 'activo', 'deudora', false),
            ('1.1.05.01', 'Seguros pagados por anticipado', 'activo', 'deudora', true),
            ('1.2', 'Activos no corrientes', 'activo', 'deudora', false),
            ('1.2.01', 'Propiedad, planta y equipo', 'activo', 'deudora', false),
            ('1.2.01.01', 'Edificaciones (categoría 1)', 'activo', 'deudora', true),
            ('1.2.01.02', 'Vehículos, mobiliario y equipos de oficina y cómputo (categoría 2)', 'activo', 'deudora', true),
            ('1.2.01.03', 'Maquinaria y otros activos depreciables (categoría 3)', 'activo', 'deudora', true),
            ('1.2.01.04', 'Terrenos', 'activo', 'deudora', true),
            ('1.2.02', 'Depreciación acumulada', 'activo', 'acreedora', false),
            ('1.2.02.01', 'Depreciación acumulada edificaciones', 'activo', 'acreedora', true),
            ('1.2.02.02', 'Depreciación acumulada vehículos y equipos', 'activo', 'acreedora', true),
            ('1.2.02.03', 'Depreciación acumulada maquinaria y otros', 'activo', 'acreedora', true),
            ('2', 'Pasivos', 'pasivo', 'acreedora', false),
            ('2.1', 'Pasivos corrientes', 'pasivo', 'acreedora', false),
            ('2.1.01', 'Cuentas por pagar', 'pasivo', 'acreedora', false),
            ('2.1.01.01', 'Cuentas por pagar proveedores', 'pasivo', 'acreedora', true),
            ('2.1.01.02', 'Tarjetas de crédito por pagar', 'pasivo', 'acreedora', true),
            ('2.1.02', 'Impuestos por pagar', 'pasivo', 'acreedora', false),
            ('2.1.02.01', 'ITBIS por pagar', 'pasivo', 'acreedora', true),
            ('2.1.02.02', 'Otros impuestos y propina legal por pagar', 'pasivo', 'acreedora', true),
            ('2.1.02.03', 'ISR por pagar', 'pasivo', 'acreedora', true),
            ('2.1.03', 'Retenciones por pagar', 'pasivo', 'acreedora', false),
            ('2.1.03.01', 'ITBIS retenido por pagar', 'pasivo', 'acreedora', true),
            ('2.1.03.02', 'ISR retenido por pagar', 'pasivo', 'acreedora', true),
            ('2.1.04', 'Nómina y acumulaciones por pagar', 'pasivo', 'acreedora', false),
            ('2.1.04.01', 'Nómina y TSS por pagar', 'pasivo', 'acreedora', true),
            ('2.2', 'Pasivos no corrientes', 'pasivo', 'acreedora', false),
            ('2.2.01', 'Préstamos bancarios a largo plazo', 'pasivo', 'acreedora', true),
            ('3', 'Patrimonio', 'patrimonio', 'acreedora', false),
            ('3.1', 'Capital', 'patrimonio', 'acreedora', false),
            ('3.1.01', 'Capital social', 'patrimonio', 'acreedora', true),
            ('3.2', 'Reservas', 'patrimonio', 'acreedora', false),
            ('3.2.01', 'Reserva legal', 'patrimonio', 'acreedora', true),
            ('3.3', 'Resultados', 'patrimonio', 'acreedora', false),
            ('3.3.01', 'Resultados acumulados', 'patrimonio', 'acreedora', true),
            ('3.3.02', 'Resultado del ejercicio', 'patrimonio', 'acreedora', true),
            ('4', 'Ingresos', 'ingreso', 'acreedora', false),
            ('4.1', 'Ingresos operacionales', 'ingreso', 'acreedora', false),
            ('4.1.01', 'Ventas gravadas', 'ingreso', 'acreedora', true),
            ('4.1.02', 'Ventas exentas', 'ingreso', 'acreedora', true),
            ('4.1.03', 'Exportaciones', 'ingreso', 'acreedora', true),
            ('4.1.04', 'Devoluciones y descuentos en ventas', 'ingreso', 'deudora', true),
            ('4.2', 'Otros ingresos', 'ingreso', 'acreedora', false),
            ('4.2.01', 'Ingresos financieros', 'ingreso', 'acreedora', true),
            ('4.2.02', 'Otros ingresos', 'ingreso', 'acreedora', true),
            ('5', 'Costos', 'costo', 'deudora', false),
            ('5.1', 'Costo de ventas', 'costo', 'deudora', false),
            ('5.1.01', 'Costo de ventas (compras de mercancías)', 'costo', 'deudora', true),
            ('6', 'Gastos', 'gasto', 'deudora', false),
            ('6.1', 'Gastos operacionales', 'gasto', 'deudora', false),
            ('6.1.01', 'Gastos de personal', 'gasto', 'deudora', true),
            ('6.1.02', 'Trabajos, suministros y servicios', 'gasto', 'deudora', true),
            ('6.1.03', 'Arrendamientos', 'gasto', 'deudora', true),
            ('6.1.04', 'Gastos de activos fijos', 'gasto', 'deudora', true),
            ('6.1.05', 'Gastos de representación', 'gasto', 'deudora', true),
            ('6.1.06', 'Otras deducciones admitidas', 'gasto', 'deudora', true),
            ('6.1.07', 'Gastos de seguros', 'gasto', 'deudora', true),
            ('6.1.08', 'Depreciación', 'gasto', 'deudora', true),
            ('6.1.09', 'Gastos extraordinarios', 'gasto', 'deudora', true),
            ('6.1.10', 'Diferencias por redondeo', 'gasto', 'deudora', true),
            ('6.2', 'Gastos financieros', 'gasto', 'deudora', false),
            ('6.2.01', 'Intereses y gastos financieros', 'gasto', 'deudora', true),
            ('6.2.02', 'Comisiones y cargos bancarios', 'gasto', 'deudora', true)
        ) AS v (codigo, nombre, tipo, naturaleza, acepta);
        UPDATE contabilidad.cuentas h
        SET padre_id = p.id
        FROM contabilidad.cuentas p
        WHERE p.codigo = regexp_replace(h.codigo, '[.][^.]+$', '') AND h.codigo LIKE '%.%';
        INSERT INTO contabilidad.cuentas_por_defecto (concepto, cuenta_id)
        SELECT v.concepto, c.id
        FROM (VALUES
            ('caja', '1.1.01.01'),
            ('cxc_clientes', '1.1.02.01'),
            ('ventas_gravadas', '4.1.01'),
            ('ventas_exentas', '4.1.02'),
            ('exportaciones', '4.1.03'),
            ('devoluciones_ventas', '4.1.04'),
            ('itbis_por_pagar', '2.1.02.01'),
            ('otros_impuestos_por_pagar', '2.1.02.02'),
            ('redondeo', '6.1.10'),
            ('banco', '1.1.01.03'),
            ('caja_chica', '1.1.01.02'),
            ('tarjeta_credito', '2.1.01.02'),
            ('gastos_bancarios', '6.2.02'),
            ('ingresos_financieros', '4.2.01'),
            ('cxp_proveedores', '2.1.01.01'),
            ('itbis_adelantado', '1.1.04.01'),
            ('itbis_retenido_por_pagar', '2.1.03.01'),
            ('isr_retenido_por_pagar', '2.1.03.02'),
            ('gasto_606_01', '6.1.01'),
            ('gasto_606_02', '6.1.02'),
            ('gasto_606_03', '6.1.03'),
            ('gasto_606_04', '6.1.04'),
            ('gasto_606_05', '6.1.05'),
            ('gasto_606_06', '6.1.06'),
            ('gasto_606_07', '6.2.01'),
            ('gasto_606_08', '6.1.09'),
            ('gasto_606_09', '5.1.01'),
            ('gasto_606_10', '1.2.01.03'),
            ('gasto_606_11', '6.1.07'),
            ('activo_fijo_1', '1.2.01.01'),
            ('activo_fijo_2', '1.2.01.02'),
            ('activo_fijo_3', '1.2.01.03'),
            ('depreciacion_acumulada_1', '1.2.02.01'),
            ('depreciacion_acumulada_2', '1.2.02.02'),
            ('depreciacion_acumulada_3', '1.2.02.03'),
            ('gasto_depreciacion', '6.1.08')
        ) AS v (concepto, codigo)
        JOIN contabilidad.cuentas c ON c.codigo = v.codigo
        ON CONFLICT (concepto) DO NOTHING;
    END IF;
END;
$$;

CREATE SCHEMA IF NOT EXISTS bancos;

-- Cuentas bancarias, cajas chicas y tarjetas de crédito de la empresa.
-- `numero` se guarda completo, pero la API solo devuelve los últimos 4
-- caracteres. `saldo_inicial` = saldo al empezar el día
-- `fecha_saldo_inicial`; el saldo actual = saldo inicial + movimientos
-- desde esa fecha. `cuenta_contable_id` = su cuenta del catálogo (sin ella,
-- los asientos usan la cuenta por defecto 'banco', 'caja_chica' o
-- 'tarjeta_credito').
CREATE TABLE IF NOT EXISTS bancos.cuentas (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    nombre               text NOT NULL,
    banco                text NOT NULL,
    tipo                 text NOT NULL CHECK (tipo IN ('corriente', 'ahorro', 'caja_chica', 'tarjeta')),
    numero               text,
    moneda               text NOT NULL DEFAULT 'DOP' CHECK (moneda IN ('DOP', 'USD')),
    saldo_inicial        numeric(18, 2) NOT NULL DEFAULT 0,
    fecha_saldo_inicial  date NOT NULL,
    cuenta_contable_id   uuid REFERENCES contabilidad.cuentas (id) ON DELETE RESTRICT,
    activa               boolean NOT NULL DEFAULT true,
    notas                text,
    creado_en            timestamptz NOT NULL DEFAULT now(),
    actualizado_en       timestamptz NOT NULL DEFAULT now()
);

-- Transferencias entre cuentas propias: un par de movimientos
-- (transferencia_salida en la de origen, transferencia_entrada en la de
-- destino) creados y anulados en la misma transacción. `monto_destino` =
-- lo que llega (distinto de `monto` solo entre monedas distintas);
-- `tasa_cambio` = RD$ por US$ cuando alguna de las cuentas es en dólares.
CREATE TABLE IF NOT EXISTS bancos.transferencias (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    cuenta_origen_id   uuid NOT NULL REFERENCES bancos.cuentas (id) ON DELETE RESTRICT,
    cuenta_destino_id  uuid NOT NULL REFERENCES bancos.cuentas (id) ON DELETE RESTRICT,
    fecha              date NOT NULL,
    monto              numeric(18, 2) NOT NULL CHECK (monto > 0),
    monto_destino      numeric(18, 2) NOT NULL CHECK (monto_destino > 0),
    tasa_cambio        numeric(12, 4),
    concepto           text,
    referencia         text,
    anulada            boolean NOT NULL DEFAULT false,
    anulada_en         timestamptz,
    creado_en          timestamptz NOT NULL DEFAULT now(),
    CHECK (cuenta_origen_id <> cuenta_destino_id)
);

-- Movimientos registrados en Bancos (depósitos, retiros, cargos, intereses,
-- comisiones y las dos patas de cada transferencia). Los cobros
-- (ventas.cobros) y pagos a proveedores (compras.pagos) con
-- `cuenta_bancaria_id` también son movimientos de la cuenta, pero se leen
-- de sus tablas (no se copian aquí). `monto` siempre positivo: el tipo dice
-- si entra o sale. `cuenta_contable_id` = contrapartida del asiento (sin
-- ella, la del tipo: caja, gastos bancarios, ingresos financieros).
CREATE TABLE IF NOT EXISTS bancos.movimientos (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    cuenta_id           uuid NOT NULL REFERENCES bancos.cuentas (id) ON DELETE RESTRICT,
    fecha               date NOT NULL,
    tipo                text NOT NULL
                        CHECK (tipo IN ('deposito', 'retiro', 'cargo_bancario', 'interes', 'comision',
                                        'transferencia_entrada', 'transferencia_salida')),
    monto               numeric(18, 2) NOT NULL CHECK (monto > 0),
    descripcion         text,
    referencia          text,
    tasa_cambio         numeric(12, 4),
    cuenta_contable_id  uuid REFERENCES contabilidad.cuentas (id) ON DELETE RESTRICT,
    transferencia_id    uuid REFERENCES bancos.transferencias (id) ON DELETE RESTRICT,
    anulado             boolean NOT NULL DEFAULT false,
    anulado_en          timestamptz,
    creado_en           timestamptz NOT NULL DEFAULT now(),
    CHECK ((transferencia_id IS NULL) = (tipo NOT IN ('transferencia_entrada', 'transferencia_salida')))
);

CREATE INDEX IF NOT EXISTS movimientos_cuenta_fecha_idx ON bancos.movimientos (cuenta_id, fecha);
CREATE INDEX IF NOT EXISTS movimientos_transferencia_idx ON bancos.movimientos (transferencia_id);

-- Extractos (estados de cuenta) importados y sus líneas. `monto` con signo:
-- + crédito del banco (entra), − débito (sale). Una línea conciliada apunta
-- a su movimiento (`mov_origen` 'manual' | 'cobro' | 'pago' + `mov_id`); un
-- movimiento se concilia con a lo sumo una línea. `huella` evita duplicar
-- líneas al volver a importar el mismo archivo.
CREATE TABLE IF NOT EXISTS bancos.extractos (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    cuenta_id       uuid NOT NULL REFERENCES bancos.cuentas (id) ON DELETE RESTRICT,
    nombre_archivo  text,
    desde           date,
    hasta           date,
    saldo_final     numeric(18, 2),
    columnas        jsonb,
    importado_en    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS bancos.extracto_lineas (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    extracto_id    uuid NOT NULL REFERENCES bancos.extractos (id) ON DELETE CASCADE,
    cuenta_id      uuid NOT NULL REFERENCES bancos.cuentas (id) ON DELETE RESTRICT,
    fila           integer,
    fecha          date NOT NULL,
    descripcion    text NOT NULL,
    referencia     text,
    monto          numeric(18, 2) NOT NULL CHECK (monto <> 0),
    saldo          numeric(18, 2),
    huella         text NOT NULL,
    mov_origen     text CHECK (mov_origen IN ('manual', 'cobro', 'pago')),
    mov_id         uuid,
    conciliada_en  timestamptz,
    CHECK ((mov_origen IS NULL) = (mov_id IS NULL))
);

CREATE UNIQUE INDEX IF NOT EXISTS extracto_lineas_huella_idx ON bancos.extracto_lineas (cuenta_id, huella);
CREATE UNIQUE INDEX IF NOT EXISTS extracto_lineas_mov_idx
    ON bancos.extracto_lineas (mov_origen, mov_id) WHERE mov_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS extracto_lineas_cuenta_fecha_idx ON bancos.extracto_lineas (cuenta_id, fecha);

-- Conciliaciones cerradas: al corte `hasta`, saldo según el banco y según
-- libros, con las partidas en tránsito (movimientos que el banco aún no
-- refleja). Cerrada, nada de la cuenta con fecha <= `hasta` se puede
-- cambiar en Bancos hasta reabrirla (borrar la última).
CREATE TABLE IF NOT EXISTS bancos.conciliaciones (
    id                       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    cuenta_id                uuid NOT NULL REFERENCES bancos.cuentas (id) ON DELETE RESTRICT,
    hasta                    date NOT NULL,
    saldo_banco              numeric(18, 2) NOT NULL,
    saldo_libros             numeric(18, 2) NOT NULL,
    transito_entradas        numeric(18, 2) NOT NULL DEFAULT 0,
    transito_salidas         numeric(18, 2) NOT NULL DEFAULT 0,
    diferencia               numeric(18, 2) NOT NULL DEFAULT 0,
    lineas_conciliadas       integer NOT NULL DEFAULT 0,
    movimientos_en_transito  integer NOT NULL DEFAULT 0,
    notas                    text,
    cerrada_en               timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS conciliaciones_cuenta_hasta_idx ON bancos.conciliaciones (cuenta_id, hasta);

-- Cuenta bancaria de los cobros y pagos a proveedores (opcional; el texto
-- libre `cuenta_destino` / `cuenta_origen` se conserva por compatibilidad).
ALTER TABLE ventas.cobros
    ADD COLUMN IF NOT EXISTS cuenta_bancaria_id uuid REFERENCES bancos.cuentas (id) ON DELETE RESTRICT;
ALTER TABLE compras.pagos
    ADD COLUMN IF NOT EXISTS cuenta_bancaria_id uuid REFERENCES bancos.cuentas (id) ON DELETE RESTRICT;
CREATE INDEX IF NOT EXISTS cobros_cuenta_bancaria_idx ON ventas.cobros (cuenta_bancaria_id);
CREATE INDEX IF NOT EXISTS pagos_cuenta_bancaria_idx ON compras.pagos (cuenta_bancaria_id);
