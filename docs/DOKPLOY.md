# Despliegue en Dokploy

Este proyecto se despliega en [Dokploy](https://dokploy.com) como un único
recurso tipo **Compose**, definido en
[`docker-compose.dokploy.yml`](../docker-compose.dokploy.yml): los 11 módulos
del catálogo DGII + `facturas-api` + `inventario-api` + los tres módulos de
Receptor Electrónico + `formato-606` + `erp-api` + el `frontend` (React). La base de
datos es **Supabase** (self-hosted, recurso aparte en Dokploy) — ver
[`SUPABASE.md`](SUPABASE.md).

> Este archivo es distinto de `docker-compose.yml` (+ overrides
> `docker-compose.{test,qa,prod}.yml`), que son para **desarrollo local**
> (ver [`AMBIENTES.md`](AMBIENTES.md)). El de Dokploy es autocontenido — no
> necesita encadenar `-f -f` — y lee su configuración de variables de
> entorno en vez de los archivos `.env.<ambiente>` del repo.

## 1. Configurar el recurso en Dokploy

1. Crear un nuevo recurso **Compose** en tu proyecto de Dokploy, apuntado a
   este repositorio (rama a desplegar: `main`, o la que definas).
2. En **Compose Path**, poner `docker-compose.dokploy.yml`.
3. En la pestaña **Environment**, cargar las variables de la sección 2.

## 2. Variables de entorno requeridas

| Variable | Descripción |
|---|---|
| `EMISOR_RNC` | RNC del emisor autorizado como electrónico por la DGII |
| `CERTIFICADO_PASSWORD` | Contraseña del certificado `.p12` (ver paso 3) |
| `DATABASE_URL` | Cadena de conexión al Postgres de Supabase (pooler Supavisor, puerto 5432). Ver [`SUPABASE.md`](SUPABASE.md), paso 2 |
| `SUPABASE_NETWORK` | Red de Docker del recurso de Supabase (p. ej. `facturacion-supabase-soscqp_default`), para llegar a `supavisor` por la red interna. Ver [`SUPABASE.md`](SUPABASE.md), paso 2 |
| `EMISOR_100_ELECTRONICO` | Opcional, por defecto `true`: la empresa emite solo e-CF, así que el 607 y el 608 aparecen como "No aplica". Póngalo en `false` si todavía emite NCF serie B. Ver [`ERP_API.md`](ERP_API.md) |
| `APROBACION_COMERCIAL_URL` | Opcional, por defecto `http://aprobacion-comercial:8000` (red interna): el módulo al que `erp-api` le pide firmar y enviar a la DGII la aprobación/rechazo comercial de la bandeja de entrada de Compras |
| `EMISOR_RAZON_SOCIAL` | Opcional: razón social de la empresa en los certificados de retención de Compras (vacío = el frontend usa la suya) |

Opcionales (con default razonable si se omiten):

| Variable | Default | Descripción |
|---|---|---|
| `APP_ENV` | `production` | `test` \| `qa` \| `production` — determina el ambiente DGII (TesteCF/CerteCF/eCF) |
| `ALLOWED_ORIGINS` | `["*"]` | Orígenes CORS permitidos, formato JSON |
| `LOG_LEVEL` | `INFO` | Nivel de log |
| `DEBUG` | `false` | Modo debug de FastAPI |
| `VITE_API_URL` | `/api` | Base URL que el frontend usa para llamar a `facturas-api`. El default `/api` hace que nginx (dentro del propio contenedor `frontend`) reenvíe esas llamadas a `facturas-api` por la red interna `facturacion` -- mismo origen que el frontend, sin CORS ni el problema de "mixed content" de llamar a un backend `http://` desde un frontend `https://`. **No hace falta cambiarlo** salvo que quieras exponer `facturas-api` con su propia URL pública. Se hornea en el bundle del frontend en build time — cambiarlo exige reconstruir la imagen (redeploy), no basta con reiniciar el contenedor |
| `VITE_INVENTARIO_API_URL` | `/api-inventario` | Lo mismo que `VITE_API_URL` pero para `inventario-api` (catálogo de productos, proveedores, compras y kardex). Mismo mecanismo de proxy same-origin vía nginx, mismas advertencias |
| `VITE_ERP_API_URL` | `/api-erp` | Lo mismo que `VITE_API_URL` pero para `erp-api` (contactos, cotizaciones, cobros y cuentas por cobrar, ver [`ERP_API.md`](ERP_API.md)). Mismo mecanismo de proxy same-origin vía nginx, mismas advertencias |
| `VITE_MARCA` | `Kontara` | Nombre de la marca en el menú lateral y la barra superior. Se hornea en build time: cambiarlo exige redeploy |
| `VITE_EMPRESA_NOMBRE` | `Mi empresa` | Nombre de la empresa emisora en el selector de empresa de la barra superior. Build time |
| `VITE_EMPRESA_RNC` | RNC de la primera factura | RNC que se muestra junto al nombre de la empresa. Build time |

Nunca se commitean estos valores en el repo — se configuran únicamente en el
panel de Dokploy.

## 3. Certificado digital del emisor (.p12)

El certificado **no está ni puede estar en el repositorio** (está en
`.gitignore`). El compose lo espera montado en `./certs/emisor.p12` (relativo
a donde Dokploy clona el repo en el servidor). Pasos:

1. Conectarse por SSH al servidor donde corre Dokploy.
2. Ubicar el directorio del repo clonado por Dokploy para este recurso
   (normalmente bajo algo como `/etc/dokploy/compose/<id-del-recurso>/code`).
3. Crear la carpeta `certs/` si no existe y copiar ahí el `emisor.p12` real.
4. Si Dokploy vuelve a clonar el repo en un redeploy, repetir este paso (o
   usar un volumen persistente de Dokploy apuntado a esa ruta para que
   sobreviva a los redeploys).

## 4. Base de datos: Supabase

La base de datos vive en **Supabase** instalado en Dokploy como recurso
aparte. Instalación, migración de los datos existentes, configuración de
`DATABASE_URL` y uso de **Supabase Studio** (ver y editar tablas): ver
[`SUPABASE.md`](SUPABASE.md).

- Los módulos `recepcion`, `recepcion_rfce`, `aprobacion_comercial` y
  `anulacion_encf` registran en `facturacion.envios_dgii`, de forma
  best-effort (nunca bloquean la respuesta si falla), cada envío a la DGII.
- `facturas-api` usa `facturacion.facturas` de forma **obligatoria**: si
  `DATABASE_URL` no está disponible, no arranca. También le pregunta a
  `consulta-resultado` por la red interna (`CONSULTA_RESULTADO_URL`, default
  `http://consulta-resultado:8000`) el resultado de la DGII de cada e-CF
  enviado, para pasarlo de `enviado` a `aceptado`/`rechazado`: justo
  después de enviar, con el botón "Actualizar estado DGII" y en una tarea
  de fondo cada `SINCRONIZAR_ESTADO_CADA_SEGUNDOS` (default `120`, `0` la
  desactiva). Cada consulta se audita en `facturacion.envios_dgii` con
  `modulo = 'consulta_resultado'`. Ver la sección "Facturación" del README.
- `inventario-api` usa el esquema `inventario` y `formato-606` el esquema
  `compras606`, ambos de forma obligatoria.
- `erp-api` usa el esquema `ventas` (obligatorio) y **lo crea por sí mismo
  al arrancar** (`CREATE ... IF NOT EXISTS`, más la función
  `ventas.fecha_dgii_segura`): no hace falta correr `db/init/007_ventas.sql`
  ni `009_fecha_dgii_segura.sql` en Studio. Solo lee `facturacion.facturas`. Ver
  [`ERP_API.md`](ERP_API.md). También crea `compras` (facturas de compra y
  pagos a proveedores) y `recepcion.ecf_recibidos` (bandeja de entrada, la
  llena `receptor-recepcion`): igual, no hace falta correr
  `010_compras.sql`. Los pagos a proveedores escriben la fecha de pago y
  las retenciones en `compras606.registros`.
- Los cambios de esquema futuros se agregan como `db/init/00N_*.sql`
  idempotentes y se ejecutan una vez en Studio → SQL Editor (en una
  instalación nueva, en orden desde `001`).

### Dominios (exponer al público)

Para administrar la base visualmente use **Supabase Studio** (dominio del
servicio `kong` del recurso de Supabase, ver [`SUPABASE.md`](SUPABASE.md)).
**No exponga el puerto 5432 del pooler de Supabase a Internet** sin
restringirlo por firewall.

#### IMPORTANTE: no asignar un "Domain" de Dokploy a `facturas-api` ni a `inventario-api` (ni a ningún módulo)

**Síntoma:** después de cada redeploy, `facturas-api` (o cualquier módulo) no
puede resolver `postgres` y hay que reconectarlo manualmente a la red:

```
docker network connect <proyecto>_facturacion <proyecto>-facturas-api-1
docker exec <proyecto>-facturas-api-1 getent hosts postgres   # para confirmar
```

**Causa raíz confirmada:** todos los servicios de `docker-compose.dokploy.yml`
declaran `networks: [facturacion]` de forma explícita y consistente (vía el
ancla `x-modulo-comun`), así que `docker compose up` por sí solo siempre deja
cada contenedor correctamente conectado a esa red. El problema NO está en el
`docker-compose.dokploy.yml` de este repo. Está en la función "Domains" del
panel de Dokploy: cuando le asignas un Domain a un servicio del Compose (p. ej.
`apifa.acentosdeco.lat` -> `facturas-api`), Dokploy gestiona el enrutamiento de
ese contenedor por fuera de `docker compose up` (para conectarlo a la red de
su Traefik) y, en instalaciones donde ese Traefik no está activo o coexiste
con otro reverse proxy en 80/443, ese paso de Dokploy puede dejar el contenedor sin
la red `facturacion` original en el siguiente redeploy. Por eso el fix manual
(`docker network connect`) soluciona el síntoma una vez, pero vuelve a pasar
en el próximo despliegue: la causa (el Domain de Dokploy) sigue ahí.

**Fix de raíz (permanente):** en el panel de Dokploy, quitar/eliminar el
Domain asignado a `facturas-api` (y a cualquier otro módulo que tenga uno).

`facturas-api` **no necesita ningún Domain ni URL pública propia** -- el
frontend ya no le habla directo. `frontend/nginx.conf` reenvía todo lo que
llega a `/api/*` hacia `facturas-api` por la red interna `facturacion`
(`proxy_pass http://facturas-api:8000/`), y `VITE_API_URL` tiene como
default `/api` (mismo origen que el frontend, ver sección 2). Esto además
evita el problema de "mixed content" del navegador (bloquea llamadas
`http://` desde una página servida por `https://`) que da un
`TypeError: NetworkError when attempting to fetch resource` si en cambio le
apuntas `VITE_API_URL` a `http://<IP>:8012` directo desde un frontend con
Domain HTTPS.

Si en algún caso puntual sí necesitas que `facturas-api` sea alcanzable
públicamente por su cuenta (p. ej. para depurar con `curl` o integrarlo con
otro sistema), usa su puerto ya publicado directo
(`http://<IP-del-servidor>:8012`, ver sección 5) o -- para HTTPS con
hostname propio -- un Cloudflare Tunnel (u otro reverse proxy externo, fuera
de Dokploy) apuntando ahí. Pero
para que el frontend funcione, ninguna de las dos hace falta.

## 5. Puertos de los módulos

Cada módulo publica su puerto interno `8000` en un puerto de host distinto
para poder asignarle un dominio en Dokploy. El rango arranca en **8001**, no
8000, porque en un servidor Dokploy real es común que el puerto 8000 ya esté
tomado por otro servicio del host (si tu servidor no tiene ese conflicto,
puedes correr el rango de vuelta a 8000 editando `docker-compose.dokploy.yml`):

> **Desde la fase 6 todos se publican solo en `127.0.0.1`** del servidor
> (`"127.0.0.1:8012:8000"`): el Cloudflare Tunnel los alcanza por
> `localhost`, pero ya no desde Internet por IP:puerto. Los del receptor
> (8015-8017) se pueden volver a abrir con `RECEPTOR_IP_PUBLICACION=0.0.0.0`.
> Inicio de sesión, variables nuevas y qué revisar: [`AUTH.md`](AUTH.md).

| Módulo | Puerto |
|---|---|
| autenticacion | 8001 |
| recepcion | 8002 |
| recepcion-rfce | 8003 |
| consulta-rfce | 8004 |
| consulta-resultado | 8005 |
| consulta-estado | 8006 |
| consulta-trackids | 8007 |
| aprobacion-comercial | 8008 |
| anulacion-encf | 8009 |
| consulta-directorio | 8010 |
| consulta-timbre | 8011 |
| facturas-api | 8012 |
| inventario-api | 8014 |
| frontend | 8013 (nginx sirve en el 80 del contenedor) |
| receptor-autenticacion | 8015 |
| receptor-recepcion | 8016 |
| receptor-aprobacion-comercial | 8017 |
| formato-606 | 8018 |
| erp-api | 8019 |

## 6. Inventario: reglas de negocio

El módulo de inventario (`inventario-api` + las pantallas "Productos",
"Proveedores", "Compras" y "Movimientos" del frontend) implementa:

- **Catálogo de productos** (SKU, nombre, precio de venta, costo promedio,
  stock actual). Cada línea de una factura debe vincularse a un producto del
  catálogo (el selector de "Producto" en el editor de ítems reemplaza el
  campo de texto libre que había antes) -- es lo que le permite a
  `facturas-api` saber qué descontar.
- **Compras** (entrada de mercancía): se crean en estado `borrador`
  (editable), y al **confirmarlas** se genera un movimiento de entrada por
  cada línea, se suma al `stock_actual` del producto y se recalcula su
  `costo_promedio` (promedio ponderado). Una compra confirmada ya no se
  edita ni se borra.
- **Salida de mercancía por venta**: NO ocurre al guardar el borrador de la
  factura, sino recién cuando esa factura se **envía exitosamente a la
  DGII** (`POST /facturas/{id}/enviar`, trackId recibido) -- ver
  `services/facturas_api/app/main.py::enviar_factura`. Antes de enviar,
  `facturas-api` le pregunta a `inventario-api` si hay stock suficiente
  (`POST /movimientos/validar-disponibilidad`) y **bloquea el envío con 409**
  si falta stock de algún producto (no se envía nada a la DGII en ese caso).
  Si `inventario-api` no responde a esa validación previa, el envío también
  se bloquea (502) -- no se puede confirmar que hay stock, así que por
  seguridad no se deja facturar.
- Una vez que la DGII acepta el envío (documento fiscal, ya irreversible), se
  llama a `inventario-api` para registrar la salida real
  (`POST /movimientos/salida-por-factura`, idempotente por factura). Si esa
  llamada falla (p. ej. `inventario-api` caído en ese momento puntual), la
  respuesta de `/enviar` **no falla** -- el e-CF ya se envió y eso no se
  puede deshacer -- pero queda registrado en
  `facturacion.facturas.inventario_sincronizado = false` (y
  `inventario_error` con el detalle) para poder detectarlo y correr la
  sincronización manualmente después.
- **Despliegue de la Fase 4 de inventario** (almacenes y lotes): la primera
  vez que arranca, `inventario-api` migra su esquema (una sola vez, anotado
  en `inventario.schema_version`) y elimina el índice único
  `movimientos_salida_factura_idx`, del que depende el `inventario-api`
  anterior para registrar cada salida por factura. **No dejar atendiendo a
  la vez el contenedor viejo y el nuevo**: el redeploy normal del Compose
  (detiene el viejo y arranca el nuevo, una réplica) es seguro; no
  configurar un despliegue rolling/start-first ni escalar `inventario-api`
  a más de una réplica durante ese despliegue. Volver a la versión anterior
  exige recrear el índice a mano. Detalle, y cómo recuperar las facturas
  que hayan quedado con `inventario_sincronizado = false`, en
  [`docs/INVENTARIO.md`](INVENTARIO.md) ("Despliegue").

## 7. Receptor Electrónico: exposición pública

`receptor-autenticacion`, `receptor-recepcion` y `receptor-aprobacion-comercial`
(ver [`docs/dgii/EMISORES_ELECTRONICOS.md`](dgii/EMISORES_ELECTRONICOS.md))
son los servicios que ESTE contribuyente expone para que **otros**
contribuyentes le envíen e-CF y aprobaciones comerciales cuando actúa como
receptor. A diferencia de `facturas-api`/`inventario-api` (que solo el
propio frontend necesita alcanzar, por eso viven detrás del proxy
same-origin de nginx sin URL pública propia), estos tres **sí deben ser
alcanzables desde Internet por terceros** -- el estándar oficial exige SSL y
"puertos de red tradicionales" (443), no un puerto arbitrario como 8015.

#### IMPORTANTE: no asignarles un "Domain" de Dokploy en este servidor

Por la misma causa raíz ya documentada en la sección 4 ("no asignar un
'Domain' de Dokploy a `facturas-api` ni a `inventario-api`"): en este
servidor, la función "Domains" de Dokploy depende de un Traefik que no está
desplegado de forma confiable (coexiste con otro reverse proxy en 80/443), y
asignarle un Domain a un servicio del Compose puede dejarlo sin la red
`facturacion` interna en el siguiente redeploy. Ese riesgo aplica igual aquí.

**Recomendado:** usar
un Cloudflare Tunnel (u otro reverse proxy externo, fuera de Dokploy)
apuntando directo a `http://<IP-del-servidor>:8015` (autenticación),
`:8016` (recepción) y `:8017` (aprobación comercial), cada uno con su propio
hostname HTTPS en el puerto 443 estándar. Eso satisface el requisito de SSL
+ puerto tradicional sin depender del Traefik de Dokploy.

Si el servidor no tiene el conflicto de puertos que motivó el uso de un
Traefik externo, asignar un Domain de Dokploy normalmente a estos tres
servicios es la alternativa más simple -- solo evitarlo en este servidor en
particular mientras el problema de red documentado siga sin resolverse.

### Variables adicionales de estos tres módulos

| Variable | Default | Aplica a | Descripción |
|---|---|---|---|
| `SEMILLA_TTL_SEGUNDOS` | `300` | receptor-autenticacion | Segundos que una semilla emitida por `GET /fe/autenticacion/api/semilla` sigue siendo válida antes de expirar si nadie la firma y la devuelve |
| `REQUIERE_AUTENTICACION` | `true` | receptor-recepcion, receptor-aprobacion-comercial | Si `false`, no exige el header `Authorization: Bearer` (útil si no se despliega `receptor-autenticacion` -- el estándar hace la autenticación opcional en general) |

## 8. Formato 606: Compras de Bienes y Servicios

`formato-606` (ver [`docs/dgii/FORMATO_606.md`](dgii/FORMATO_606.md)) es,
igual que `facturas-api`/`inventario-api`, un módulo que **solo el propio
frontend necesita alcanzar** -- no expone nada que otro contribuyente deba
llamar. `frontend/nginx.conf` reenvía todo lo que llega a `/api-606/*` hacia
`formato-606` por la red interna `facturacion`
(`proxy_pass http://formato-606:8000/`), y `VITE_FORMATO_606_API_URL` tiene
como default `/api-606` (mismo origen que el frontend). Por eso, igual que se
documenta para `facturas-api`/`inventario-api` en la sección 4, **no le
asignes un "Domain" de Dokploy** -- no lo necesita, y corre el mismo riesgo
de perder la red `facturacion` interna en el siguiente redeploy.

Renglones auto-poblados: `receptor-recepcion` escribe un renglón por cada
e-CF recibido de un proveedor, y `facturas-api` escribe uno por cada
comprobante autoemitido tipo 41 (Compras Electrónico), 43 (Gastos Menores
Electrónico) o 47 (Pagos al Exterior Electrónico) que se envía exitosamente.
Ambas escrituras son best-effort (nunca bloquean la respuesta del módulo de
origen) e idempotentes (par `rnc_cedula` + `ncf` único). El usuario clasifica
manualmente, desde la pantalla "Formato 606" del frontend, los campos que
ningún e-CF trae (Tipo de Bienes y Servicios Comprados, Forma de Pago,
retenciones) antes de poder generar el archivo `DGII_F_606_<RNC>_<periodo>.TXT`
que se remite por la Oficina Virtual.

## 9. Verificar el despliegue

Tras el primer deploy, confirmar que cada módulo responde:

```bash
curl https://<dominio-o-ip>:8001/health
# {"status":"ok","app_env":"production","dgii_environment":"ecf"}
```

Y que la auditoría funciona: enviar un e-CF de prueba por `recepcion` y
verificar la fila en Supabase Studio (Table Editor, o en el SQL Editor `SELECT * FROM
facturacion.envios_dgii ORDER BY creado_en DESC LIMIT 5;`).

`inventario-api` responde igual:

```bash
curl https://<dominio-o-ip>:8014/health
# {"status":"ok","app_env":"production"}
```

Si devuelve error de conexión a la base de datos, revisar primero que se haya
aplicado la migración manual de la sección 4 ("IMPORTANTE: en un servidor ya
desplegado, aplicar `004_inventario.sql`...") -- es la causa más probable en
un servidor que ya tenía el proyecto desplegado antes de este módulo.

`formato-606` responde igual:

```bash
curl https://<dominio-o-ip>:8018/health
# {"status":"ok","app_env":"production"}
```

Mismo caso: si falla la conexión a la base de datos, confirmar que
`006_compras606.sql` se haya aplicado a mano (sección 4) en un servidor que
ya tenía el proyecto desplegado antes de este módulo.

Los tres módulos de Receptor Electrónico también:

```bash
curl https://<dominio-o-ip>:8015/health   # receptor-autenticacion
curl https://<dominio-o-ip>:8016/health   # receptor-recepcion
curl https://<dominio-o-ip>:8017/health   # receptor-aprobacion-comercial
```

Para probar el flujo completo de autenticación (opcional, solo si se
despliega `receptor-autenticacion`):

```bash
# 1) Pedir una semilla
curl https://<dominio-o-ip>:8015/fe/autenticacion/api/semilla -o semilla.xml

# 2) Firmarla con CUALQUIER certificado de prueba (el propio, por ejemplo) y
#    canjearla por un token -- ver libs/ecf_common/auth/signing.py::firmar_xml
#    para firmar un XML desde Python, o cualquier herramienta de firma XML-DSig.
curl -X POST https://<dominio-o-ip>:8015/fe/autenticacion/api/validacioncertificado \
  -H 'accept: application/json' \
  -F 'xml=@semilla-firmada.xml;type=text/xml'
# {"token": "...", "expira": "...", "expedido": "..."}
```

Luego usar ese `token` como `Authorization: Bearer <token>` al enviar un e-CF
de prueba a `POST https://<dominio-o-ip>:8016/fe/recepcion/api/ecf` (campo
`xml`) y confirmar que responde con un ARECF firmado (`Content-Type: text/xml`,
contiene un `<Signature>`).

Para el frontend: abrir `http://<dominio-o-ip>:8013`, crear al menos un
producto en Inventario > Productos, luego crear una factura de prueba
vinculando esa línea al producto y confirmar que "Generar XML" no reporta
errores de validación contra el XSD. Con los defaults `VITE_API_URL=/api` y
`VITE_INVENTARIO_API_URL=/api-inventario` (ver sección 2) esto debería
funcionar sin configuración adicional. Si en la consola del navegador ves
`TypeError: NetworkError when attempting to fetch resource` (o un error de
"mixed content"), es casi seguro que alguna de esas dos variables está
apuntando a una URL `http://` propia en vez de a su default `/api*` -- ver
sección 4 arriba ("IMPORTANTE: no asignar un 'Domain' de Dokploy a
`facturas-api` ni a `inventario-api`"). Cambiar cualquiera de las dos exige
reconstruir la imagen `frontend` (redeploy), no solo reiniciarla.

Si algún módulo (típicamente `facturas-api` o `inventario-api`) no puede
resolver `postgres` después de un redeploy, no es un problema del
`docker-compose.dokploy.yml` -- es casi seguro un Domain de Dokploy asignado a
ese servicio. Ver "IMPORTANTE: no asignar un 'Domain' de Dokploy a
`facturas-api` ni a `inventario-api`" en la sección 4 arriba para la causa
raíz y el fix permanente (no requiere tocar código).
