# Modo contingencia (e-CF sin conexión con la DGII)

Qué hace el sistema cuando el emisor no puede transmitir sus e-CF a la DGII,
qué reglas de la DGII aplica y de dónde salen.

## Reglas de la DGII

Base normativa: Ley 32-23 de Facturación Electrónica y su Reglamento, el
**Decreto 587-24, capítulo IX, artículos 40 a 43** ("contingencia":
situación excepcional que impide el desarrollo normal del ciclo de la
facturación electrónica), desarrollado en el **Instructivo de Contingencia de
Facturación Electrónica** de la DGII.

| Situación | Qué exige la DGII | Plazo | Cómo lo aplica el sistema |
|---|---|---|---|
| **Falta de conectividad** (internet o los servicios de la DGII no responden), pero el sistema **sí puede generar y firmar** el e-CF | Generar el e-CF fuera de línea con su e-NCF, entregar al comprador la representación impresa con la leyenda de contingencia y enviarlo a la DGII al restablecerse la conexión. **No** se pasa a serie B. | Envío "en un plazo no mayor de setenta y dos (72) horas" | Estado `contingencia`, firma local, envío automático por lotes, leyenda en la RI y el ticket, aviso del plazo de 72 h en el Centro de sincronización |
| **Falla de los sistemas de la DGII** | Almacenar los e-CF y transmitirlos cuando se restablezca la comunicación | Al restablecerse | Igual que la fila anterior (es la misma situación vista desde el emisor) |
| **Imposibilidad técnica de emitir e-CF** (el sistema del emisor no puede generarlos) | Notificar la contingencia en la Oficina Virtual (Facturación Electrónica → Contingencia FE, total o parcial), emitir **comprobantes no electrónicos (serie B) autorizados**, y luego emitir los e-CF que los referencian (código de modificación 4, "Reemplazo NCF emitido en contingencia") enviándolos solo a la DGII | Máximo **15 días** calendario de contingencia; e-CF de reemplazo dentro de los **30 días** siguientes a la salida | Registro de facturas en papel en el Centro de sincronización, con su e-CF de reemplazo; alimentan el 607 (si la empresa emite serie B) y la contabilidad |

**Leyenda de la representación impresa** (Instructivo de Contingencia de la
DGII), que el sistema imprime recuadrada bajo el encabezado de la RI y en el
ticket del POS de todo e-CF emitido en contingencia:

> e-CF emitido en modalidad de Contingencia, el cual podrá ser consultado
> para su validez fiscal, a partir de las setenta y dos (72) horas.

El comprador no puede sustentar costos, gastos ni crédito fiscal con ese
e-CF hasta que la DGII lo tenga "Aceptado" o "Aceptado Condicional".

**`IdDoc.IndicadorEnvioDiferido` no se usa en contingencia.** En los XSD
oficiales (p. ej. `services/facturas_api/xsd/eCF_31.xsd`,
`IndicadorEnvioDiferidoType`) su único valor es `1` = "Envío diferido
autorizado": es la modalidad de **envío diferido** que la DGII autoriza a
ciertos modelos de operación, distinta de la contingencia (las fuentes
advierten expresamente no confundirlas). Además el XML de un e-CF emitido en
contingencia ya se le entregó al comprador: agregarle un campo al enviarlo
cambiaría la firma y con ella el código de seguridad y el QR de la RI.

**Código de seguridad, QR y fecha de firma.** Salen del XML firmado
(`FechaHoraFirma` + los 6 primeros caracteres del hash de `SignatureValue`,
sección 18 del Informe Técnico del e-CF). Por eso el XML que se envía a la
DGII días después debe ser **byte a byte** el que se firmó y se imprimió: el
sistema firma una vez, guarda ese XML (`xml_firmado`, `firmado_en`) y lo
envía tal cual.

### Fuentes

