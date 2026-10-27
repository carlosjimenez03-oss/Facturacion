# Varias empresas en una instalación (Mis empresas)

Una instalación de Kontara administra varias empresas (emisores, cada una
con su RNC). Cada empresa tiene sus propios clientes, productos, facturas,
compras, bandeja, bancos, contabilidad, impuestos, nómina, POS, portal y
configuración (certificado digital, secuencias, ambiente DGII, WhatsApp y
correo, pagos en línea, método de inventario, nómina). Un usuario entra a
las empresas de las que es miembro y cambia entre ellas desde la barra
superior.

Una instalación de **una sola empresa sigue igual que antes**: no hay
variables nuevas obligatorias, la empresa de siempre es la 1 y todo lo que
ya existía queda en ella.

## Modelo de datos: `empresa_id` en cada tabla + RLS

Decisión: **cada tabla de negocio tiene una columna `empresa_id` y una
política de Row Level Security (RLS) de Postgres** que solo deja ver,
insertar y cambiar las filas de la empresa de la conexión.

Alternativas descartadas:

| Opción | Por qué no |
| --- | --- |
| `empresa_id` / `rnc_emisor` + `WHERE` en cada consulta | ~700 consultas SQL a mano en 30 repositorios: una sola que se olvide filtra datos de otra empresa, y nada lo detecta. |
| Una base (o un esquema) por empresa | Las consultas usan nombres calificados (`ventas.clientes`), las migraciones se multiplican, Supabase no deja crear bases desde la aplicación y Planes / Mis empresas necesitan ver todas. |
| Solo `rnc_emisor` | Muchas tablas no tienen RNC (asientos, existencias, empleados...) y el RNC de una empresa puede cambiar; un entero estable es más simple. |

Con RLS ninguna consulta existente tuvo que cambiar para no devolver filas
ajenas, y una consulta nueva que olvide filtrar **tampoco puede**: el filtro
lo pone Postgres.

### Tablas

- `configuracion.empresas`: id, RNC (único), razón social, nombre
  comercial, actividad, dirección, provincia, municipio, teléfono, correo,
  logo, `estado` (`activa` / `inactiva`), `configurada`. La **empresa 1**
  se crea con los datos de `configuracion.empresa` (la fila única de antes,
  que se sigue escribiendo para la 1 por si hay que volver a una versión
  anterior) o con `EMISOR_RNC` / `EMISOR_RAZON_SOCIAL`; las nuevas empiezan
  en 2.
- `configuracion.empresa_usuarios`: (empresa, usuario de Supabase Auth,
  correo, rol `admin` | `usuario`, `estado` `activo` | `inactivo` |
  `invitado` | `retirado`). Las filas no se borran (quitar a alguien lo deja
  `retirado`). Solo entra quien tiene una fila `activo` en una empresa
  `activa`.
- **Usuarios de antes de la multiempresa** (`configuracion.multiempresa_legado`,
  una fila): al migrar, si la base ve `auth.users` (Supabase), cada usuario
  con acceso a Kontara (`app_metadata.kontara` o en `KONTARA_ADMIN_EMAILS`)
  recibe su fila en la empresa 1, admin si tiene `kontara_admin` o está en
  `KONTARA_ADMIN_EMAILS`; si así no queda **ningún** admin, todos quedan
  admin (como antes: sin administradores configurados, todos lo eran). Si la
  base no ve `auth.users`, la fila se escribe en el **primer inicio de
  sesión** de cada uno (`/auth/verificar`) y `previos_admin` recuerda si la
  instalación no tenía administradores configurados (entonces quedan
  admin). Hasta entonces, un usuario **sin ninguna fila** es miembro de la
  empresa 1 con ese mismo rol. Esa regla nunca alcanza a un usuario nuevo:
  todo usuario que crea o agrega un administrador recibe su fila **antes**
  de la marca de acceso, y la acción de otra empresa nunca escribe filas en
  la 1.
- `configuracion.usuario_empresa_actual`: la última empresa que eligió cada
  usuario (la que se abre si el navegador no tiene otra guardada).
