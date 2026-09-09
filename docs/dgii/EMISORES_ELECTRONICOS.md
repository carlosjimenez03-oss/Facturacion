# Servicios Web "Receptor Electrónico" — Referencia técnica

Extraído de **"Descripción Técnica Servicios Emisores Electrónicos"**
(Gerencia de Tecnología de la Información y Comunicaciones, DGII — Versión
1.7, Mayo 2026), documento oficial adjuntado por el cliente. Es el documento
"hermano" de [`SERVICIOS_DGII.md`](SERVICIOS_DGII.md) (que describe los
servicios de la DGII que ESTE contribuyente consume como cliente); este
describe los servicios que ESTE contribuyente debe exponer públicamente
para poder actuar como **Receptor Electrónico** (recibir e-CF y aprobaciones
comerciales que le envíen otros contribuyentes, cuando actúa como comprador).

Fuente de verdad para los contratos (URLs, métodos, formatos); cualquier
discrepancia con el código debe resolverse a favor de este documento (o de
una versión más nueva del PDF oficial). Ver también la sección 13
("Comunicación Emisor-Receptor") de `SERVICIOS_DGII.md`, que documenta el
simulador de la DGII para probar este flujo en TesteCF.

## Propósito

Asegurar un intercambio de información seguro, homogéneo y confiable entre
contribuyentes, tanto en su rol de emisores como de receptores.

## Estándares como Emisor Electrónico

### Nombre de los archivos XML

| Formato XML | Nombre de archivo | Ejemplo |
|---|---|---|
| e-CF | `RNCEmisor+e-NCF` | `101672919E310000000001.xml` |
| Aprobación Comercial | `RNCComprador+e-NCF` | `101672919E310000000001.xml` |
| Acuse de recibo | `RNCComprador+e-NCF` | `101672919E310000000001.xml` |
| Resumen de Factura de Consumo (32 < 250,000.00) | `RNCEmisor+e-NCF` | `101672919E320000000001.xml` |

### Restricciones de contenido/caracteres en los XML

En campos `ALFANUM`, reemplazar estos caracteres por su entidad/referencia
(ya implementado donde aplica: `xml_builder.py`/`etree` escapa automáticamente
`< > & ' "` al serializar; los demás — `©`, `€`, `®` — deben evitarse o
escaparse manualmente si un campo los contiene):

| Carácter | Entidad | Decimal | Hexadecimal |
|---|---|---|---|
| `"` | `&quot;` | `&#34;` | `&#x22;` |
| `'` | `&apos;` | `&#39;` | `&#x27;` |
| `<` | `&lt;` | `&#60;` | `&#x3C;` |
| `>` | `&gt;` | `&#62;` | `&#x3E;` |
| `&` | `&amp;` | `&#38;` | `&#x26;` |
| `©` | `&copy;` | `&#169;` | `&#xA9;` |
| `€` | `&euro;` | `&#8364;` | `&#x20AC;` |
| `®` | `&reg;` | `&#174;` | `&#xAE;` |

En los datos del código de seguridad del QR (representación impresa), estos
caracteres reservados de URL deben ir en su representación hexadecimal
(`%XX`) — ya implementado en `libs/ecf_common/timbre_qr.py` vía
`urllib.parse.quote` (que cubre este mismo conjunto de caracteres reservados
de RFC 3986):

espacio `%20`, `!` `%21`, `#` `%23`, `$` `%24`, `&` `%26`, `'` `%27`, `(`
`%28`, `)` `%29`, `*` `%2A`, `+` `%2B`, `,` `%2C`, `/` `%2F`, `:` `%3A`, `;`
`%3B`, `=` `%3D`, `?` `%3F`, `@` `%40`, `[` `%5B`, `]` `%5D`, `"` `%22`, `-`
`%2D`, `.` `%2E`, `<` `%3C`, `>` `%3E`, `\` `%5C`, `^` `%5E`, `_` `%5F`, `` ` ``
`%60`.

**No incluir tags vacíos en los XML** — todo tag no utilizado debe excluirse
(ya implementado: `_agregar_nodo` en `xml_builder.py` omite campos `None`).

### Firmado de XML

- Protocolo: **SHA-256**.
- El campo `SN` (serialNumber) del certificado digital debe corresponder al
  RNC, Cédula o Pasaporte del propietario.
- Firmar sin preservar espacios (`preservewhitespace = false`).
- Un XML firmado no puede alterarse después.

