# Inicio de sesión, puertos y plataforma (fase 6)

Hasta la fase 5 la aplicación y **todas** sus APIs estaban abiertas: cualquiera
que abriera `https://front.acentosdeco.lat` (o el IP del servidor y un puerto
80xx) podía ver y emitir facturas. Desde la fase 6:

- El frontend pide **inicio de sesión con Supabase Auth** (GoTrue, el que ya
  viene en el recurso de Supabase de Dokploy).
- nginx (contenedor `frontend`) solo reenvía `/api`, `/api-inventario`,
  `/api-606` y `/api-erp` si la petición trae una sesión válida
  (`auth_request` a `erp-api`, `GET /auth/verificar`).
- Los puertos de los servicios se publican solo en `127.0.0.1` del servidor.
- Configuración (empresa, certificado, ambiente, secuencias, inventario,
  correo/WhatsApp, vendedores, usuarios) y "Estado de las soluciones".

Mientras no configure las variables de abajo **nada cambia**: la aplicación
sigue abierta y muestra el aviso amarillo *"Inicio de sesión no
configurado"*. Así el despliegue de esta versión no deja a nadie fuera.

## Cómo funciona

```
Navegador ── https://front.acentosdeco.lat ── Cloudflare Tunnel ── 127.0.0.1:8013 (nginx del frontend)
   │                                                                  │
   │ POST /auth/v1/token (correo + contraseña, apikey = ANON_KEY)     ├─ /auth/v1/*  → api-gw:8000 (gateway de Supabase, red `supabase`)
   │ ← access_token (JWT HS256, 1 h) + refresh_token                  │
   │                                                                  ├─ /api*       → auth_request → erp-api /auth/verificar (204/401)
   │ GET /api-erp/... Authorization: Bearer <access_token>            │                 → facturas-api / inventario-api / formato-606 / erp-api
   │                                                                  ├─ /api-erp/portal/publico/* → erp-api (PÚBLICO: portal de clientes, lo protege su token)
   │                                                                  └─ /fe/*       → receptores (PÚBLICO, sin sesión)
```

- El token lo firma GoTrue con el `JWT_SECRET` del recurso de Supabase. erp-api
  lo valida con el mismo secreto (`SUPABASE_JWT_SECRET`): firma HS256,
  vencimiento (`exp`), `aud = authenticated` y `role = authenticated` (la
  `ANON_KEY` y la `SERVICE_ROLE_KEY` también son JWT de ese secreto, pero no
  son sesiones y se rechazan). Ver `libs/ecf_common/auth/sesion_supabase.py`.
- El frontend renueva el token un minuto antes de que venza
  (`grant_type=refresh_token`), y si una API responde 401 lo renueva una vez y
  reintenta. Si no puede, vuelve a `/login`. Ver `frontend/src/lib/sesion.ts`.
- Los enlaces que abre el navegador sin `fetch` (PDF de una factura, XML de la
  bandeja) llevan el mismo token en la cookie `kontara_sesion`
  (`SameSite=Strict`, `Secure`), que nginx también acepta.
- Las llamadas **internas** entre servicios (facturas-api → inventario-api,
  erp-api → aprobacion-comercial, erp-api → facturas-api…) no pasan por nginx
  y no llevan token: siguen funcionando.
- Los servicios del Receptor Electrónico (`/fe/autenticacion/...`,
  `/fe/recepcion/...`, `/fe/aprobacioncomercial/...`) los llaman la DGII y los
  proveedores: **nunca** piden sesión.
- El portal de clientes (Soluciones) se abre sin sesión: la página `/p/<token>`
  del frontend llama `/api-erp/portal/publico/<token>/...`, que nginx reenvía
  sin `auth_request` (location propia) y erp-api con `REQUIRE_AUTH=true` deja
  pasar. El resto de `/api-erp/portal/*` sí pide sesión.

## Pasos en Dokploy y Supabase (en este orden)

### 1. Copie los valores del recurso de Supabase

Dokploy → recurso **Supabase** → pestaña **Environment**. Necesita:

| Variable en Supabase | Para qué |
|---|---|
| `JWT_SECRET` | `SUPABASE_JWT_SECRET` de la facturación |
| `ANON_KEY` | `VITE_SUPABASE_ANON_KEY` (es pública por diseño: va en el navegador) |
| `SERVICE_ROLE_KEY` | `SUPABASE_SERVICE_ROLE_KEY` (secreta: **solo** erp-api) |

