#!/bin/sh
# Genera, al arrancar el contenedor (la imagen oficial de nginx corre todo
# /docker-entrypoint.d/*.sh antes de nginx), los dos archivos que incluye
# nginx.conf. Ver docs/AUTH.md.
#
#   KONTARA_AUTH          no vacía = exigir sesión en /api* (auth_request a
#                         erp-api). docker-compose.dokploy.yml la enciende sola
#                         cuando existe SUPABASE_JWT_SECRET.
#   SUPABASE_GATEWAY_URL  gateway del recurso de Supabase por la red interna
#                         (por defecto http://api-gw:8000), para /auth/v1/.
#   KONTARA_NGINX_DIR     carpeta de salida (por defecto /etc/nginx/kontara;
#                         las pruebas locales usan otra).
set -eu

DIR="${KONTARA_NGINX_DIR:-/etc/nginx/kontara}"
GATEWAY="${SUPABASE_GATEWAY_URL:-http://api-gw:8000}"
mkdir -p "$DIR"

case "$GATEWAY" in
  http://*|https://*) ;;
  *) echo "40-kontara-sesion: SUPABASE_GATEWAY_URL inválida: $GATEWAY" >&2; exit 1 ;;
esac
# Sin barra final: proxy_pass con variable y sin URI reenvía /auth/v1/... tal cual.
GATEWAY="${GATEWAY%/}"
printf 'set $supabase_gateway %s;\n' "$GATEWAY" > "$DIR/supabase.conf"

if [ -n "${KONTARA_AUTH:-}" ] && [ "${KONTARA_AUTH}" != "off" ] && [ "${KONTARA_AUTH}" != "false" ]; then
  cat > "$DIR/sesion.conf" <<'EOF'
# Generado por 40-kontara-sesion.sh: sesión EXIGIDA (KONTARA_AUTH).
auth_request /_kontara/verificar;
error_page 401 = @kontara_sin_sesion;
EOF
  echo "40-kontara-sesion: inicio de sesión EXIGIDO en /api*"
else
  echo "# Generado por 40-kontara-sesion.sh: sin KONTARA_AUTH, /api* queda abierto." > "$DIR/sesion.conf"
  echo "40-kontara-sesion: AVISO: inicio de sesión NO configurado, /api* abierto (ver docs/AUTH.md)"
fi
