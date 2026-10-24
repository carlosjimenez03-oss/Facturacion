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
`facturacion`, `inventario` y `compras606`. `erp-api` agrega el esquema
`ventas`, que crea él mismo al arrancar (ver [`ERP_API.md`](ERP_API.md)).
`inventario-api` también crea y migra el esquema `inventario` al arrancar
(almacenes, lotes, ajustes...; ver [`INVENTARIO.md`](INVENTARIO.md)).

> **Orden importante:** haga los pasos 1 a 4 **antes** de unir a `main` el
> cambio que quita el `postgres` del compose. Mientras tanto, la aplicación
> sigue funcionando con la base actual y el script de migración la lee de
> ahí. Nada de esto borra la base actual: su volumen queda en el servidor
> como respaldo hasta que usted decida eliminarlo.

## 1. Instalar Supabase en Dokploy

1. En Dokploy, en su proyecto: **Create Service → Template**, busque
   **Supabase** y créelo (es un recurso Compose aparte del de la
   facturación). La plantilla genera los secretos al azar.
2. En su pestaña **Environment** cambie como mínimo `DASHBOARD_PASSWORD`
   (larga) y ponga `DISABLE_SIGNUP=true` (los usuarios de la aplicación
   los crea usted: el inicio de sesión usa Supabase Auth, ver
   [`AUTH.md`](AUTH.md)). Use para `POSTGRES_PASSWORD` solo letras y números, así no
   hay que codificarla en la cadena de conexión.
3. **Deploy** y espere a que todos los contenedores estén *healthy*.

### Acceso a Studio con Cloudflare Tunnel

Si el servidor publica sus sitios con un Cloudflare Tunnel (rutas tipo
`front.acentosdeco.lat → http://localhost:8013`), el gateway de Supabase
(`api-gw`, puerto interno 8000) no es alcanzable desde el túnel: solo vive
en la red de Docker de Supabase. **No** apunte la ruta a `localhost:8000`:
ese puerto del servidor puede ser de otro servicio.

1. En Dokploy → recurso de Supabase → **General → Edit compose** (o la
   pestaña del compose), en el servicio `api-gw` agregue:

   ```yaml
       ports:
         - "127.0.0.1:8040:8000"
   ```

   `127.0.0.1` lo publica solo dentro del servidor (para el túnel), no a
   Internet. Si el 8040 está ocupado (`ss -ltn | grep :8040`), use otro.
2. **Deploy** del recurso de Supabase.
3. En Cloudflare → **Zero Trust → Networks → Tunnels → su túnel → Rutas**,
   edite `supa.acentosdeco.lat` y cambie el servicio a `http://localhost:8040`.
4. Abra `https://supa.acentosdeco.lat`: pide `DASHBOARD_USERNAME` /
   `DASHBOARD_PASSWORD` y abre Studio. (El dominio que Dokploy le asigna a
   `api-gw` no se usa con el túnel; puede dejarlo o quitarlo.)

## 2. Conexión de la aplicación (`DATABASE_URL` y `SUPABASE_NETWORK`)

La aplicación entra a la red interna de Docker del recurso de Supabase y se
conecta al pooler **Supavisor** por su nombre, en modo sesión (5432). Así no
hace falta publicar el puerto de la base en el servidor.

1. Nombre de la red de Supabase. Por SSH en el servidor:

   ```sh
   docker network ls --format '{{.Name}}' | grep -i supabase
   ```

   Normalmente es `<nombre-del-recurso>_default`, por ejemplo
   `facturacion-supabase-soscqp_default`. Ese valor es `SUPABASE_NETWORK`.
2. Cadena de conexión (`POOLER_TENANT_ID` y `POSTGRES_PASSWORD` están en el
   Environment del recurso de Supabase):

   ```
   postgresql://postgres.<POOLER_TENANT_ID>:<POSTGRES_PASSWORD>@supavisor:5432/postgres
   ```

   Si la contraseña tiene caracteres especiales (`@ : / ? #`), escríbalos
   codificados (`@` → `%40`, `:` → `%3A`, `/` → `%2F`). El modo transacción
   (`supavisor:6543`) también funciona: la aplicación desactiva la caché de
   sentencias de `asyncpg` automáticamente. Para **varias empresas**
   (docs/MULTIEMPRESA.md) use el modo sesión (5432): el modo transacción no
   conserva la empresa de cada conexión y solo deja trabajar con la empresa 1.

Nunca escriba estos valores en el repositorio ni en chats: van solo en la
pestaña Environment de Dokploy.

## 3. Migrar los datos actuales

Con la aplicación todavía en la versión anterior (su contenedor `postgres`
viejo sigue encendido):

1. Por SSH, conecte temporalmente el `postgres` viejo a la red de Supabase
   para que pueda verlo:

   ```sh
   docker ps --format '{{.Names}}' | grep -E 'facturacion.*postgres'
   docker network connect <SUPABASE_NETWORK> <nombre-del-contenedor-postgres-viejo>
   ```

2. Dokploy → recurso de la facturación → **Open Terminal** → contenedor
   **postgres**. Pegue el script
   [`scripts/supabase/migrar_datos.sh`](../scripts/supabase/migrar_datos.sh)
   en un archivo y ejecútelo con la cadena del paso 2:

   ```sh
   cat > /tmp/migrar.sh <<'FIN'
   (pegue aquí el contenido completo de scripts/supabase/migrar_datos.sh)
   FIN
   DESTINO_URL='postgresql://postgres.<POOLER_TENANT_ID>:<POSTGRES_PASSWORD>@supavisor:5432/postgres' sh /tmp/migrar.sh
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
   - agregue `DATABASE_URL` y `SUPABASE_NETWORK` con los valores del paso 2;
   - puede borrar `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`,
     `PGADMIN_DEFAULT_EMAIL` y `PGADMIN_DEFAULT_PASSWORD` (ya no se usan).
2. Una a `main` el cambio de este documento (o haga **Deploy** si ya está).
3. En **Domains**, borre el dominio de **pgadmin** (`pg.acentosdeco.lat`):
   ese servicio ya no existe.
4. Abra el frontend y compruebe que aparecen sus facturas, productos y
   proveedores. Si un servicio no arranca, revise sus logs: un error de
   conexión casi siempre es `SUPABASE_NETWORK`, el tenant o la contraseña de
   `DATABASE_URL`.

## 5. Ver y modificar los datos en Supabase Studio

1. Entre al dominio de Studio del paso 1 (`supa.acentosdeco.lat`) con
   `DASHBOARD_USERNAME` / `DASHBOARD_PASSWORD`.
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
- La base no necesita ningún puerto publicado en el servidor: la aplicación
  llega a `supavisor` por la red interna. Si la plantilla de Supabase publica
  5432/6543 en el host y el servidor tiene IP pública, ciérrelos con el
  firewall.
- Proteja Studio con una `DASHBOARD_PASSWORD` fuerte; quien entra ahí puede
  modificar todo.