En esa misma pestaña de Supabase revise también:

- `DISABLE_SIGNUP=true` (que nadie pueda registrarse solo; los usuarios los
  crea usted).
- `SITE_URL=https://front.acentosdeco.lat` y
  `ADDITIONAL_REDIRECT_URLS=https://front.acentosdeco.lat/login` (a donde
  vuelven los enlaces de "¿Olvidó su contraseña?" e invitación).
- `API_EXTERNAL_URL` es la URL que llevan esos correos (p. ej.
  `https://supa.acentosdeco.lat`); `/auth/v1/verify` no pide la contraseña de
  Studio, así que funciona por ese dominio.
- Para enviar correos de recuperación/invitación, GoTrue necesita SMTP
  (`SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`, `SMTP_ADMIN_EMAIL`,
  `ENABLE_EMAIL_AUTOCONFIRM=false`). Sin SMTP, cree los usuarios con
  contraseña (paso 2) y cámbielas desde Seguridad.

Si cambió algo, **Deploy** del recurso de Supabase.

### 2. Cree el primer usuario

Supabase Studio (`https://supa.acentosdeco.lat`) → **Authentication → Users →
Add user → Create new user**: correo y contraseña (8+ caracteres), marque
**Auto Confirm User**. Hágalo **antes** del paso 3 o se quedará fuera.

Los siguientes usuarios se pueden crear desde la propia aplicación
(Configuración → Usuarios) una vez configurado `SUPABASE_SERVICE_ROLE_KEY`.

### 3. Variables de la facturación

Dokploy → recurso Compose de la facturación → **Environment**, agregue:

```env
# Inicio de sesión (las tres juntas)
VITE_SUPABASE_URL=/
VITE_SUPABASE_ANON_KEY=<ANON_KEY de Supabase>
SUPABASE_JWT_SECRET=<JWT_SECRET de Supabase>

# Opcional: Configuración → Usuarios desde la aplicación
SUPABASE_SERVICE_ROLE_KEY=<SERVICE_ROLE_KEY de Supabase>

# Opcional: cargar el certificado .p12 y guardar contraseñas SMTP/WhatsApp
# desde Configuración. Texto largo al azar (p. ej. `openssl rand -base64 48`).
# Si lo cambia después, el certificado cargado y las contraseñas guardadas
# dejan de poder leerse (se vuelve a usar certs/emisor.p12).
CERTIFICADOS_CLAVE=<texto largo al azar>
```

- `VITE_SUPABASE_URL=/` = mismo dominio: nginx reenvía `/auth/v1/` al gateway
  de Supabase (`SUPABASE_GATEWAY_URL`, por defecto `http://api-gw:8000`) por
  la red interna `supabase`, a la que ahora también se une el `frontend`. Si
  su gateway se llama distinto (`docker ps` en el servidor), agregue
  `SUPABASE_GATEWAY_URL=http://<nombre>:8000`. También puede poner la URL
  pública (`https://supa.acentosdeco.lat`): el gateway permite CORS desde
  cualquier origen; la opción `/` evita depender de ese dominio.
- `VITE_*` se hornean en el build: hace falta **Deploy** (rebuild), no solo
  restart.
- Con `SUPABASE_JWT_SECRET` presente, el compose enciende solo el
  `auth_request` de nginx (`KONTARA_AUTH=on`). Las tres variables van juntas:
  con el secreto pero sin las `VITE_*`, las APIs piden sesión y el frontend no
  tiene cómo iniciarla.

**Deploy.** Abra `https://front.acentosdeco.lat`: debe pedir el inicio de
sesión. Compruebe desde fuera:

```bash
curl -i https://front.acentosdeco.lat/api-erp/configuracion/empresa   # 401 {"detail":"Inicie sesión para continuar"}
curl -i https://front.acentosdeco.lat/fe/autenticacion/api/semilla    # 200 (receptor público)
curl -i https://front.acentosdeco.lat/api-erp/portal/publico/xxx      # 404 (portal público: sin 401)
```

### 4. Defensa en profundidad (opcional): `REQUIRE_AUTH`

