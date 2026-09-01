# Facturacion-electronica

Sistema de facturación electrónica para República Dominicana (e-CF / DGII),
construido como emisor electrónico certificado: un conjunto de módulos
Python/FastAPI, uno por cada servicio web de la DGII, que se comunican por
API REST sobre TLS 1.2/1.3 e intercambian archivos XML.

## Servicios DGII y módulos

El catálogo completo de servicios de la DGII (URLs, endpoints, formatos de
entrada/salida) está documentado en
[`docs/dgii/SERVICIOS_DGII.md`](docs/dgii/SERVICIOS_DGII.md).

| Módulo | Servicio DGII | Estado |
|---|---|---|
| `services/autenticacion` | Autenticación (semilla, firma, token) | ✅ Implementado |
| `services/recepcion` | Recepción de e-CF | ⏳ Pendiente |
| `services/recepcion_rfce` | Recepción de resumen factura de consumo (RFCE) | ⏳ Pendiente |
| — | Consulta de RFCE, resultado, estado, trackId | ⏳ Pendiente |
| — | Aprobación comercial, anulación de e-NCF | ⏳ Pendiente |
| — | Consulta directorio, timbre (QR), timbre FC (QR) | ⏳ Pendiente |

## Ambientes

El proyecto define tres ambientes, cada uno mapeado al ambiente
correspondiente de e-CF de la DGII (TesteCF / CerteCF / eCF). Ver el detalle
completo, variables de entorno, Docker Compose y pipelines de CI/CD en
[`docs/AMBIENTES.md`](docs/AMBIENTES.md).

## Estructura

```
libs/ecf_common/        # Compartido: ambientes DGII, settings, cliente TLS, utilidades XML
services/autenticacion/  # Módulo 1 (ver tabla arriba)
  app/                    # Código del servicio FastAPI
  tests/                  # Tests del módulo
  Dockerfile
```

## Desarrollo local

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt

cp .env.example .env.test  # o editar el .env.test ya presente
APP_ENV=test python -m uvicorn services.autenticacion.app.main:app --reload
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
docker compose -f docker-compose.yml -f docker-compose.test.yml up --build
```
