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

## Auditoría (PostgreSQL + PostgREST + pgAdmin)

Los módulos que envían algo a la DGII (`recepcion`, `recepcion_rfce`,
`aprobacion_comercial`, `anulacion_encf`) registran cada envío —best-effort,
nunca bloqueante— en `facturacion.envios_dgii` (Postgres), vía
`libs.ecf_common.db`. Ese esquema se expone de solo lectura a través de
**PostgREST**, y **pgAdmin** queda disponible para administrar la base
visualmente. Ver [`docs/DOKPLOY.md`](docs/DOKPLOY.md) para el despliegue
completo (incluye Postgres/PostgREST/pgAdmin) en Dokploy.

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
# Levanta los 11 módulos a la vez (puertos 8000-8010, ver docker-compose.test.yml)
docker compose -f docker-compose.yml -f docker-compose.test.yml up --build
```
