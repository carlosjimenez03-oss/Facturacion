# erp-api: ventas (contactos, cotizaciones, cobros), impuestos y compras

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
| `GET /cobros?factura_id=` · `POST /cobros` | Registra un cobro: 422 si la factura no es a crédito/no fue enviada o si el monto es ≤ 0 o supera lo pendiente. Fecha por defecto = hoy |
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
  46 en estado `aceptado`, por `FechaEmision`), `facturacion.envios_dgii`
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
     activos) no es gasto, así que se pide la depreciación como ajuste. Las
     compras sin clasificar se excluyen, con una advertencia.
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

## Configuración y plataforma (fase 6)

Rutas en `app/configuracion.py` (prefijo `/configuracion`) y
`app/plataforma.py`; esquema `configuracion` (`app/migraciones_configuracion.py`
= `db/init/013_configuracion.sql`). Contrato: `frontend/src/lib/configuracionApi.ts`.

| Método y ruta | Qué hace |
|---|---|
| `GET/PUT /configuracion/empresa` | Datos de la empresa (una fila). El RNC es `EMISOR_RNC` si está configurado |
| `GET/POST /configuracion/certificado` | Estado del `.p12` con que se firma; carga validada de uno nuevo (ver AUTH.md) |
| `GET /configuracion/secuencias`, `GET/PUT/DELETE /configuracion/secuencias/{tipo}` | Rango e-NCF por tipo (31…47): siguiente e-NCF = mayor entre `desde`, `proximo` y el último usado en `facturacion.facturas` + 1; usados, disponibles, consumo y avisos (vencida, agotada, por vencer ≤ 30 días, por agotarse ≤ 10 o ≥ 90 %) |
| `GET/POST /configuracion/vendedores`, `PUT/DELETE /configuracion/vendedores/{id}` | Vendedores (la factura guarda `facturacion.facturas.vendedor_id`, columna de facturas_api). No se borra uno con facturas: se desactiva |
| `GET/POST /configuracion/usuarios`, `POST /configuracion/usuarios/{id}/desactivar\|activar` | Usuarios de Supabase Auth por la API admin de GoTrue (`SUPABASE_SERVICE_ROLE_KEY`) |
| `GET/PUT /configuracion/inventario` | Método de valuación (preferencia; el costo sigue siendo promedio ponderado) |
| `GET /configuracion/envio`, `PUT /configuracion/envio/correo\|whatsapp`, `DELETE /configuracion/envio/whatsapp`, `POST …/prueba` | SMTP y WhatsApp Cloud API (secretos cifrados con `CERTIFICADOS_CLAVE`) |
| `GET /auth/verificar` | 204/401: sesión válida (lo consulta nginx con `auth_request`) |
| `GET /estado-servicios` | `/health` de cada servicio por la red interna (2,5 s cada uno), base de datos y Supabase Auth |

El e-NCF no se reserva aquí: facturas_api sigue recibiendo el que manda el
formulario, que lo propone a partir de esta información.

## Despliegue

Servicio `erp-api` en `docker-compose.dokploy.yml` (puerto de host 8019,
redes `facturacion` + `supabase`, mismo `DATABASE_URL` que los demás). El
frontend lo llama por el proxy de nginx `/api-erp/` (`VITE_ERP_API_URL`);
igual que `inventario-api`, no necesita Domain propio. Ver
[`DOKPLOY.md`](DOKPLOY.md).