- DGII, *Instructivo de Contingencia de Facturación Electrónica* (cita el
  art. 40 del Decreto 587-24; plazo de 72 h, leyenda de la RI y límite de 15
  días): https://dgii.gov.do/cicloContribuyente/facturacion/comprobantesFiscalesElectronicosE-CF/Documentacin%20sobre%20eCF/Instructivos%20sobre%20Facturaci%C3%B3n%20Electr%C3%B3nica/Instructivo%20de%20Contingencia%20de%20FE.pdf
- DGII, *Instructivo Contingencia FE* (registro de entrada y salida de la
  contingencia en la Oficina Virtual):
  https://dgii.gov.do/cicloContribuyente/facturacion/comprobantesFiscalesElectronicosE-CF/Documentacin%20sobre%20eCF/Instructivos%20sobre%20Facturaci%C3%B3n%20Electr%C3%B3nica/Instructivo-Contingencia-FE.pdf
- Siempre al Día (Alegra), *Contingencia en la facturación electrónica
  República Dominicana: ¿Cuándo puedes usar comprobantes B y cómo
  regularizar?* (27-08-2026; tabla de plazos 72 h / 15 días / 30 días,
  envío diferido ≠ contingencia):
  https://siemprealdia.co/republica-dominicana/impuestos/contingencia-en-la-facturacion-electronica/
- Siempre al Día (Alegra), *¿Cómo manejar una contingencia en la facturación
  electrónica en República Dominicana?* (16-04-2026; arts. 40-43 del Decreto
  587-24, crédito fiscal del receptor):
  https://siemprealdia.co/republica-dominicana/impuestos/manejar-una-contingencia-en-la-facturacion-electronica/
- XSD oficiales del e-CF en `services/facturas_api/xsd/` (`IndicadorEnvioDiferido`,
  `CodigoModificacion` 4).

Nota: los PDF de dgii.gov.do no se pudieron descargar desde el entorno donde
se implementó esto (HTTP 403); el texto de la leyenda y el plazo de 72 horas
se tomaron del extracto del instructivo oficial que muestran los buscadores y
coinciden con las fuentes secundarias. Conviene confirmarlos contra el PDF
vigente antes de certificar.

## Cómo funciona

### facturas-api (`services/facturas_api/app/contingencia.py`, `envio.py`)

Estados de la factura:

```
generado/firmado/error (sin trackId) --envío sin conexión--> contingencia
contingencia --envío en lote, trackId--> enviado --> aceptado | rechazado
contingencia --la DGII lo rechaza al recibirlo (4xx / 502)--> error
```

- **Firma.** `POST /facturas/{id}/enviar` firma el XML con el mismo
  `firmar_xml` de `recepcion` (o reutiliza la firma guardada, p. ej. la
  vista previa de `POST /firmar` ya impresa), la guarda **antes** de enviar
  y se la manda a `recepcion` con `firmado=true`, que la envía tal cual.
  Regenerar el XML borra la firma; `POST /firmar` no vuelve a firmar una
  factura enviada o en contingencia.
- **Qué es "sin conexión".** Error de conexión o timeout hacia `recepcion`,
  o `recepcion` responde **503/504** (la DGII no contesta, o contesta 5xx:
  `recepcion` traduce los errores de red y los 5xx de la DGII a 503). Un
  **502** (la DGII rechazó), un 4xx o un 500 (certificado/configuración) no
  son contingencia: la factura queda en `error`.
- **Respuesta.** Sin conexión: `200` con `estado = "contingencia"`,
  `contingencia = true` y `mensaje = "Emitida en contingencia — se enviará
  automáticamente…"`. La RI ya lleva la leyenda.
- **Estado compartido** `facturacion.contingencia` (una fila): lo activa
  cualquier envío o sonda que no llega a la DGII y lo desactiva cualquiera
  que sí llega. Mientras está activo, `enviar` hace primero una **sonda
  corta** (`GET {recepcion}/disponibilidad`, `TIMEOUT_SONDA_SEGUNDOS`, 5 s) y,
  si la DGII sigue sin responder, deja la factura en contingencia sin esperar
  el timeout del envío (`TIMEOUT_ENVIO_SEGUNDOS`, 60 s).
