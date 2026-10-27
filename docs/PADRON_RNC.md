# Padrón de RNC de la DGII

Kontara llena los datos de un contribuyente (razón social, nombre comercial,
actividad económica, estado y régimen de pago) a partir del **listado público
de RNC de la DGII**, que la DGII publica a diario como `DGII_RNC.zip`
(dgii.gov.do → Herramientas → Consultas → RNC → "Descargar listado").

Se usa en:

- **Agregar empresa** (Mis empresas): "Verificar RNC" llena razón social,
  nombre comercial y actividad económica y muestra estado (aviso si no está
  ACTIVO) y régimen. El padrón **no trae** dirección, teléfono, correo ni
  provincia: la pantalla lo dice. Saber si el RNC es **emisor electrónico**
  sigue saliendo del directorio de e-CF de la DGII (`consulta_directorio`),
  que pide el token de la DGII de la empresa en uso: sin certificado digital
  esa parte no se consulta y se explica por qué, en lugar del "La consulta a
  la DGII respondió 500" de antes.
- **Contactos**: "Verificar" llena el nombre y muestra el estado (badge);
  el panel "Estado DGII" de un contacto guardado sale del padrón solo.
- **Factura** (comprador): "Verificar" busca primero en Contactos; si no
  está, el nombre sale del padrón. Si el comprador está suspendido o dado de
  baja, se avisa bajo la razón social.

## El archivo

La DGII no publica una especificación del formato. Lo que sigue sale de
proyectos que lo descargan y procesan (consultados el 27/10/2026; desde este
entorno dgii.gov.do responde 403 a las descargas automáticas, así que no se
pudo revisar un archivo real):

