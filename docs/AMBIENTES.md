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
libs/ecf_common/       # Compartido por todos los módulos
services/
  autenticacion/        # Módulo 1: Autenticación (semilla, firma, token) — implementado
  recepcion/             # Módulo 2: Recepción de e-CF — pendiente
  ...                     # Resto de módulos del catálogo (ver SERVICIOS_DGII.md)
```

Cada módulo tiene su propio `app/`, `tests/` y `Dockerfile` (con build
context en la raíz del repo, para poder copiar `libs/`).

## Cómo se selecciona el ambiente

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

Verificar que el ambiente activo es el esperado (módulo Autenticación, puerto 8000):

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

Los workflows usan una matriz (`matrix.modulo`) que hoy solo incluye
`autenticacion`; al agregar un nuevo módulo (p. ej. `recepcion`) basta con
añadirlo a la lista `modulo: [...]` en los 4 workflows.

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

## Flujo de ramas sugerido

```
feature/* → develop (Test/TesteCF) → qa (QA/CerteCF) → main (Producción/eCF)
```
