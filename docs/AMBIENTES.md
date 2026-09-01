# Ambientes de Facturación Electrónica (República Dominicana)

Este proyecto define tres ambientes de aplicación, cada uno mapeado al
ambiente correspondiente de e-CF de la DGII:

| Ambiente app (`APP_ENV`) | Rama Git   | Ambiente DGII | URL base DGII                     | Validez fiscal |
|--------------------------|-----------|----------------|------------------------------------|-----------------|
| `test`                   | `develop` | TesteCF        | https://ecf.dgii.gov.do/testecf    | No — pruebas del contribuyente |
| `qa`                     | `qa`      | CerteCF        | https://ecf.dgii.gov.do/certecf    | No — certificación formal ante la DGII |
| `production`             | `main`    | eCF            | https://ecf.dgii.gov.do/ecf        | Sí — comprobantes reales |

> Verificar siempre las URLs y rutas de los servicios web contra el Reporte
> Técnico e-CF vigente publicado por la DGII (https://dgii.gov.do), ya que
> pueden actualizarse entre versiones del estándar.

## Cómo se selecciona el ambiente

- La variable `APP_ENV` (`test` | `qa` | `production`) determina:
  - Qué archivo `.env.<ambiente>` carga `app/config.py`.
  - A qué ambiente de la DGII apunta `DGIIClient` (`app/core/environments.py`).
- Los archivos `.env.test`, `.env.qa` y `.env.production` en la raíz del
  repositorio contienen la configuración no sensible de cada ambiente.
  **Ninguno contiene credenciales reales**: `EMISOR_RNC`, `DGII_API_USER`,
  `DGII_API_PASSWORD`, `CERTIFICADO_PASSWORD` y las contraseñas de base de
  datos de QA/producción se inyectan como *secrets* desde GitHub Actions
  (ver más abajo) o el gestor de secretos del entorno de despliegue.

## Ejecutar localmente con Docker Compose

```bash
# Test (TesteCF)
docker compose -f docker-compose.yml -f docker-compose.test.yml up --build

# QA (CerteCF) — requiere POSTGRES_PASSWORD en el entorno
POSTGRES_PASSWORD=change-me docker compose -f docker-compose.yml -f docker-compose.qa.yml up --build -d

# Producción (eCF) — requiere POSTGRES_PASSWORD en el entorno
POSTGRES_PASSWORD=change-me docker compose -f docker-compose.yml -f docker-compose.prod.yml up --build -d
```

Verificar que el ambiente activo es el esperado:

```bash
curl http://localhost:8000/health
# {"status":"ok","app_env":"test","dgii_environment":"testecf"}
```

## CI/CD (GitHub Actions)

| Workflow                              | Dispara con              | Ambiente GitHub | Acción |
|----------------------------------------|---------------------------|------------------|--------|
| `.github/workflows/ci.yml`             | Push/PR a cualquier rama  | —                | Lint + tests + build de validación |
| `.github/workflows/deploy-test.yml`    | Push a `develop`          | `test`           | Build, push de imagen y deploy a Test |
| `.github/workflows/deploy-qa.yml`      | Push a `qa`               | `qa`             | Build, push de imagen y deploy a QA |
| `.github/workflows/deploy-prod.yml`    | Push a `main`             | `production`     | Build, push de imagen y deploy a Producción (requiere aprobación manual) |

### Configuración pendiente en GitHub (Settings → Environments)

Crear los ambientes `test`, `qa` y `production` en el repositorio y cargar,
para cada uno, los secretos correspondientes:

- `EMISOR_RNC`, `DGII_API_USER`, `DGII_API_PASSWORD`
- `CERTIFICADO_PASSWORD` (y el `.p12` del certificado, vía un mecanismo
  seguro — no como secreto de texto plano)
- `POSTGRES_PASSWORD` (QA y producción)
- Credenciales de despliegue (SSH, kubeconfig, etc.) una vez se defina la
  infraestructura destino

En `production` se recomienda activar **Required reviewers** para exigir
aprobación manual antes de desplegar contra el ambiente eCF real.

### Pasos de despliegue pendientes

Los workflows de `deploy-test.yml`, `deploy-qa.yml` y `deploy-prod.yml`
construyen y publican la imagen Docker en GHCR, pero el paso final de
despliegue (`Deploy al servidor de ...`) queda como placeholder hasta
definir la infraestructura destino de cada ambiente (servidor propio,
Kubernetes, Cloud Run, etc.).

## Flujo de ramas sugerido

```
feature/* → develop (Test/TesteCF) → qa (QA/CerteCF) → main (Producción/eCF)
```