- Todas las demás tablas de negocio (61) reciben `empresa_id integer NOT NULL`:
  `DEFAULT 1` al agregarla (las filas existentes quedan en la 1 sin
  reescribir la tabla) y luego `DEFAULT configuracion.empresa_actual()`.
  Política `empresa_aislada` con `FORCE ROW LEVEL SECURITY` (vale también
  para el dueño de las tablas):
  `USING / WITH CHECK (empresa_id = configuracion.empresa_actual())` — sin
  `OR`: Postgres la usa como condición de los índices `(empresa_id, ...)`.
  Las lecturas de todas las empresas (Planes, tareas de fondo, búsquedas
  sin empresa) van con otro rol, `kontara_todas`, y su propia política
  `empresa_todas FOR SELECT TO kontara_todas USING (true)`: solo lee.
- Siguen globales (sin `empresa_id`): `nomina.parametros` (tasas de ley del
  país), `configuracion.padron_rnc` y su estado (el listado público de RNC
  de la DGII, ver PADRON_RNC.md), `soluciones.suscripcion` y `soluciones.solicitudes_plan` (el plan es
  de la instalación: Planes cuenta los e-CF de todas las empresas y los
  desglosa por empresa), `inventario.schema_version`.

### Claves que pasan a ser por empresa

Cada índice único o clave natural se rehace con `empresa_id` delante (mismo
nombre): secuencias e-NCF `(empresa_id, ambiente, tipo_ecf)`, SKU y código de
barras, nombre de almacén y almacén principal, correlativos de ajustes,
transferencias, cotizaciones y asientos, catálogo de cuentas y cuentas por
defecto, RNC de cliente, cédula de empleado, períodos de nómina, 606
(`rnc_cedula, ncf`), anulaciones 608, ajustes y declaraciones de
impuestos, e-CF recibidos (`rnc_emisor, encf`), papel (NCF), tareas
automáticas, automatizaciones y envíos de Kontara Ventas, caja abierta del
POS, filas únicas (contingencia, ambiente DGII, datos de nómina, portal,
Kontara Ventas). La unicidad del e-NCF ya era por emisor
(`rnc_emisor, ambiente, encf`). Siguen únicos globales los identificadores
que se buscan sin saber la empresa: hash del token del portal, referencia y
orden de los pagos en línea, clave de idempotencia del POS (UUID).

Los correlativos legibles (COT-0001, A-0001...) salen de secuencias de
Postgres compartidas: en cada empresa son únicos pero no consecutivos.

## Migración de una instalación existente

Automática, al arrancar la versión nueva (no hay pasos manuales):

1. Cada servicio con migraciones (facturas-api, inventario-api, erp-api)
   toma **primero** el advisory lock compartido 7 000 021 (antes del suyo y
   de cualquier lock de tabla: todos en el mismo orden, sin deadlocks), con
   `lock_timeout` de 5 s y reintentos con espera creciente si una tabla está
   ocupada. Al final de sus migraciones corre el DDL común
   (`libs/ecf_common/multiempresa_ddl.py`): crea el registro, la empresa 1
   con sus datos de siempre, los usuarios de antes, la función
   `configuracion.empresa_actual()`, los roles `kontara_app` y
   `kontara_todas` y las ayudas `kontara_aislar` / `kontara_conceder`.
2. Luego aísla sus tablas (`SELECT configuracion.kontara_aislar(...)`):
   columna, índices únicos, RLS y política. Cada paso consulta el catálogo
   antes del `ALTER`: en una base ya migrada no toma locks de tabla (el DDL
   de facturas-api también: lo comprueba una prueba con una venta en curso).
   inventario-api lo anota como versión 21 de `inventario.schema_version`.
3. `db/init/021_multiempresa.sql` repite exactamente el mismo DDL para el
   Postgres local (lo verifican las pruebas de migraciones).

Cero pérdida de datos: no se borra ni se reescribe ninguna fila; todas quedan
en la empresa 1 (`ADD COLUMN ... DEFAULT 1`). El e2e lo comprueba contando las
filas de las 66 tablas antes y después.

