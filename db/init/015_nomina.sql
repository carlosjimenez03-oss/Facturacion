-- Kontara Nómina (esquema `nomina`) de erp-api: empresa empleadora,
-- parámetros de ley, empleados, períodos y líneas de nómina.
--
-- Igual que 007/008/009/010: este archivo es para el Postgres local de
-- desarrollo. En producción (Supabase) services/erp_api lo crea por sí
-- mismo al arrancar, con exactamente este mismo DDL
-- (services/erp_api/app/migraciones.py, DDL_NOMINA). Si cambia uno, cambie
-- el otro -- lo verifica services/erp_api/tests/test_migraciones.py.

-- Kontara Nómina: empresa empleadora, parámetros de ley por fecha de
-- vigencia (tasas TSS, topes en salarios mínimos cotizables y escala del
-- ISR de asalariados), empleados, períodos de nómina y sus líneas. Las
-- fuentes de cada valor están en docs/NOMINA.md; todo es editable en
-- Nómina › Configuración (los INSERT de abajo solo siembran lo que falte).
CREATE SCHEMA IF NOT EXISTS nomina;

-- Una sola fila (id = 1). `tasa_srl` = tasa del Seguro de Riesgos Laborales
-- de la empresa (1 % fijo + 0.1 % a 0.3 % según su riesgo).
CREATE TABLE IF NOT EXISTS nomina.empresa (
    id              smallint PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    rnc             text,
    razon_social    text,
    categoria       text,
    municipio       text,
    contacto_nombre text,
    tasa_srl        numeric(6, 4) NOT NULL DEFAULT 0.0110 CHECK (tasa_srl >= 0 AND tasa_srl < 0.1),
    frecuencia_pago text NOT NULL DEFAULT 'mensual' CHECK (frecuencia_pago IN ('mensual', 'quincenal')),
    dia_pago        smallint NOT NULL DEFAULT 30 CHECK (dia_pago BETWEEN 1 AND 31),
    banco           text,
    cuenta_origen   text,
    actualizado_en  timestamptz NOT NULL DEFAULT now()
);

-- Parámetros de ley vigentes desde `vigente_desde` (hasta la fila
-- siguiente). Tasas como fracción (0.0287 = 2.87 %); topes en cantidad de
-- salarios mínimos cotizables; `escala_isr` = tramos anuales
-- [{"desde", "tasa", "fijo"}]; `regalia_tope_exento` NULL = regalía
-- pascual exenta sin tope.
CREATE TABLE IF NOT EXISTS nomina.parametros (
    vigente_desde                 date PRIMARY KEY,
    salario_minimo_cotizable      numeric(14, 2) NOT NULL CHECK (salario_minimo_cotizable > 0),
    afp_empleado                  numeric(7, 5) NOT NULL CHECK (afp_empleado >= 0 AND afp_empleado < 1),
    afp_empleador                 numeric(7, 5) NOT NULL CHECK (afp_empleador >= 0 AND afp_empleador < 1),
    sfs_empleado                  numeric(7, 5) NOT NULL CHECK (sfs_empleado >= 0 AND sfs_empleado < 1),
    sfs_empleador                 numeric(7, 5) NOT NULL CHECK (sfs_empleador >= 0 AND sfs_empleador < 1),
    srl_empleador                 numeric(7, 5) NOT NULL CHECK (srl_empleador >= 0 AND srl_empleador < 1),
    infotep_empleador             numeric(7, 5) NOT NULL CHECK (infotep_empleador >= 0 AND infotep_empleador < 1),
    infotep_bonificacion_empleado numeric(7, 5) NOT NULL
                                  CHECK (infotep_bonificacion_empleado >= 0 AND infotep_bonificacion_empleado < 1),
    tope_afp_salarios             numeric(6, 2) NOT NULL CHECK (tope_afp_salarios > 0),
    tope_sfs_salarios             numeric(6, 2) NOT NULL CHECK (tope_sfs_salarios > 0),
    tope_srl_salarios             numeric(6, 2) NOT NULL CHECK (tope_srl_salarios > 0),
    escala_isr                    jsonb NOT NULL,
    regalia_tope_exento           numeric(14, 2) CHECK (regalia_tope_exento >= 0),
    divisor_salario_diario        numeric(6, 2) NOT NULL DEFAULT 23.83 CHECK (divisor_salario_diario > 0),
    horas_jornada                 numeric(4, 2) NOT NULL DEFAULT 8 CHECK (horas_jornada > 0),
    recargo_horas_extra           numeric(5, 4) NOT NULL DEFAULT 0.35 CHECK (recargo_horas_extra >= 0),
    recargo_horas_extra_exceso    numeric(5, 4) NOT NULL DEFAULT 1.00 CHECK (recargo_horas_extra_exceso >= 0),
    fuente                        text,
    actualizado_en                timestamptz NOT NULL DEFAULT now()
);

