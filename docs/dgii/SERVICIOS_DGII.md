# Servicios Web DGII — Referencia técnica

Extraído de **"Descripción Técnica Servicios DGII"** (Gerencia de Tecnología de
la Información y Comunicaciones, DGII — Versión 1.7, Mayo 2026), documento
oficial adjuntado por el cliente. Esta es la fuente de verdad para los
contratos (URLs, métodos, formatos) de cada servicio; cualquier discrepancia
con el código debe resolverse a favor de este documento (o de una versión más
nueva del PDF oficial).

## Ambientes

| Ambiente         | Propósito | Rango de secuencias de prueba |
|------------------|-----------|--------------------------------|
| **Pre-Certificación** (`TesteCF`) | Pruebas de adecuación/integración del contribuyente. Los envíos se almacenan 60 días. | 1–10,000,000 (1–50,000,000 para FC tipo 32). Vencen 31-12-2028 (FC tipo 32 y NC tipo 34 sin vencimiento). |
| **Certificación** (`CerteCF`)     | Valida capacidades del sistema del contribuyente antes de pasar a producción. | — |
| **Producción** (`eCF`)            | Ambiente productivo; validez fiscal real. | — |

Dos dominios base según el servicio:
- `https://ecf.dgii.gov.do/{ambiente}/...` — autenticación, recepción e-CF, consultas, aprobación comercial, anulación, directorio, timbre.
- `https://fc.dgii.gov.do/{ambiente}/...` — recepción y consulta de Resumen de Factura de Consumo Electrónica (RFCE) y su timbre.

`{ambiente}` toma los valores `testecf`, `certecf` o `ecf`.

## Formatos XML (etiqueta madre obligatoria)

| Formato XML | Etiqueta madre |
|---|---|
| e-CF | `ECF` |
| Aprobación Comercial | `ACECF` |
| Acuse de Recibo | `ARECF` |
| Anulación de Secuencias e-NCF | `ANECF` |
| Resumen Factura de Consumo (< RD$250,000.00) | `RFCE` |

Nombre de archivo XML: `RNC+e-NCF.xml` (ej. `101672919E3100000001.xml`).

## Catálogo de servicios

### 1. Autenticación
Genera una sesión validando la identidad del contribuyente mediante certificado
digital sobre un archivo semilla, que se firma y se envía para recibir un
`token` (duración: 1 hora). El token se usa como header
`Authorization: Bearer {token}` en el resto de servicios.

- Base: `https://ecf.dgii.gov.do/{ambiente}/autenticacion`
- `GET /api/autenticacion/semilla` → XML `SemillaModel { valor, fecha }`
- `POST /api/autenticacion/validarsemilla` (multipart, campo `xml` = semilla firmada) → `{ token, expira, expedido }` (JSON o XML `RespuestaAutenticacion`)

