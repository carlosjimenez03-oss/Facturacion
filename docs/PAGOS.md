# Pagos con tarjeta en línea (fase 7)

El cliente abre su enlace del **portal de clientes** (`/p/<token>`), pulsa
**«Pagar con tarjeta»** en una factura con saldo pendiente y paga en la
**Página de Pagos de Azul** (Banco Popular Dominicano). Al volver, erp-api
valida la respuesta firmada, registra **un** cobro en esa factura (método
`tarjeta`, el mismo camino que un cobro manual) con su asiento contable y lo
lleva a la página del resultado (`/p/pago/<referencia>`).

**Kontara nunca ve, recibe ni guarda los datos de la tarjeta**: el navegador
del cliente publica un formulario firmado directamente en la página de Azul y
Azul lo devuelve con el resultado.

Código: `services/erp_api/app/pagos_proveedores.py` (firma y pasarelas,
funciones puras), `pagos_repository.py` (intentos, retornos, cobro),
`pagos.py` (rutas), `pagos_schemas.py`; contrato del frontend
`frontend/src/lib/pagosApi.ts`. Tablas `soluciones.pagos_en_linea` y
`soluciones.pagos_en_linea_eventos` (`migraciones.DDL_PAGOS` =
`db/init/018_pagos_en_linea.sql`).

## Flujo

```
Portal (/p/<token>)                 erp-api                                   Azul
  «Pagar con tarjeta» ── POST /portal/publico/<token>/pagos ─►  intento 'pendiente'
                     ◄── formulario firmado (AuthHash) ──────   (orden, monto, ITBIS, vence)
  el navegador publica el formulario ───────────────────────────────────────►  Página de Pagos
                                                                              (el cliente escribe su tarjeta)
  ◄──────────── 302 a ApprovedUrl / DeclinedUrl?OrderNumber=…&AuthHash=… ─────
  GET /portal/publico/pagos/<ref>/retorno/aprobado ──►  1. valida AuthHash (tiempo constante)
                                                       2. orden y monto = los del intento
                                                       3. FOR UPDATE del intento: aprobado → cobro
                                                          (repository.registrar_cobro_en) en la
                                                          misma transacción; repetido → nada
                                                       4. asiento del cobro (Banco contra CxC)
  ◄── 303 /p/pago/<ref> (resultado) ──────────────────
```

- **Estados** del intento: `pendiente` → `aprobado` / `declinado` /
  `cancelado` / `expirado` (`PAGOS_VIGENCIA_MINUTOS`, 30 por defecto; se
  marca al leer). Un intento nuevo de la misma factura cancela los
  pendientes (el cliente volvió atrás desde Azul).
- **Un aprobado firmado gana siempre**: aunque el intento hubiera vencido o
  sido reemplazado, el banco ya cobró, así que se registra el cobro. Si la
  factura ya no tiene saldo (se pagó por otro lado), el intento queda
  `aprobado` **sin cobro** y con `incidencia` («Revisar» en Cobros): hay que
  reembolsar o aplicarlo a otra factura.
- **Idempotencia**: el retorno se aplica con la fila bloqueada (`FOR
  UPDATE`) y el cobro se inserta en la misma transacción (índice único
  `pagos_en_linea_cobro_idx`); recargas, «atrás» y retornos simultáneos
  nunca registran dos cobros (prueba con 6 retornos a la vez). Si la cuenta
  bancaria configurada ya no es válida, el cobro se registra sin ella y
  queda la incidencia.
- **Rechazos** (no cambian el estado, quedan en la bitácora
  `pagos_en_linea_eventos` y en el log como `WARNING`): firma inválida o
  ausente (`?aviso=firma`), número de orden o monto distintos a los del
  intento (`?aviso=datos`; si venía aprobado, el intento queda con
  incidencia), respuesta del simulado en producción.
- **CancelUrl** llega sin parámetros (ni firma, así lo define Azul): solo
  cancela un intento `pendiente`; nunca toca uno aprobado ni declinado. La
  referencia del intento (32 bytes al azar) va en la ruta; el token del
  portal **no** viaja a Azul.