El `ADD COLUMN` toma un `AccessExclusiveLock` breve por tabla la primera
vez (sin reescribirla). Como con cualquier despliegue con migraciones,
conviene hacerlo con poco tráfico.

**Volver atrás**: la versión anterior no conoce `empresa_id` ni RLS; sus
`ON CONFLICT (...)` apuntan a claves que ya cambiaron (secuencias,
preferencias, contingencia...) y, si la base tiene más de una empresa y se
conecta como superusuario, vería las filas de todas. Haga un respaldo antes
de actualizar (como en cualquier despliegue con migraciones) y, si tiene que
volver, restáurelo.

## Empresa de cada conexión

`libs/ecf_common/empresa.py`:

- Un `ContextVar` con la empresa del request o tarea (`en_empresa(id)`,
  `todas_las_empresas()` para búsquedas sin empresa, `modo_sistema()` para
  migraciones). Las tareas de `asyncio` copian el contexto.
- El pool de asyncpg (`libs/ecf_common/db.crear_pool`) tiene un `setup` que,
  en cada `acquire` y en una sola consulta, fija `kontara.empresa_id` y el
  rol: si el usuario de la base es superusuario o tiene `BYPASSRLS` (que
  saltan RLS), `SET ROLE kontara_app` (rol `NOLOGIN NOBYPASSRLS` con
  permisos sobre las tablas); para las lecturas de todas,
  `SET ROLE kontara_todas` (solo `SELECT`). La migración crea los dos roles
  y se los da al usuario de la base.
- **Postgres 16** separa ser miembro de un rol de poder hacer `SET ROLE`: a
  quien crea un rol con `CREATEROLE` (el usuario de Supabase) solo le queda
  `ADMIN`. La migración comprueba el privilegio **SET** (`pg_has_role(...,
  'SET')`; en Postgres 15, la membresía) y, si falta, lo pide con
  `GRANT kontara_app TO CURRENT_USER WITH INHERIT FALSE, SET TRUE` (en 15,
  un `GRANT` simple). El `setup` también comprueba SET, no la membresía.
- **Modo "solo la empresa 1"**: si la conexión no puede quedar aislada (sin
  los roles, sin el privilegio SET, un `SET ROLE` que igual falla — se
  registra una vez en el log y el pool sigue sin cambiar de rol —, o el
  pooler de Supabase en **modo transacción, puerto 6543**) solo se entregan
  conexiones para la empresa 1 y para las lecturas de todas: una instalación
  de una empresa sigue funcionando completa (arranques, Planes, inventario)
  y otra empresa falla cerrada con un **503** `sin_aislamiento`
  (`AislamientoNoDisponibleError`; "Agregar empresa" lo explica). Use el
  pooler en modo sesión (5432) o la conexión directa.
- **Puerto 6543**: el pooler reparte las conexiones del servidor entre
  clientes por transacción, así que una variable de sesión o un `SET ROLE`
  quedaría puesto para el próximo cliente. Ahí el `setup` **no ejecuta
  nada** (ni `set_config` ni `SET ROLE`): las políticas ven la empresa 1 y
  cualquier otra se niega.
- **El dueño de las tablas sin `BYPASSRLS`** (ni superusuario) queda sujeto a
  las políticas (`FORCE ROW LEVEL SECURITY`): sus conexiones de empresa
  quedan aisladas sin cambiar de rol, pero las de sistema (migraciones)
  solo ven la empresa 1 — suficiente para las migraciones de hoy, que no
  leen datos de otras empresas.
- Al arrancar, erp-api, facturas-api e inventario-api escriben en el log en
  qué modo quedó (`diagnosticar`): aislado, sin aislamiento (ERROR si la base
  ya tiene más de una empresa), modo transacción, o dueño sujeto a RLS.
- Tareas de fondo (sincronización de estados DGII, contingencia, reverso de
  inventario, envío automático por correo, Kontara Ventas) dan una vuelta
  por empresa activa.

## Empresa de cada request

