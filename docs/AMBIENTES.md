# Ambientes de Facturación Electrónica (República Dominicana)

Este proyecto define tres ambientes de aplicación, cada uno mapeado al
ambiente correspondiente de e-CF de la DGII:

| Ambiente app (`APP_ENV`) | Rama Git   | Ambiente DGII | Validez fiscal |
|--------------------------|-----------|----------------|-----------------|
| `test`                   | `develop` | TesteCF        | No — pruebas del contribuyente |
| `qa`                     | `qa`      | CerteCF        | No — certificación formal ante la DGII |
| `production`             | `main`    | eCF            | Sí — comprobantes reales |

El catálogo completo de servicios de la DGII (URLs, endpoints, formatos de
entrada/salida) está documentado en
[`docs/dgii/SERVICIOS_DGII.md`](dgii/SERVICIOS_DGII.md), extraído del
documento oficial *"Descripción Técnica Servicios DGII"*. Ante cualquier
duda, esa es la fuente de verdad — no este archivo.

## Estructura del proyecto (monorepo por módulos)

Cada servicio de la DGII se implementa como un módulo/servicio FastAPI
independiente bajo `services/`, y comparte infraestructura común (ambientes,
configuración, cliente HTTP con TLS forzado, utilidades XML) desde
`libs/ecf_common/`:

```
libs/ecf_common/       # Compartido por todos los módulos (incluye auth/: token DGII reutilizable)
services/
  autenticacion/         # Autenticación (semilla, firma, token)
  recepcion/              # Recepción de e-CF
  recepcion_rfce/          # Recepción de resumen factura de consumo (RFCE)
  consulta_rfce/            # Consulta de RFCE
  consulta_resultado/        # Consulta de resultado e-CF
  consulta_estado/            # Consulta de estado e-CF
  consulta_trackids/           # Consulta de trackId e-CF
  aprobacion_comercial/         # Recepción de aprobación comercial
  anulacion_encf/                # Anulación de e-NCF
  consulta_directorio/            # Consulta Directorio de Servicios
  consulta_timbre/                 # Consulta timbre (QR) e-CF y FC
  facturas_api/                     # Generación/gestión de e-CF (backend del frontend React)
```

Los 11 módulos del catálogo DGII más `facturas_api` están implementados. Ver
el detalle de cada uno (y el endpoint propio que expone) en la tabla de
[`README.md`](../README.md).

Cada módulo tiene su propio `app/`, `tests/` y `Dockerfile` (con build
context en la raíz del repo, para poder copiar `libs/`).

## Cómo se selecciona el ambiente

**Desde la fase 7 el ambiente DGII se cambia en Configuración → Ambiente
DGII** (solo administradores), sin tocar Dokploy. `APP_ENV` queda como valor
por defecto: mientras nadie lo cambie en Configuración, cada servicio usa el
suyo como siempre.

- **Dónde vive.** `configuracion.ambiente_dgii` (una fila, la crea erp-api:
  `services/erp_api/app/migraciones_configuracion.py`, `DDL_AMBIENTE`; copia
  en `db/init/018_fase7_fiscal.sql`) y su auditoría
  `configuracion.ambiente_dgii_cambios` (anterior, nuevo, usuario, fecha).
- **Quién lo lee.** facturas-api, recepcion, recepcion-rfce, las consultas
  (resultado, estado, trackIds, directorio, RFCE, timbre), anulacion-encf,
  aprobacion-comercial y autenticacion, en tiempo de ejecución
  (`libs/ecf_common/ambiente.py`): la fila manda sobre `APP_ENV`, con una
  caché de 15 s; sin fila, sin tabla, sin `DATABASE_URL` o con la base
  caída, `APP_ENV`. Por eso los servicios de la DGII necesitan
  `DATABASE_URL` (en `docker-compose.dokploy.yml` ya lo tienen para la
  auditoría). Cada servicio guarda un token de la DGII por ambiente.
  `GET /health` muestra el ambiente activo.
- **Facturas.** Cada factura guarda el ambiente activo al crearse
  (`facturacion.facturas.ambiente`) y se envía, se consulta y se timbra (QR
  de la RI) siempre en ese ambiente: facturas-api le pasa `ambiente` a
  `recepcion` (`POST /ecf`) y a `consulta_resultado` (`GET
  /resultado/{trackId}?ambiente=`). Una factura todavía no enviada de otro
  ambiente que el activo no se envía (409). `GET /facturas?ambiente=` filtra
  la lista (y la pantalla tiene el filtro).
- **Secuencias e-NCF por ambiente.** La DGII autoriza rangos distintos en
  TesteCF, CerteCF y eCF: `configuracion.secuencias` tiene clave
  `(ambiente, tipo_ecf)`, el siguiente número mira solo las facturas del
  mismo ambiente y el índice único del e-NCF es `(rnc_emisor, ambiente,
  encf)`. Las secuencias que ya existían quedan en el `APP_ENV` de la
  instalación. En Configuración → Secuencias autorizadas se elige el
  ambiente (por defecto el activo), para registrar las de producción antes
  de cambiar.
- **Reglas del cambio** (`PUT /configuracion/ambiente` de erp-api):
  solo un administrador (`kontara_admin` o `KONTARA_ADMIN_EMAILS`; sin
  inicio de sesión configurado, cualquiera, y la auditoría queda sin
  usuario); bloqueado mientras haya facturas en `contingencia` o lotes de
  contingencia abiertos (esos e-CF están firmados para el ambiente actual y
  deben llegar a él); a producción, solo con un certificado vigente del RNC
  de la empresa y confirmando en el diálogo que desde ese momento los
  comprobantes tienen validez fiscal. Si el ambiente nuevo no tiene
  secuencias, la respuesta lo advierte.

