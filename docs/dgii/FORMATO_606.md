# Formato 606 — Compras de Bienes y Servicios (referencia técnica)

Extraído de dos documentos oficiales de la DGII adjuntados por el cliente:

- **"Instructivo Llenado y Remisión del Formato de Envío de Compras de
  Bienes y Servicios (606)"** (PDF, Impuestos Internos, versión Febrero 2026):
  describe el llenado, la pre-validación y la remisión por la Oficina
  Virtual.
- **"Formato de Envío 606 (Norma General 07-2018 y 05-2019)"** (la propia
  plantilla de Excel oficial, "Herramienta de Envío Formato 606", versión
  2025): sus macros (VBA) son la fuente de verdad **exacta** del formato del
  archivo de texto a generar (delimitador, orden de campos, formato de
  fecha) y de las reglas de validación -- se referencian aquí porque el PDF
  describe el llenado en la hoja de Excel pero no el formato binario/textual
  exacto del `.TXT` resultante.

Norma General 07-2018: las compras del contribuyente deben remitirse
mensualmente, a más tardar el día 15 de cada mes. Si no hubo operaciones en
el período, debe remitirse igual, de forma informativa ("en cero"), vía
Oficina Virtual > Declaraciones Juradas > Declaraciones en Cero.

## Fuentes de las compras en este sistema

Este módulo (`services/formato_606`) NO le pide al usuario que reintroduzca
manualmente cada compra -- las auto-completa a partir de lo que el propio
sistema ya procesa, y solo pide clasificar los campos que ningún e-CF trae
(ver "Campos que solo el usuario puede completar" abajo):

1. **e-CF recibidos como receptor**: cuando un proveedor nos envía un e-CF a
   `POST /fe/recepcion/api/ecf` (`services/receptor_recepcion`), ese
   servicio registra automáticamente un renglón en `compras606.registros`
   (`origen = 'receptor_recepcion'`) con los datos que sí vienen en el e-CF:
   RNC del proveedor, e-NCF, NCF modificado (si es una nota que afecta un
   e-CF previo), fecha de emisión, y la separación Bienes/Servicios +
   ITBIS facturado, calculados sumando `DetallesItems.Item` por
   `IndicadorBienoServicio`.
2. **Comprobantes autoemitidos** (`origen = 'auto_emitido'`): cuando
   `facturas_api` envía exitosamente a la DGII una factura de tipo **41**
   (Compras Electrónico), **43** (Gastos Menores Electrónico) o **47**
   (Pagos al Exterior Electrónico), registra el renglón correspondiente
   igual que el caso anterior.
3. **Manual** (`origen = 'manual'`): el usuario puede agregar un renglón a
   mano desde la pantalla de Formato 606, para cualquier compra que no pase
   por ninguno de los flujos anteriores.

## RNC/Cédula a reportar por tipo (regla especial para autoemitidos)