INSERT INTO nomina.parametros (
    vigente_desde, salario_minimo_cotizable, afp_empleado, afp_empleador, sfs_empleado, sfs_empleador,
    srl_empleador, infotep_empleador, infotep_bonificacion_empleado, tope_afp_salarios, tope_sfs_salarios,
    tope_srl_salarios, escala_isr, regalia_tope_exento, fuente
) VALUES
    ('2025-04-01', 21674.80, 0.0287, 0.0710, 0.0304, 0.0709, 0.0110, 0.0100, 0.0050, 20, 10, 4,
     '[{"desde": "0.00", "tasa": "0", "fijo": "0.00"}, {"desde": "416220.00", "tasa": "0.15", "fijo": "0.00"}, {"desde": "624329.00", "tasa": "0.20", "fijo": "31216.00"}, {"desde": "867123.00", "tasa": "0.25", "fijo": "79776.00"}]',
     NULL, 'TSS: salario mínimo cotizable RD$21,674.80 (abr. 2025 - ene. 2026). DGII: escala ISR de asalariados 2025'),
    ('2026-02-01', 23223.00, 0.0287, 0.0710, 0.0304, 0.0709, 0.0110, 0.0100, 0.0050, 20, 10, 4,
     '[{"desde": "0.00", "tasa": "0", "fijo": "0.00"}, {"desde": "416220.00", "tasa": "0.15", "fijo": "0.00"}, {"desde": "624329.00", "tasa": "0.20", "fijo": "31216.00"}, {"desde": "867123.00", "tasa": "0.25", "fijo": "79776.00"}]',
     NULL, 'TSS Res. 01-2025: salario mínimo cotizable RD$23,223.00 desde el 01/02/2026. DGII Res. DDG-AR1-2026-00001: escala ISR 2026')
ON CONFLICT (vigente_desde) DO NOTHING;