- **URL**: `https://dgii.gov.do/app/WebApps/Consultas/RNC/DGII_RNC.zip`
  (con o sin `www.`). Configurable con `DGII_PADRON_URL`.
  Fuentes: [dgii-ts](https://github.com/franklinmdev/dgii-ts) (`src/bulk/types.ts`,
  `DGII_BULK_URL`, paquete npm `dgii-ts@0.1.3`) y
  [mcp-rnc-dgii](https://github.com/iaconsultingrd/mcp-rnc-dgii) (`server.py`, `URL_DGII`).
- **Contenido**: un TXT (`DGII_RNC.TXT`, a veces dentro de una carpeta
  `TMP/`), sin encabezado, una línea por contribuyente, separado por `|`, en
  **Latin-1 / Windows-1252**, fin de línea CRLF. ~800 000 líneas (~23 MB
  comprimido según mcp-rnc-dgii; ~37 % son cédulas).
- **Columnas** (11; dgii-ts `src/bulk/parser.ts`, `EXPECTED_COLUMNS = 11`, y
  [RNC_DGII_DB](https://github.com/Ruben890/RNC_DGII_DB) `src/agregar_datos.py`, `len(row) == 11`):

  | # | Campo |
  |---|---|
  | 0 | RNC (9 dígitos) o cédula (11) |
  | 1 | Razón social / nombre |
  | 2 | Nombre comercial |
  | 3 | Actividad económica |
  | 4–7 | Sin uso (vacías o espacios) |
  | 8 | Fecha de inicio de operaciones (persona física) o de constitución (jurídica), `dd/mm/aaaa`; `00/00/0000` = sin fecha |
  | 9 | Estado: `ACTIVO`, `SUSPENDIDO`, `DADO DE BAJA`, `CESE TEMPORAL`, `ANULADO`, `RECHAZADO` (dgii-ts `DGII_ESTADOS`; la Comunidad de Ayuda de la DGII menciona Activo, Suspendido, Cese Temporal y Dado de baja) |
  | 10 | Régimen de pago: `NORMAL`, `RST`, … |

- **Uso**: el listado incluye cédulas (datos personales); mcp-rnc-dgii
  advierte que los términos de la DGII permiten el uso no comercial sin
  redistribución. Kontara solo lo usa para consultar un número puntual
  dentro de la instalación y no lo expone en bloque.

### Lector tolerante (`services/erp_api/app/padron_archivo.py`)

Como el formato puede cambiar sin aviso:

- Sin encabezado o con uno (se descarta: su "RNC" no tiene 9/11 dígitos).
- RNC con guiones o espacios (se guardan solo los dígitos); se descartan
  las líneas cuyo número no tiene 9 u 11 dígitos o que no traen nombre.
- **El estado se busca por su valor**, de derecha a izquierda, en las
  columnas 4 en adelante: así funcionan el diseño de 11 columnas, uno de 9
  (dos columnas sin uso) y uno con `|` sobrantes al final. El régimen es el
  primer valor después del estado y la fecha, la última fecha antes. Con un
  estado desconocido se usan las posiciones 8/9/10.
- Comillas sueltas, NUL, espacios repetidos, líneas vacías, CRLF, BOM.
- Codificación **por línea**: UTF-8 si lo es, si no Windows-1252, y si trae
  bytes que cp1252 no define, Latin-1 (nunca falla).
- Fechas `dd/mm/aaaa`, `d/m/aaaa`, `dd-mm-aaaa`, `aaaa-mm-dd`; las imposibles
  quedan vacías. Campos de más de 250 caracteres se recortan.
- **Comprobación de formato**: si las primeras 2 000 líneas casi no tienen
  contribuyentes, o al final las descartadas superan a las válidas, o no hay
  ninguna, el archivo se rechaza ("¿cambió el formato del archivo de la DGII?").

Seguridad (el archivo viene de internet o lo sube un usuario):

- Todo en **streaming**: el ZIP se descarga a un archivo temporal con tope
  (`PADRON_MAX_MB`, 200), el TXT se lee línea a línea (tope de 8 KB por
  línea; una más larga se salta) y se copia a Postgres de a 5 000 filas.
  La memoria del proceso no crece con el tamaño del archivo (medido: RSS
  constante de 146 MB durante una importación de 800 000 líneas).
- **Bomba de ZIP**: se rechaza si el TXT declara más de
  `PADRON_MAX_DESCOMPRIMIDO_MB` (1 500), si comprime más de 100 a 1 (un TXT
  real comprime 5–8 a 1), y se cuentan los bytes realmente leídos con el
  mismo tope (también para un TXT subido sin comprimir). ZIP cifrado, dañado
  o cortado: error claro.
- Se elige el `.txt`/`.csv` más grande del ZIP (ignora otros archivos).
- El archivo subido se guarda en una carpeta temporal propia que se borra al
  terminar; su nombre solo se usa para mostrarlo.

## Base de datos

`db/init/022_padron_rnc.sql` = `DDL_PADRON` de `services/erp_api/app/migraciones.py`.

```sql
configuracion.padron_rnc (rnc text PRIMARY KEY, razon_social, nombre_comercial,
                          actividad, estado, regimen, fecha_inicio date, actualizado_en)
configuracion.padron_rnc_estado (id = 1, importado_en, filas, origen, nuevas,
                                 cambiadas, eliminadas, duracion_segundos,
                                 intentado_en, error, error_en)
```

- **Global**: no tiene `empresa_id` ni RLS (como `nomina.parametros`): es el
  mismo listado público para todas las empresas. `kontara_conceder` le da
  los permisos a `kontara_app` (las consultas de cada empresa) y SELECT a
  `kontara_todas`, como al resto del esquema `configuracion`; no se
  menciona en `kontara_aislar`, así que nunca recibe la política
  `empresa_aislada`. No hay `GRANT ... TO CURRENT_USER` (ver
  `libs/ecf_common/tests/test_ddl_supabase.py`).
- **Importación en una transacción** (`padron.importar`):
  1. `pg_try_advisory_lock(7007022)` en una conexión del pool (modo sistema,
     el dueño de las tablas). Si otra réplica lo tiene: "Ya se está
     actualizando" (409 en los botones; la tarea diaria lo salta). El lock
     se suelta siempre (también si falla).
  2. Anota `intentado_en`, descarga (o toma el archivo subido).
  3. `BEGIN` → `CREATE TEMP TABLE padron_carga … ON COMMIT DROP` → `COPY`
     (asyncpg `copy_records_to_table` con un iterador asíncrono; la lectura
     del ZIP corre en un hilo, de a lotes) → si el archivo trae menos de la
     mitad de los contribuyentes actuales (con más de 1 000), se rechaza por
     incompleto → `INSERT … ON CONFLICT DO UPDATE … WHERE (…) IS DISTINCT
     FROM (…)` (solo toca lo que cambió; el RNC repetido: gana la última
     línea) → `DELETE` de los que ya no están → estado → `COMMIT`.
  4. Quien consulta mientras tanto ve el padrón anterior **completo** (MVCC:
     no hay `TRUNCATE` ni cambio de tablas, ningún lock que bloquee lecturas).
     Si algo falla, `ROLLBACK`: queda el anterior y se anota `error`/`error_en`
     (el último bueno conserva `importado_en`, `filas` y `origen`).
- **Tamaño** (medido con 800 001 filas en `supabase/postgres:17.6.1.136`):
  99 MB de tabla + 24 MB de índice = **123 MB**. Una importación que cambia
  todas las filas deja la tabla en ~247 MB (filas muertas que autovacuum
  libera y se reutilizan); las diarias normales cambian pocas filas. WAL por
  importación ≈ lo que cambió (la primera, ~el tamaño de la tabla).
- **Tiempos** (misma prueba): primera carga de 800 001 filas 30,6 s;
  re-importación con 2 000 altas, 8 000 cambios y 4 000 bajas 26,2 s (casi
  todo es leer y copiar el archivo). Consulta por RNC: 0,1 ms (clave primaria).

## Actualización

- **Automática** (erp-api, `padron.bucle_actualizacion`): la primera vez a
  los `PADRON_PRIMERA_ESPERA_SEGUNDOS` (60) de arrancar si nunca se importó,
  y luego revisa cada 15 minutos si el último bueno tiene
  `PADRON_CADA_HORAS` (24). Tras un error reintenta cada hora. Todas las
  réplicas revisan, una sola importa (advisory lock).
- **Manual** (Configuración → Padrón de RNC de la DGII): última importación
  (fecha, contribuyentes, cambios, origen, último error) y, para el
  **administrador de la instalación** (`KONTARA_ADMIN_EMAILS` / `kontara_admin`;
  el padrón es de todas las empresas, así que un administrador de una sola
  no puede reemplazarlo): "Actualizar ahora" y "Subir DGII_RNC.zip" para
  servidores que no llegan a dgii.gov.do (descargarlo en otro equipo y
  subirlo; también acepta el TXT). La importación corre en segundo plano y
  la pantalla consulta el estado cada 3 s.
- nginx: `location = /api-erp/padron/archivo` con `client_max_body_size 210m`
  y sin buffer (el resto de la app sigue con 10m).

## API (erp-api)

| Método y ruta | Qué hace |
|---|---|
| `GET /padron/{rnc}` | RNC (9) o cédula (11), con o sin guiones → `rnc, razon_social, nombre_comercial, actividad, estado, regimen, fecha_inicio, activo, fuente: "padron", padron_actualizado_en`. **404** "El RNC … no aparece en el padrón de RNC de la DGII (actualizado el dd/mm/aaaa)". **503** si nunca se importó: "El listado de RNC de la DGII aún se está descargando…" (descarga automática activa o en curso) o cómo subirlo. **422** número inválido |
| `GET /padron/estado` | Última importación, último error, `en_curso` (el advisory lock está tomado), URL, `puede_actualizar` |
| `POST /padron/actualizar` | 202: descarga e importa en segundo plano (administrador de la instalación; 409 si ya está en curso) |
| `POST /padron/archivo` | 202: el cuerpo es el ZIP (o TXT) tal cual, sin multipart; `X-Nombre-Archivo` opcional. 413 sobre `PADRON_MAX_MB`, 422 si no es un ZIP ni un TXT con `|` |
| `GET /empresas/consulta-rnc/{rnc}` | Además de `registrada` y el directorio de e-CF, trae `padron` y `padron_detalle` |

Contrato del frontend: `frontend/src/lib/padronApi.ts`; presentación
(badge, avisos) en `frontend/src/lib/padronUtil.ts`.

## Variables (erp-api)

| Variable | Default | Uso |
|---|---|---|
| `DGII_PADRON_URL` | `https://dgii.gov.do/app/WebApps/Consultas/RNC/DGII_RNC.zip` | De dónde se descarga |
| `PADRON_ACTIVO` | `true` | `false` apaga la descarga automática (se puede subir a mano) |
| `PADRON_CADA_HORAS` | `24` | Cada cuánto se vuelve a descargar |
| `PADRON_PRIMERA_ESPERA_SEGUNDOS` | `60` | Espera tras arrancar antes de la primera revisión |
| `PADRON_TIMEOUT_SEGUNDOS` | `300` | Espera de la descarga (lectura) |
| `PADRON_MAX_MB` | `200` | Tope del ZIP descargado o subido |
| `PADRON_MAX_DESCOMPRIMIDO_MB` | `1500` | Tope del TXT descomprimido |

## Pruebas

- `services/erp_api/tests/test_padron_archivo.py`: líneas de ejemplo (11 y
  9 columnas, `|` sobrantes, comillas, cédula con guiones, CRLF, NUL, Ñ en
  cp1252/UTF-8/Latin-1, encabezado), fechas, ZIP con otros archivos, TXT
  suelto con BOM, línea gigante, formato cambiado, bomba de ZIP, tamaño
  declarado y real, ZIP dañado/cortado/vacío, streaming.
- `services/erp_api/tests/test_padron_postgres.py` (Postgres real): tablas
  globales sin RLS con SELECT para los roles; altas/cambios/bajas;
  **atomicidad** (con la transacción detenida antes del COMMIT, otra
  conexión y la API ven el padrón anterior y otra importación recibe 409);
  advisory lock de otra réplica; un fallo (ZIP dañado, archivo incompleto,
  formato que falla al final del COPY, vacío) conserva el anterior y anota
  el error; descarga desde un servidor local (403, página HTML, tope); la
  tarea diaria; rutas (503/404/422/200, cualquier empresa ve lo mismo),
  `consulta-rnc` con y sin certificado, subida (413/422/403).
- `frontend/pruebas/padron.test.ts`: badge y aviso por estado, régimen, fechas.
- Corridas también contra `supabase/postgres:17.6.1.136` como el rol
  `postgres` (no superusuario, `BYPASSRLS`): `db/init` completo, erp-api con
  la descarga automática de un ZIP de 800 001 líneas y la subida de otro
  por la API, sin ninguna caída del servidor (`terminated by signal`: 0).
