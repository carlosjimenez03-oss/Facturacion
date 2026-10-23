# Facturacion-electronica

Sistema de facturación electrónica para República Dominicana (e-CF / DGII),
construido como emisor electrónico certificado: un conjunto de módulos
Python/FastAPI, uno por cada servicio web de la DGII, que se comunican por
API REST sobre TLS 1.2/1.3 e intercambian archivos XML.

## Servicios DGII y módulos

El catálogo completo de servicios de la DGII (URLs, endpoints, formatos de
entrada/salida) está documentado en
[`docs/dgii/SERVICIOS_DGII.md`](docs/dgii/SERVICIOS_DGII.md). Los 11 módulos
del catálogo están implementados:

| Módulo | Servicio DGII | Endpoint propio |
|---|---|---|
| `services/autenticacion` | Autenticación (semilla, firma, token) | `GET /token` |
| `services/recepcion` | Recepción de e-CF | `POST /ecf` |
| `services/recepcion_rfce` | Recepción de resumen factura de consumo (RFCE) | `POST /rfce` |
| `services/consulta_rfce` | Consulta de RFCE | `GET /rfce` |
| `services/consulta_resultado` | Consulta de resultado e-CF | `GET /resultado/{track_id}` |
| `services/consulta_estado` | Consulta de estado e-CF | `GET /estado` |
| `services/consulta_trackids` | Consulta de trackId e-CF | `GET /trackids` |
| `services/aprobacion_comercial` | Recepción de aprobación comercial | `POST /aprobacion` |
| `services/anulacion_encf` | Anulación de e-NCF | `POST /anulacion` |
| `services/consulta_directorio` | Consulta Directorio de Servicios | `GET /directorio`, `GET /directorio/{rnc}` |
| `services/consulta_timbre` | Consulta timbre (QR) e-CF y FC | `GET /timbre/*`, `GET /timbre-fc/*` |

