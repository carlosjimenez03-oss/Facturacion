# erp-api: ventas (contactos, cotizaciones, cobros), impuestos, compras, bancos y contabilidad

`services/erp_api` es el backend de las pantallas de **Contactos**,
**Cotizaciones**, **Cobros / cuentas por cobrar** y **Notas** del frontend.
El contrato (rutas, campos y tipos) es
[`frontend/src/lib/erpApi.ts`](../frontend/src/lib/erpApi.ts); el backend lo
implementa 1:1. Montos viajan como string con 2 decimales (`"1250.00"`),
fechas como `YYYY-MM-DD` y marcas de tiempo en ISO 8601.

## Base de datos

- Usa su propio esquema **`ventas`** (`clientes`, `cotizaciones`, `cobros` y
  la secuencia `cotizaciones_numero_seq` del correlativo `COT-0001`).
- **Lo crea por sí mismo al arrancar**: después de conectarse aplica el DDL
  de [`services/erp_api/app/migraciones.py`](../services/erp_api/app/migraciones.py)
  (todo `IF NOT EXISTS`, idempotente, serializado con un advisory lock). En
  Supabase no hace falta correr nada a mano.
  [`db/init/007_ventas.sql`](../db/init/007_ventas.sql) es el mismo DDL, para
  el Postgres local de desarrollo (un test verifica que no se separen).
- Las fechas DD-MM-AAAA de los e-CF (FechaEmision, FechaLimitePago) se leen
  con `ventas.fecha_dgii_segura(text)` (mismas migraciones, `DDL_FUNCIONES`;
  copia en [`db/init/009_fecha_dgii_segura.sql`](../db/init/009_fecha_dgii_segura.sql)):
  devuelve NULL para una fecha imposible como `31-02-2026` en vez de hacer
  fallar la consulta entera como haría `to_date`.
- Fase 5 agrega los esquemas **`bancos`** y **`contabilidad`** (mismas
  migraciones, `DDL_CONTABILIDAD` y `DDL_BANCOS`; copia en
  [`db/init/012_bancos_contabilidad.sql`](../db/init/012_bancos_contabilidad.sql))
  y la columna `cuenta_bancaria_id` en `ventas.cobros` y `compras.pagos`.
- **Solo lee** `facturacion.facturas` (de `facturas-api`) -- nunca la
  modifica. De `datos` (columna `json`) toma TipoPago, FechaEmision,
  FechaLimitePago, comprador, MontoTotal e InformacionReferencia.

## Endpoints

| Método y ruta | Qué hace |
|---|---|
| `GET /clientes?q=&tipo=&activo=` | Lista contactos. `q` busca en nombre y RNC; `tipo=cliente`/`proveedor` incluye también a los `ambos` |
| `GET /clientes/{id}` · `GET /clientes/por-rnc/{rnc}` | Un contacto (404 si no existe) |
| `POST /clientes` · `PUT /clientes/{id}` | Crea / reemplaza. El RNC se guarda solo con dígitos: 9 para `rnc`, 11 para `cedula` (422 si no); RNC repetido → 409 |
| `DELETE /clientes/{id}` | 409 si tiene cotizaciones (desactívelo con `activo=false`) |
| `GET /cotizaciones?q=&estado=` · `GET /cotizaciones/{id}` | `estado=vencida` = abiertas con `vence` pasado (no se guarda, se calcula) |
| `POST /cotizaciones` · `PUT /cotizaciones/{id}` | El backend calcula importe por línea, subtotal, descuentos, ITBIS (18 % y 16 %; 0 y exento no suman) y total. `vence` por defecto = fecha + 15 días. PUT solo si está abierta (409) |
| `POST /cotizaciones/{id}/estado` | `abierta` \| `aceptada` \| `rechazada`; 409 si ya está facturada |
| `POST /cotizaciones/{id}/facturada` | La marca facturada y guarda `factura_id` |
| `DELETE /cotizaciones/{id}` | Solo abiertas (409) |
| `GET /cuentas-por-cobrar?estado=&cliente_rnc=` | Facturas de venta (E31, E32, E33, E44, E45, E46) enviadas/aceptadas a crédito (TipoPago 2) con cobrado, pendiente (total − notas de crédito enviadas/aceptadas que la referencian − cobros), estado (`pendiente`/`vencida`/`cobrada`) y el teléfono del contacto. Sin `estado`: lo no cobrado; `estado=todas`: todo. Sin FechaLimitePago, vence a los 30 días de la emisión |
| `GET /cobros?factura_id=` · `POST /cobros` | Registra un cobro: 422 si la factura no es a crédito/no fue enviada o si el monto es ≤ 0 o supera lo pendiente. Fecha por defecto = hoy. `cuenta_bancaria_id` (opcional): cuenta de Bancos donde entra el dinero (ver "Bancos") |
| `POST /cobros/{id}/anular` | Anula el cobro (deja de contar como cobrado); 409 si ya estaba anulado |
| `GET /documentos-venta?tipo_ecf=&estado=&q=` | Cualquier factura con cliente, fecha de emisión, total y, en notas de crédito/débito, el e-NCF afectado y el código de modificación. Más recientes primero |

"Hoy" se calcula en hora de República Dominicana (UTC-4), no en la del
servidor de la base de datos.

## Impuestos

Fase 2: formatos 607 y 608, pre-cálculos IT-1 / IR-17 / IR-2 y el
calendario de declaraciones. El contrato es
[`frontend/src/lib/impuestosApi.ts`](../frontend/src/lib/impuestosApi.ts).
Las rutas están en `app/impuestos.py`, las consultas en
`app/impuestos_repository.py` y los cálculos (funciones puras, con tests) en
`app/formatos.py` y `app/precalculos.py`.

### Datos

- **Solo lee** `facturacion.facturas` (e-CF de venta 31, 32, 33, 34, 44, 45 y
  46 en estado `aceptado`, por `FechaEmision`, solo del ambiente DGII activo: ver docs/AMBIENTES.md), `facturacion.envios_dgii`
  (anulaciones de `anulacion_encf`), `compras606.registros` (606) e
  `inventario.productos` (Kardex). Nunca los modifica (los pagos a
  proveedores de Compras sí escriben el 606, ver "Compras").
- Lo que guarda vive en el esquema **`impuestos`** (`anulaciones_608`,
  `ajustes_precalculo`, `declaraciones_presentadas`), creado por las mismas
  migraciones de arranque (`DDL_IMPUESTOS`) y copiado en
  [`db/init/008_impuestos.sql`](../db/init/008_impuestos.sql).
- El RNC del encabezado de los TXT es `EMISOR_RNC` (sin guiones). Si falta,
  los TXT devuelven 422.
- **`EMISOR_100_ELECTRONICO`** (booleano, por defecto `true`): la empresa
  solo emite e-CF. Según la DGII (ver las fuentes), el emisor 100 %
  electrónico no presenta el 607 ni el 608, así que con `true` las
  declaraciones 607/608 salen con estado `no_aplica` (nunca `vencida`) y
  las advertencias y los 422 de los TXT lo explican. **Póngalo en `false`
  si la empresa todavía emite o anula NCF serie B** (papel o impresora
  fiscal): entonces el 607/608 vuelven a vencer cada mes y los mensajes
  piden declararlos en cero cuando no hay comprobantes serie B.
- `anulacion_encf` ahora guarda en `respuesta` de `envios_dgii`, junto con la
  respuesta de la DGII, los rangos anulados (`rangos: [{tipo_ecf, desde,
  hasta}]`) y `fecha_hora_anulacion`, que saca del ANECF enviado
  (`services/anulacion_encf/app/detalle.py`). La DGII no devuelve qué
  secuencias se anularon. Las anulaciones anteriores a este cambio no tienen
  rangos y el 608 avisa que no las puede listar.

### Endpoints

| Método y ruta | Qué hace |
|---|---|
| `GET /impuestos/607?periodo=AAAAMM` | Los e-CF de venta aceptados del período con los 23 campos del 607, totales (E34 resta) y `advertencias` (e-CF `enviado` sin respuesta, resumen de consumo, comprobantes sin RNC) |
| `GET /impuestos/607/txt?periodo=` | `DGII_F_607_<RNC>_<AAAAMM>.TXT` (`text/plain; charset=utf-8`). Solo incluye comprobantes que la DGII acepta en el 607 (ver la interpretación 1). 422 si no hay ninguno, si falta el RNC o si algún renglón no pasa las validaciones de la plantilla oficial |
| `GET /impuestos/608?periodo=` | Los e-NCF anulados con ANECF en el período (rangos expandidos, `origen=anulacion_encf`, `id=null`) más los registrados a mano (`origen=manual`) |
| `GET /impuestos/608/tipos-anulacion` | La tabla oficial 01–10 |
| `POST /impuestos/608` | Registra un NCF anulado a mano. 422 si el NCF no cumple la plantilla del 608 (11 o 19 posiciones, serie A/B/P/Q y tipo válido) o si es un e-NCF; 409 si el NCF ya está registrado o el 608 de ese mes ya está marcado presentado. El período es el mes de `fecha_comprobante` |
| `DELETE /impuestos/608/{id}` | Solo filas manuales (las de e-NCF no tienen id). 404 si no existe, 409 si el 608 del período ya está presentado |
| `GET /impuestos/608/txt?periodo=` | `DGII_F_608_<RNC>_<AAAAMM>.TXT` con las filas manuales (NCF serie B). 422 si no hay ninguna (también si solo hay anulaciones ANECF) o falta el RNC. El frontend no ofrece el botón en ese caso |
| `GET /impuestos/precalculo/{it1\|ir17\|ir2}?periodo=` | El pre-cálculo. `it1` e `ir17` usan `AAAAMM` e `ir2` usa `AAAA` (422 si no) |
| `PUT /impuestos/precalculo/{formulario}/ajustes?periodo=` | `{"valores": {clave: "monto"}}` guarda los valores manuales de las claves enviadas (las demás no se tocan) y devuelve el pre-cálculo recalculado. Un valor vacío (`""` o `null`) borra el manual y vuelve al sugerido. 422 con claves desconocidas o montos inválidos. El frontend envía solo las claves que el usuario cambió |
| `GET /impuestos/declaraciones?anio=AAAA` | 606, 607, 608, IT-1 e IR-17 de los 12 meses, más el IR-2 del ejercicio (61 filas), ordenadas por vencimiento. `estado` es `presentada` si se marcó, `no_aplica` para el 607/608 con `EMISOR_100_ELECTRONICO=true`, `vencida` si `vence` < hoy y `pendiente` en otro caso |
| `POST /impuestos/declaraciones/{formulario}/{periodo}/presentada` | Marca la declaración como presentada (`monto`, `referencia`, `presentada_en`, que por defecto es hoy). Repetir la llamada actualiza la marca |
| `DELETE …/presentada` | Quita la marca (idempotente) y devuelve la declaración con su estado recalculado |

Cada elemento de `ajustes` trae `valor` (el que se usó en el cálculo: el
manual guardado o, si no hay, el sugerido o 0), `manual` (si `valor` es un
valor guardado) y `sugerido` (lo que calcula el sistema, o `null` si no
sugiere nada). Hoy sugieren valor `it1.saldo_favor_anterior` (el nuevo saldo
a favor del pre-cálculo del mes anterior) e `ir2.inventario_final` (la
valuación del Kardex). El frontend muestra "Sugerido por el sistema" o
"Manual · Restablecer" (que envía `""`).

