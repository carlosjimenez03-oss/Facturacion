#!/bin/sh
# Genera, al arrancar el contenedor (la imagen oficial de nginx corre todo
# /docker-entrypoint.d/*.sh antes de nginx), los archivos que incluye
# nginx.conf. Ver docs/AUTH.md.
#
#   KONTARA_AUTH          on = exigir sesión en /api* (auth_request a erp-api).
#                         docker-compose.dokploy.yml la enciende sola cuando
#                         existe SUPABASE_JWT_SECRET. off = abierto A PROPÓSITO
#                         (rollback). Vacía: abierto solo si el frontend se
#                         compiló SIN inicio de sesión; si se compiló CON él
#                         (marca KONTARA_MARCA_LOGIN), /api* responde 503:
#                         falla cerrado en vez de quedar abierto con una
#                         pantalla de login de adorno (p. ej. falta o está mal
#                         escrito SUPABASE_JWT_SECRET en Dokploy).
#   KONTARA_MARCA_LOGIN   archivo que deja el build (Dockerfile) cuando
#                         VITE_SUPABASE_URL y VITE_SUPABASE_ANON_KEY tienen
#                         valor (por defecto /etc/kontara/login-configurado).
#   SUPABASE_GATEWAY_URL  gateway del recurso de Supabase por la red interna
#                         (por defecto http://api-gw:8000), para /auth/v1/.
#   KONTARA_PROXY_CONFIABLE  IPs/redes (separadas por espacio o coma) de las
#                         que se cree la cabecera CF-Connecting-IP (IP real
#                         del cliente para el límite de intentos de login).
#                         Por defecto loopback + redes privadas de Docker.
#   KONTARA_NGINX_DIR     carpeta de salida (por defecto /etc/nginx/kontara;
#                         las pruebas locales usan otra).
set -eu

DIR="${KONTARA_NGINX_DIR:-/etc/nginx/kontara}"
GATEWAY="${SUPABASE_GATEWAY_URL:-http://api-gw:8000}"
MARCA="${KONTARA_MARCA_LOGIN:-/etc/kontara/login-configurado}"
CONFIABLES="${KONTARA_PROXY_CONFIABLE:-127.0.0.1 ::1 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16}"
AUTH="${KONTARA_AUTH:-}"
mkdir -p "$DIR"

case "$GATEWAY" in
  http://*|https://*) ;;
  *) echo "40-kontara-sesion: SUPABASE_GATEWAY_URL inválida: $GATEWAY" >&2; exit 1 ;;
esac
# Sin barra final: proxy_pass con variable reenvía $uri tal cual (/auth/v1/...).
GATEWAY="${GATEWAY%/}"
printf 'set $supabase_gateway %s;\n' "$GATEWAY" > "$DIR/supabase.conf"

# IP real (CF-Connecting-IP) solo desde proxies de confianza.
{
  echo "# Generado por 40-kontara-sesion.sh (KONTARA_PROXY_CONFIABLE)."
  echo "real_ip_header CF-Connecting-IP;"
  for red in $(echo "$CONFIABLES" | tr ',' ' '); do
    case "$red" in
      *[!0-9A-Fa-f.:/]*) echo "40-kontara-sesion: KONTARA_PROXY_CONFIABLE inválida: $red" >&2; exit 1 ;;
    esac
    echo "set_real_ip_from $red;"
  done
} > "$DIR/ip_real.conf"

case "$AUTH" in
  off|false|OFF|False|FALSE)
    echo "# Generado por 40-kontara-sesion.sh: KONTARA_AUTH=off, /api* abierto a propósito." > "$DIR/sesion.conf"
    echo "40-kontara-sesion: AVISO: KONTARA_AUTH=off, /api* ABIERTO sin inicio de sesión (rollback, ver docs/AUTH.md)"
    ;;
  "")
    if [ -f "$MARCA" ]; then
      cat > "$DIR/sesion.conf" <<'EOF'
# Generado por 40-kontara-sesion.sh: el frontend tiene inicio de sesión pero
# KONTARA_AUTH está vacía (¿falta SUPABASE_JWT_SECRET?): falla CERRADO.
default_type application/json;
add_header Cache-Control "no-store" always;
return 503 '{"detail":"Inicio de sesión mal configurado: falta SUPABASE_JWT_SECRET en el servidor (ver docs/AUTH.md). Las APIs quedan cerradas hasta corregirlo."}';
EOF
      echo "40-kontara-sesion: ERROR: el frontend se compiló con inicio de sesión (VITE_SUPABASE_*) pero KONTARA_AUTH está vacía" >&2
      echo "40-kontara-sesion: ERROR: ¿falta o está mal escrito SUPABASE_JWT_SECRET? /api* responde 503 (cerrado) hasta corregirlo;" >&2
      echo "40-kontara-sesion: ERROR: para abrirlo a propósito ponga KONTARA_AUTH=off (ver docs/AUTH.md)" >&2
    else
      echo "# Generado por 40-kontara-sesion.sh: sin inicio de sesión configurado, /api* queda abierto." > "$DIR/sesion.conf"
      echo "40-kontara-sesion: AVISO: inicio de sesión NO configurado, /api* abierto (ver docs/AUTH.md)"
    fi
    ;;
  *)
    cat > "$DIR/sesion.conf" <<'EOF'
# Generado por 40-kontara-sesion.sh: sesión EXIGIDA (KONTARA_AUTH).
auth_request /_kontara/verificar;
auth_request_set $kontara_motivo $upstream_http_x_kontara_motivo;
error_page 401 = @kontara_sin_sesion;
EOF
    echo "40-kontara-sesion: inicio de sesión EXIGIDO en /api*"
    ;;
esac
