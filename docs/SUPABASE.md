# Base de datos en Supabase (self-hosted en Dokploy)

Desde esta versión la base de datos de la aplicación vive en **Supabase**
instalado en el mismo servidor de Dokploy. Reemplaza al `postgres`,
`postgrest` y `pgadmin` que antes levantaba `docker-compose.dokploy.yml`:

| Antes | Ahora |
|---|---|
| `postgres` (contenedor propio del compose) | Postgres de Supabase |
| `pgadmin` (puerto 8901) | **Supabase Studio**: editor de tablas, SQL Editor, respaldos |
| `postgrest` (solo lectura de auditoría) | La API REST de Supabase (no la usa la aplicación) |

Los módulos (`facturas-api`, `inventario-api`, `formato-606`, los módulos
DGII que auditan envíos y los futuros) siguen hablando con Postgres
directamente por `asyncpg`: lo único que cambia es la variable
`DATABASE_URL`. Las tablas siguen en sus esquemas de siempre:
`facturacion`, `inventario` y `compras606`.

> **Orden importante:** haga los pasos 1 a 4 **antes** de unir a `main` el
> cambio que quita el `postgres` del compose. Mientras tanto, la aplicación
> sigue funcionando con la base actual y el script de migración la lee de
> ahí. Nada de esto borra la base actual: su volumen queda en el servidor
> como respaldo hasta que usted decida eliminarlo.

## 1. Instalar Supabase en Dokploy

1. En Dokploy, en su proyecto: **Create Service → Template**, busque
   **Supabase** y créelo (es un recurso Compose aparte del de la
   facturación).
2. Antes del primer deploy, en la pestaña **Environment** del recurso de
   Supabase, cambie como mínimo:
   - `POSTGRES_PASSWORD`: contraseña larga y única (la usará `DATABASE_URL`).
   - `DASHBOARD_USERNAME` y `DASHBOARD_PASSWORD`: usuario y clave para entrar
     a Studio.
   - `JWT_SECRET`, `ANON_KEY`, `SERVICE_ROLE_KEY`: genérelos nuevos (la guía
     oficial "Self-Hosting with Docker" de Supabase explica cómo); no deje los
     de ejemplo.
   - Anote `POOLER_TENANT_ID` (por defecto `your-tenant-id`; puede cambiarlo).
3. **Domains**: asigne un dominio al servicio **kong** (por ejemplo
   `supabase.acentosdeco.lat`, puerto `8000`). Por ahí se entra a Studio.
4. **Deploy** y espere a que todos los contenedores estén en verde.

## 2. Armar la cadena de conexión (`DATABASE_URL`)

La aplicación se conecta al pooler de Supabase (**Supavisor**) en **modo
sesión, puerto 5432**:

```
postgresql://postgres.<POOLER_TENANT_ID>:<POSTGRES_PASSWORD>@<HOST>:5432/postgres
```

- `<HOST>`: la IP privada o pública del servidor de Dokploy (el pooler
  publica el puerto 5432 en el host). Para confirmarlo, en el servidor:
  `docker ps --format '{{.Names}}  {{.Ports}}' | grep -i -E 'supavisor|pooler'`
  debe mostrar `0.0.0.0:5432->5432`. Si no aparece, en el compose de
  Supabase agregue `ports: ["5432:5432"]` al servicio `supavisor` y redeploy.
- Si la contraseña tiene caracteres especiales (`@ : / ? #`), escríbalos
  codificados (`@` → `%40`, `:` → `%3A`, `/` → `%2F`).
- También funciona el modo transacción (puerto `6543`): la aplicación lo
  detecta y desactiva la caché de sentencias de `asyncpg`. Use 5432 para el
  script de migración.

Nunca escriba esta cadena en el repositorio ni en chats: va solo en la
pestaña Environment de Dokploy.

## 3. Migrar los datos actuales

Con la aplicación todavía en la versión anterior (con su `postgres`):

1. Dokploy → recurso de la facturación → **Open Terminal** → elija el
   contenedor **postgres**.
2. Pegue el script [`scripts/supabase/migrar_datos.sh`](../scripts/supabase/migrar_datos.sh)
   en un archivo y ejecútelo con su cadena del paso 2:

   ```sh
   cat > /tmp/migrar.sh <<'FIN'
   (pegue aquí el contenido completo de scripts/supabase/migrar_datos.sh)
   FIN
   DESTINO_URL='postgresql://postgres.your-tenant-id:SU_CLAVE@IP_DEL_SERVIDOR:5432/postgres' sh /tmp/migrar.sh
   ```

3. El script exporta los esquemas `facturacion`, `inventario` y `compras606`
   con todos sus datos, los importa en Supabase en una sola transacción (si
   algo falla, no queda nada a medias) y compara la cantidad de filas tabla
   por tabla. Debe terminar con `Migración completa`. Si alguna tabla sale
   `DIFERENTE`, no siga al paso 4.
4. Deja un respaldo `.sql` en `/tmp` del contenedor por si lo necesita.

Si el destino ya tiene esos esquemas (por ejemplo, de un intento anterior),
el script se detiene y explica cómo borrarlos antes de reintentar.

**Instalación nueva sin datos previos:** en Studio → **SQL Editor**, ejecute
en orden el contenido de `db/init/001_schema.sql` a `db/init/006_compras606.sql`.

## 4. Apuntar la aplicación a Supabase

1. Dokploy → recurso de la facturación → **Environment**:
   - agregue `DATABASE_URL` con la cadena del paso 2;
   - puede borrar `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`,
     `PGADMIN_DEFAULT_EMAIL` y `PGADMIN_DEFAULT_PASSWORD` (ya no se usan).
2. Una a `main` el cambio de este documento (o haga **Deploy** si ya está).
3. En **Domains**, borre el dominio de **pgadmin** (`pg.acentosdeco.lat`):
   ese servicio ya no existe.
4. Abra el frontend y compruebe que aparecen sus facturas, productos y
   proveedores. Si un servicio no arranca, revise sus logs: un error de
   conexión casi siempre es `<HOST>`, el puerto o la contraseña de
   `DATABASE_URL`.

## 5. Ver y modificar los datos en Supabase Studio

1. Entre al dominio de **kong** del paso 1 con `DASHBOARD_USERNAME` /
   `DASHBOARD_PASSWORD`.
2. **Table Editor** → en el selector de esquema (arriba a la izquierda) elija
   `facturacion`, `inventario` o `compras606`. Ahí puede ver, filtrar, editar,
   insertar y borrar filas como en una hoja de cálculo.
3. **SQL Editor** para consultas libres, por ejemplo:

   ```sql
   select encf, estado, dgii_codigo, error, creado_en
   from facturacion.facturas order by creado_en desc limit 50;
   ```

4. **Database → Backups** (o `pg_dump` desde el contenedor `db`) para
   respaldos.

Cuidado al editar a mano: cambiar el `estado`, `track_id` o `xml_firmado` de
una factura ya enviada no la cambia en la DGII; solo cambia lo que muestra la
aplicación.

## Seguridad

- Los esquemas de la aplicación **no** están expuestos por la API REST de
  Supabase (por defecto solo expone `public`). No los agregue en
  *Settings → API → Exposed schemas*: la aplicación no lo necesita y
  dejaría leer facturas con la `ANON_KEY`.
- No publique el puerto 5432 a Internet: si el servidor tiene IP pública,
  limite el puerto con el firewall a la propia máquina o a su red privada.
- Proteja Studio con una `DASHBOARD_PASSWORD` fuerte; quien entra ahí puede
  modificar todo.