1. El frontend manda `X-Kontara-Empresa: <id>` en cada llamada
   (`fetchConSesion`, `frontend/src/lib/empresaActual.ts`) y la cookie
   `kontara_empresa` para los enlaces que abre el navegador (PDF, XML).
2. nginx (`auth_request`) le pregunta a erp-api `/auth/verificar`, que
   valida la sesión **y que el usuario sea miembro de esa empresa**: 204 con
   `X-Kontara-Empresa` = la verificada, o **403** `sin_empresa`. nginx
   reenvía a las APIs la verificada (`auth_request_set` +
   `proxy_set_header X-Kontara-Empresa $kontara_empresa`) en lugar de la del
   navegador. Sin sesión exigida (`KONTARA_AUTH=off`) no reenvía ninguna y
   todo va a la empresa predeterminada.
3. Cada servicio (`instalar_empresa`): con `REQUIRE_AUTH` vuelve a validar la
   membresía del usuario del token (no confía en la cabecera); las llamadas
   internas con `X-Kontara-Interno` pasan la empresa explícita
   (`cabeceras_internas` la agrega sola); sin sesión verificada en el
   servicio usa la que puso nginx. Sin cabecera: la última elegida del
   usuario (si sigue siendo miembro activo de una empresa activa), otra de
   sus empresas activas o la 1. La cookie `kontara_empresa` solo se mira con
   un usuario verificado en el servicio (que se valida como miembro): sin
   inicio de sesión se ignora.
   Las membresías y el registro se cachean 30 s en cada servicio: quitar a
   un usuario de una empresa corta su acceso a ella en ese plazo (como el
   cierre de sesión, ver AUTH.md).
4. El rol del usuario en la empresa reemplaza a `admin` en
   `request.state.usuario` (la marca global queda en `admin_global`): un
   administrador de una empresa no lo es de las demás.
5. El portal público y el retorno de la pasarela de pagos no llevan sesión:
   entran a la empresa del enlace (hash del token) o del pago (referencia).
6. Los servicios DGII (recepción, consultas, aprobación, anulación) reciben
   la empresa de facturas-api / erp-api y usan su certificado, su ambiente,
   su RNC y su token. El receptor electrónico (público, la DGII y otros
   contribuyentes) elige la empresa por el **RNC del comprador** del e-CF.

## Configuración por empresa

- **Certificado .p12**: la empresa 1 sigue en `CERTIFICADO_ACTIVO_PATH`
  (`activo.json`, y sin él `CERTIFICADO_PATH`); cada empresa nueva en
  `empresas/<id>.json` del mismo volumen (debe poder escribirse, como hoy).
  Una empresa nueva sin certificado **no** firma con el de las variables
  (es de otro RNC).
- **Token de la DGII**: uno por (empresa, ambiente).
- **Ambiente DGII, secuencias, WhatsApp y correo, pagos en línea, método de
  inventario, datos de nómina, portal, Kontara Ventas**: filas por empresa
  (RLS); el ambiente se cachea por empresa.
- `EMISOR_RNC` / `EMISOR_RAZON_SOCIAL` / `CERTIFICADO_PATH` solo describen
  a la empresa 1.
- Una empresa nueva trae su catálogo de cuentas sugerido; el almacén
  principal y demás filas únicas se crean al primer uso.

## Usuarios

- Configuración → Usuarios muestra los miembros **de la empresa en uso**
  (los "sin acceso" de Supabase Auth, solo en la empresa 1), crea o invita
  usuarios en ella, cambia su rol y los quita de la empresa.
- **Un correo que ya es de Kontara** (de otra empresa) no se agrega en
  silencio: queda **invitado** y entra cuando acepta en Mis empresas
  (Aceptar / Rechazar). Hasta entonces la empresa que invita no lo puede
  activar ni desactivar, solo retirar la invitación.
- **Desactivar es por empresa** (`estado = 'inactivo'`): el usuario sigue en
  sus otras empresas. Solo si no es de ninguna otra se desactiva también en
  Supabase Auth (cierra todas sus sesiones); activar levanta ese bloqueo en
  el mismo caso. Un administrador de una empresa nunca bloquea ni
  desbloquea a alguien que también es de otra.