- **Monto**: por defecto todo el saldo; un monto menor solo con «Permitir
  pagos parciales». El ITBIS enviado es la parte proporcional del
  `TotalITBIS` de la factura (`ITBIS × monto / MontoTotal`).
- **Contabilidad**: el cobro es un cobro normal (`ventas.cobros`, método
  `tarjeta`, referencia `Azul · Aut. <AuthorizationCode> · RRN <RRN>`, la
  cuenta bancaria de Configuración si se eligió). Su asiento (débito la
  cuenta bancaria o `banco`, crédito `cxc_clientes`) se crea en el momento
  con `contabilidad_repository.contabilizar_documento` (mismo armado y
  huella que «Generar asientos», que después lo ve «sin cambios»). Si no se
  puede (falta una cuenta por defecto), lo hará «Generar asientos».

## Configuración → Pagos en línea

Solo un **administrador** (`app_metadata.kontara_admin` o
`KONTARA_ADMIN_EMAILS`) puede guardar (403 si no; 401 sin sesión cuando el
inicio de sesión está configurado). `GET/PUT /configuracion/pagos-en-linea`.

| Campo | |
|---|---|
| Activo | El portal muestra «Pagar con tarjeta» solo si está activo, la configuración está completa **y** el enlace del cliente incluye el método «Tarjeta» |
| Proveedor | `azul` o `simulado` (este último no existe en producción: 422) |
| Ambiente | `pruebas` → `https://pruebas.azul.com.do/PaymentPage/`; `produccion` → `https://pagos.azul.com.do/PaymentPage/Default.aspx` |
| MerchantId, MerchantName, MerchantType | Los entrega Azul. MerchantId solo dígitos; los textos sin `"` ni `\` |
| AuthKey | **Secreto**. Se guarda cifrado con `CERTIFICADOS_CLAVE` (scrypt + Fernet, formato `v2:`, igual que la contraseña SMTP) o llega por `AZUL_AUTH_KEY` (manda sobre la guardada). Nunca se devuelve: la pantalla solo dice «configurada» y de dónde |
| Cuenta donde Azul deposita | Cuenta de Bancos (en RD$) del cobro; vacía = asiento a la cuenta por defecto `banco` |
| Permitir pagos parciales | Por defecto no (se paga todo el saldo) |

Sin nada configurado y fuera de producción el proveedor por defecto es el
**simulado**: una «página del banco» que sirve el mismo erp-api
(`POST /portal/publico/pagos/<ref>/simulado`), comprueba el AuthHash del
formulario como Azul, no pide tarjeta y devuelve respuestas firmadas como
las de Azul con una llave pública fija. Lo usan las pruebas y el e2e.

### Variables de entorno (erp-api)

| Variable | |
|---|---|
| `PAGOS_URL_PUBLICA` | Origen público del frontend, p. ej. `https://front.acentosdeco.lat`. De él salen `ApprovedUrl`/`DeclinedUrl`/`CancelUrl` (`<origen>/api-erp/portal/publico/pagos/<ref>/retorno/…`) y el resultado (`<origen>/p/pago/<ref>`). **Obligatoria en producción** (sin ella los pagos no se pueden activar). Fuera de producción, si falta, se deduce del request (`X-Forwarded-Host`/`Host`) |
| `AZUL_AUTH_KEY` | AuthKey de Azul (alternativa a guardarla cifrada en Configuración) |
| `PAGOS_VIGENCIA_MINUTOS` | Minutos que vive un intento sin respuesta (30) |
| `PAGOS_RUTA_API` | Prefijo de erp-api en nginx (`/api-erp`) |

## Qué pedir a Azul / Banco Popular

1. **Afiliación de comercio electrónico con Página de Pagos (Payment Page)**
   en pesos dominicanos (CurrencyCode `$`).
2. Los datos de **pruebas** y, después de la certificación, los de
   **producción** (son distintos, y también lo son sus URL): `MerchantId`,
   `MerchantName` (lo que ve el tarjetahabiente), `MerchantType` y el
   **AuthKey** de cada ambiente.
