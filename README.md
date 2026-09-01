# Facturacion-electronica

Servicio de facturación electrónica para República Dominicana (e-CF / DGII),
construido con Python y FastAPI.

## Ambientes

El proyecto define tres ambientes, cada uno mapeado al ambiente
correspondiente de e-CF de la DGII (TesteCF / CerteCF / eCF). Ver el detalle
completo, variables de entorno, Docker Compose y pipelines de CI/CD en
[`docs/AMBIENTES.md`](docs/AMBIENTES.md).

## Desarrollo local

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt

cp .env.example .env.test  # o editar el .env.test ya presente
APP_ENV=test uvicorn app.main:app --reload
```

## Tests

```bash
pytest --cov=app
```

## Docker

```bash
docker compose -f docker-compose.yml -f docker-compose.test.yml up --build
```