Lo que sigue es el comportamiento de `APP_ENV`, que sigue siendo el valor
por defecto:

- La variable `APP_ENV` (`test` | `qa` | `production`) determina:
  - Qué archivo `.env.<ambiente>` carga cada módulo (`libs/ecf_common/settings.py`).
  - A qué ambiente de la DGII apunta el módulo (`libs/ecf_common/environments.py`).
- Los archivos `.env.test`, `.env.qa` y `.env.production` en la raíz del
  repositorio contienen la configuración no sensible, compartida por todos
  los módulos. **Ninguno contiene credenciales reales**: `EMISOR_RNC` y
  `CERTIFICADO_PASSWORD` se inyectan como *secrets* desde GitHub Actions (ver
  más abajo) o el gestor de secretos del entorno de despliegue. El
  certificado `.p12` del emisor nunca se commitea ni se incluye en la imagen
  Docker: se monta como volumen (`./certs`) en tiempo de ejecución.

## Ejecutar localmente con Docker Compose

```bash
# Test (TesteCF)
docker compose -f docker-compose.yml -f docker-compose.test.yml up --build

# QA (CerteCF)
docker compose -f docker-compose.yml -f docker-compose.qa.yml up --build -d

# Producción (eCF)
docker compose -f docker-compose.yml -f docker-compose.prod.yml up --build -d
```

Cada módulo queda expuesto en un puerto distinto (ver `docker-compose.test.yml`):
`autenticacion`=8000, `recepcion`=8001, `recepcion-rfce`=8002,
`consulta-rfce`=8003, `consulta-resultado`=8004, `consulta-estado`=8005,
`consulta-trackids`=8006, `aprobacion-comercial`=8007, `anulacion-encf`=8008,
`consulta-directorio`=8009, `consulta-timbre`=8010, `facturas-api`=8011.

`facturas-api` además requiere Postgres (`DATABASE_URL`) para funcionar —
a diferencia de los demás módulos, donde la base de datos es solo para
auditoría opcional, aquí es donde viven las facturas. Para levantarlo
localmente hace falta un Postgres accesible (ver
[`docker-compose.dokploy.yml`](../docker-compose.dokploy.yml), que sí incluye
uno, o correr uno propio y setear `DATABASE_URL` en `.env.test`).

Verificar que el ambiente activo es el esperado:

```bash
curl http://localhost:8000/health
# {"status":"ok","app_env":"test","dgii_environment":"testecf"}
```

## CI/CD (GitHub Actions)

| Workflow                              | Dispara con              | Ambiente GitHub | Acción |
|----------------------------------------|---------------------------|------------------|--------|
| `.github/workflows/ci.yml`             | Push/PR a cualquier rama  | —                | Lint + tests + build de validación (matriz por módulo) |
| `.github/workflows/deploy-test.yml`    | Push a `develop`          | `test`           | Build, push de imagen y deploy a Test (matriz por módulo) |
| `.github/workflows/deploy-qa.yml`      | Push a `qa`               | `qa`             | Build, push de imagen y deploy a QA (matriz por módulo) |
| `.github/workflows/deploy-prod.yml`    | Push a `main`             | `production`     | Build, push de imagen y deploy a Producción (requiere aprobación manual) |

Los workflows usan una matriz (`matrix.modulo`) con los 12 módulos actuales
(los 11 del catálogo DGII + `facturas_api`); al agregar un módulo nuevo
basta con sumarlo a esa lista en los 4 workflows y en `docker-compose*.yml`.

### Configuración pendiente en GitHub (Settings → Environments)

Crear los ambientes `test`, `qa` y `production` en el repositorio y cargar,
para cada uno, los secretos correspondientes:

- `EMISOR_RNC`
- `CERTIFICADO_PASSWORD` (y el `.p12` del certificado, vía un mecanismo
  seguro — no como secreto de texto plano; se monta como volumen)
- Credenciales de despliegue (SSH, kubeconfig, etc.) una vez se defina la
  infraestructura destino

En `production` se recomienda activar **Required reviewers** para exigir
aprobación manual antes de desplegar contra el ambiente eCF real.

### Pasos de despliegue pendientes

Los workflows de `deploy-test.yml`, `deploy-qa.yml` y `deploy-prod.yml`
construyen y publican la imagen Docker de cada módulo en GHCR, pero el paso
final de despliegue (`Deploy al servidor de ...`) queda como placeholder
hasta definir la infraestructura destino de cada ambiente (servidor propio,
Kubernetes, Cloud Run, etc.).

### Despliegue real: Dokploy

El despliegue efectivo del proyecto se hace en [Dokploy](https://dokploy.com)
con un archivo separado y autocontenido,
[`docker-compose.dokploy.yml`](../docker-compose.dokploy.yml) (los 11
módulos; la base de datos es Supabase, ver [`docs/SUPABASE.md`](SUPABASE.md)).
Ver la guía completa en [`docs/DOKPLOY.md`](DOKPLOY.md).

## Flujo de ramas sugerido

```
feature/* → develop (Test/TesteCF) → qa (QA/CerteCF) → main (Producción/eCF)
```