`REQUIRE_AUTH=true` hace que facturas-api, inventario-api, formato-606 y
erp-api **también** exijan el token por su cuenta (no solo nginx), para quien
llegue a ellos sin pasar por nginx. Está apagado por defecto porque las
llamadas internas entre servicios no llevan token: con `REQUIRE_AUTH=true` en
`inventario-api`, facturas-api ya no podría validar el stock; y erp-api no
podría bajar el PDF para el envío automático por correo. Con los puertos en
`127.0.0.1` (abajo) el riesgo que cubre es pequeño.

## Puertos (`127.0.0.1`)

Antes cada servicio publicaba su puerto en `0.0.0.0` (`"8012:8000"`): quien
llegara al IP del servidor entraba directo a facturas-api saltándose nginx.
Ahora todos se publican como `"127.0.0.1:80xx:8000"`: solo los ve el propio
servidor (el Cloudflare Tunnel, `cloudflared`, corre en el host y usa
`localhost`, así que sigue funcionando igual).

Lo que debe revisar:

- **Rutas del túnel:** deben apuntar a `http://localhost:<puerto>` (p. ej.
  `front.acentosdeco.lat → http://localhost:8013`). Si `cloudflared` corre en
  un contenedor **sin** `network_mode: host`, `localhost` no es el servidor:
  apunte la ruta al nombre del contenedor en una red compartida o use
  `network_mode: host`.
- **Receptor electrónico** (8015, 8016, 8017): debe ser alcanzable por la DGII
  y los proveedores. Tres opciones:
  1. Por el mismo dominio del frontend: nginx ya reenvía
     `/fe/autenticacion/`, `/fe/recepcion/` y `/fe/aprobacioncomercial/` a los
     receptores, sin sesión. URL a registrar en la DGII:
     `https://front.acentosdeco.lat` (+ la ruta estándar `/fe/...`).
  2. Por un hostname propio en el túnel (`receptor.acentosdeco.lat →
     http://localhost:8016`, etc.): funciona igual con `127.0.0.1`.
  3. Si los expone por **IP:puerto directo**, ponga
     `RECEPTOR_IP_PUBLICACION=0.0.0.0` (solo esos tres vuelven a `0.0.0.0`).
  Si los publicó con un **Domain de Dokploy** (Traefik), no usan los puertos
  del host sino la red de Docker: no les afecta.
- `ss -ltnp | grep -E ':80(0[1-9]|1[0-9])'` en el servidor debe mostrar
  `127.0.0.1:80xx` (salvo el receptor si usó la opción 3).

## Certificado digital (Configuración → Certificado digital)

Antes el `.p12` solo se cambiaba subiéndolo por SSH a `./certs/emisor.p12`
(montado de solo lectura) y cambiando `CERTIFICADO_PASSWORD`. Ahora también
se puede cargar desde la aplicación:

1. erp-api valida el archivo: contraseña correcta, llave RSA, **no vencido** y
   que pertenezca al RNC del emisor (`EMISOR_RNC`). Si algo falla, el
   certificado anterior sigue en uso.
2. Lo guarda en el volumen Docker `certificados` (`/app/certs-activos/
   activo.json`): el `.p12` en base64 y su contraseña **cifrada** con una
   llave derivada de `CERTIFICADOS_CLAVE` (que solo vive en las variables de
   entorno). Se escribe en un temporal y se cambia con `os.replace`
   (atómico). erp-api lo monta con escritura; los demás servicios, de solo
   lectura.
3. Todos los servicios que firman (`cargar_certificado_p12`) prefieren ese
   certificado; si no existe o no se puede abrir (sin volumen, sin
   `CERTIFICADOS_CLAVE`, clave cambiada), usan `certs/emisor.p12` +
   `CERTIFICADO_PASSWORD` como siempre. El proveedor de tokens de la DGII se
   recarga solo al cambiar el archivo.

La contraseña nunca se devuelve ni se registra. `GET /configuracion/
certificado` (erp-api) y `GET /certificado` (facturas-api) devuelven titular,
firmante, emisor, vigencia y `dias_para_vencer` (para los recordatorios a 60,
30 y 7 días que arma el módulo de tareas).

Para volver al certificado del servidor: borre `activo.json` del volumen
(`docker run --rm -v <proyecto>_certificados:/v alpine rm /v/activo.json`) o
quite `CERTIFICADOS_CLAVE`.

## Ambiente DGII