3. Que registren como URL de retorno del comercio el dominio
   `PAGOS_URL_PUBLICA` (Kontara envía `ApprovedUrl`, `DeclinedUrl` y
   `CancelUrl` en cada formulario, bajo
   `<dominio>/api-erp/portal/publico/pagos/`).
4. Confirmar el **formato del `Amount` de la respuesta** (Kontara acepta
   centavos sin punto, como el requerimiento, y también con punto decimal)
   y si su página muestra la pantalla de resultado (`ShowTransactionResult`;
   Kontara no lo envía, queda el valor por defecto de Azul).
5. Hacer las **pruebas de certificación** que exige Azul en su ambiente de
   pruebas antes de pasar a producción (soporte:
   `solucionesecommerce@azul.com.do`, ver el documento).

## Campos y firma (Azul)

Fuente principal: **«E-commerce AZUL · Evoluciona a un mundo de
posibilidades en la web» — Página de Pagos, versión 2023-08 (español)**,
`https://dev.azul.com.do/Pages/developer/documentos/plugins/Documento-E-Commerce-AZUL-Pagina-Pagos-(Espanol)-2023-08.pdf`
(portal de desarrolladores: `https://dev.azul.com.do/Pages/developer/pages/lib/index.aspx`).
Secciones «Invocar la Página de Pago», «Parámetros de Entrada», «Valores de
Retorno», «Manejo de la Autenticación (AuthHash)» y «Listado de códigos».

Formulario (POST desde el navegador, campos ocultos):

| Campo | Valor que envía Kontara |
|---|---|
| `MerchantId`, `MerchantName`, `MerchantType` | Configuración |
| `CurrencyCode` | `$` |
| `OrderNumber` | 12 dígitos al azar, único (`soluciones.pagos_en_linea.orden`) |
| `Amount` | Total en centavos, sin punto ni coma (`1180.00` → `118000`) |
| `ITBIS` | Igual formato (`0` → `000`, como el ejemplo de exento) |
| `ApprovedUrl`, `DeclinedUrl`, `CancelUrl` | `…/portal/publico/pagos/<ref>/retorno/aprobado` · `declinado` · `cancelado` |
| `UseCustomField1/Label/Value` | `1`, `Factura`, el e-NCF (se muestra en la página de Azul) |
| `UseCustomField2/Label/Value` | `0`, vacío, vacío |
| `AuthHash` | Ver abajo |
| `Locale` | `ES` (no va en el hash) |

**AuthHash del requerimiento** = HMAC-SHA512, llave = AuthKey, sobre la
concatenación, **en este orden**, de:

`MerchantId + MerchantName + MerchantType + CurrencyCode + OrderNumber + Amount + ITBIS + ApprovedUrl + DeclinedUrl + CancelUrl + UseCustomField1 + CustomField1Label + CustomField1Value + UseCustomField2 + CustomField2Label + CustomField2Value + AuthKey`

(el AuthKey va al final del texto y también es la llave; nunca viaja en el
POST). El texto se codifica en **UTF-16LE** («Unicode»: `Encoding.Unicode`
en el ejemplo C# del documento, `mb_convert_encoding(..., 'UTF-16LE')` +
`hash_hmac('sha512', ..., $authKey)` en el de PHP) y el resultado va en
hexadecimal minúscula. El documento dice que el requerimiento acepta UTF-8
o Unicode; Kontara usa Unicode en los dos sentidos.

**Respuesta** (querystring de `ApprovedUrl`/`DeclinedUrl`): `OrderNumber`,
`Amount`, `AuthorizationCode`, `DateTime`, `ResponseCode`, `IsoCode`,
`ResponseMessage`, `ErrorDescription`, `RRN`, `AzulOrderId`, `AuthHash`
(y los de DataVault/CardNumber, que Kontara **no** guarda). Su AuthHash es
el mismo HMAC sobre

`OrderNumber + Amount + AuthorizationCode + DateTime + ResponseCode + IsoCode + ResponseMessage + ErrorDescription + RRN + AuthKey`

