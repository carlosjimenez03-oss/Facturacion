# Despliegue en Dokploy

Este proyecto se despliega en [Dokploy](https://dokploy.com) como un único
recurso tipo **Compose**, definido en
[`docker-compose.dokploy.yml`](../docker-compose.dokploy.yml): los 11 módulos
del catálogo DGII + `facturas-api` + `inventario-api` + el `frontend` (React)
+ Postgres + PostgREST + pgAdmin.

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
| `POSTGRES_PASSWORD` | Contraseña del usuario de Postgres |
| `PGADMIN_DEFAULT_EMAIL` | Email de acceso a pgAdmin |
| `PGADMIN_DEFAULT_PASSWORD` | Contraseña de acceso a pgAdmin |

Opcionales (con default razonable si se omiten):

| Variable | Default | Descripción |
|---|---|---|
| `APP_ENV` | `production` | `test` \| `qa` \| `production` — determina el ambiente DGII (TesteCF/CerteCF/eCF) |
| `POSTGRES_USER` | `facturacion` | Usuario de Postgres (también usado por PostgREST y por los módulos) |
| `POSTGRES_DB` | `facturacion` | Nombre de la base de datos |
| `ALLOWED_ORIGINS` | `["*"]` | Orígenes CORS permitidos, formato JSON |
| `LOG_LEVEL` | `INFO` | Nivel de log |
| `DEBUG` | `false` | Modo debug de FastAPI |
| `VITE_API_URL` | `/api` | Base URL que el frontend usa para llamar a `facturas-api`. El default `/api` hace que nginx (dentro del propio contenedor `frontend`) reenvíe esas llamadas a `facturas-api` por la red interna `facturacion` -- mismo origen que el frontend, sin CORS ni el problema de "mixed content" de llamar a un backend `http://` desde un frontend `https://`. **No hace falta cambiarlo** salvo que quieras exponer `facturas-api` con su propia URL pública. Se hornea en el bundle del frontend en build time — cambiarlo exige reconstruir la imagen (redeploy), no basta con reiniciar el contenedor |
| `VITE_INVENTARIO_API_URL` | `/api-inventario` | Lo mismo que `VITE_API_URL` pero para `inventario-api` (catálogo de productos, proveedores, compras y kardex). Mismo mecanismo de proxy same-origin vía nginx, mismas advertencias |

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

## 4. Base de datos: Postgres + PostgREST + pgAdmin

#### IMPORTANTE: en un servidor ya desplegado, aplicar `004_inventario.sql` y `005_facturas_items_inventario.sql` a mano

Todo lo que esté en `db/init/*.sql` (incluyendo estos dos archivos nuevos) se
corre **solo la primera vez** que arranca Postgres con un volumen de datos
vacío (mecanismo estándar de la imagen oficial de Postgres,
`docker-entrypoint-initdb.d`). Si ya tenías este proyecto desplegado antes de
este cambio, el volumen de Postgres YA existe, así que estos dos archivos
nuevos **no se van a ejecutar solos** en el próximo redeploy -- `inventario-api`
no va a poder arrancar (le va a faltar el esquema `inventario`) y
`facturas-api` va a fallar al guardar/enviar facturas con productos
vinculados (le va a faltar la columna `items_inventario`).

Antes de (o justo después de) desplegar esta versión, correr una sola vez,
contra la base de datos de producción (vía pgAdmin, o `psql`/`docker exec`
directo al contenedor `postgres`), el contenido completo de:

1. [`db/init/004_inventario.sql`](../db/init/004_inventario.sql)
2. [`db/init/005_facturas_items_inventario.sql`](../db/init/005_facturas_items_inventario.sql)

Son idempotentes (`CREATE ... IF NOT EXISTS` / `ADD COLUMN IF NOT EXISTS`),
así que correrlos de más no rompe nada -- solo importa correrlos al menos una
vez antes de usar el módulo de inventario.

- **Postgres** inicializa automáticamente el esquema `facturacion` (tabla
  `envios_dgii`) la primera vez que arranca, vía
  [`db/init/001_schema.sql`](../db/init/001_schema.sql). El servicio `postgres`
  se construye desde [`db/Dockerfile`](../db/Dockerfile) (`build: ./db`), que
  copia ese script dentro de la imagen en `/docker-entrypoint-initdb.d/` — no
  se usa un bind-mount de `./db/init`, porque algunas plataformas de
  despliegue (incluyendo Dokploy) no resuelven de forma confiable el path
  relativo de un bind-mount de subcarpeta del repo.
- Los módulos `recepcion`, `recepcion_rfce`, `aprobacion_comercial` y
  `anulacion_encf` registran ahí, de forma best-effort (nunca bloquean la
  respuesta si falla), cada envío a la DGII: módulo, RNC emisor, e-NCF,
  trackId, estado/código y la respuesta completa de la DGII.
- `facturas-api` (backend del frontend) usa la misma base de datos, pero de
  forma **obligatoria** (no best-effort): las facturas que crea el usuario
  desde el frontend viven únicamente en la tabla `facturacion.facturas`
  ([`db/init/002_facturas.sql`](../db/init/002_facturas.sql)) — si
  `DATABASE_URL` no está disponible, `facturas-api` no arranca.
- `inventario-api` (catálogo de productos, proveedores, compras y kardex de
  inventario) usa el esquema `inventario`
  ([`db/init/004_inventario.sql`](../db/init/004_inventario.sql)), también de
  forma obligatoria -- sin `DATABASE_URL` alcanzable, no arranca. La relación
  entre una línea de factura y un producto del catálogo vive en
  `facturacion.facturas.items_inventario`
  ([`db/init/005_facturas_items_inventario.sql`](../db/init/005_facturas_items_inventario.sql)),
  ver "Inventario: reglas de negocio" más abajo.
- **PostgREST** expone ese esquema como API REST de **solo lectura** (rol
  `web_anon`) en el puerto interno `3000`. No requiere autenticación por
  defecto — ver la nota de seguridad abajo.
- **pgAdmin** (puerto interno `80`, publicado en el host como `8901`) permite
  administrar la base visualmente; conectarse ahí al host `postgres`, puerto
  `5432`, con `POSTGRES_USER`/`POSTGRES_PASSWORD`.

### Dominios (exponer al público)

`postgres` y `postgrest` NO publican puertos al host (`expose`, no `ports`)
— son alcanzables solo dentro de la red interna `facturacion`.

`pgadmin` sí publica su puerto al host (`ports: ["8901:80"]`), en vez de usar
la función de "Domains" de Dokploy (que depende de un Traefik gestionado por
Dokploy — en instalaciones donde ese Traefik no está desplegado, como cuando
coexiste con otro reverse proxy ya usando los puertos 80/443, esa función no
enruta nada). Para darle un dominio propio a pgAdmin en ese caso, apuntar un
Cloudflare Tunnel (u otro reverse proxy externo) directo a `<IP-del-servidor>:8901`,
en vez de depender de Dokploy.

**No exponer pgAdmin ni Postgres directamente a Internet** sin al menos
restringir por IP o poner autenticación adicional delante.

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
con otro reverse proxy en 80/443 (exactamente el caso de este servidor, ver
párrafo de pgAdmin arriba), ese paso de Dokploy puede dejar el contenedor sin
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
de Dokploy) apuntando ahí, igual que se documenta arriba para pgAdmin. Pero
para que el frontend funcione, ninguna de las dos hace falta.

