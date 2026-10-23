# erp-api: ventas (contactos, cotizaciones, cobros) e impuestos

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
  `inventario.productos` (Kardex). Nunca los modifica.
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

## Despliegue

Servicio `erp-api` en `docker-compose.dokploy.yml` (puerto de host 8019,
redes `facturacion` + `supabase`, mismo `DATABASE_URL` que los demás). El
frontend lo llama por el proxy de nginx `/api-erp/` (`VITE_ERP_API_URL`);
igual que `inventario-api`, no necesita Domain propio. Ver
[`DOKPLOY.md`](DOKPLOY.md).