- **Administrador de la instalación** (no de una empresa): `kontara_admin` /
  `KONTARA_ADMIN_EMAILS` o, si la instalación no tenía administradores
  configurados (`previos_admin`), un administrador de la empresa 1. Solo él
  agrega empresas y cambia los parámetros de nómina (tasas de la TSS y
  escala del ISR, de todas las empresas: `PUT/DELETE /nomina/parametros`).
- "Agregar empresa" (Mis empresas, barra superior): el administrador de la
  instalación verifica el RNC (registro local, padrón de RNC de la DGII
  —razón social, nombre comercial, actividad, estado y régimen, ver
  PADRON_RNC.md— y, con certificado digital, el directorio de emisores
  electrónicos por `consulta_directorio`, best-effort), completa los datos
  fiscales y queda como administrador de la nueva.

## Navegador

- La empresa se recuerda por usuario (`localStorage kontara.empresa.<id>`) y
  en el servidor. Cada pestaña fija la suya al cargar; cambiar de empresa
  recarga desde el inicio. Un 403 `sin_empresa` olvida la elección.
- Lo guardado en el navegador por empresa usa `claveEmpresa` (la 1 conserva
  las claves de siempre): POS sin conexión (base de IndexedDB
  `kontara-pos` / `kontara-pos.e<id>`), emisor y vencimientos recordados,
  avisos leídos, primeros pasos. Cada venta sin conexión guarda su empresa y
  solo se sincroniza en ella; al cambiar de empresa con ventas pendientes se
  avisa.
- Sin inicio de sesión en el build no se ofrece cambiar ni agregar empresas
  (nginx no reenvía ninguna). En desarrollo, con Vite directo a las APIs,
  `VITE_MULTIEMPRESA_SIN_SESION=true` lo habilita.

## Variables

Ninguna nueva obligatoria. Opcionales:

| Variable | Servicio | Uso |
| --- | --- | --- |
| `EMISOR_RAZON_SOCIAL` | facturas-api, inventario-api (ya existía en erp-api) | Nombre de la empresa 1 si Configuración → Empresa no tiene datos al migrar. |
| `CONSULTA_RNC_TIMEOUT_SEGUNDOS` | erp-api | Espera de la consulta del RNC al agregar una empresa (8 s). |
| `VITE_MULTIEMPRESA_SIN_SESION` | frontend (build) | Solo desarrollo: cambiar de empresa sin inicio de sesión. |

## Pruebas

- `services/erp_api/tests/test_multiempresa_postgres.py` (Postgres real,
  `OPERACION_TEST_DATABASE_URL`): con datos en la empresa 1, **todos** los
  GET de erp-api, facturas-api, inventario-api y formato-606 pedidos como la
  empresa 2 no devuelven nada de la 1; claves naturales por empresa; WITH
  CHECK; portal público; membresías (403) en `/auth/verificar` y con
  `REQUIRE_AUTH`; sin aislamiento la empresa 2 falla cerrada y la 1 sigue
  (arranques, Planes); rol sin SET (Postgres 16); índices `(empresa_id, ...)`
  con `EXPLAIN`; cada GET de la empresa 2 devuelve exactamente lo mismo
  antes y después de cargar los datos de la 1 (y de que la 1 llene sus
  cachés); membresías por empresa, invitaciones, desactivar por empresa,
  administrador de la instalación, usuarios de antes (`auth.users`);
  migraciones de un arranque sin esperar por tablas en uso; tareas de fondo
  por empresa. Corre también con un usuario como el de Supabase
  (`CREATEROLE BYPASSRLS`, no superusuario) en Postgres 16.
- `libs/ecf_common/tests/test_empresa.py`: contexto, cabeceras internas,
  certificado y caché de ambiente / token por empresa; `SET ROLE` negado
  (modo empresa 1), puerto 6543 sin nada en la conexión, cookie ignorada sin
  sesión.
- `frontend/pruebas/posOffline.test.ts`: la cola del POS no cruza empresas.