### 2. Recepción de e-CF
Recibe un e-CF tentativo (XML firmado) + token; retorna `trackId` (acuse de
recibo) para luego consultar el estado. **No** recibe Facturas de Consumo
(tipo 32) con monto < RD$250,000.00 — esas van al servicio RFCE (#3).

- Base: `https://ecf.dgii.gov.do/{ambiente}/recepcion`
- `POST /api/facturaselectronicas` (multipart, campo `xml`, bearer) → `{ trackId, error, mensaje }`

### 3. Recepción de resumen factura de consumo (RFCE)
Recibe un resumen (no el e-CF completo) de Facturas de Consumo Electrónicas
con monto < RD$250,000.00. El contribuyente debe conservar igual el e-CF
completo. FC con monto ≥ RD$250,000.00 se envían completas por el servicio #2.

- Base: `https://fc.dgii.gov.do/{ambiente}/recepcionfc`
- `POST /api/recepcion/ecf` (multipart, campo `xml`, bearer) → `{ codigo, estado, mensajes[], encf, secuenciaUtilizada }`
- Estados: Aceptado / Aceptado condicional / Rechazado.
- `secuenciaUtilizada` (bool): si `true`, la secuencia rechazada **no** puede reutilizarse.

### 4. Consulta de Resumen de Factura de Consumo Electrónica (RFCE)
Consulta el estado de un RFCE ya enviado.

- Base: `https://fc.dgii.gov.do/{ambiente}/consultarfce`
- `GET /api/Consultas/Consulta?RNC_Emisor={rnc}&ENCF={encf}&Cod_Seguridad_eCF={codigo}` (bearer)
- Respuesta: `{ rnc, encf, secuenciaUtilizada, codigo, estado, mensajes[] }`
- Estados: No encontrado(0), Aceptado(1), Rechazado(2); temporalmente también Aceptado condicional.

### 5. Consulta de resultado e-CF
Consulta el estado de procesamiento de un e-CF enviado **exclusivamente** por
el servicio #2 (Recepción de e-CF), vía su `trackId`.

- Base: `https://ecf.dgii.gov.do/{ambiente}/consultaresultado`
- `GET /api/consultas/estado?trackid={trackid}` (bearer)
- Respuesta: `{ trackId, codigo, estado, rnc, eNCF, secuenciaUtilizada, fechaRecepcion, mensajes[] }`
- Estados: No encontrado(0), Aceptado(1), Rechazado(2), En Proceso(3, ~200ms promedio), Aceptado Condicional(4).

### 6. Consulta de estado e-CF
Consulta la validez/estado de un e-CF por parte de emisor o receptor (deben
estar delegados). También permite consultar los enviados vía RFCE.

- Base: `https://ecf.dgii.gov.do/{ambiente}/consultaestado` (disponible en TesteCF y eCF)
- `GET /api/consultas/estado?rncemisor={rnc}&ncfelectronico={encf}&rnccomprador={rncComprador}&codigoseguridad={codigo}` (bearer; `rncemisor`/`ncfelectronico` obligatorios, `rnccomprador`/`codigoseguridad` condicionales)
- Respuesta: `{ codigo, estado, rncEmisor, ncfElectronico, montoTotal, totalITBIS, fechaEmision, fechaFirma, rncComprador, codigoSeguridad, idExtranjero }`
- Estados: No encontrado(0), Aceptado(1), Rechazado(2); Aceptado condicional para FC < RD$250,000.

### 7. Consulta de trackId e-CF
Retorna el listado de TrackIds recibidos por DGII para un e-NCF dado (puede
haber varios si se reenvió el mismo e-NCF). Requiere delegado del emisor.

- Base: `https://ecf.dgii.gov.do/{ambiente}/consultatrackids` (TesteCF y eCF)
- `GET /api/trackids/consulta?rncemisor={rnc}&encf={encf}` (bearer)
- Respuesta: lista de `{ trackId, estado, fechaRecepcion }`
- Estados: No encontrado, Aceptado, Rechazado, Aceptado Condicional, En proceso (~200ms promedio).

### 8. Recepción de aprobación comercial
Recibe la conformidad (aprobación/rechazo) emitida por el receptor sobre un
e-CF recibido de un emisor.

- Base: `https://ecf.dgii.gov.do/{ambiente}/aprobacioncomercial`
- `POST /api/aprobacioncomercial` (multipart, campo `xml`, bearer) → `{ mensaje[], estado, codigo }`
- Estados: Aprobación comercial aprobada(1), rechazada(2).

### 9. Anulación de e-NCF (ANECF)
Anula rangos de secuencias no utilizadas. Para FC, valida que no haya sido
usada por ninguna vía (e-CF completo o RFCE) en ningún monto.

- Base: `https://ecf.dgii.gov.do/{ambiente}/anulacionrangos` (TesteCF y eCF)
- `POST /api/operaciones/anularrango` (multipart, campo `xml`, bearer) → `{ rnc, codigo, nombre, mensajes[] }`

### 10. Consulta Directorio de Servicios
Retorna los contribuyentes electrónicos autorizados y las URLs de sus
servicios de recepción, aprobación comercial y autenticación (opcional). En
Pre-Certificación retorna las URLs de prueba del servicio Emisor-Receptor.

- Base: `https://ecf.dgii.gov.do/{ambiente}/consultadirectorio` (TesteCF y eCF)
- `GET /api/consultas/listado` (bearer) → lista `{ nombre, rnc, urlRecepcion, urlAceptacion, urlOpcional }`
- `GET /api/consultas/obtenerdirectorioporrnc?RNC={rnc}` (bearer) → igual estructura, filtrado

### 11. Consulta timbre (QR) — e-CF
Responde la validez de un e-CF (enviado por el servicio #2) a partir de los
datos incluidos en el timbre/QR de su representación impresa (RI). Es una URL
pública construida por concatenación de parámetros (no requiere bearer).

- Base: `https://ecf.dgii.gov.do/{ambiente}/consultatimbre`
- Parámetros (query string): `rncemisor, rnccomprador, encf, fechaemision, montototal, fechafirma, codigoseguridad`
- QR versión 8. Salida: `{ RNC Emisor, Razón social emisor, RNC Comprador, Razón social comprador, e-NCF, Fecha de Emisión, Total ITBIS, Monto Total, Estado }`
- Estados: No fue encontrada la factura, Aceptado (incluye condicional), Rechazado.

### 12. Consulta timbre FC (QR) — RFCE
Igual al anterior pero para Resúmenes de Factura de Consumo (< RD$250,000.00).

- Base: `https://fc.dgii.gov.do/{ambiente}/consultatimbrefc`
- Parámetros: `rncemisor, encf, montototal, codigoseguridad`
- Salida: `{ RNC Emisor, Razón Social, e-NCF, Estado }`

### 13. Comunicación Emisor-Receptor (solo TesteCF)
Simula ser otro contribuyente para poder probar la interacción emisor↔receptor
en Pre-Certificación (autenticarse, recibir y enviar comprobantes/aprobaciones
como si fuera la contraparte). Documentado parcialmente en este PDF; el
endpoint completo (`EmisionComprobantes`, `Consulta de acuse de recibo`,
`RecepcioneCF`) vive en el documento separado **"Descripción Técnica
Servicios Emisores Electrónicos"** (no disponible aún — solicitar si se
necesita implementar esta simulación).

- Base: `https://ecf.dgii.gov.do/testecf/emisorreceptor`
- `GET /fe/autenticacion/api/semilla` → XML semilla (igual formato que #1)
- `POST /fe/autenticacion/api/validacioncertificado` (multipart, campo `xml`) → `{ token, expira, expedido }`

## Recomendaciones del documento oficial

1. Verificar siempre que las URLs sean correctas antes de enviar e-CF o
   aprobación comercial.
2. Comprobantes tipo 32 (Factura de Consumo): usar el servicio de recepción
   correcto según el monto (`Recepción de e-CF` vs `Recepción RFCE`).
3. Representación impresa de e-CF tipo 32: hay dos variantes de consulta
   timbre según el servicio de recepción usado (`Consulta timbre` vs
   `Consulta timbre FC`).
4. `Consulta de estado` requiere estar delegado (como emisor o receptor) antes
   de consultar.
5. Familiarizarse con las validaciones de los formatos XML de cada operación.