Ya implementado en `libs/ecf_common/auth/signing.py::firmar_xml` (XML-DSig
enveloped, RSA-SHA256, C14N exclusivo) — usado tanto para firmar e-CF/RFCE/
aprobación comercial/anulación salientes como para firmar el ARECF que
`receptor_recepcion` devuelve.

## Estándar como Receptor Electrónico

### Reglas generales

1. El software debe, además de emitir e-CF y aprobaciones comerciales,
   **recibirlos** según los estándares de esta sección.
2. Los servicios deben: usar SSL, usar puertos de red tradicionales (443),
   no ser sensibles a mayúsculas/minúsculas, ser accesibles desde Internet,
   y no estar en listas negras / categorizados como proxy avoidance.
3. Mantener estos estándares en el tiempo y adaptarse a los cambios del
   sistema de Facturación Electrónica.

### Creación de servicios

Todos los contribuyentes deben exponer estos servicios bajo el **mismo
estándar de recurso** — la única diferencia entre contribuyentes es la
dirección de host. Implementados en `services/receptor_autenticacion`,
`services/receptor_recepcion` y `services/receptor_aprobacion_comercial`
(ver [`docs/DOKPLOY.md`](../DOKPLOY.md) para cómo exponerlos públicamente).

#### Autenticación (opcional)

Establece controles de seguridad en la interacción entre contribuyentes.
Implementado igual en espíritu al servicio de autenticación de la propia
DGII (semilla → firma → token), pero aquí ESTE contribuyente es el servidor.

- `GET /fe/autenticacion/api/semilla` → XML `SemillaModel{valor, fecha}` sin
  firmar (mismo formato que el servicio #1 de la DGII).
- `POST /fe/autenticacion/api/validacioncertificado` (multipart, campo
  `xml` = la semilla firmada por el CERTIFICADO DEL LLAMANTE) → token:
  `{ token, expira, expedido }` en JSON o `<RespuestaAutenticacion>` en XML,
  según el `Accept` del llamante. Fechas en formato universal
  `yyyy-MM-ddTHH:mm:ssZ`.

Limitación conocida (documentada en el código): se valida que la firma sea
íntegra (el certificado embebido firmó realmente ese XML) y se extrae el
RNC/Cédula/Pasaporte del campo `SN` del certificado, pero **no se valida la
cadena de confianza contra la CA raíz de INDOTEL** (requeriría su bundle
público, no incluido en este repo) — mismo alcance pragmático que ya declara
`signing.py` para la firma saliente.

#### Recepción (obligatorio)

Recibe el e-CF que otro contribuyente nos emite (cuando actuamos como
receptor/comprador).

- `POST /fe/recepcion/api/ecf` (multipart, campo `xml` = e-CF recibido;
  `Authorization: Bearer <token>` condicional a que se haya implementado
  autenticación) → un XML de acuse de recibo (ARECF) **firmado
  digitalmente** por este contribuyente:

```xml
<?xml version="1.0" encoding="utf-8"?>
<ARECF>
  <DetalleAcuseDeRecibo>
    <Version>1.0</Version>
    <RNCEmisor>131880600</RNCEmisor>
    <RNCComprador>132880600</RNCComprador>
    <eNCF>E310000000001</eNCF>
    <Estado>0</Estado>
    <FechaHoraAcuseRecibo>17-12-2020 11:19:06</FechaHoraAcuseRecibo>
  </DetalleAcuseDeRecibo>
</ARECF>
```

> Nota del documento oficial: tomar de referencia el formato y XSD
> compartidos en el portal web de DGII (www.dgii.gov.do) — el XSD oficial
> del ARECF no viene adjunto en este PDF; el nombrado de elementos arriba
> sigue la misma convención PascalCase que el resto de XSD de e-CF ya
> presentes en `services/facturas_api/xsd/`. Verificar contra el XSD oficial
> cuando esté disponible y ajustar `receptor_recepcion/app/arecf_builder.py`
> si difiere.

#### Aprobación comercial (obligatorio)

Recibe las aprobaciones comerciales que nos emitan sobre nuestras propias
facturas (cuando actuamos como emisor y el comprador nos responde).

- `POST /fe/aprobacioncomercial/api/ecf` (multipart, campo `xml` = formato
  ACECF; `Authorization: Bearer <token>` condicional) → `HTTP 200`
  (satisfactorio) o `HTTP 400` (insatisfactorio).