### Nota de seguridad sobre PostgREST

El rol anónimo (`web_anon`) tiene acceso de **solo lectura** a
`facturacion.envios_dgii` — nadie puede escribir a través de PostgREST (las
escrituras solo ocurren desde los módulos, vía conexión directa a Postgres).
Aun así, si le asignas un dominio público a `postgrest`, cualquiera con esa
URL podrá leer el log de auditoría completo (RNC, e-NCF, respuestas de la
DGII). Si eso es sensible, no le asignes dominio público (queda accesible
solo dentro de la red interna), o configura `PGRST_JWT_SECRET` y ajusta los
roles/grants en `db/init/001_schema.sql` para exigir autenticación antes de
exponerlo.

## 5. Puertos de los módulos

Cada módulo publica su puerto interno `8000` en un puerto de host distinto
para poder asignarle un dominio en Dokploy. El rango arranca en **8001**, no
8000, porque en un servidor Dokploy real es común que el puerto 8000 ya esté
tomado por otro servicio del host (si tu servidor no tiene ese conflicto,
puedes correr el rango de vuelta a 8000 editando `docker-compose.dokploy.yml`):

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

## 7. Verificar el despliegue

Tras el primer deploy, confirmar que cada módulo responde:

```bash
curl https://<dominio-o-ip>:8001/health
# {"status":"ok","app_env":"production","dgii_environment":"ecf"}
```

Y que la auditoría funciona: enviar un e-CF de prueba por `recepcion` y
verificar la fila en Postgres (vía pgAdmin, o `SELECT * FROM
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