Se muestra el de los servicios (`APP_ENV`). No se cambia desde la pantalla:
todos los servicios deben cambiarlo a la vez, así que el selector explica los
pasos (Dokploy → Environment → `APP_ENV=test|qa|production` → Deploy).

## Correo y WhatsApp (Configuración → WhatsApp y correo)

- **Correo:** SMTP de la empresa (servidor, puerto, STARTTLS/SSL, usuario,
  contraseña cifrada con `CERTIFICADOS_CLAVE`), remitente y CCO. "Enviar
  correo de prueba" lo manda al usuario con sesión. Con *Enviar al ser
  aceptada por la DGII* encendido, erp-api revisa cada 5 minutos
  (`ENVIO_AUTOMATICO_CADA_SEGUNDOS`, 0 = apagado) las facturas **aceptadas**
  de los últimos 30 días con correo del comprador y les envía el PDF (y el XML
  firmado si se pide), una sola vez por factura (`configuracion.envios`).
- **WhatsApp Business:** Meta WhatsApp Cloud API (Phone number ID + token de
  acceso permanente). Sin credenciales queda *No conectado*. Con ellas se
  puede mandar un mensaje de prueba (plantilla aprobada, por defecto
  `hello_world`). El envío automático de cada factura por WhatsApp **todavía no
  está implementado** (requiere una plantilla con documento aprobada por
  Meta).

## Usuarios

Configuración → Usuarios lista, crea (con contraseña) o invita (sin
contraseña, requiere SMTP en Supabase) y desactiva/activa usuarios con la API
admin de GoTrue (`/auth/v1/admin/users`), que erp-api llama por la red interna
con `SUPABASE_SERVICE_ROLE_KEY`. Nadie puede desactivarse a sí mismo. Todos
los usuarios con sesión pueden administrar usuarios (no hay roles todavía).
Sin la llave, la pantalla explica cómo hacerlo en Studio.

## Revertir (rollback)

- **Apagar el inicio de sesión sin redeploy del código:** en Dokploy ponga
  `KONTARA_AUTH=off` (nginx deja de pedir sesión) y borre
  `VITE_SUPABASE_URL` / `VITE_SUPABASE_ANON_KEY` → **Deploy** (el frontend
  vuelve a abrir sin login, con el aviso amarillo). Quitar
  `SUPABASE_JWT_SECRET` tiene el mismo efecto sobre nginx.
- **Puertos:** si algo dejó de verse tras pasar a `127.0.0.1`, revise la ruta
  del túnel (arriba). Para volver atrás un servicio, quite `127.0.0.1:` de su
  `ports` en `docker-compose.dokploy.yml`.
- **Código anterior:** volver a desplegar el commit previo a la fase 6. Las
  tablas nuevas (`configuracion.*`, la columna `facturacion.facturas.vendedor_id`)
  no molestan al código anterior.

## Variables nuevas (resumen)

| Variable | Servicio | Por defecto | Qué hace |
|---|---|---|---|
| `VITE_SUPABASE_URL` | frontend (build) | vacía | Base de GoTrue para el navegador (`/` = mismo dominio). Vacía = sin login |
| `VITE_SUPABASE_ANON_KEY` | frontend (build) | vacía | `ANON_KEY` de Supabase |
| `SUPABASE_JWT_SECRET` | todos los módulos | vacía | `JWT_SECRET` de Supabase: valida la sesión; enciende el `auth_request` de nginx |
| `KONTARA_AUTH` | frontend | `on` si hay `SUPABASE_JWT_SECRET` | `off` apaga el `auth_request` (rollback) |
| `SUPABASE_GATEWAY_URL` | frontend, erp-api | `http://api-gw:8000` | Gateway de Supabase por la red `supabase` |
| `SUPABASE_SERVICE_ROLE_KEY` | erp-api | vacía | API admin de GoTrue (Configuración → Usuarios) |
| `REQUIRE_AUTH` | todos los módulos | `false` | La API misma exige sesión (ver arriba) |
| `CERTIFICADOS_CLAVE` | todos los módulos | vacía | Cifra la contraseña del `.p12` cargado y las de SMTP/WhatsApp |
| `RECEPTOR_IP_PUBLICACION` | receptores | `127.0.0.1` | `0.0.0.0` para exponerlos por IP:puerto |
