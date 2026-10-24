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
  correo, rol `admin` | `usuario`). Un usuario **sin ninguna fila** (todos
  los de antes) es miembro implícito de la empresa 1 con el rol de su marca
  global (`kontara_admin` / `KONTARA_ADMIN_EMAILS`); al darle su primera
  membresía en otra empresa se escribe la implícita, para que no pierda la 1.
- `configuracion.usuario_empresa_actual`: la última empresa que eligió cada
  usuario (la que se abre si el navegador no tiene otra guardada).
- Todas las demás tablas de negocio (61) reciben `empresa_id integer NOT NULL`:
  `DEFAULT 1` al agregarla (las filas existentes quedan en la 1 sin
  reescribir la tabla) y luego `DEFAULT configuracion.empresa_actual()`.
  Política `empresa_aislada` con `FORCE ROW LEVEL SECURITY` (vale también
  para el dueño de las tablas):
  `USING / WITH CHECK (empresa_id = configuracion.empresa_actual() OR configuracion.todas_las_empresas())`.
- Siguen globales (sin `empresa_id`): `nomina.parametros` (tasas de ley del
  país), `soluciones.suscripcion` y `soluciones.solicitudes_plan` (el plan es
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
   corre al final de las suyas el DDL común
   (`libs/ecf_common/multiempresa_ddl.py`) con el advisory lock compartido
   7 000 021: crea el registro, la empresa 1 con sus datos de siempre, las
   funciones `configuracion.empresa_actual()` / `todas_las_empresas()`, el
   rol `kontara_app` y las ayudas `kontara_aislar` / `kontara_conceder`.
2. Luego aísla sus tablas (`SELECT configuracion.kontara_aislar(...)`):
   columna, índices únicos, RLS y política. Cada paso consulta el catálogo
   antes del `ALTER`: en una base ya migrada no toma locks de tabla.
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
  en cada `acquire`, fija `kontara.empresa_id` y — si el usuario de la base
  es superusuario o tiene `BYPASSRLS`, que saltan RLS — hace
  `SET ROLE kontara_app` (rol `NOLOGIN NOBYPASSRLS` con permisos sobre las
  tablas, que la migración crea y da al usuario de la base). Si la conexión
  no puede quedar aislada (sin el rol, sin permiso para crearlo, o el pooler
  de Supabase en **modo transacción, puerto 6543**, que no conserva
  variables de sesión) **solo se entregan conexiones para la empresa 1**:
  una instalación de una empresa sigue funcionando y una segunda falla
  cerrada (`AislamientoNoDisponibleError`, y "Agregar empresa" lo explica).
  Use el pooler en modo sesión (5432) o la conexión directa.
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
   usuario o la 1.
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

- Configuración → Usuarios muestra los miembros **de la empresa en uso**,
  invita o crea usuarios en ella (si el correo ya es de un usuario, lo
  agrega como miembro), cambia su rol y los quita de la empresa. Activar /
  desactivar (Supabase Auth) solo a miembros.
- "Agregar empresa" (Mis empresas, barra superior): un administrador de la
  empresa en uso verifica el RNC (registro local y directorio de emisores
  electrónicos de la DGII por `consulta_directorio`, best-effort), completa
  los datos fiscales y queda como administrador de la nueva.

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
  `REQUIRE_AUTH`; sin aislamiento la empresa 2 falla cerrada; tareas de
  fondo por empresa.
- `libs/ecf_common/tests/test_empresa.py`: contexto, cabeceras internas,
  certificado y caché de ambiente / token por empresa.
- `frontend/pruebas/posOffline.test.ts`: la cola del POS no cruza empresas.
