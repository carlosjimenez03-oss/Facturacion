# Despliegue en Dokploy

Este proyecto se despliega en [Dokploy](https://dokploy.com) como un único
recurso tipo **Compose**, definido en
[`docker-compose.dokploy.yml`](../docker-compose.dokploy.yml): los 11 módulos
de facturación electrónica + Postgres + PostgREST + pgAdmin.

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
- **PostgREST** expone ese esquema como API REST de **solo lectura** (rol
  `web_anon`) en el puerto interno `3000`. No requiere autenticación por
  defecto — ver la nota de seguridad abajo.
- **pgAdmin** (puerto interno `80`) permite administrar la base visualmente;
  conectarse ahí al host `postgres`, puerto `5432`, con `POSTGRES_USER`/
  `POSTGRES_PASSWORD`.

### Dominios (exponer al público)

Los tres servicios de infraestructura (`postgres`, `postgrest`, `pgadmin`) NO
publican puertos al host (`expose`, no `ports`) — son alcanzables solo dentro
de la red interna `facturacion`. Para exponer `postgrest` y/o `pgadmin` con
un dominio propio, usar la función de Dokploy de asignar un dominio a un
servicio del compose (Dokploy detecta el puerto interno vía `expose`).
**No exponer pgAdmin ni Postgres directamente a Internet** sin al menos
restringir por IP o poner autenticación adicional delante.

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
para poder asignarle un dominio en Dokploy:

| Módulo | Puerto |
|---|---|
| autenticacion | 8000 |
| recepcion | 8001 |
| recepcion-rfce | 8002 |
| consulta-rfce | 8003 |
| consulta-resultado | 8004 |
| consulta-estado | 8005 |
| consulta-trackids | 8006 |
| aprobacion-comercial | 8007 |
| anulacion-encf | 8008 |
| consulta-directorio | 8009 |
| consulta-timbre | 8010 |

## 6. Verificar el despliegue

Tras el primer deploy, confirmar que cada módulo responde:

```bash
curl https://<dominio-o-ip>:8000/health
# {"status":"ok","app_env":"production","dgii_environment":"ecf"}
```

Y que la auditoría funciona: enviar un e-CF de prueba por `recepcion` y
verificar la fila en Postgres (vía pgAdmin, o `SELECT * FROM
facturacion.envios_dgii ORDER BY creado_en DESC LIMIT 5;`).