Además, `services/facturas_api` genera, valida, firma (vista previa) y envía
e-CF construidos desde el `frontend/` — ver [Facturación (frontend + API)](#facturación-frontend--api).

Nota: el servicio "Comunicación Emisor-Receptor" (simulación de contraparte
en TesteCF) está documentado parcialmente en el PDF oficial recibido — su
especificación completa vive en un documento separado
("Descripción Técnica Servicios Emisores Electrónicos") que aún no se ha
compartido, por lo que no está implementado.

## Ambientes

El proyecto define tres ambientes, cada uno mapeado al ambiente
correspondiente de e-CF de la DGII (TesteCF / CerteCF / eCF). Ver el detalle
completo, variables de entorno, Docker Compose y pipelines de CI/CD en
[`docs/AMBIENTES.md`](docs/AMBIENTES.md).

## Estructura

```
libs/ecf_common/            # Compartido por todos los módulos
  auth/                       # Autenticación DGII reutilizable (semilla, firma, token, cache)
  environments.py              # Ambientes y catálogo de endpoints DGII
  settings.py, http_client.py, xml_tools.py

services/<modulo>/           # Uno por cada fila de la tabla de arriba
  app/                          # Código del servicio FastAPI (config, schemas, dgii_client, main)
  tests/                         # Tests del módulo
  Dockerfile

conftest.py                  # Fixtures de certificado de prueba, compartidas por todos los tests
```

Cada módulo firma sus propios XML (cuando aplica) y obtiene su propio token
de la DGII a través de `libs.ecf_common.auth.token_provider.TokenProvider`
— sin llamarse entre sí por HTTP.

## Base de datos (Supabase)

Los módulos que envían algo a la DGII (`recepcion`, `recepcion_rfce`,
`aprobacion_comercial`, `anulacion_encf`) registran cada envío —best-effort,
nunca bloqueante— en `facturacion.envios_dgii`, vía `libs.ecf_common.db`.
En producción la base es **Supabase** self-hosted en Dokploy, con **Supabase
Studio** para ver y editar los datos. Ver [`docs/SUPABASE.md`](docs/SUPABASE.md)
(instalación, migración y acceso) y [`docs/DOKPLOY.md`](docs/DOKPLOY.md).
En desarrollo local `docker-compose.yml` sigue levantando un Postgres propio.

## Desarrollo local

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt

cp .env.example .env.test  # o editar el .env.test ya presente
APP_ENV=test python -m uvicorn services.autenticacion.app.main:app --reload
# Repetir con el módulo que corresponda: services.recepcion.app.main:app, etc.
```

El certificado digital del emisor (`.p12`) va en `certs/` (ignorado por git;
nunca se commitea) y se referencia vía `CERTIFICADO_PATH`/`CERTIFICADO_PASSWORD`
en el `.env.<ambiente>` correspondiente.

## Tests

```bash
pytest --cov=libs --cov=services
```

## Docker

```bash
# Levanta los 12 módulos backend a la vez (puertos 8000-8011, ver docker-compose.test.yml)
docker compose -f docker-compose.yml -f docker-compose.test.yml up --build
```

`facturas-api` (puerto 8011) además requiere Postgres (`DATABASE_URL`) para
funcionar — ver la nota en [`docs/AMBIENTES.md`](docs/AMBIENTES.md).

## Facturación (frontend + API)

`frontend/` es una app React (Vite + TypeScript) que permite crear e-CF de
cualquiera de los 10 tipos del catálogo (31, 32, 33, 34, 41, 43, 44, 45, 46,
47) desde un formulario, validando la estructura contra los XSD oficiales de
la DGII antes de firmar y enviar.

```bash
cd frontend
npm install
npm run dev       # http://localhost:5173, apunta a VITE_API_URL (default http://localhost:8011)
```

Flujo de una factura: **borrador** (se guarda en Postgres) → **Generar XML**
(se arma y valida contra el XSD del tipo, `services/facturas_api/app/xml_builder.py`)
→ **Vista previa firmada** (opcional, firma local para revisión) →
**Enviar a la DGII** (el backend `facturas_api` delega la firma real y el
envío al módulo `recepcion`, que ya implementa ese flujo). El resultado
(`trackId`, estado, XML) queda guardado en `facturacion.facturas`.

La DGII valida el e-CF de forma asíncrona: el envío solo devuelve un
`trackId` y la factura queda en **enviado**. `facturas_api` le consulta el
resultado a `consulta_resultado` (`GET /resultado/{trackId}`) y la pasa a
**aceptado** (también "Aceptado Condicional") o **rechazado** (con los
mensajes de la DGII en `error` y `dgii_respuesta`; la respuesta original del
envío queda en `dgii_respuesta.envio`). Eso ocurre en tres momentos: una
vez justo después de enviar (best-effort, nunca hace fallar el envío), con
el botón **Actualizar estado DGII** del detalle de la factura
(`POST /facturas/{id}/actualizar-estado`) y en una tarea de fondo que cada
`SINCRONIZAR_ESTADO_CADA_SEGUNDOS` revisa hasta 50 facturas en `enviado` de
los últimos 30 días. Cada consulta queda auditada en
`facturacion.envios_dgii` (`modulo = 'consulta_resultado'`; la tarea de
fondo solo audita resultados definitivos, no cada "En Proceso").

| Variable de `facturas-api` | Default | Uso |
|---|---|---|
| `RECEPCION_SERVICE_URL` | `http://recepcion:8000` | Módulo que firma y envía el e-CF |
| `INVENTARIO_API_URL` | `http://inventario-api:8000` | Validar stock y registrar la salida de mercancía |
| `CONSULTA_RESULTADO_URL` | `http://consulta-resultado:8000` | Consultar el resultado de la DGII por `trackId` |
| `SINCRONIZAR_ESTADO_CADA_SEGUNDOS` | `120` | Intervalo de la tarea de fondo; `0` la desactiva |

Ver [`services/facturas_api/`](services/facturas_api) para el backend y
[`services/facturas_api/xsd/`](services/facturas_api/xsd) para los XSD
oficiales bundleados (uno de ellos, `eCF_31.xsd`, corrige un typo del
documento original de la DGII — ver el comentario en ese archivo).