- **Envío por lotes.** En cada vuelta de la tarea de fondo
  (`SINCRONIZAR_ESTADO_CADA_SEGUNDOS`, 120 s) y con `POST
  /contingencia/reintentar` ("Reintentar conexión ahora"): sonda; si
  responde, retoma el lote abierto más antiguo o arma uno nuevo
  (`A-0001`, `A-0002`…) con las `CONTINGENCIA_LOTE_TAMANO` (20) facturas en
  contingencia más antiguas (por `firmado_en`), las envía en orden y sigue el
  flujo normal de cada una: trackId, salida de inventario, 606 de los tipos
  41/43/47 y consulta del resultado. Si la conexión se cae a mitad, el lote
  queda `interrumpido` y se retoma en la siguiente vuelta; al terminar sin
  fallas se sale de contingencia. Un advisory lock de Postgres evita que dos
  réplicas envíen a la vez. Un envío exitoso desde `enviar` con pendientes
  lanza el envío por lotes en segundo plano.
- **Endpoints.** `GET /contingencia` (estado, pendientes, `vence_en` = la
  pendiente más antigua + 72 h, últimos lotes), `GET /contingencia/lotes`,
  `POST /contingencia/reintentar`.
- **Esquema** (`app/migraciones.py`, `DDL_CONTINGENCIA`, idempotente al
  arrancar; mismo DDL en `db/init/017_contingencia.sql`): columnas
  `firmado_en`, `emitida_en_contingencia`, `contingencia_en`, `lote_id`,
  `intentos_envio`, `ultimo_intento_envio` de `facturacion.facturas`, y las
  tablas `facturacion.contingencia` y `facturacion.lotes_contingencia`.
  `facturacion.facturas.estado` es `text` sin CHECK: el estado nuevo no
  requiere tocar restricciones.

### erp-api

- `GET /sincronizacion/resumen` lee el estado real (`contingencia.fuente =
  "facturas_api"`): activa/desde/errores/último error, pendientes = facturas
  en `contingencia`, plazo de 72 h y los lotes (abiertos, con resultados
  pendientes o del último día; `completado` con rechazos sale como
  `con_rechazos`). Si facturas-api aún no creó sus tablas, lo calcula como
  antes de la auditoría (`fuente = "calculada"`).
- Las facturas en `contingencia` cuentan en las cuentas por cobrar (la venta
  ya se hizo) y salen en los pendientes de sincronizar (canal `dgii_envio`,
  "se envía sola").
- **Facturas en papel** (`operacion.facturas_papel`, serie B): van una a una
  en el **607** (y en su TXT) solo si la empresa emite serie B
  (`EMISOR_100_ELECTRONICO=false`); el emisor 100 % electrónico las
  regulariza con su e-CF de reemplazo y el 607 lo advierte. En
  **contabilidad** generan su asiento de venta automático (origen
  `factura_papel`, idempotente); si ya hay un e-CF de reemplazo aceptado
  (`encf_reemplazo` o un e-CF con `NCFModificado` = el NCF y código 4), la
  venta la contabiliza el e-CF y el asiento de la factura en papel se anula.

### Frontend

Estado `contingencia` ("Emitida en contingencia") en la lista (con su
filtro), el detalle, el formulario, el POS y su ticket (con la leyenda); el
Centro de sincronización muestra el progreso real por lotes y "Reintentar
conexión ahora" llama a `POST /contingencia/reintentar` de facturas-api.

## Límites conocidos

- Un timeout después de que la DGII ya recibió el e-CF lo deja en
  contingencia y se reenvía; la DGII lo rechazará como secuencia ya
  utilizada (queda en `error` con ese motivo para revisarlo).
- La salida de inventario se registra al enviar a la DGII (flujo normal), no
  al emitir en contingencia.
- Las facturas en papel no entran todavía al IT-1.
- La notificación de la contingencia en la Oficina Virtual (solo exigida
  para la serie B) es manual.
