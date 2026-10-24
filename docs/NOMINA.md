# Kontara Nómina

Módulo de nómina para República Dominicana dentro de `erp-api` y el
frontend: empleados, períodos de nómina mensuales o quincenales, cálculo de
TSS (AFP, SFS, SRL), INFOTEP e ISR de asalariados, aprobación, pagos (archivo
para el banco y recibos), reportes TSS / IR-3 / IR-13 y la configuración de
la empresa y de los parámetros de ley.

| Pieza | Archivo |
|---|---|
| Cálculos puros (con tests) | [`services/erp_api/app/nomina_calculos.py`](../services/erp_api/app/nomina_calculos.py) |
| Consultas | [`services/erp_api/app/nomina_repository.py`](../services/erp_api/app/nomina_repository.py) |
| Rutas y esquemas (`/nomina/*`) | [`services/erp_api/app/nomina.py`](../services/erp_api/app/nomina.py) |
| DDL (esquema `nomina`) | `DDL_NOMINA` en [`migraciones.py`](../services/erp_api/app/migraciones.py), copia en [`db/init/015_nomina.sql`](../db/init/015_nomina.sql) |
| Contrato con el frontend | [`frontend/src/lib/nominaApi.ts`](../frontend/src/lib/nominaApi.ts) |
| Pantallas | [`frontend/src/pages/nomina/`](../frontend/src/pages/nomina/) (diseño: `docs/diseno/prototipo-kontara/pantallas/Nomina.dc.html`) |

## Parámetros de ley (tabla `nomina.parametros`)

Nada de lo que cambia por año está fijo en el código: cada fila rige desde
`vigente_desde` hasta la fila siguiente, y una nómina usa la fila vigente al
**inicio** de su período y guarda una copia (`periodos.parametros`), así que
editar la tabla no altera nóminas aprobadas. Se editan en
**Nómina › Configuración › Parámetros por vigencia** (o `PUT
/nomina/parametros/{vigente_desde}`). Las migraciones siembran (sin pisar lo
editado, `ON CONFLICT DO NOTHING`):

| Concepto | Desde 01/04/2025 | Desde 01/02/2026 |
|---|---|---|
| Salario mínimo cotizable (TSS) | RD$21,674.80 | **RD$23,223.00** |
| Tope AFP (20 salarios) | 433,496.00 | **464,460.00** |
| Tope SFS (10 salarios) | 216,748.00 | **232,230.00** |
| Tope SRL (4 salarios) | 86,699.20 | **92,892.00** |
| AFP empleado / empleador | 2.87 % / 7.10 % | 2.87 % / 7.10 % |
| SFS empleado / empleador | 3.04 % / 7.09 % | 3.04 % / 7.09 % |
| SRL empleador (por defecto; la empresa fija la suya) | 1.10 % | 1.10 % |
| INFOTEP empleador / 0.5 % de bonificaciones del empleado | 1 % / 0.5 % | 1 % / 0.5 % |
| Escala ISR anual | igual que 2026 | exento ≤ 416,220.00 · 15 % del excedente hasta 624,329.00 · 31,216.00 + 20 % hasta 867,123.00 · 79,776.00 + 25 % |
| Regalía pascual exenta | sin tope | sin tope |

La TSS publica los topes en pesos; aquí se guardan como cantidad de salarios
mínimos (20, 10 y 4) y se calculan: si la TSS cambia el salario mínimo
cotizable, basta una fila nueva con el valor nuevo.

### Fuentes

- TSS — nuevos topes de cotización (salario mínimo cotizable RD$23,223.00
  desde el 1 de febrero de 2026, Resolución 01-2025; SRL RD$92,892.00, SFS
  RD$232,230.00, pensiones RD$464,460.00):
  <https://tss.gob.do/tss-informa-nuevos-topes-de-cotizacion-del-regimen-contributivo-del-sdss/>
- TSS — topes anteriores (RD$21,674.80 de abril 2025 a enero 2026):
  <https://tss.gob.do/tesoreria-de-la-seguridad-social-informa-nuevos-topes-de-cotizacion-para-el-regimen-contributivo/>
- TSS — preguntas frecuentes (tasas AFP 2.87 %/7.10 %, SFS 3.04 %/7.09 %, topes):
  <https://tss.gob.do/preguntas-frecuentes/>
- TSS — SRL: cuota básica fija de 1 % + cuota adicional de 0.1 % a 0.3 %
  según el riesgo, a cargo del empleador (por eso 1.10 %, 1.20 % o 1.30 %):
  <https://tss.gob.do/assets/regla_srl_v2.pdf>
- TSS — instructivo de bonificaciones: el 0.5 % de INFOTEP sobre
  bonificaciones (Ley 116-80, art. 24 lit. b) lo retiene el empleador:
  <https://tss.gob.do/assets/inst_ar_boni_v123.pdf>
