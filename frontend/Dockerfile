# Build multi-stage: compila los assets estáticos con Vite y los sirve con
# nginx. Build context: ./frontend (no depende de libs/ ni del resto del
# monorepo). Uso: docker build -f frontend/Dockerfile ./frontend
FROM node:20-alpine AS build
WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

# VITE_API_URL se hornea en el bundle en build time (Vite no lee variables
# de entorno en runtime) -- debe apuntar a la URL PÚBLICA de facturas-api
# (la que el navegador del usuario puede alcanzar), no al nombre interno del
# servicio Docker.
ARG VITE_API_URL=http://localhost:8011
ENV VITE_API_URL=${VITE_API_URL}

# Mismo mecanismo que VITE_API_URL, para el módulo de inventario
# (services/inventario_api).
ARG VITE_INVENTARIO_API_URL=http://localhost:8012
ENV VITE_INVENTARIO_API_URL=${VITE_INVENTARIO_API_URL}

# Mismo mecanismo que VITE_API_URL, para el módulo de Formato 606
# (services/formato_606).
ARG VITE_FORMATO_606_API_URL=http://localhost:8016
ENV VITE_FORMATO_606_API_URL=${VITE_FORMATO_606_API_URL}

# Mismo mecanismo que VITE_API_URL, para el ERP de ventas
# (services/erp_api). Default "/api-erp": el proxy de nginx.conf, igual que
# en docker-compose.dokploy.yml.
ARG VITE_ERP_API_URL=/api-erp
ENV VITE_ERP_API_URL=${VITE_ERP_API_URL}

# Identidad visual (opcional): nombre de la marca, de la empresa y RNC que
# muestran el menú lateral y la barra superior. Vacío = valores por defecto.
ARG VITE_MARCA=
ENV VITE_MARCA=${VITE_MARCA}
ARG VITE_EMPRESA_NOMBRE=
ENV VITE_EMPRESA_NOMBRE=${VITE_EMPRESA_NOMBRE}
ARG VITE_EMPRESA_RNC=
ENV VITE_EMPRESA_RNC=${VITE_EMPRESA_RNC}

# Inicio de sesión con Supabase Auth (ver docs/AUTH.md). VITE_SUPABASE_URL
# es la base de GoTrue para el navegador: "/" = el mismo dominio del
# frontend (nginx reenvía /auth/v1/ al gateway de Supabase), o la URL
# pública del gateway. VITE_SUPABASE_ANON_KEY es la ANON_KEY del recurso de
# Supabase (pública por diseño). Vacías = sin inicio de sesión (la
# aplicación muestra el aviso "Inicio de sesión no configurado").
ARG VITE_SUPABASE_URL=
ENV VITE_SUPABASE_URL=${VITE_SUPABASE_URL}
ARG VITE_SUPABASE_ANON_KEY=
ENV VITE_SUPABASE_ANON_KEY=${VITE_SUPABASE_ANON_KEY}
RUN npm run build

FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
# Genera al arrancar /etc/nginx/kontara/{sesion,supabase}.conf a partir de
# KONTARA_AUTH y SUPABASE_GATEWAY_URL (ver docs/AUTH.md).
COPY docker/40-kontara-sesion.sh /docker-entrypoint.d/40-kontara-sesion.sh
RUN chmod +x /docker-entrypoint.d/40-kontara-sesion.sh
ENV KONTARA_AUTH="" \
    SUPABASE_GATEWAY_URL=http://api-gw:8000

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -q -O- http://localhost:80/ || exit 1