-- Empleados. `cedula` solo dígitos (11). `tipo_pago` decide en qué
-- períodos entra (mensual o quincenal). Dar de baja = estado 'inactivo'.
CREATE TABLE IF NOT EXISTS nomina.empleados (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    cedula          text NOT NULL UNIQUE CHECK (cedula ~ '^[0-9]{11}$'),
    nombre          text NOT NULL CHECK (btrim(nombre) <> ''),
    cargo           text,
    departamento    text,
    fecha_ingreso   date NOT NULL,
    salario_mensual numeric(14, 2) NOT NULL CHECK (salario_mensual > 0),
    tipo_pago       text NOT NULL DEFAULT 'mensual' CHECK (tipo_pago IN ('mensual', 'quincenal')),
    banco           text,
    cuenta_bancaria text,
    tipo_cuenta     text CHECK (tipo_cuenta IN ('ahorro', 'corriente')),
    afp             text,
    ars             text,
    correo          text,
    estado          text NOT NULL DEFAULT 'activo' CHECK (estado IN ('activo', 'inactivo')),
    fecha_salida    date,
    creado_en       timestamptz NOT NULL DEFAULT now(),
    actualizado_en  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS empleados_nombre_idx ON nomina.empleados (lower(nombre));

-- Período de nómina: borrador -> calculada -> aprobada -> pagada -> cerrada.
-- `parametros` guarda la copia de los parámetros con que se calculó (así
-- editar la tabla de parámetros no cambia una nómina ya calculada).
CREATE TABLE IF NOT EXISTS nomina.periodos (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tipo         text NOT NULL CHECK (tipo IN ('mensual', 'quincenal')),
    anio         smallint NOT NULL CHECK (anio BETWEEN 2000 AND 2100),
    mes          smallint NOT NULL CHECK (mes BETWEEN 1 AND 12),
    quincena     smallint CHECK (quincena IN (1, 2)),
    fecha_inicio date NOT NULL,
    fecha_fin    date NOT NULL,
    fecha_pago   date NOT NULL,
    estado       text NOT NULL DEFAULT 'borrador'
                 CHECK (estado IN ('borrador', 'calculada', 'aprobada', 'pagada', 'cerrada')),
    parametros   jsonb,
    tasa_srl     numeric(6, 4),
    notas        text,
    calculado_en timestamptz,
    aprobado_en  timestamptz,
    pagado_en    timestamptz,
    cerrado_en   timestamptz,
    creado_en    timestamptz NOT NULL DEFAULT now(),
    CHECK ((tipo = 'mensual') = (quincena IS NULL))
);

CREATE UNIQUE INDEX IF NOT EXISTS periodos_unico_idx ON nomina.periodos (tipo, anio, mes, COALESCE(quincena, 0));

-- Una línea por empleado y período: copia de los datos del empleado al
-- calcular, novedades capturadas (horas extra, otros ingresos, descuentos)
-- y el resultado (NULL mientras no se calcule).
CREATE TABLE IF NOT EXISTS nomina.lineas (
    periodo_id              uuid NOT NULL REFERENCES nomina.periodos (id) ON DELETE CASCADE,
    empleado_id             uuid NOT NULL REFERENCES nomina.empleados (id),
    cedula                  text NOT NULL,
    nombre                  text NOT NULL,
    cargo                   text,
    departamento            text,
    salario_mensual         numeric(14, 2) NOT NULL,
    banco                   text,
    cuenta_bancaria         text,
    tipo_cuenta             text,
    horas_extra_35          numeric(8, 2) NOT NULL DEFAULT 0 CHECK (horas_extra_35 >= 0),
    horas_extra_100         numeric(8, 2) NOT NULL DEFAULT 0 CHECK (horas_extra_100 >= 0),
    otros_ingresos          numeric(14, 2) NOT NULL DEFAULT 0 CHECK (otros_ingresos >= 0),
    bonificacion            numeric(14, 2) NOT NULL DEFAULT 0 CHECK (bonificacion >= 0),
    ingresos_exentos        numeric(14, 2) NOT NULL DEFAULT 0 CHECK (ingresos_exentos >= 0),
    regalia                 numeric(14, 2) NOT NULL DEFAULT 0 CHECK (regalia >= 0),
    otros_descuentos        numeric(14, 2) NOT NULL DEFAULT 0 CHECK (otros_descuentos >= 0),
    salario                 numeric(14, 2),
    horas_extra             numeric(14, 2),
    total_ingresos          numeric(14, 2),
    salario_cotizable       numeric(14, 2),
    base_isr                numeric(14, 2),
    afp                     numeric(14, 2),
    sfs                     numeric(14, 2),
    isr                     numeric(14, 2),
    infotep_empleado        numeric(14, 2),
    total_descuentos        numeric(14, 2),
    neto                    numeric(14, 2),
    afp_empleador           numeric(14, 2),
    sfs_empleador           numeric(14, 2),
    srl                     numeric(14, 2),
    infotep                 numeric(14, 2),
    total_aportes_empleador numeric(14, 2),
    costo_total             numeric(14, 2),
    pagado                  boolean NOT NULL DEFAULT false,
    pagado_en               timestamptz,
    referencia_pago         text,
    PRIMARY KEY (periodo_id, empleado_id)
);

CREATE INDEX IF NOT EXISTS lineas_empleado_idx ON nomina.lineas (empleado_id);