- Comité Nacional de Salarios, Resolución CNS-01-2025 (salario mínimo del
  sector privado no sectorizado, base del salario mínimo cotizable):
  <https://transparencia.mt.gob.do/images/docs/base_legal/resoluciones/CNS/2025/RESOLUCION%20CNS-01-2025%20PRIVADO%20NO%20SECTORIZADO%202....pdf>
- DGII — Resolución DDG-AR1-2026-00001 (escala de retención de asalariados 2026):
  <https://dgii.gov.do/legislacion/resoluciones/Documents/DDG-%20AR1-2026-00001.pdf>
- DGII — Comunidad de Ayuda CA687, escala salarial 2026 y aclaración de que
  los tramos del art. 10 de la **Ley 30-26** rigen a partir del ejercicio
  fiscal **2027** (2026 sigue con la escala anterior):
  <https://ayuda.dgii.gov.do/conversations/impuesto-sobre-la-renta-isr/ca687-cul-es-la-escala-salarial-correspondiente-al-ao-2026-del-impuesto-sobre-la-renta-isr/696a664277932619036537b8>
- DGII — Guía 11, Retenciones del ISR (horas extra, comisiones y
  bonificaciones suman a la base del mes; pago a más tardar el día 10):
  <https://dgii.gov.do/publicacionesOficiales/bibliotecaVirtual/contribuyentes/retencionesRetribucionesComplementarias/Documents/2-Guia-11-Retenciones%20del%20Impuesto%20Sobre%20la%20Renta.pdf>
- DGII — IR-3 (declaración mensual de retenciones de asalariados):
  <https://dgii.gov.do/publicacionesOficiales/bibliotecaVirtual/contribuyentes/retencionesRetribucionesComplementarias/Documents/4-IR-3.pdf>
- DGII — Consulta sobre ISR en el salario de Navidad:
  <https://dgii.gov.do/legislacion/consultas/Documents/Julio/Consulta%2019-%20ISR%20en%20salario%20de%20navidad.pdf>
- Código de Trabajo (Ley 16-92): arts. 76 (preaviso), 80 (cesantía), 177 y
  180 (vacaciones), 203–204 (horas extra 35 % / 100 %), 219 (salario de
  Navidad). Ministerio de Trabajo: <https://mt.gob.do/>

Lo que **no** se pudo confirmar en fuente oficial y queda configurable:

- **Ley 30-26 (2027)**: fuentes secundarias indican exención de RD$480,000
  y un tramo nuevo de 27 %; no se siembra. Cuando la DGII publique la
  escala 2027, agregue una fila con `vigente_desde = 2027-01-01`.
- **Regalía pascual y el tope de 5 salarios mínimos**: el Reglamento 139-98
  limitaba la exención; según fuentes secundarias el art. 33 de la Ley 30-26
  (18/06/2026) confirma la exención sin tope. Se siembra `regalia_tope_exento
  = NULL` (sin tope); si su asesor indica otra cosa, ponga el monto exento.
- El 0.5 % **patronal** sobre bonificaciones que algunas guías mencionan no
  se calcula (la norma de la TSS solo habla de la retención al empleado).
- Fechas exactas de la TSS (feriados) y del IR-13: el vencimiento de la TSS
  se estima como el 3.er día hábil del mes siguiente sin contar feriados.

## Cálculo (por empleado y período)

`fracción` = 1 (mensual) o 0.5 (quincena). `proporción` = días del período
trabajados si ingresó o salió a mitad del período.

1. Salario = salario mensual × fracción × proporción.
2. Horas extra: valor hora = salario mensual / 23.83 / 8; × 1.35 las horas al
   35 % y × 2 las horas al 100 %.
3. Salario cotizable TSS = salario + horas extra + comisiones/otros ingresos.
   La bonificación, la regalía y los ingresos exentos no cotizan.
4. AFP = mín(cotizable / fracción, tope AFP) × 2.87 % × fracción; SFS igual
   con su tope. (La TSS cotiza por mes; la quincena se lleva a mes para
   aplicar el tope.)
5. Base ISR = cotizable + bonificación + regalía gravada − AFP − SFS.
   Retención = ISR anual de (base / fracción × 12) / 12 × fracción.
6. INFOTEP empleado = 0.5 % de la bonificación.
7. Neto = ingresos − (AFP + SFS + ISR + INFOTEP + otros descuentos). Un neto
   negativo es un error (422).
8. Empleador: AFP 7.10 % y SFS 7.09 % con sus topes, SRL con la tasa de la
   empresa y tope de 4 salarios, INFOTEP 1 % sin tope. Costo = ingresos +
   aportes.

Cada componente se redondea a centavos (mitad hacia arriba). Ejemplo
(tests): salario 50,000 → AFP 1,435.00, SFS 1,520.00, ISR 1,854.00, neto
45,191.00, aportes 8,145.00, costo 58,145.00; salario 500,000 → AFP
13,330.00 (tope), SFS 7,059.79 (tope), ISR 108,485.49, neto 371,124.72.

Utilidades: regalía = 1/12 del salario ordinario del año (o la estimación
proporcional con el salario actual, botón «Calcular regalía»); días de
vacaciones (14 / 18 / proporcionales); estimado de prestaciones (preaviso,
cesantía, vacaciones y regalía proporcional; desahucio o renuncia).