Los `codigo` de las `casillas` ("1", "2", …) son **números de línea** del
pre-cálculo, no las casillas del formulario oficial (no se pudo verificar
esa numeración en una fuente oficial). La pantalla los muestra como `L1`,
`L2`… y "Copiar valores" los copia como "Línea N", con un aviso: se
transcribe cada valor buscando su concepto en el formulario.

Claves de `ajustes`:

- **`it1`:** `itbis_retenido_terceros` y `saldo_favor_anterior`.
- **`ir17`:** `recargos`.
- **`ir2`:** `inventario_inicial`, `inventario_final`, `depreciacion`,
  `ajustes_fiscales`, `anticipos` y `retenciones_isr`.

En `resumen`, `valor` es un decimal plano (`"1234.56"`) cuando es un monto,
y el frontend lo formatea. Cualquier otro texto (`"63.0%"`) se muestra tal
cual.

### Formato de los TXT

Se tomó de las macros de las plantillas oficiales de Excel ("Herramienta de
Envío Formato 607" y "Herramienta de envío Formato 608", versión NG 07-2018
y 05-2019), igual que el 606 (`docs/dgii/FORMATO_606.md`):

- **Delimitadores:** `|`, CRLF entre líneas y sin salto de línea al final.
- **Codificación:** UTF-8 sin BOM.
- **Fechas:** `AAAAMMDD`.
- **Montos:** con punto decimal y dos decimales. Es la misma convención del
  606 del repo; las casillas opcionales en cero salen `0.00`.
- **607:** una línea de encabezado `607|RNC|AAAAMM|cantidad` y los 23 campos
  en este orden: RNC/cédula · tipo id · NCF · NCF modificado · tipo de
  ingreso · fecha · fecha de retención · monto facturado · ITBIS facturado ·
  ITBIS retenido · ITBIS percibido · retención renta · ISR percibido ·
  selectivo · otros impuestos · propina legal · efectivo ·
  cheque/transferencia · tarjeta · crédito · bonos · permuta · otras. El
  tipo de ingreso va con **un dígito** (la macro toma `Mid(celda, 2, 1)`
  de "01".."06"). En la API viaja como "01".."06".
- **608:** una línea de encabezado `608|RNC|AAAAMM|cantidad` y una línea
  `NCF|AAAAMMDD|TT` por comprobante, con el tipo de anulación en 2 dígitos.

### Fuentes oficiales consultadas