El campo 1 (`RNC o Cédula`) del 606 siempre identifica **a quién le
compramos**, nunca a nosotros mismos -- excepto en los dos casos que el PDF
documenta explícitamente como excepción (página 25, "Remisión de NCF
especiales"):

| Origen / tipo | RNC o Cédula a reportar |
|---|---|
| e-CF recibido (receptor_recepcion) | `RNCEmisor` del e-CF (el proveedor) |
| Tipo 41 (Compras Electrónico) | `RNCComprador` de la factura -- en este sistema `Emisor` siempre somos nosotros, así que la contraparte (el proveedor informal a quien se le hace la compra) queda en la sección `Comprador` del formulario |
| Tipo 43 (Gastos Menores Electrónico) | El RNC del **encabezado** (nuestro propio `EMISOR_RNC`) -- no hay proveedor identificado en el XSD de este tipo (sin sección `Comprador`) |
| Tipo 47 / NCF B17 (Pagos al Exterior) | El RNC del **encabezado** (nuestro propio `EMISOR_RNC`) -- cita textual del PDF: *"Al colocar un NCF para Pagos al Exterior (B17), en el Formato de Envío de Gastos 606, se debe utilizar el mismo RNC ó Cédula del encabezado del formato."* |

Para **Pagos al Exterior (tipo 47 / B17)**, el mismo PDF exime de llenado
(quedan en blanco/0, no error) estas casillas: ITBIS Retenido, ITBIS sujeto
a Proporcionalidad (Art. 349), ITBIS llevado al Costo, ISR Percibido en
compras, Impuesto Selectivo al Consumo, Otros Impuestos/Tasas, Monto Propina
Legal.

## Campos que solo el usuario puede completar

Ningún e-CF (ni el que emitimos ni el que recibimos) trae estos datos --
son clasificaciones contables que hace el propio contribuyente, así que
todo renglón nace con estos campos vacíos y el módulo bloquea la generación
del archivo hasta que se completen (igual que la herramienta oficial):

- **Tipo de Bienes y Servicios Comprados** (obligatorio, 1-11).
- **Forma de Pago** (obligatorio, 1-7).
- ITBIS Retenido, ITBIS sujeto a Proporcionalidad, ITBIS llevado al Costo,
  ITBIS percibido en compras, Tipo de Retención en ISR, Monto Retención
  Renta, ISR Percibido en compras, Impuesto Selectivo al Consumo, Otros
  Impuestos/Tasas, Monto Propina Legal (todos opcionales -- se dejan en 0
  si no aplican).

## Formato del archivo de texto (`DGII_F_606_<RNC>_<periodo>.TXT`)

Derivado directamente de la macro `GenerarArchivo` (módulo `modServicios`)
de la plantilla oficial. Delimitador: `|` (pipe). Codificación: la que la
Oficina Virtual acepta para este formato (texto plano, sin BOM).

**Línea de encabezado:**

```
606|<RNC o Cédula>|<Periodo AAAAMM>|<Cantidad de Registros>
```

**Una línea por comprobante, con estos 23 campos en este orden exacto**
(la última línea del archivo no lleva salto de línea final, réplica del
comportamiento de la macro oficial):

| # | Campo | Formato |
|---|---|---|
| 1 | RNC o Cédula | numérico, 9 (RNC) u 11 (Cédula) dígitos |
| 2 | Tipo Id | `1` = RNC, `2` = Cédula |
| 3 | Tipo de Bienes y Servicios Comprados | 2 dígitos, `01`-`11` |
| 4 | NCF | 11, 13 (e-NCF) o 19 posiciones alfanuméricas |
| 5 | NCF o Documento Modificado | igual formato que NCF, opcional |
| 6 | Fecha Comprobante | `AAAAMMDD` (8 dígitos) |
| 7 | Fecha Pago | `AAAAMMDD`, opcional (vacío si no ha sido pagado) |
| 8 | Monto Facturado en Servicios | decimal, punto como separador |
| 9 | Monto Facturado en Bienes | decimal |
| 10 | Total Monto Facturado | decimal -- **calculado** = campo 8 + campo 9 |
| 11 | ITBIS Facturado | decimal |
| 12 | ITBIS Retenido | decimal, opcional |
| 13 | ITBIS sujeto a Proporcionalidad (Art. 349) | decimal, opcional |
| 14 | ITBIS llevado al Costo | decimal, opcional |
| 15 | ITBIS por Adelantar | decimal -- **calculado** = campo 11 - campo 14 |
| 16 | ITBIS percibido en compras | decimal, opcional |
| 17 | Tipo de Retención en ISR | 2 dígitos `01`-`09`, opcional |
| 18 | Monto Retención Renta | decimal, opcional |
| 19 | ISR Percibido en compras | decimal, opcional |
| 20 | Impuesto Selectivo al Consumo | decimal, opcional |
| 21 | Otros Impuestos/Tasas | decimal, opcional |
| 22 | Monto Propina Legal | decimal, opcional |
| 23 | Forma de Pago | 2 dígitos `01`-`07` |

## Catálogos

**Tipo de Bienes y Servicios Comprados:**
`01` Gastos de personal · `02` Gastos por trabajos, suministros y servicios ·
`03` Arrendamientos · `04` Gastos de activos fijos · `05` Gastos de
representación · `06` Otras deducciones admitidas · `07` Gastos financieros
· `08` Gastos extraordinarios · `09` Compras y gastos que formarán parte del
costo de venta · `10` Adquisiciones de activos · `11` Gastos de seguros.

**Tipo de Retención en ISR:**
`01` Alquileres · `02` Honorarios por servicios · `03` Otras rentas · `04`
Otras rentas (rentas presuntas) · `05` Intereses pagados a personas
jurídicas residentes · `06` Intereses pagados a personas físicas residentes
· `07` Retención por proveedores del Estado · `08` Juegos telefónicos · `09`
Retenciones subsector de ganadería de carne bovina.

**Forma de Pago:**
`01` Efectivo · `02` Cheques/Transferencias/Depósito · `03` Tarjeta
crédito/débito · `04` Compra a crédito · `05` Permuta · `06` Notas de
crédito · `07` Mixto.

## Reglas de validación (derivadas de `clsFormato606Validator`/`clsValidator`)

- **RNC**: exactamente 9 dígitos numéricos. **Cédula**: exactamente 11
  dígitos numéricos. (La plantilla oficial NO valida el dígito verificador
  en este flujo -- solo longitud + que sea numérico -- así que este módulo
  tampoco lo exige, para no ser más estricto que la herramienta oficial de
  la DGII.)
- **Periodo**: `AAAAMM`, `>= 201805`, mes entre `01` y `12`.
- **NCF / NCF Modificado**: si empieza con `E`/`e` (e-NCF), debe medir
  exactamente 13 caracteres. Si no, debe medir 11 o 19 caracteres. Patrón
  por tipo:
  - 19 posiciones: `(A|Q|P)` + 8 dígitos + tipo (`01`-`04` o `11`-`15`) + 8
    dígitos.
  - 11 posiciones: `B` + tipo (`01`-`04`, `11`-`15` o `17`) + 8 dígitos.
  - 13 posiciones (e-NCF): `E` + tipo (`31`-`34`, `41`-`45` o `47`) + 10
    dígitos.
- **Fecha Comprobante / Fecha Pago**: exactamente 8 dígitos, formato
  `AAAAMMDD` válido (año 1950-2099, mes/día coherentes con el mes).
- **Montos**: numéricos, `>= 0` (los opcionales pueden quedar vacíos).
- **Tipo de Bienes**, **Forma de Pago**: obligatorios, deben ser uno de los
  valores del catálogo.
- **Tipo de Retención en ISR**: opcional, si se llena debe ser uno de los
  valores del catálogo.
- **NCF repetido**: un mismo par (RNC del proveedor, NCF) no puede
  reportarse dos veces -- ver la nota de idempotencia en
  `db/init/006_compras606.sql`.

## Alertas de la Oficina Virtual (post-envío, informativo)

Documentadas en el PDF, sección "Validaciones Formato 606" -- no se pueden
verificar localmente (dependen de datos internos de la DGII: RNC inactivos,
NCF autorizados, e-CF aceptados, etc.), así que no forman parte de la
validación de este módulo, pero conviene conocerlas para interpretar el
resultado tras remitir el archivo por la Oficina Virtual: RNC/Cédula
inactivo, NCF no habilitado, NCF no existe o fue eliminado, NCF no
autorizado por Impuestos Internos, NCF ya reportado, NCF afectado por una
Nota de Crédito no remitido previamente, NCF de compras a nombre de persona
física registrada como contribuyente, RNC/Cédula no registrada como
contribuyente, emisor/receptor de e-CF no coincide con los registros.