## Flujo y estados

`borrador` (creado, con las líneas de los empleados) → `calculada` (cada
cambio de novedades recalcula todo) → `aprobada` (no se modifica; se puede
reabrir mientras no haya pagos) → `pagada` (todas las líneas pagadas; el pago
puede ser por empleado) → `cerrada`. Solo borrador/calculada se eliminan. Un
empleado que ya está en una nómina no se elimina: se da de baja
(`estado = inactivo`, `fecha_salida`).

## Endpoints (`/nomina`)

| Método y ruta | Qué hace |
|---|---|
| `GET/PUT /empresa` | RNC, razón social, categoría, municipio, responsable, tasa SRL, frecuencia y día de pago, banco y cuenta de origen |
| `GET /parametros` · `GET /parametros/vigentes?fecha=` · `PUT/DELETE /parametros/{vigente_desde}` | Parámetros de ley (409 si se intenta borrar la última fila) |
| `GET /indicadores?fecha=` | Salario mínimo cotizable, topes y exento del ISR vigentes |
| `GET /resumen` · `GET /avisos` | Inicio (pasos de la guía, período, indicadores) y Bandeja (avisos calculados) |
| `GET/POST /empleados` · `GET/PUT/DELETE /empleados/{id}` | Empleados (cédula 11 dígitos, única → 409) |
| `POST /empleados/importar` | `{csv}`: crea o actualiza por cédula; devuelve las filas con error |
| `GET /empleados/{id}/prestaciones?fecha_salida=&desahucio=` | Estimado de prestaciones |
| `GET/POST /periodos` · `GET/DELETE /periodos/{id}` | Períodos (único por tipo, año, mes y quincena → 409) |
| `POST /periodos/{id}/calcular` · `aprobar` · `reabrir` · `pagar` · `cerrar` | Ciclo de vida (409 si el estado no lo permite) |
| `PUT /periodos/{id}/lineas/{empleado_id}` | Novedades del empleado; recalcula |
| `GET /periodos/{id}/lineas/{empleado_id}/regalia` | Regalía sugerida |
| `GET /periodos/{id}/archivo-banco` | CSV genérico de dispersión (aprobada en adelante) |
| `GET /reportes/tss?anio=&mes=` · `GET /reportes/tss/csv` | Resumen de la autodeterminación |
| `GET /reportes/ir3?anio=&mes=` · `GET /reportes/ir13?anio=` · `GET /reportes/costo-pago?anio=` | IR-3, IR-13 y gráfico costo vs pago |

Los reportes solo cuentan nóminas aprobadas, pagadas o cerradas y avisan
cuántas del período siguen sin aprobar.

### Archivos: qué son y qué no

- **Archivo para el banco**: CSV genérico (RNC, cuenta origen, fecha, cédula,
  nombre, banco, tipo y número de cuenta, monto, concepto). **No** es el
  formato propietario de ningún banco: se usa para llenar la plantilla de
  carga masiva del banco o como respaldo.
- **Resumen TSS**: CSV con las columnas de la autodeterminación (tipo y
  número de documento, salario cotizable, aporte voluntario, salario ISR,
  otras remuneraciones, regalía, aportes). **No** es el archivo de ancho fijo
  del SUIR+: la TSS no publica en abierto la especificación vigente (el
  instructivo de autodeterminación y novedades y su validador de estructura
  se obtienen dentro del SUIR+). Sirve para llenar la plantilla de la TSS.
- **Recibos de pago**: página imprimible (una hoja por empleado) en
  `/nomina/periodos/{id}/recibos`.

## Frontend

Nómina tiene su propio menú (logo «kontara Nómina», Inicio, Bandeja de
entrada con el contador de no leídos, Empleados, Nómina, Pagos,
Suscripciones, Configuración, «← Volver a Facturación» y la tarjeta de
prueba). Mientras el layout global no lo dibuje, cada pantalla lo muestra
como columna a la izquierda (`NominaShell`). Para que reemplace al menú
principal como en el prototipo, `AppShell` puede renderizar
`<NominaSidebar />` en las rutas `/nomina*` (`esRutaNomina`) y envolver el
contenido en `<NominaSidebarGlobal.Provider value={true}>`.

«Leído» de los avisos de la bandeja vive en `localStorage` de cada
navegador. Suscripciones es un marcador que lleva a `/planes`.

## Pruebas

- `tests/test_nomina_calculos.py`: casos calculados a mano (tramos y límites
  del ISR, topes AFP/SFS/SRL, quincenas, horas extra, bonificación, regalía
  con y sin tope, proporción, vacaciones, prestaciones, cédula).
- `tests/test_nomina.py`: rutas con el repositorio simulado.
- `tests/test_nomina_postgres.py`: contra Postgres real; se salta sin
  `NOMINA_TEST_DATABASE_URL` (p. ej.
  `postgresql://postgres@localhost:5432/postgres`, con permiso para crear bases).