- Instructivo 607: [Llenado y Remisión del Formato de Envío de Ventas de Bienes y Servicios (607)](https://dgii.gov.do/publicacionesOficiales/bibliotecaVirtual/contribuyentes/formatoEnvioDatos/Documents/5-InstructivoLlenadoyenvioFomato607.pdf) (dic. 2025). Lista los 23 campos, el tope de RD$250,000 de la NG 10-18, el vencimiento el día 15 y el resumen de facturas de consumo en la Oficina Virtual.
- Instructivo 608: [Llenado y Remisión del Formato de Envío de Comprobantes Fiscales Anulados (608)](https://dgii.gov.do/publicacionesOficiales/bibliotecaVirtual/contribuyentes/formatoEnvioDatos/Documents/3-Instructivo-de-llenado-y-env%C3%ADo-Formato-608.pdf). Tiene la tabla de tipos de anulación 01–10.
- Plantillas de Excel oficiales del 607 y del 608, de la [página de Formatos de Envío de Datos](https://dgii.gov.do/herramientas/formularios/formatoEnvioDatos/Paginas/default.aspx). Sus macros (`GenerarArchivo`, `clsFormato607Validator`, `cmdValidar_Click`) dan el layout y las validaciones exactas, por ejemplo que el NCF debe tener 11 o 19 posiciones.
- Comunidad de Ayuda DGII, respuestas oficiales:
  [los emisores electrónicos no reportan los e-CF en el 607](https://ayuda.dgii.gov.do/conversations/discusiones/se-deben-reportar-en-el-formato-607-las-facturas-electronicas-de-ventas-e31/65f278cc2e503b6c8e8bb766)
  (base legal: Norma General 01-20 y Ley 32-23) y
  [el emisor electrónico no está obligado a remitir 607 ni 608, pero sí el 606 con serie B y E](https://ayuda.dgii.gov.do/conversations/discusiones/factura-electrnica/66917ec9bc79b41a23ee247f).
  También las [Preguntas frecuentes del Facturador Gratuito](https://dgii.gov.do/cicloContribuyente/facturacion/comprobantesFiscalesElectronicosE-CF/Preguntas%20frecuentes/Generales/Preguntas-Frecuentes-Facturador-Gratuito.pdf) (preguntas 39 y 71).
- [Formato Anulación de e-NCF v1.0](https://dgii.gov.do/cicloContribuyente/facturacion/comprobantesFiscalesElectronicosE-CF/Documentacin%20sobre%20eCF/Formatos%20XML/Formato%20Anulaci%C3%B3n%20de%20e-NCF%20v1.0.pdf) (campos del ANECF) y [Descripción Técnica Servicios DGII](https://dgii.gov.do/cicloContribuyente/facturacion/comprobantesFiscalesElectronicosE-CF/Documentacin%20sobre%20eCF/Informe%20y%20Descripci%C3%B3n%20T%C3%A9cnica/Descripcion%20Tecnica%20Servicios%20DGII.pdf) (respuesta de `anularrango`).
- [Instructivo de llenado del IT-1](https://dgii.gov.do/publicacionesOficiales/bibliotecaVirtual/contribuyentes/itbis/Documents/Instructivo%20LLENADO%20IT-1%202020.pdf) (versión julio 2026). Cubre las tasas del 18 % y el 16 %, exportaciones, exentas y exentas por destino, el coeficiente de proporcionalidad de la casilla 54 (exportaciones + exentas por destino + gravadas, entre el total), el ITBIS por adelantar del 606, las retenciones, el saldo a favor anterior y los recargos.
- [Página de formularios de retenciones de la DGII](https://dgii.gov.do/herramientas/formularios/formularioDeclaraciones/Paginas/impuestosRetencionesyRetribuciones.aspx) (IR-17 en Excel). Se usó para las tasas; la Ley 30-26 sube alquileres, honorarios y otras rentas a 15 % y las rentas presuntas a 3 % para los pagos desde julio 2026. El pre-cálculo **no** reproduce la numeración de casillas del formulario. Ver también el [instructivo IR-17](https://dgii.gov.do/publicacionesOficiales/bibliotecaVirtual/contribuyentes/retencionesRetribucionesComplementarias/Documents/3-IR-17.pdf), que fija el vencimiento el día 10.
- IR-2: [instructivo del formulario IR-2](https://dgii.gov.do/publicacionesOficiales/bibliotecaVirtual/contribuyentes/isr/ISR%20Persona%20Jurdica/7-InstructivoUsoFormularioIR-2EnExcel.pdf) (tasa del 27 %, plazo de 120 días tras el cierre). La tasa transitoria del 30 % de la Ley 30-26 (ejercicios 2026–2028, ingresos de RD$1,000 millones o más) está resumida en el [Tax Flash de Deloitte](https://www.deloitte.com/latam/es/services/tax/perspectives/do-3jul26-ley-tributaria-cambios-isr-itbis-fiscalidad.html). Es una fuente secundaria: no se encontró el texto oficial de la ley.

### Interpretaciones y supuestos

1. **e-CF en el 607/608.** La DGII dice expresamente que el emisor
   electrónico no reporta sus e-CF en el 607 ni sus e-NCF anulados en el
   608 (ver las fuentes). Las plantillas oficiales, además, rechazan los NCF
   de 13 posiciones. Por eso:
   - `GET /607` y `GET /608` muestran los e-CF y los e-NCF como
     **referencia** (cuadre con el IT-1), con una advertencia.
   - Los TXT solo llevan lo que la DGII espera: en el 608, los NCF serie B
     registrados a mano; en el 607, ningún comprobante de este sistema, que
     solo emite e-CF.
   - Sin comprobantes reportables, el TXT responde 422. Con
     `EMISOR_100_ELECTRONICO=true` el mensaje dice que el formato no aplica;
     con `false`, que se declare **en cero** (Oficina Virtual >
     Declaraciones Juradas > Declaraciones en Cero).
   - El frontend aplica la misma regla: solo ofrece "Generar archivo TXT"
     si hay comprobantes serie B (en el 607, NCF que no son e-NCF; en el
     608, filas manuales) y, si no, muestra la explicación. "Validar" cuenta
     solo esos comprobantes. El alta manual del 608 acepta NCF serie B de 11
     posiciones o el formato de 19 y rechaza los e-NCF (se anulan con ANECF
     o con una nota de crédito).
   - El 606 sí incluye e-CF, como pide la DGII.
2. **Consumo.** Un E32 con `MontoTotal` menor de RD$250,000 no va renglón
   por renglón: se suma en la advertencia del "Resumen General de Facturas de
   Consumo". Se compara el monto con impuestos, que es lo conservador. Desde
   el tope, el E32 va individual y, si no trae RNC, genera una advertencia.
3. **Mapeo del 607:**
   - **Desglose por tasa (607 e IT-1/IR-2):** cada e-CF sale de **una sola
     fuente**, para que el gravado y su ITBIS cuadren: (1) los totales por
     tasa (`MontoGravadoI1/I2/I3`, `MontoExento`, `TotalITBIS1/2`) si el
     e-CF trae alguno; si no, (2) las líneas por `IndicadorFacturacion`
     (descontando el ITBIS si `IndicadorMontoGravado=1`), con el ITBIS =
     gravado × tasa (es lo que escribe este sistema,
     `frontend/src/lib/buildDatos.ts`); y si no hay líneas, (3) los totales
     globales `MontoGravadoTotal`/`MontoExento`/`TotalITBIS`, con la tasa
     deducida de `TotalITBIS / MontoGravadoTotal` (sin ITBIS = 0 %; la más
     cercana entre 16 % y 18 %).
   - **Monto facturado:** gravado + exento sin impuestos, del desglose.
   - **Propina legal:** el impuesto adicional con código `001`. El
     selectivo es la suma del específico y el ad valorem; el resto va a
     "otros impuestos".
   - **Formas de pago:** `FormaPago` 7 (nota de crédito) y 8 van a "otras
     formas". Si no hay tabla: `TipoPago` 2 → crédito, 3 (gratuito) →
     otras y cualquier otro → efectivo. Las E34 no llevan forma de pago.
   - **Retenciones:** si el e-CF informa retenciones
     (`TotalITBISRetenido`/`TotalISRRetencion`), se usa la fecha de emisión
     como fecha de retención, con una advertencia.
   - **Sin identificación del comprador:** `tipo_id` sale 3, porque el
     contrato lo exige numérico; en el TXT ese campo queda vacío.
4. **608 desde ANECF.**
   - **Qué cuenta:** solo los envíos sin `error` **y** con `codigo = "1"`,
     el código de aceptación que `anulacion_encf` guarda de la respuesta de
     `anularrango`. Los demás se cuentan en una advertencia. Si los mensajes
     de la DGII indican anulación parcial, se avisa.
   - **Fecha:** la fecha es `FechaHoraAnulacioneNCF` o, si falta, la fecha
     del envío.
   - **Tipo de anulación:** **08 "Errores en secuencia de NCF"**, que es el
     motivo de la tabla que mejor describe una secuencia no usada o un e-CF
     no enviado. Es informativo, porque no va al TXT.
   - **Rangos:** se expanden hasta 4,999 e-NCF por período, que es el
     límite de la plantilla.
5. **IT-1.** Las líneas siguen el prototipo
   (`docs/diseno/prototipo-kontara/pantallas/PreCalculo.dc.html`), no la
   numeración de casillas del formulario oficial (ver "Claves de
   `ajustes`" arriba).
   - **Ventas:** las E34 restan y las E33 suman. El 0 % cuenta como
     exportación y el exento como exentas. En E44, lo exento se trata como
     exento por destino, que no reduce la proporcionalidad.
   - **Facturas en papel (serie B):** las de `operacion.facturas_papel`
     del período (por `fecha`) entran en las ventas y el ITBIS facturado
     **siempre**, también con `EMISOR_100_ELECTRONICO=true`: el IT-1
     declara todas las ventas (a diferencia del 607). Salen además en las
     líneas de detalle **5.1** ("De ellos: facturas en papel") y **8.1**
     ("De él: ITBIS de facturas en papel"), `tipo = "detalle"`, que no
     suman. Una factura en papel cuyo e-CF de reemplazo ya fue **aceptado**
     (`encf_reemplazo`, o un e-CF con `NCFModificado` = su NCF y código de
     modificación 4, el mismo criterio que la contabilidad) no se cuenta: la
     venta la declara el e-CF en el período de su `FechaEmision`, y el
     pre-cálculo lo advierte. El coeficiente de proporcionalidad (y el
     asiento mensual de Contabilidad) usa las mismas ventas.
   - **ITBIS adelantado:** `itbis_facturado − itbis_costo` del 606 del
     período; las notas de crédito B04/E34 del 606 restan.
   - **Proporcionalidad:** solo se aplica con ventas exentas, y solo a
     `itbis_proporcionalidad`. Si eso es 0, se aplica a todo el adelantado,
     que es lo conservador, con una advertencia.
   - **ITBIS retenido a proveedores:** `itbis_retenido` del 606 con
     `fecha_pago` en el período.
   - **Saldo a favor anterior:** por defecto, el nuevo saldo a favor del
     pre-cálculo del mes anterior, calculado con sus ajustes guardados y sin
     encadenar más meses, que es lo conservador.
   - **Liquidación:** si las operaciones propias dan negativo, queda saldo
     a favor, pero las retenciones a proveedores se pagan igual.
6. **IR-17.**
   - **Qué suma:** `monto_retencion_renta` del 606 con `fecha_pago` en el
     período, agrupado por `tipo_retencion_isr`.
   - **Tasa vigente:** depende del mes de **pago** (el período del IR-17):
     la anterior a la Ley 30-26 hasta junio 2026 y la nueva desde
     `PERIODO_LEY_30_26` = 202607. Se muestra en la ayuda de cada línea.
   - **Importe y etiqueta:** el importe es lo retenido según el 606. La
     etiqueta muestra la base del 606 (monto facturado de esas compras) y
     la **tasa efectiva** (retenido / base), para que el porcentaje cuadre
     con el número. Si la efectiva no coincide con la vigente, se avisa
     (revisar el "Monto Retención Renta" del 606).
   - **Tipos del 606:**

     | Tipo del 606 | Concepto | Tasa antes / desde Ley 30-26 |
     |---|---|---|
     | 01 | Alquileres | 10 % / 15 % |
     | 02 | Honorarios | 10 % / 15 % |
     | 03 | Otras rentas (Art. 309 lit. f) | 10 % / 15 % |
     | 04 | Rentas presuntas (Decreto 139-98) | 2 % / 3 % |
     | 05 | Intereses a personas jurídicas | 1 % |
     | 06 | Intereses a personas físicas | 10 % |
     | 07 | Proveedores del Estado | 5 % |
     | 08 | Juegos telefónicos | 5 % |
     | 09 | Ganadería bovina | 1 % |

   - **Sin tipo de retención:** se listan aparte, con una advertencia.
7. **IR-2.**
   - **Ingresos:** las ventas sin ITBIS del año.
   - **Compras:** el 606 de tipo 09 (costo de venta). Al costo o al gasto se
     suma el ITBIS llevado al costo.
   - **Inventario final:** por defecto, `Σ max(stock_actual, 0) ×
     costo_promedio` del Kardex, con una advertencia: solo es exacto al 31
     de diciembre. Se puede reemplazar con un ajuste.
   - **Gastos:** las categorías del 606. El tipo 10 (adquisiciones de
     activos) no es gasto, así que se pide la depreciación como ajuste. Su
     valor **sugerido** es la depreciación fiscal del año de los activos
     fijos de Contabilidad (método del Art. 287, ver "Contabilidad"); un
     valor manual guardado la reemplaza, y borrarlo vuelve a la sugerencia.
     Sin activos registrados no hay sugerencia (0). Las compras sin
     clasificar se excluyen, con una advertencia.
   - **Tasa:** 27 %, o 30 % en 2026–2028 si los ingresos son de RD$1,000
     millones o más.
8. **Vencimientos.**
   - **Mensuales:** IR-17 el día 10, 606/607/608 el día 15 e IT-1 el día
     20 del mes siguiente.
   - **IR-2:** 31 de diciembre + 120 días, que es el 30 de abril, o el 29
     si el año siguiente es bisiesto.
   - **Fin de semana:** pasa al lunes, como en
     `frontend/src/lib/calendarioFiscal.ts`.
   - **Feriados:** no se consideran.

## Compras

Fase 3: bandeja de entrada de e-CF recibidos (aprobación comercial),
facturas de compra y cuentas por pagar, pagos a proveedores con
retenciones, comprobantes E41/E43 y retenciones del mes con su certificado.
El contrato es [`frontend/src/lib/comprasApi.ts`](../frontend/src/lib/comprasApi.ts).
Rutas en `app/compras.py`, consultas en `app/compras_repository.py`,
cálculos puros (ACECF, campos de pago del 606, sugerencia de retenciones)
en `app/compras_calculos.py` y el cliente de `aprobacion_comercial` en
`app/aprobacion_client.py`. Las consultas se prueban contra un Postgres
real en `tests/test_compras_postgres.py` (se salta sin
`COMPRAS_TEST_DATABASE_URL`, p. ej. `postgresql://postgres@localhost:5432/postgres`).

### Modelo

- **Bandeja de entrada** = `recepcion.ecf_recibidos`. `receptor-recepcion`
  guarda ahí el XML de cada e-CF que recibe, *best-effort* (como la
  auditoría y el 606: si falla, el ARECF igual se devuelve) e idempotente
  por (`rnc_emisor`, `encf`); enlaza su renglón del 606 en `compra606_id`.
  La tabla la crea erp-api al arrancar (`DDL_COMPRAS`, copia en
  [`db/init/010_compras.sql`](../db/init/010_compras.sql));
  `receptor-recepcion` no tiene migraciones y, si la tabla aún no existe,
  solo deja un aviso en el log (`libs/ecf_common/ecf_recibidos.py`).
  - **El receptor nunca espera por la base:** `ON CONFLICT DO NOTHING` y,
    para un reenvío, `FOR UPDATE SKIP LOCKED` (si la fila está ocupada no se
    actualiza), con `lock_timeout` 2 s / `statement_timeout` 10 s.
  - **Firma:** al guardarlo verifica la firma XML-DSig con el certificado
    embebido y que su SN sea el `RNCEmisor` (`firma_valida`, motivo en
    `firma_detalle`). No valida la cadena hasta la CA de INDOTEL (su bundle
    no está en el repositorio). Nunca cambia el ARECF. Se conserva la
    primera copia, salvo que su firma no sea válida y llegue otra con firma
    válida, mientras siga pendiente: entonces la reemplaza y corrige los
    montos de su 606 (si no tiene pagos y el 606/IT-1 del mes no están
    presentados; si no, queda un aviso en el log).
  - **Vínculo con el 606:** solo el renglón del mismo (RNC, e-NCF) que no
    esté vinculado a otro e-CF.
  - **XML:** se guarda decodificado según su declaración (ISO-8859-1,
    UTF-16…), sin caracteres NUL; la descarga lo devuelve con esa misma
    codificación.
  - **606 desde el e-CF** (`datos_606_desde_ecf`, el mismo para el
    receptor y para `vincular`): servicios/bienes = MontoTotal − TotalITBIS
    − impuestos adicionales, repartido según las líneas por
    `IndicadorBienoServicio` (sin el ITBIS de cada línea si
    `IndicadorMontoGravado` = 1). Selectivo (específico + ad valorem),
    otros impuestos y propina legal (código `001`) van a sus casillas, como
    en el 607. Así el renglón suma exactamente el MontoTotal.
- **Factura de compra = un renglón de `compras606.registros`.** Ahí están
  los datos fiscales (proveedor, NCF, montos, retenciones, fecha y forma de
  pago). Llega sola de un e-CF recibido o de un E41/E47 autoemitido
  (`facturas-api`), o se registra con `POST /compras/facturas` (origen
  `compras`) cuando el proveedor tiene NCF/e-NCF propio; al proveedor
  informal se le emite un **E41** en `/facturas/nueva?tipo=41`.
  `compras.facturas` (1:1 por `registro606_id`) guarda lo que el 606 no
  tiene: nombre del proveedor, contacto (`ventas.clientes`), compra de
  inventario vinculada (`inventario.compras`, la recepción de mercancía),
  concepto y vencimiento.
- **Proveedor:** el RNC/cédula del 606. El nombre sale, en este orden, de la
  factura de compra, del e-CF recibido, del contacto con ese RNC, de
  `inventario.proveedores` o del Comprador del E41. Los días de crédito del
  contacto dan el vencimiento si el e-CF no trae `FechaLimitePago` (30 por
  defecto).
- **Cuentas por pagar:** renglones del 606 que no son notas de crédito
  (E34/B04), ni gastos menores E43 (se pagan al momento), ni e-CF
  rechazados comercialmente. `pendiente = total − notas de crédito del
  proveedor que la modifican − retenciones del 606 − pagos no anulados`;
  estado `pendiente`/`vencida`/`pagada` calculado con "hoy" en hora RD.
  `total` es el `MontoTotal` del e-CF recibido (lo que cobra el proveedor),
  o la suma de las casillas del 606 si no hay e-CF.
- **Notas de crédito del proveedor:** llegan como E34 recibido o se
  registran con `POST /compras/facturas` (B04/E34, `ncf_modificado`
  obligatorio; forma de pago por defecto 06). No son cuentas por pagar: su
  detalle (`GET /compras/facturas/{id}`) sale con `es_nota_credito`,
  `ncf_modificado`, `afecta_registro606_id`, pendiente 0 y estado
  `pagada`; su total se descuenta de la factura que modifican.
- **Pagos (`compras.pagos`)**: `monto` es lo que sale del banco (neto de
  retenciones). Las retenciones van en **un solo pago** por factura (409 si
  ya hay otro con retenciones). Cada pago o anulación **recalcula los
  campos de pago del 606**:
  - `fecha_pago`: la del pago con retenciones o, si ninguno las tiene, la
    del último pago. Es la que decide el mes del IT-1 y el IR-17.
  - `itbis_retenido`, `tipo_retencion_isr`, `monto_retencion_renta`: las
    del pago con retenciones o, si ninguno, las que el renglón tenía antes
    del primer pago (p. ej. clasificadas a mano en el 606).
  - `forma_pago` (casilla 23 del 606): mientras quede saldo, **4**
    (compra a crédito). Saldada: 1 efectivo, 2 transferencia/cheque, 3
    tarjeta; 6 (notas de crédito) se suma si una nota cubrió parte, y si hay
    más de una forma, 7 (mixto).
  - Sin pagos vigentes, el renglón vuelve exactamente a como estaba antes
    del primer pago (`compras.facturas.base_*`, guardado al registrarlo).
  - 409 si cambia cualquier campo del renglón y el **606** de su período
    está marcado como presentado, o si altera el ITBIS retenido de un IT-1
    o el ISR de un IR-17 ya presentado (mismo candado que el 608); hay que
    desmarcarlo primero. El mensaje nombra la declaración y el mes ("El
    IT-1 de octubre 2026 ya está marcado como presentado…"). El mismo
    candado del 606 (y del IT-1 por el ITBIS adelantado) aplica al
    registrar, eliminar o reclasificar (`tipo_bienes_servicios`) una
    factura de compra.
  - La fecha del pago no puede ser futura (hora RD).

  **Por qué SQL directo y no `PUT /registros/{id}` de `formato-606`:** el
  pago y el 606 se escriben en la **misma transacción**, con el renglón
  bloqueado (`FOR UPDATE`, que además serializa pagos simultáneos). Con una
  llamada HTTP entre servicios, un fallo de red después de guardar el pago
  dejaría el pago sin retenciones en el 606 (o al revés) y el IT-1/IR-17
  mal. Las validaciones son las de `libs/ecf_common/compras606.py` (las
  mismas funciones que usa `formato-606`: fecha AAAAMMDD, tipo de retención
  1-9, forma de pago 1-7, montos no negativos, fecha de pago obligatoria
  con retenciones), más: fecha de pago no anterior al comprobante, ITBIS
  retenido ≤ facturado, ISR retenido ≤ monto facturado, y monto +
  retenciones ≤ pendiente. Si se editan a mano en el 606 los campos de pago
  de una factura con pagos, el próximo pago o anulación los recalcula.
- **Aprobación comercial:** `aprobar`/`rechazar` arman el ACECF (Estado 1/2,
  `DetalleMotivoRechazo`, `FechaHoraAprobacionComercial` en hora RD;
  `FechaEmision` obligatoria: 422 si el e-CF no la tiene) y lo mandan a
  `aprobacion-comercial` (`POST /aprobacion`, que lo firma, lo envía a la
  DGII y devuelve el XML firmado). Solo desde `pendiente` (409).
  - **Sin candados ni conexiones durante el envío:** (1) transacción corta
    que marca la fila `enviando` (`decision_en_envio`, `envio_iniciado_en`;
    la API la muestra `pendiente` con `envio_en_curso`); (2) la llamada
    HTTP, sin conexión tomada; (3) guarda el resultado. Un segundo clic
    mientras se envía → 409. Si el proceso muere a mitad, a los 5 minutos
    se puede volver a intentar.
  - **Solo `codigo` "1" de la DGII es éxito.** Cualquier otro: sigue
    `pendiente` con `error_aprobacion` (los mensajes de la DGII) y 502; 409
    si los mensajes dicen que el e-CF no requiere aprobación comercial. Si
    el módulo falla, también 502.
  - **Rechazo:** 409 si la factura tiene pagos vigentes (anúlelos antes) o
    si el 606/IT-1 de su mes (o el IT-1/IR-17 de retenciones clasificadas a
    mano) está presentado; se valida antes de enviar. Aceptado el rechazo,
    su renglón del 606 se borra: un e-CF rechazado no es una compra válida
    para el comprador, así que no va al 606 ni su ITBIS al IT-1. El rechazo
    no se puede deshacer (el ACECF ya está en la DGII) y, si el proveedor
    reenvía el e-CF, `receptor-recepcion` no lo vuelve a poner en el 606.
  - **Entrega al proveedor** (estándar del receptor: el ACECF también va al
    emisor, a su `POST /fe/aprobacioncomercial/api/ecf`): tras el éxito en la
    DGII, se busca su `urlAceptacion` en `consulta-directorio` y se le
    envía el ACECF firmado. Best-effort: el resultado queda en
    `enviado_al_emisor` / `error_envio_emisor` y nunca hace fallar la
    aprobación. No se autentica ante el proveedor: si su servicio exige
    token, la entrega falla con su 401.
  - El XSD oficial del ACECF no está en el repositorio: los elementos siguen
    el formato publicado por la DGII, igual que el ARECF de
    `receptor-recepcion`.
- **E41/E43:** se emiten con el formulario de facturas (`facturas-api`), que
  al enviarlos ya crea su renglón del 606. `GET /compras/comprobantes` los
  lista con proveedor (solo E41: el Comprador), concepto (primera línea),
  retenciones según el e-CF y las del 606.
- **Retenciones del mes:** renglones del 606 con `fecha_pago` del mes y
  ITBIS o ISR retenido -- exactamente lo que suman el IT-1 ("ITBIS retenido
  a proveedores") y el IR-17. `declarada` si el IT-1/IR-17 del mes están
  marcados como presentados.

### Endpoints

| Método y ruta | Qué hace |
|---|---|
| `GET /compras/bandeja?estado=&q=&periodo=` | e-CF recibidos, más recientes primero. `estado` = `pendiente`/`aprobado`/`rechazado`; `q` busca en e-NCF, RNC y razón social; `periodo` = AAAAMM de emisión |
| `GET /compras/bandeja/{id}` | Detalle con las líneas del XML, subtotal, el XML y la respuesta de la DGII al ACECF |
| `GET /compras/bandeja/{id}/xml` | Descarga `RNC+e-NCF.xml` |
| `POST /compras/bandeja/{id}/aprobar` · `POST /compras/bandeja/{id}/rechazar` `{motivo}` | Envía el ACECF por `aprobacion-comercial` y, aceptado, al proveedor. 409 si ya se decidió, se está enviando, la DGII dice que no requiere aprobación o (rechazo) tiene pagos / 606 presentado; 422 sin FechaEmision; 502 si falla el envío o la DGII no lo acepta; motivo obligatorio (≤ 250) |
| `POST /compras/bandeja/{id}/vincular` `{registro606_id?, compra_inventario_id?}` | Asegura su renglón del 606 (lo busca o lo crea desde el XML) y lo vincula a una compra de inventario. 409 si fue rechazado; 422 si `registro606_id` es de otro proveedor u otro NCF |
| `GET /compras/cuentas-por-pagar?estado=&proveedor_rnc=&q=&periodo=` | Facturas de compra con total, retenciones, pagado, pendiente, vence y estado. Sin `estado`: lo no pagado; `todas`: todo |
| `POST /compras/facturas` | Registra una factura de compra o nota de crédito (renglón del 606 origen `compras` + datos de gestión). 409 NCF repetido del proveedor o 606/IT-1 del mes presentado; 422 si es E41/E43/E47, nota sin `ncf_modificado` o datos inválidos |
| `GET /compras/facturas/{registro606_id}` · `PUT` · `DELETE` | Detalle con sus pagos (también de una nota de crédito). `PUT` cambia solo lo enviado (gestión + `tipo_bienes_servicios` del 606; no vuelve a null una vez clasificado: 422). `DELETE` solo origen `compras` sin pagos vigentes (409). 409 si el 606 (o el IT-1) del mes está presentado |
| `GET /compras/pagos?registro606_id=&periodo=` · `POST /compras/pagos` | Pagos a proveedores; el `POST` actualiza el 606 (ver Modelo). Fecha por defecto = hoy; 422 si es futura |
| `POST /compras/pagos/{id}/anular` | Anula y recalcula el 606; 409 si ya estaba anulado o el IT-1/IR-17 afectado está presentado |
| `GET /compras/comprobantes?tipo_ecf=41\|43&estado=&q=&periodo=` | E41 y E43 emitidos |
| `GET /compras/retenciones?periodo=AAAAMM` | Retenciones del mes por comprobante y por proveedor, con totales |
| `GET /compras/retenciones/certificado?periodo=&rnc=` | Datos del certificado de retención del proveedor en el mes (agente = `EMISOR_RNC` + `EMISOR_RAZON_SOCIAL`). 404 si no hay retenciones |
| `GET /compras/retenciones/sugerencia?persona=&tipo_bienes_servicios=&monto=&itbis=&emisor_electronico=&fecha_pago=` | Retenciones sugeridas para "Nueva factura de compra" (ver abajo) |

**Sugerencia de retenciones** (orientativa; el usuario confirma los montos
al pagar). Persona física: servicios (02, 05) → ISR honorarios (tipo 2) +
ITBIS 100 %; arrendamiento (03) → ISR alquileres (tipo 1) + ITBIS 100 %;
bienes (04, 09, 10) a un proveedor sin e-CF (E41) → ISR otras rentas
presuntas (tipo 4) + ITBIS 100 %. Persona jurídica, servicios (02, 05) →
ITBIS 30 %, sin ISR (Norma General 02-2005). La tasa de ISR es la de
`precalculos.RETENCIONES_ISR` para el mes del **pago** (Ley 30-26 desde
julio 2026: 10 % → 15 %, 2 % → 3 %), la misma con la que el IR-17 compara.
Los pagos al exterior (E47) no se sugieren: no tienen tipo de retención en
el 606.

### Configuración

- `APROBACION_COMERCIAL_URL` (por defecto `http://aprobacion-comercial:8000`)
  y `APROBACION_COMERCIAL_TIMEOUT_SEGUNDOS` (60).
- `CONSULTA_DIRECTORIO_URL` (por defecto `http://consulta-directorio:8000`)
  y `ENTREGA_EMISOR_TIMEOUT_SEGUNDOS` (30), para entregar el ACECF al
  proveedor.
- `EMISOR_RAZON_SOCIAL` (opcional) para el certificado de retención.

## Bancos

Fase 5: cuentas bancarias, movimientos, transferencias entre cuentas,
importación del extracto (CSV) y conciliación. El contrato es
[`frontend/src/lib/bancosApi.ts`](../frontend/src/lib/bancosApi.ts) (pantalla
`Bancos.dc.html` del prototipo: pestañas Movimientos, Conciliación y
Transferencias). Rutas en `app/bancos.py`, consultas en
`app/bancos_repository.py`, lectura del CSV y sugerencias en
`app/bancos_calculos.py` (puras). Esquema `bancos` (`DDL_BANCOS`, copia en
[`db/init/012_bancos_contabilidad.sql`](../db/init/012_bancos_contabilidad.sql)).

### Modelo

- **Cuenta bancaria** (`bancos.cuentas`): banco, tipo (`corriente`,
  `ahorro`, `caja_chica`, `tarjeta`), número (se guarda completo; la API
  solo devuelve `numero_enmascarado` = `****` + los últimos 4), moneda
  (DOP/USD; la caja chica solo DOP), saldo inicial al empezar
  `fecha_saldo_inicial`, cuenta contable opcional y `activa`.
  **Saldo actual = saldo inicial + movimientos desde esa fecha.**
- **Movimientos** de una cuenta = los registrados en Bancos
  (`bancos.movimientos`: depósito, retiro, cargo bancario, interés,
  comisión y las dos patas de cada transferencia) **más** los cobros
  (`ventas.cobros`) y pagos a proveedores (`compras.pagos`) con
  `cuenta_bancaria_id`. Los cobros/pagos no se copian: anularlos los saca de
  la cuenta sin tocar Bancos. Cada movimiento se identifica por (`origen`
  `manual` | `cobro` | `pago`, `id`); el monto va con signo (+ entra,
  − sale). Un pago a proveedor mueve el banco por `monto` (neto de
  retenciones); un pago solo de retenciones (monto 0) no es movimiento.
- **`cuenta_bancaria_id` en cobros y pagos** (`POST /cobros`,
  `POST /compras/pagos`): opcional; el texto libre `cuenta_destino` /
  `cuenta_origen` se conserva por compatibilidad. 422 si la cuenta no
  existe, está inactiva, es en USD (los cobros y pagos son en RD$) o la
  fecha es anterior a su saldo inicial. La columna y su índice los agrega
  `DDL_BANCOS` **solo si faltan** (bloque `DO` que consulta
  `information_schema` / `to_regclass` antes del `ALTER TABLE` / `CREATE
  INDEX`): `ventas.cobros` y `compras.pagos` reciben escrituras todo el día
  y un `ALTER TABLE` toma su AccessExclusiveLock en cada arranque aunque la
  columna ya exista. La migración corre con `lock_timeout` de 5 s y, si una
  tabla está ocupada o hay un deadlock, reintenta con espera creciente
  (como `inventario_api`).
- **Anular un cobro o pago de una cuenta bancaria** (`POST
  /cobros/{id}/anular`, `POST /compras/pagos/{id}/anular`) toma el candado
  de la cuenta: si está conciliado con una línea del extracto que ya quedó
  en una conciliación cerrada (fecha de la línea <= `conciliada_hasta`), 409
  ("reabra esa conciliación"); si la línea está en el período abierto, se
  desconcilia en la misma transacción (la línea vuelve a quedar pendiente).
- **Transferencias** (`bancos.transferencias`): la transferencia y sus dos
  movimientos (`transferencia_salida` / `transferencia_entrada`) se crean y
  se anulan en una sola transacción, con el candado de las dos cuentas en
  orden fijo (no hay transferencias a medias ni bloqueos cruzados). Entre
  monedas distintas exige `monto_destino` y la tasa sale de los dos montos;
  entre dos cuentas en USD exige `tasa_cambio`. Los movimientos manuales en
  una cuenta en USD exigen `tasa_cambio` (para el asiento en RD$).
- **Extracto** (`bancos.extractos`, `bancos.extracto_lineas`): se sube el
  CSV (multipart `archivo`, opcional `mapeo` en JSON y `saldo_final`). Se
  detectan el separador (`,` `;` tabulador `|`), la fila de encabezado (la
  primera con una columna de fecha y otra de monto/débito/crédito; admite
  títulos antes) y cada columna por sinónimos: "Fecha", "Fecha Posteo",
  "Fecha Transacción"; "Descripción", "Concepto", "Detalle";
  "Referencia", "No. Referencia", "Documento", "Cheque"; "Débito",
  "Retiros", "Cargos"; "Crédito", "Depósitos", "Abonos"; un solo "Monto"
  con signo o con una columna D/C ("Tipo", "DB/CR"); "Balance", "Saldo".
  Montos con `RD$`, miles con coma o punto, negativos con `-`, `(…)` o
  `-` al final; fechas día primero (DD/MM/AAAA) o ISO. UTF-8,
  Windows-1252 o UTF-16 con BOM (Excel "Texto Unicode"); un CSV que el
  módulo `csv` no puede leer da 422. **Separador decimal por columna:** sin
  `separador_decimal` en el mapeo, cada columna de montos toma el estilo de
  sus valores no ambiguos ("2.300,50" o "1.250.000" = miles con punto;
  "1,250.00" o "12.50" = decimales con punto); "1.250" solo es 1,250 si la
  columna es del primer estilo, si no es 1.25 (y "1,250" es 1,250). En la
  columna D/C se aceptan también "ND"/"NC" (nota de débito / crédito).
  `mapeo` fija cualquier columna por nombre o posición, el
  formato de fecha, el separador decimal, el signo de la columna única y la
  fila del encabezado. Las filas sin fecha (totales) se ignoran; las que no
  se pueden leer van a `errores` con su número de fila. Cada línea tiene una
  huella (cuenta, fecha, monto, referencia, descripción y número de
  repetición dentro del archivo): volver a subir el mismo archivo no duplica
  (`duplicadas`). `POST /bancos/extractos/previsualizar` lee sin guardar
  para la pantalla de mapeo. Los layouts de los bancos dominicanos no están
  publicados: los sinónimos son supuestos razonables, y el mapeo explícito
  cubre lo demás.
- **Conciliación:** una línea se concilia con un movimiento de la misma
  cuenta y el **mismo monto** (con signo; si difieren, se registra la
  diferencia aparte). Un movimiento se concilia con a lo sumo una línea;
  una línea cuyo movimiento se anuló vuelve a contar como no conciliada.
  - **Sugerencias / automática:** pares sin conciliar con el mismo monto y
    fechas a ±`dias` (3 por defecto); puntaje 100 − 10 por día de
    diferencia + 50 si la referencia de uno aparece en la referencia o
    descripción del otro (sin espacios ni guiones). Se asignan de mayor a
    menor puntaje, uno a uno. Con el corte `hasta`, una línea de fecha <=
    corte no se sugiere con un movimiento posterior al corte (quedaría
    pendiente y no dejaría cerrar).
  - **Línea sin registro** (comisión, cargo por SMS, intereses): `POST
    /bancos/extracto-lineas/{id}/movimiento` crea el movimiento (cargo
    bancario o depósito por defecto, según el signo) y la concilia.
  - **Cierre** al corte `hasta`: saldo según el banco (`saldo_banco`
    enviado, o el `saldo_final` del extracto que termina ese día, o la
    columna de saldo de su última línea **en el tiempo** con fecha <= corte,
    o el anterior + las líneas) contra
    saldo en libros − partidas en tránsito (movimientos con fecha <= corte
    no conciliados con una línea de fecha <= corte). 409 si quedan líneas
    del extracto sin conciliar hasta el corte o si la diferencia no es 0.
    Cerrada, todo lo que Bancos registra con fecha <= corte (movimientos,
    transferencias, conciliar/desconciliar, cambiar el saldo inicial) da
    409 hasta reabrirla (`DELETE`, solo la última); las líneas de un
    extracto con esas fechas se omiten al importar (van a `errores`). Los cobros y pagos **no** se bloquean (no se frenan los flujos
    de Ventas/Compras): uno con fecha ya conciliada queda como partida en
    tránsito de la siguiente.
  - **Última línea en el tiempo:** unos bancos listan primero lo más
    antiguo y otros lo más reciente (con varias líneas el mismo día, la
    última fila no es siempre la última operación). El orden de cada
    extracto sale del saldo corrido (saldo anterior + monto = saldo, en un
    sentido o en el otro) o, si no alcanza, de las fechas; si nada lo decide
    se toma la última fila y `resumen.advertencias` lo dice.
- Todas las operaciones que cambian una cuenta toman su candado
  (`pg_advisory_xact_lock`), igual que el cierre.
- Las consultas de una sola cuenta (su resumen, movimientos, conciliación,
  cuadre, líneas) filtran por la cuenta dentro de la unión de movimientos
  (índices `movimientos_cuenta_fecha_idx`, `cobros_cuenta_bancaria_idx`,
  `pagos_cuenta_bancaria_idx`, `extracto_lineas_cuenta_fecha_idx`) en lugar
  de recorrer los movimientos de todas las cuentas.

### Endpoints

| Método y ruta | Qué hace |
|---|---|
| `GET /bancos/cuentas?activa=` · `POST` · `GET/PUT/DELETE /bancos/cuentas/{id}` | Cuentas con `saldo_actual`, `movimientos_sin_conciliar`, `lineas_sin_conciliar`, `conciliada_hasta`. `PUT` cambia solo lo enviado (409: saldo inicial con conciliaciones cerradas, moneda con movimientos, fecha inicial posterior a un movimiento). `DELETE`: 409 si tiene movimientos, cobros, pagos o extractos |
| `GET /bancos/cuentas/{id}/movimientos?desde=&hasta=&conciliado=&q=` | Movimientos (más recientes primero) con el saldo después de cada uno, saldo al inicio y al final, entradas y salidas |
| `POST /bancos/movimientos` · `POST /bancos/movimientos/{id}/anular` | Depósito, retiro, cargo bancario, interés o comisión (monto positivo). 409 si la fecha está conciliada; anular: 409 si es de una transferencia |
| `GET /bancos/transferencias?cuenta_id=` · `POST` · `POST /bancos/transferencias/{id}/anular` | Transferencias entre cuentas propias (atómicas) |
| `POST /bancos/extractos/previsualizar` (multipart) | Lee el CSV sin guardar: columnas, mapeo, 20 primeras líneas, errores |
| `POST /bancos/cuentas/{id}/extractos` (multipart) · `GET` · `DELETE /bancos/extractos/{id}` | Importa (`importadas`, `duplicadas`, `errores`); 413 si pasa de 5 MB. `DELETE`: 409 con líneas conciliadas |
| `GET /bancos/cuentas/{id}/conciliacion?hasta=` | Líneas posteriores a la última conciliación, movimientos sin conciliar y el cuadre (`puede_cerrar`) |
| `GET /bancos/cuentas/{id}/conciliacion/sugerencias?dias=` · `POST …/conciliacion/auto` | Sugerencias / aplicarlas (todas o `lineas`) |
| `POST /bancos/extracto-lineas/{id}/conciliar` `{origen, movimiento_id}` · `…/desconciliar` · `…/movimiento` | Conciliación manual y "crear movimiento desde la línea" |
| `GET/POST /bancos/cuentas/{id}/conciliaciones` · `DELETE /bancos/conciliaciones/{id}` | Conciliaciones cerradas / cerrar / reabrir la última |

## Contabilidad

Fase 5: catálogo de cuentas, cuentas por defecto, asientos manuales y
automáticos, reportes y activos fijos. El contrato es
[`frontend/src/lib/contabilidadApi.ts`](../frontend/src/lib/contabilidadApi.ts)
(pantalla `Contabilidad.dc.html`: pestañas Catálogo de cuentas, Asientos y
Activos fijos). Rutas en `app/contabilidad.py`, consultas en
`app/contabilidad_repository.py`, armado de asientos, catálogo sugerido y
depreciación en `app/contabilidad_calculos.py` (puras). Esquema
`contabilidad` (`DDL_CONTABILIDAD`, copia en `db/init/012_…`). Las pruebas
contra un Postgres real están en `tests/test_bancos_contabilidad_postgres.py`
(se salta sin `CONTABILIDAD_TEST_DATABASE_URL`).

### Catálogo de cuentas

- Árbol por código: `1.1.01.03` es hija de `1.1.01`. Al crear una cuenta el
  padre sale del código (422 si no existe), el tipo del padre y la
  naturaleza del tipo (activo, costo y gasto deudoras; pasivo, patrimonio e
  ingreso acreedoras; las cuentas "contra" se marcan con la contraria).
  Solo las hojas aceptan movimientos: una cuenta que recibe una subcuenta
  pasa a ser de grupo (409 si ya tiene movimientos o es una cuenta por
  defecto / de un banco), y vuelve a ser de movimiento cuando se borra su
  última subcuenta.
- **Catálogo sugerido** (estilo NIIF para PYMES, como lo usan las PYMES
  dominicanas): 1 Activos (caja general, caja chica, bancos, CxC clientes,
  inventario, ITBIS pagado por compensar, anticipos y retenciones de ISR que
  nos hicieron, propiedad planta y equipo por categoría del Art. 287 y su
  depreciación acumulada), 2 Pasivos (CxP proveedores, tarjetas, ITBIS por
  pagar, otros impuestos, ITBIS e ISR retenidos por pagar, nómina,
  préstamos), 3 Patrimonio (capital, reserva legal, resultados), 4 Ingresos
  (ventas gravadas, exentas, exportaciones, devoluciones en ventas,
  ingresos financieros), 5 Costos (costo de ventas) y 6 Gastos (uno por
  categoría del 606, depreciación, redondeo, gastos financieros y
  comisiones bancarias). 79 cuentas. Se siembra al arrancar (bloque `DO`
  de `DDL_CONTABILIDAD`, generado desde
  `contabilidad_calculos.CATALOGO_SUGERIDO`) **solo si el catálogo está
  vacío**: nunca toca uno que el usuario ya tenga.
- Saldo de cada cuenta (con sus subcuentas) según su naturaleza, solo con
  asientos `contabilizado`.

### Asientos

- **Manuales:** fecha, concepto, referencia y líneas débito/crédito (una de
  las dos por línea, en cuentas de movimiento activas). `borrador` (puede
  estar descuadrado, se edita y se borra) → `contabilizado` (debe cuadrar,
  422 con la diferencia) → `anulado`. Numeración `AS-0001` correlativa.
- **Automáticos** (`POST /contabilidad/asientos/generar {desde, hasta}`): a
  pedido, nunca dentro de las transacciones de Ventas/Compras/Bancos, con un
  candado propio. Para cada documento del rango arma el asiento, lo resuelve
  con las cuentas por defecto y:
  - lo **crea** si falta (idempotente: índice único parcial por (`origen`,
    `origen_id`) de los no anulados);
  - lo deja igual si su `huella` (fecha, concepto, cuentas y montos) no
    cambió;
  - si cambió el documento (p. ej. se reclasificó el 606) o una cuenta por
    defecto, **anula** el anterior y crea uno nuevo (`actualizados`);
  - **anula** el de un documento que ya no existe o se anuló (cobro o pago
    anulado, e-CF rechazado comercialmente, renglón del 606 borrado). Un
    documento que cambió de fecha se busca por su id.
  - Lo que no se puede contabilizar va a `no_contabilizados` con el motivo:
    falta la cuenta por defecto, 606 sin "tipo de bienes y servicios",
    venta gratuita (TipoPago 3), e-CF sin MontoTotal, cobro de una factura
    que la DGII no ha aceptado. Si el documento ya tenía asiento (p. ej. el
    606 quedó sin clasificar, la factura del cobro pasó a rechazada), ese
    asiento se anula.
  - La compra y los pagos de un mismo renglón del 606 se regeneran juntos
    (aunque sean de otro mes): de ellos depende quién lleva las retenciones
    del 606 (ver la tabla).
  - `GET /contabilidad/asientos/pendientes` hace lo mismo sin guardar (lo
    que se crearía/actualizaría/anularía y los no contabilizables). El
    tablero (`GET /contabilidad/resumen`) reutiliza ese conteo hasta 60 s
    por rango (en memoria del proceso; `generar` lo vacía) en lugar de
    simularlo en cada carga.
  - Los automáticos no se editan ni se anulan a mano (409): se corrige el
    documento y se vuelve a generar. La depreciación del mes sí se puede
    anular (para recalcularla).

**Asientos automáticos y cuentas por defecto** (`concepto` → cuenta del
catálogo sugerido; se cambian en `PUT /contabilidad/cuentas-por-defecto`):

| Documento (origen) | Fecha | Débito | Crédito |
|---|---|---|---|
| e-CF de venta **aceptado** E31/E32/E44/E45/E46 (`venta`) y nota de débito E33 (`nota_debito`) | FechaEmision | `cxc_clientes` 1.1.02.01 (TipoPago 2) por el MontoTotal; al contado, según la TablaFormasPago: cheque/transferencia/depósito (2) y tarjeta (3) a `banco` 1.1.01.03, el resto (efectivo y demás formas, o sin tabla) a `caja` 1.1.01.01 | `ventas_gravadas` 4.1.01 (base 18 % + 16 %), `ventas_exentas` 4.1.02, `exportaciones` 4.1.03 (tasa 0 %), `itbis_por_pagar` 2.1.02.01 (ITBIS 18 % + 16 %); la diferencia con el MontoTotal (selectivo, otros impuestos, propina) a `otros_impuestos_por_pagar` 2.1.02.02, o a `redondeo` 6.1.10 si es ≤ RD$1 |
| Nota de crédito E34 (`nota_credito`) | FechaEmision | `devoluciones_ventas` 4.1.04 (base) + `itbis_por_pagar` | `cxc_clientes` si la factura que modifica fue a crédito, si no `caja` |
| Cobro (`cobro`; solo de facturas aceptadas) | fecha | la cuenta bancaria (su cuenta contable o `banco` 1.1.01.03 / `caja_chica` / `tarjeta_credito` según su tipo); sin cuenta bancaria: `caja` si es en efectivo, si no `banco` | `cxc_clientes` |
| Renglón del 606 (`compra`; no los e-CF rechazados) | fecha del comprobante | `gasto_606_NN` según el tipo de bienes y servicios (01→6.1.01 personal, 02→6.1.02 servicios, 03→6.1.03 arrendamientos, 04→6.1.04, 05→6.1.05, 06→6.1.06, 07→6.2.01 financieros, 08→6.1.09, 09→5.1.01 costo de ventas, 10→1.2.01.03 activo fijo, 11→6.1.07 seguros) por el total − ITBIS adelantado; `itbis_adelantado` 1.1.04.01 por ITBIS facturado − ITBIS llevado al costo. **Sin pagos**, si el renglón ya tiene retenciones (clasificadas antes de pagar, p. ej. un E41): `cxp_proveedores` por esas retenciones | `cxp_proveedores` 2.1.01.01 por el total por pagar (MontoTotal del e-CF o la suma de las casillas del 606); E43 gasto menor: `caja_chica`. Nota de crédito del proveedor (B04/E34): al revés. Retenciones del renglón sin pagos: `itbis_retenido_por_pagar` / `isr_retenido_por_pagar` |
| Pago a proveedor (`pago`) | fecha | `cxp_proveedores` por monto + retenciones | la cuenta bancaria por `monto` (sin cuenta: `caja` en efectivo, `tarjeta_credito` con tarjeta, si no `banco`), `itbis_retenido_por_pagar` 2.1.03.01, `isr_retenido_por_pagar` 2.1.03.02. Si ningún pago de la factura lleva retenciones pero el renglón del 606 sí las tiene, las lleva el **último** pago vigente (su fecha es la fecha de pago del 606, la que decide el mes del IT-1 / IR-17) |
| Ajuste por proporcionalidad (`proporcionalidad`, `origen_id` AAAAMM) | último día del mes | `itbis_no_admitido` 6.1.06 por el ITBIS adelantado no admitido del IT-1 del mes (casillas 10/11, mismo coeficiente: `precalculos.itbis_adelantado_admitido`) | `itbis_adelantado`. Opcional: sin cuenta por defecto `itbis_no_admitido` no se genera (y se anulan los que había; en un catálogo sembrado antes de que existiera el concepto, asígnela en Cuentas por defecto). Uno por mes, regenerado si cambian las ventas o compras del mes |
| Movimiento bancario (`movimiento`) | fecha | depósito/interés: la cuenta bancaria; retiro/cargo/comisión: la contrapartida | depósito/interés: contrapartida; retiro/cargo/comisión: la cuenta bancaria. Contrapartida = la `cuenta_contable_id` del movimiento o `caja` (depósito, retiro), `gastos_bancarios` 6.2.02 (cargo, comisión), `ingresos_financieros` 4.2.01 (interés). En USD: monto × tasa |
| Transferencia (`transferencia`) | fecha | cuenta de destino | cuenta de origen (en RD$: el lado en pesos, o monto × tasa entre dos cuentas en USD) |
| Depreciación del mes (`depreciacion`, `POST /contabilidad/depreciacion`) | último día del mes | `gasto_depreciacion` 6.1.08 | `depreciacion_acumulada_N` 1.2.02.0N por categoría |

Coherencia (verificada contra Postgres real en las pruebas): con el mismo
desglose por tasa que el IT-1 y el 607 (`formatos.desglose_por_tasa`), los
ingresos del estado de resultados = total de ingresos del IT-1, el saldo de
ITBIS por pagar = ITBIS facturado del IT-1, ITBIS adelantado = el
"adelantado admitido" del IT-1 (con el ajuste por proporcionalidad), ITBIS
retenido por pagar = "ITBIS retenido a proveedores" del IT-1 del mes del
pago (también las retenciones clasificadas en el 606 antes de pagar), ISR
retenido por pagar = el IR-17, y CxC/CxP = lo pendiente de Ventas/Compras.
Diferencias esperadas: el saldo a favor no se asienta solo; los saldos
iniciales de las cuentas bancarias tampoco (registre un asiento de
apertura); las facturas solo "enviadas" (sin aceptación de la DGII) y sus
cobros no se contabilizan, igual que en el IT-1. El costo de ventas es
periódico (las compras del 606 tipo 09 van a 5.1.01; el ajuste de
inventario final es un asiento manual), como el IR-2.

**Limitaciones** (conocidas; se resuelven con asientos manuales o quedan
para una fase siguiente):

- **E43 (gastos menores) y caja chica:** el asiento del E43 acredita la
  cuenta por defecto `caja_chica`, pero no crea un movimiento en Bancos:
  el saldo de una cuenta bancaria de tipo caja chica en Bancos no baja con
  los E43 (registre el retiro/reposición en Bancos) y su saldo contable
  puede diferir del de Bancos.
- **Ventas al contado:** van a `caja` o a la cuenta por defecto `banco`
  según la forma de pago del e-CF, nunca a una cuenta bancaria concreta (el
  e-CF no dice a cuál), y no generan un movimiento en Bancos: el depósito
  del efectivo o la liquidación de la tarjeta se registra en Bancos y su
  asiento (depósito: Banco contra Caja) cuadra la caja.
- **Sin diferencias cambiarias:** los movimientos y transferencias en USD se
  asientan con la tasa de cada uno; no se calculan ganancias o pérdidas por
  diferencia de cambio ni se revalúan los saldos en dólares al cierre.
- **Pago de las declaraciones:** pagar el IT-1, el IR-17 o el ISR no genera
  asiento (ITBIS por pagar / retenciones contra el banco): regístrelo como
  movimiento bancario con la cuenta contable del impuesto como
  contrapartida, o con un asiento manual.
- **Sin cierre de períodos contables:** generar asientos de un mes pasado
  (o un documento que cambió de fecha o de datos) los regenera aunque ese
  mes ya se haya reportado; no hay candado por período ni asientos de
  cierre del ejercicio.

### Reportes

Solo asientos contabilizados; sin fechas, el mes en curso (hora RD).

| Método y ruta | Qué hace |
|---|---|
| `GET /contabilidad/resumen?desde=&hasta=` | Tablero: activos, pasivos y patrimonio al `hasta`; ingresos, costos, gastos y utilidad del período; asientos y documentos por contabilizar |
| `GET /contabilidad/libro-diario?desde=&hasta=` | Asientos con sus líneas y totales |
| `GET /contabilidad/mayor?cuenta_id=&desde=&hasta=` | Movimientos de la cuenta (y sus subcuentas) con saldo inicial y saldo corrido según su naturaleza |
| `GET /contabilidad/balanza?desde=&hasta=&nivel=` | Balanza de comprobación: saldo inicial, débitos, créditos, saldo final (deudor/acreedor); `cuadrada` = débitos = créditos y saldos deudores = acreedores |
| `GET /contabilidad/estado-resultados?desde=&hasta=` | Ingresos (netos de devoluciones), costos, utilidad bruta, gastos y utilidad neta, por cuenta |
| `GET /contabilidad/balance-general?fecha=` | Activos, pasivos y patrimonio; sin asientos de cierre, el resultado se muestra como "del ejercicio" (desde el 1 de enero) y "de ejercicios anteriores no cerrados". `cuadrado` |

Otros endpoints: `GET/POST /contabilidad/cuentas`, `GET/PUT/DELETE
/contabilidad/cuentas/{id}`, `GET/PUT /contabilidad/cuentas-por-defecto`,
`GET/POST /contabilidad/asientos?desde=&hasta=&origen=&estado=&q=&cuenta_id=`,
`GET/PUT/DELETE /contabilidad/asientos/{id}`, `POST
/contabilidad/asientos/{id}/contabilizar`, `POST …/anular`.

### Activos fijos y depreciación

- `GET/POST /contabilidad/activos-fijos`, `GET/PUT/DELETE …/{id}`, `POST
  …/{id}/baja {fecha, valor_venta?}`, `GET …/{id}/depreciacion?hasta=AAAAMM` (cuadro
  mensual y la parte del activo en la depreciación fiscal de cada año), `GET /contabilidad/depreciacion?periodo=`
  (vista previa del mes), `POST /contabilidad/depreciacion {periodo}`
  (asiento del mes; 409 si ya está registrada, se anula el asiento para
  recalcularla), `GET /contabilidad/depreciacion/fiscal?anio=` (total,
  por activo, `categorias` con la cuenta de las categorías 2 y 3 e
  `ingreso_gravable`).
- **Categorías (Art. 287, literal e, del Código Tributario, Ley 11-92):**
  1 = edificaciones y sus componentes estructurales, **5 %** anual;
  2 = automóviles y camiones livianos de uso común, equipos y muebles de
  oficina, computadoras y equipos de procesamiento de datos, **25 %**;
  3 = cualquier otra propiedad depreciable, **15 %**.
- **Métodos de los asientos (libros):** `dgii`: categoría 1 sobre el costo
  (línea recta al 5 %); categorías 2 y 3 por saldo decreciente, activo por
  activo, la tasa sobre el valor pendiente al inicio de cada año. `linea_recta`: (costo − residual
  − depreciación acumulada inicial) / vida útil en meses; el último mes
  ajusta los centavos. Cuota mensual = anual / 12. Se deprecia desde
  `depreciar_desde` (por defecto el mes siguiente a la adquisición) y hasta
  el mes de la baja; `depreciacion_acumulada_inicial` es la que el activo
  ya tenía hasta el mes anterior a `depreciar_desde`.
- **Depreciación fiscal (IR-2)**, sea cual sea el método de los asientos
  (`contabilidad_calculos.depreciacion_fiscal_del_anio`):
  - categoría 1: por activo, 5 % del costo al año por los meses en uso;
  - categorías 2 y 3: **una cuenta por categoría**. Base del año = valor
    fiscal al cierre del año anterior + adiciones del año (costo −
    depreciación acumulada inicial) − valor de venta de los dados de baja
    en el año; depreciación = base × 25 % / 15 % (año completo); el resto
    pasa al año siguiente. Un retiro sin venta (`valor_venta` vacío) no
    rebaja la cuenta. Si las ventas superan la cuenta, la base queda en 0 y
    el exceso es `ingreso_gravable` (el pre-cálculo IR-2 lo advierte).
    `activos` reparte la depreciación de la cuenta entre sus activos en
    proporción a su valor fiscal (informativo).
  - Es el valor sugerido del ajuste `depreciacion` del pre-cálculo IR-2; un
    valor manual lo reemplaza.
  - Crear, cambiar (categoría, fecha de adquisición, costo,
    `depreciar_desde`, depreciación acumulada inicial), dar de baja o borrar
    un activo da 409 si el IR-2 de su año de entrada (o de la baja) o de uno
    posterior ya está marcado como presentado: la cuenta de la categoría
    pasa de un año al siguiente.
- Con depreciación registrada no se cambian costo, categoría, método ni
  fechas (409) ni se borra el activo (se da de baja). La ganancia o pérdida
  contable de la venta o retiro del activo se registra con un asiento
  manual.

**Interpretaciones (activos):** el Art. 287 y el Reglamento 139-98
aplican el porcentaje a la cuenta de cada categoría (las categorías 2 y 3
en una cuenta conjunta por categoría) al cierre del año fiscal; así se
calcula la depreciación fiscal. Supuestos: año fiscal = año calendario
(como el IR-2 de este sistema; no hay un ajuste de "mes de cierre" porque
el IR-2 tampoco lo tiene); un activo entra a la cuenta el año de su
adquisición, salvo que su depreciación empiece después del mes siguiente
(activos de antes de usar el sistema, con su depreciación acumulada
inicial): entonces el año de `depreciar_desde`. No se aplica una regla de
"medio año" para las adiciones (no verificada en el texto oficial) ni la
deducción del saldo de una categoría que se queda sin activos. Los
asientos de depreciación (libros) siguen activo por activo. La NG de la
DGII en consulta sobre depreciación acelerada (doble tasa de la categoría
3 para maquinaria industrial, Ley 30-26) no se aplica: es un borrador.

**Fuentes:** [Código Tributario, Título II (ISR), DGII](https://dgii.gov.do/legislacion/codigoTributario/Cdigo%20Tributario/Titulo2.pdf)
(Art. 287 literal e: categorías y porcentajes 5/25/15 y su aplicación a la
cuenta de activos al cierre del año) y el
[borrador de Norma General sobre depreciación acelerada](https://dgii.gov.do/publicacionesOficiales/foroContribuyentes/Documents/Borrador-NG-dispone-lista-de-bienes-podra-acogerse-a-depreciacion-acelerada.pdf)
(Ley 30-26, numeral IX del literal e). dgii.gov.do no respondió desde el
entorno de desarrollo (403): el contenido se tomó del resumen del buscador
de esas páginas oficiales; conviene revisar el texto completo.

## Operación (fase 6): Mis tareas, Centro de sincronización y Reportes

Rutas en `app/tareas.py`, `app/sincronizacion.py` y `app/reportes.py`
(repositorios `*_repository.py`, esquemas en `app/operacion_schemas.py`).
Contrato con el frontend:
[`frontend/src/lib/operacionApi.ts`](../frontend/src/lib/operacionApi.ts)
(todas las peticiones pasan por su `request()` local).

### Datos

- Esquema propio **`operacion`** (`DDL_OPERACION` de `migraciones.py`; copia
  en [`db/init/014_operacion.sql`](../db/init/014_operacion.sql)):
  `tareas` (manuales), `tareas_automaticas` (completada / pospuesta de una
  tarea automática, por su clave estable) y `facturas_papel`.
- Todo lo demás se **calcula al leer**, con consultas de solo lectura sobre
  las tablas de los otros módulos y los mismos criterios que sus pantallas:
  `facturacion.facturas` y `facturacion.envios_dgii` (facturas-api y los
  módulos DGII), `recepcion.ecf_recibidos`, `compras606.registros`,
  `ventas.*`, `compras.*`, `impuestos.declaraciones_presentadas` e
  `inventario.*` (de inventario-api: productos, lotes, existencias,
  movimientos — se lee por SQL, nunca se escribe).

### Mis tareas

| Método y ruta | Qué hace |
|---|---|
| `GET /tareas?estado=&categoria=` | Manuales + automáticas con su `estado` (`pendiente`/`pospuesta`/`completada`), las vencidas y urgentes primero |
| `GET /tareas/resumen` | Contadores para la campana de la barra superior: pendientes, vencidas, vencen hoy, alta, por categoría y las 5 más urgentes |
| `POST /tareas` · `PUT /tareas/{id}` · `DELETE /tareas/{id}` | Tareas manuales (título, descripción, vence, prioridad); las automáticas no se editan ni se borran (422) |
| `POST /tareas/{id}/completar` · `/reabrir` · `/posponer` (`{hasta}`) | Para ambas; `id` es el uuid o la clave de la automática (404 si su motivo ya no existe) |

Tareas automáticas (clave → condición): `declaracion:<form>:<periodo>`
(pendientes que vencen en ≤ 30 días o vencidas hace ≤ 180, de
`listar_declaraciones`), `factura_rechazada|factura_error:<id>` (últimos 90
días), `dgii_en_proceso:<huella>` ('enviado' hace > 1 h), `ecf_por_aprobar:<id>`,
`cxc_vencida:<factura>`, `cxp_vencida|cxp_por_vencer:<registro606>` (≤ 7 días),
`cotizacion_por_vencer:<id>:<vence>` (≤ 3 días), `lote_vencido|lote_por_vencer:<lote>:<fecha>`
(con existencia, ≤ 30 días, criterio de `alertas_lotes`), `stock_bajo:<producto>`
(criterio de `estado_stock`), `inventario_sin_sincronizar:<huella>` y
`papel_sin_reemplazo:<id>`. Al listar se borra el estado guardado de las
claves cuya condición desapareció (si vuelve a darse, la tarea reaparece
pendiente); si una fuente falla (p. ej. falta el esquema de inventario), sus
tareas no salen y su estado no se toca.

### Centro de sincronización

| Método y ruta | Qué hace |
|---|---|
| `GET /sincronizacion/resumen` | Estado por canal (envío DGII, resultado DGII, inventario, aprobación comercial, entrega del ACECF), contadores de 7 días y la **contingencia** (estado de facturas-api, o calculada) |
| `GET /sincronizacion/pendientes?canal=` | Lo pendiente con su `accion`: `actualizar_estado` / `reenviar` (facturas-api), `reintentar_aprobacion` / `decidir_aprobacion` (`/compras/bandeja/{id}/aprobar|rechazar`), `reintentar_entrega` |
| `GET /sincronizacion/historial?modulo=&resultado=&q=&desde=&hasta=` | Bitácora de `facturacion.envios_dgii` |
| `GET /sincronizacion/envios-por-dia?dias=7` | Historial de envíos por día |
| `POST /sincronizacion/entregas/{id}/reintentar` | Vuelve a entregar al proveedor el ACECF ya aceptado (mismas columnas y función que `decidir_aprobacion`) |
| `GET/POST /sincronizacion/facturas-papel` · `PUT/DELETE /sincronizacion/facturas-papel/{id}` | Facturas emitidas en papel (NCF serie B, cliente, montos por tasa, ITBIS calculado si no se indica, forma de pago del 607, e-CF de reemplazo) |

**Contingencia.** `contingencia` es el estado real que guarda facturas-api
(`fuente = "facturas_api"`: `facturacion.contingencia`, las facturas en
estado `contingencia` como `pendientes` y los lotes de
`facturacion.lotes_contingencia` con sus contadores en vivo; `vence_en` = la
pendiente más antigua + 72 h). Si facturas-api todavía no creó esas tablas se
calcula como antes (`fuente = "calculada"`): activa cuando los últimos 3 o
más envíos/consultas a la DGII auditados en `envios_dgii` (últimas 24 h)
fallaron seguidos, y `pendientes` son los e-CF en 'error' sin trackId. Las
facturas en papel van al 607 (solo con `EMISOR_100_ELECTRONICO=false`) y a
los asientos automáticos (origen `factura_papel`). Ver
[docs/dgii/CONTINGENCIA.md](dgii/CONTINGENCIA.md).

### Reportes

Todos con `desde`/`hasta` (ambos incluidos, ≤ 1100 días) salvo antigüedad e
inventario valorizado (a hoy):

| Ruta | Qué agrega |
|---|---|
| `GET /reportes/ventas?agrupar=mes\|dia\|cliente\|producto\|tipo_ecf&cliente_rnc=&tipo_ecf=` | e-CF de venta aceptados (E34 resta) con el desglose por tasa del IT-1 (`formatos.desglose_por_tasa`); por producto, las líneas |
| `GET /reportes/itbis` | Por mes completo: ITBIS de ventas (como el IT-1), compras/ITBIS/costo/adelantado del 606 y retenido por mes de pago |
| `GET /reportes/compras?agrupar=proveedor\|categoria\|mes&proveedor_rnc=&tipo_bienes_servicios=` | 606 por fecha del comprobante (notas de crédito restan) |
| `GET /reportes/antiguedad-cxc?cliente_rnc=` · `/antiguedad-cxp?proveedor_rnc=` | Saldos de los CTE de cuentas por cobrar/pagar en tramos por vencer, 1-30, 31-60, 61-90, > 90 días, por contacto y por documento |
| `GET /reportes/cobros-pagos` | Cobros y pagos no anulados por mes, por método y detalle |
| `GET /reportes/inventario-valorizado?almacen_id=&categoria=` | Existencia × costo promedio (negativos valen 0) |
| `GET /reportes/rotacion-inventario?categoria=` | Existencia inicial/final reconstruida del Kardex, entradas/salidas (sin transferencias), rotación = salidas / existencia promedio |
| `GET /reportes/mas-vendidos?limite=&categoria=` | Salidas por factura del Kardex |
| `GET /reportes/retenciones` | Efectuadas (606 por fecha de pago, por proveedor y por tipo) y las que retuvieron los clientes en e-CF aceptados |

Pruebas: `tests/test_operacion.py` (rutas, repos simulados) y
`tests/test_operacion_postgres.py` (Postgres real con
`OPERACION_TEST_DATABASE_URL`, datos de `tests/operacion_semilla.py` y
totales calculados a mano).

## Configuración y plataforma (fase 6)

Rutas en `app/configuracion.py` (prefijo `/configuracion`) y
`app/plataforma.py`; esquema `configuracion` (`app/migraciones_configuracion.py`
= `db/init/013_configuracion.sql`). Contrato: `frontend/src/lib/configuracionApi.ts`.

| Método y ruta | Qué hace |
|---|---|
| `GET/PUT /configuracion/empresa` | Datos de la empresa (una fila). El RNC es `EMISOR_RNC` si está configurado |
| `GET/POST /configuracion/certificado` | Estado del `.p12` con que se firma; carga validada de uno nuevo (ver AUTH.md) |
| `GET /configuracion/secuencias`, `GET/PUT/DELETE /configuracion/secuencias/{tipo}` | Rango e-NCF por tipo (31…47) **y ambiente DGII** (`?ambiente=test\|qa\|production`; por defecto el activo): siguiente e-NCF = mayor entre `desde`, `proximo` y el último usado en `facturacion.facturas` del mismo ambiente + 1; usados, disponibles, consumo y avisos (vencida, agotada, por vencer ≤ 30 días, por agotarse ≤ 10 o ≥ 90 %) |
| `GET /configuracion/ambiente`, `PUT /configuracion/ambiente` | Ambiente DGII activo (TesteCF/CerteCF/eCF) y su cambio: `{ambiente, confirmacion}`. Solo administradores (403); 409 con facturas en contingencia o lotes abiertos; a producción, 422 sin `confirmacion` y 409 sin certificado vigente del RNC. Devuelve origen (`configuracion`/`variables`), quién y cuándo, bloqueos, certificado, secuencias y facturas por ambiente y los últimos cambios (auditoría). Ver docs/AMBIENTES.md |
| `GET/POST /configuracion/vendedores`, `PUT/DELETE /configuracion/vendedores/{id}` | Vendedores (la factura guarda `facturacion.facturas.vendedor_id`, columna de facturas_api). No se borra uno con facturas: se desactiva |
| `GET/POST /configuracion/usuarios`, `POST /configuracion/usuarios/{id}/desactivar\|activar` | Usuarios de Supabase Auth por la API admin de GoTrue (`SUPABASE_SERVICE_ROLE_KEY`): los miembros de la empresa del request, con su `rol_empresa`; crear/invitar recibe `rol` y agrega a la empresa |
| `PUT /configuracion/usuarios/{id}/rol`, `DELETE /configuracion/usuarios/{id}/empresa` | Rol del miembro en la empresa del request; quitarlo de ella (409 si es su única empresa) |
| `GET /empresas`, `POST /empresas`, `PUT /empresas/actual`, `GET /empresas/consulta-rnc/{rnc}` | Mis empresas (multiempresa, docs/MULTIEMPRESA.md): las del usuario con su rol; agregar una (RNC + datos fiscales, admin, 409 si el RNC ya está o la base no puede aislar empresas); recordar la elegida; verificar un RNC (registro local + directorio DGII) |
| `GET/PUT /configuracion/inventario` | Método de valuación (preferencia; el costo sigue siendo promedio ponderado) |
| `GET /configuracion/envio`, `PUT /configuracion/envio/correo\|whatsapp`, `DELETE /configuracion/envio/whatsapp`, `POST …/prueba` | SMTP y WhatsApp Cloud API (secretos cifrados con `CERTIFICADOS_CLAVE`) |
| `GET /auth/verificar` | 204/401: sesión válida de un usuario con acceso a Kontara (lo consulta nginx con `auth_request`; la cookie solo vale en GET de descargas); 403 si no es miembro de la empresa de `X-Kontara-Empresa`, y en el 204 la empresa verificada |
| `POST /configuracion/usuarios/{id}/acceso` | Da acceso a Kontara (`app_metadata.kontara`) a un usuario que ya existía en Supabase Auth |
| `GET /estado-servicios` | `/health` de cada servicio por la red interna (2,5 s cada uno), base de datos y Supabase Auth |

El e-NCF lo asigna facturas_api al crear la factura (`POST /facturas` con
`asignar_encf: true` o sin `encf`): toma el siguiente de esta secuencia con la
fila bloqueada (`SELECT … FOR UPDATE`), así dos cajas a la vez nunca reciben el
mismo, y responde 409 si la secuencia está desactivada, vencida o agotada. Un
e-NCF que mande el cliente se acepta si ninguna otra factura del emisor lo
tiene (índice único `(rnc_emisor, encf)`). Ver
`services/facturas_api/app/secuencias_encf.py`. `GET /auth/verificar` responde
503 sin `SUPABASE_JWT_SECRET`.

## Soluciones (fase 6): POS, portal de clientes, planes y Kontara Ventas

Rutas en `app/soluciones.py`, consultas en `app/soluciones_repository.py`,
reglas puras en `app/soluciones_calculos.py`; esquema `soluciones`
(`DDL_SOLUCIONES` = `db/init/016_soluciones.sql`). Contrato:
`frontend/src/lib/solucionesApi.ts`.

- **Planes**: `GET /suscripcion` (plan, `estado` prueba/activa/prueba_vencida
  y `dias_restantes`; lo lee la tarjeta de prueba de los menús laterales) y
  `GET /suscripcion/consumo`. Los **usuarios** del consumo son los usuarios
  activos de Supabase Auth, contados con la API admin de GoTrue
  (`app/gotrue_admin.py`) cuando erp-api tiene `SUPABASE_SERVICE_ROLE_KEY`;
  sin ella llegan `null` y Planes muestra «—».
- **Portal de clientes**: `/portal/*` (crear, revocar enlaces y datos
  bancarios) pide sesión como todo `/api-erp`; `/portal/publico/<token>/*`
  (lo abre el cliente desde `/p/<token>` del frontend) es **público**: nginx
  tiene una `location /api-erp/portal/publico/` sin `auth_request` y, con
  `REQUIRE_AUTH=true`, erp-api también la deja pasar (`main.py`). Lo protege
  el token del enlace (solo se guarda su hash, vence y se revoca).
- **Canales de Kontara Ventas y del portal** (`app/soluciones_canales.py`,
  `canales_de_envio`): primero el SMTP y el WhatsApp Cloud API guardados en
  Configuración → WhatsApp y correo (`configuracion.preferencias`, secretos
  cifrados, ver `app/configuracion_envio.py`); si un canal no está completo
  allí, las variables `SMTP_HOST`, `SMTP_PORT`, `SMTP_USUARIO`, `SMTP_CLAVE`,
  `SMTP_REMITENTE`, `WHATSAPP_TOKEN` y `WHATSAPP_PHONE_NUMBER_ID`. Sin canal
  los envíos quedan `pendiente_canal`.
- **Tarea diaria** de Kontara Ventas: `SOLUCIONES_TAREA_ACTIVA` (true por
  defecto) y `SOLUCIONES_TAREA_INTERVALO_HORAS` (24). El PDF de las facturas
  lo pide a `FACTURAS_API_URL` (por defecto `http://facturas-api:8000`).
- **POS**: sesiones de caja y ventas; cada venta es un E32 (o E31 con RNC)
  emitido por facturas-api con el emisor, el siguiente e-NCF y el vencimiento
  de la secuencia de Configuración.

## Pagos con tarjeta en línea (fase 7)

Rutas en `app/pagos.py` (repositorio `app/pagos_repository.py`, pasarelas y
firma en `app/pagos_proveedores.py`); tablas `soluciones.pagos_en_linea` y
`soluciones.pagos_en_linea_eventos` (`DDL_PAGOS` = `db/init/019_pagos_en_linea.sql`).
Contrato: `frontend/src/lib/pagosApi.ts`. Detalle, campos de Azul y fuentes:
[`PAGOS.md`](PAGOS.md).

| Método y ruta | Qué hace |
|---|---|
| `GET/PUT /configuracion/pagos-en-linea` | Configuración → Pagos en línea (guardar: solo administradores, 403). El AuthKey nunca se devuelve |
| `GET /pagos-en-linea?factura_id=&estado=` · `GET /pagos-en-linea/{id}/eventos` | Intentos de pago con su estado, AuthorizationCode, RRN, cobro e incidencia (Cobros) |
| `POST /portal/publico/{token}/pagos` | **Público**: crea el intento y devuelve el formulario firmado para la página del banco |
| `GET\|POST /portal/publico/pagos/{ref}/retorno/{aprobado\|declinado\|cancelado}` | **Público**: retorno del banco; valida la firma, registra el cobro una sola vez y redirige a `/p/pago/{ref}` |
| `GET /portal/publico/pagos/{ref}` | **Público**: estado del intento para la página del resultado |
| `POST /portal/publico/pagos/{ref}/simulado` | **Público**: página de pago del proveedor simulado (nunca en producción) |

`GET /portal/publico/{token}` trae además `pago_en_linea` (null si no se
ofrece). El cobro de un pago aprobado se registra con
`repository.registrar_cobro_en` (mismas validaciones y candado que
`POST /cobros`) y su asiento con `contabilidad_repository.contabilizar_documento`.

## Despliegue

Servicio `erp-api` en `docker-compose.dokploy.yml` (puerto de host 8019,
redes `facturacion` + `supabase`, mismo `DATABASE_URL` que los demás). El
frontend lo llama por el proxy de nginx `/api-erp/` (`VITE_ERP_API_URL`);
igual que `inventario-api`, no necesita Domain propio. Ver
[`DOKPLOY.md`](DOKPLOY.md).
