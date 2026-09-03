# Imagen propia de Postgres con el script de inicialización copiado dentro,
# para no depender de un bind-mount de subcarpeta del repo (frágil entre
# distintas plataformas de despliegue como Dokploy).
FROM postgres:16-alpine

COPY init/*.sql /docker-entrypoint-initdb.d/
