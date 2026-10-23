#!/bin/sh
# Copia los esquemas de la aplicación (facturacion, inventario, compras606)
# con todos sus datos desde el Postgres actual hacia el Postgres de Supabase,
# y verifica que la cantidad de filas coincida tabla por tabla.
#
# Pensado para correr DENTRO del contenedor `postgres` actual (Dokploy >
# recurso Compose > Open Terminal > contenedor postgres), que ya trae
# pg_dump y psql. Ver docs/SUPABASE.md, paso 3.
#
# Uso:
#   DESTINO_URL='postgresql://postgres.<tenant>:<clave>@<host>:5432/postgres' \
#     sh migrar_datos.sh
#
# Variables:
#   ORIGEN_URL   Postgres actual. Por defecto, el del propio contenedor
#                (usuario/base de POSTGRES_USER y POSTGRES_DB).
#   DESTINO_URL  Postgres de Supabase (obligatoria). Use el puerto 5432
#                (modo sesión) o la conexión directa, nunca 6543.
#   FORZAR=1     Continuar aunque los esquemas ya existan en el destino.
set -eu

ESQUEMAS="facturacion inventario compras606"
ORIGEN_URL="${ORIGEN_URL:-postgresql://${POSTGRES_USER:-facturacion}@localhost:5432/${POSTGRES_DB:-facturacion}}"
DESTINO_URL="${DESTINO_URL:?Defina DESTINO_URL con la cadena de conexión del Postgres de Supabase}"
DUMP="${TMPDIR:-/tmp}/migracion_supabase_$(date +%Y%m%d_%H%M%S).sql"

echo "==> Comprobando conexión con el origen"
psql "$ORIGEN_URL" -At -c "select 'origen: ' || version()"

echo "==> Comprobando conexión con Supabase"
psql "$DESTINO_URL" -At -c "select 'destino: ' || version()"

existentes=$(psql "$DESTINO_URL" -At -c "select string_agg(nspname, ', ') from pg_namespace where nspname in ('facturacion','inventario','compras606')")
if [ -n "$existentes" ] && [ "${FORZAR:-0}" != "1" ]; then
  echo "ERROR: el destino ya tiene los esquemas: $existentes" >&2
  echo "Si es un intento anterior incompleto, bórrelos en Supabase Studio (SQL Editor):" >&2
  echo "  DROP SCHEMA IF EXISTS facturacion, inventario, compras606 CASCADE;" >&2
  echo "o vuelva a correr con FORZAR=1 para continuar de todos modos." >&2
  exit 1
fi

args=""
for e in $ESQUEMAS; do
  if [ "$(psql "$ORIGEN_URL" -At -c "select count(*) from pg_namespace where nspname = '$e'")" = "1" ]; then
    args="$args -n $e"
  else
    echo "    (el origen no tiene el esquema $e, se omite)"
  fi
done
[ -n "$args" ] || { echo "ERROR: el origen no tiene ninguno de los esquemas esperados" >&2; exit 1; }

echo "==> Exportando:$args"
# --no-owner/--no-privileges: los dueños y permisos del origen (usuario
# facturacion, rol web_anon de PostgREST) no existen en Supabase.
# shellcheck disable=SC2086
pg_dump "$ORIGEN_URL" $args --no-owner --no-privileges --format=plain --file="$DUMP"
echo "    Respaldo guardado en $DUMP ($(wc -c < "$DUMP") bytes)"

echo "==> Importando en Supabase"
psql "$DESTINO_URL" -v ON_ERROR_STOP=1 --single-transaction -q -f "$DUMP" > /dev/null

echo "==> Verificando cantidad de filas"
consulta="select n.nspname || '.' || c.relname from pg_class c join pg_namespace n on n.oid = c.relnamespace
          where c.relkind = 'r' and n.nspname in ('facturacion','inventario','compras606') order by 1"
errores=0
for tabla in $(psql "$ORIGEN_URL" -At -c "$consulta"); do
  o=$(psql "$ORIGEN_URL" -At -c "select count(*) from $tabla")
  d=$(psql "$DESTINO_URL" -At -c "select count(*) from $tabla")
  if [ "$o" = "$d" ]; then marca="OK"; else marca="DIFERENTE"; errores=$((errores + 1)); fi
  printf '    %-40s origen=%-8s supabase=%-8s %s\n' "$tabla" "$o" "$d" "$marca"
done

if [ "$errores" -ne 0 ]; then
  echo "ERROR: $errores tabla(s) con diferente cantidad de filas. No cambie DATABASE_URL todavía." >&2
  exit 1
fi
echo "==> Migración completa. Ya puede configurar DATABASE_URL en Dokploy (docs/SUPABASE.md, paso 4)."