Se compara con `hmac.compare_digest`. **Aprobada** = `IsoCode` `00` (y
`ResponseCode` `ISO8583`); cualquier otro resultado firmado es declinado.

**Vector de prueba**: Azul **no publica** uno. Los de
`services/erp_api/tests/test_pagos_proveedores.py` usan el formulario de
ejemplo del documento (MerchantId `99999999999`, «Comercio prueba»,
`ECommerce`, `$`, orden `1234`, `15000`, `2057` y sus tres URL) con una
llave inventada (`kontara-clave-de-prueba-no-real`), y el resultado
esperado se calculó aparte con `iconv -t utf-16le | openssl dgst -sha512
-hmac` — es decir, verifican que el código hace lo que dice el documento,
no que Azul lo acepte. **La confirmación real es la primera transacción en
el ambiente de pruebas de Azul.**

Referencias de apoyo (no oficiales, usadas solo para contrastar):
el plugin de WooCommerce `lupena/wc-azul-payment-gateway`
(`https://github.com/lupena/wc-azul-payment-gateway`, mismo orden de campos
del requerimiento y de la respuesta; usa SHA-512 plano con la llave
concatenada, una versión anterior del esquema) y la gema
`iCueto/Azul-Pay-Button` (`https://github.com/iCueto/Azul-Pay-Button`). El plugin usa también las
mismas URL de pruebas y producción
(`https://pruebas.azul.com.do/PaymentPage/default.aspx`,
`https://pagos.azul.com.do/PaymentPage/default.aspx`).

## Seguridad

- Las rutas públicas están **todas** bajo `/portal/publico/` (la única
  excepción de `ERP_REQUIRE_AUTH` y la única `location` de nginx sin
  `auth_request`; no se agregó ninguna excepción nueva). Un test
  (`test_pagos.py`) fija la lista exacta de rutas públicas. Configuración y
  el listado de pagos piden sesión.
- El inicio de pago exige un enlace vigente (ni vencido ni revocado), con el
  método «Tarjeta», y una factura de **ese** cliente (RNC fijado al crear el
  enlace), aceptada por la DGII, a crédito y con saldo; si no, el mismo 404.
- Límites: nginx `kontara_pagos` (10 inicios por minuto por IP, ráfagas de
  5, 429) y erp-api 20 intentos por enlace y hora (429).
- `Referrer-Policy: no-referrer` en `/p/`, en `/api-erp/portal/publico/` y
  en la página simulada (que además lleva `Content-Security-Policy` sin
  scripts).
- El simulado nunca se acepta con `APP_ENV=production`: ni se puede
  elegir, ni se ofrece en el portal, ni se acepta un retorno de un intento
  simulado.

## Limitaciones

- **Sin confirmación servidor a servidor**: la Página de Pagos devuelve el
  resultado por el navegador del cliente. Si el cliente cierra la pestaña
  después de pagar y antes de volver, el intento queda `pendiente`/`expirado`
  aunque Azul haya cobrado; la empresa lo ve en Cobros → Pagos en línea y
  debe conciliarlo con el reporte de Azul (registrar el cobro a mano). Azul
  tiene un `ResponsePostUrl` en integraciones de terceros, pero no está en
  el documento oficial de la Página de Pagos: no se implementó.
- **Sitio alterno**: Kontara publica en el sitio principal
  (`pagos.azul.com.do`); el alterno de contingencia
  (`contpagos.azul.com.do`) está en el código pero no hay un reintento
  automático si el principal no carga.
- **Sin reembolsos ni anulaciones** desde Kontara: se hacen en el portal de
  Azul; anular el cobro en Kontara no reversa el cargo.
- Las incidencias (aprobado sin cobro, monto distinto) no avisan por
  correo: se ven en Cobros.
- Solo Azul y el simulado; CardNet u otra pasarela es una clase nueva en
  `pagos_proveedores.PROVEEDORES` (`validar_config`, `formulario`,
  `leer_retorno`) y su clave en el `CHECK` de `proveedor`.
