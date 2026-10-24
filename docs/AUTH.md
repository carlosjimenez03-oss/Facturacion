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
- **Además, el usuario tiene que estar autorizado en Kontara**:
  `app_metadata.kontara = true` en su usuario de GoTrue. `app_metadata` solo
  lo cambia la `SERVICE_ROLE_KEY` (nunca el propio usuario): lo pone erp-api
  al crear o invitar desde Configuración → Usuarios (o con «Dar acceso»). Un
  usuario que exista en el mismo Supabase por otro camino (registro abierto,
  OTP, magic link, SSO, otra aplicación del mismo Supabase) o anónimo
  (`is_anonymous`) recibe 401 *"Su usuario no tiene acceso a Kontara"*.
  Primer usuario: ver el paso 2.
- Si la base es la de Supabase (tiene el esquema `auth`), erp-api comprueba
  también que la sesión siga en `auth.sessions` y que el usuario no esté
  desactivado (caché de 60 s): cerrar sesión o desactivar a alguien corta su
  acceso en ~1 minuto en vez de cuando vence el token (1 h). Si erp-api no
  puede leer `auth.sessions`, lo registra una vez en el log y sigue solo con
  el vencimiento.
- El frontend renueva el token un minuto antes de que venza
  (`grant_type=refresh_token`), y si una API responde 401 lo renueva una vez y
  reintenta. Si no puede, vuelve a `/login`. Ver `frontend/src/lib/sesion.ts`.
- Los enlaces que abre el navegador sin `fetch` (PDF de una factura, XML de la
  bandeja) llevan el mismo token en la cookie `kontara_sesion`
  (`SameSite=Strict`, `Secure`). La cookie **solo** vale para `GET`/`HEAD` de
  esas descargas (`/api/facturas/<id>/representacion-impresa` y
  `/api-erp/compras/bandeja/<id>/xml`): nginx le pasa a erp-api el método y la
  ruta originales (`X-Original-Method`, `X-Original-URI`). Todo lo demás
  necesita `Authorization: Bearer`, que otro sitio no puede poner (CSRF).
- Las llamadas **internas** entre servicios (facturas-api → inventario-api,
  erp-api → aprobacion-comercial, erp-api → facturas-api…) no pasan por nginx
  y no llevan token. Con `REQUIRE_AUTH` encendido se identifican con la
  cabecera `X-Kontara-Interno` = `KONTARA_TOKEN_INTERNO` (paso 4); nginx borra
  esa cabecera de todo lo que manda el navegador.
- **Falla cerrado:** si el frontend se compiló con inicio de sesión
  (`VITE_SUPABASE_*`) pero al contenedor le llega `KONTARA_AUTH` vacía (p. ej.
  falta o está mal escrito `SUPABASE_JWT_SECRET`), nginx **no** queda abierto:
  `/api*` responde 503 *"Inicio de sesión mal configurado"* y el log del
  contenedor lo dice en `ERROR`. Para abrirlo a propósito hay que poner
  `KONTARA_AUTH=off`. Además el frontend comprueba al cargar que
  `/api-erp/health` sin token responda el 401 de nginx (erp-api deja `/health`
  abierto: solo nginx puede negarlo); si no (nginx abierto, o 5xx porque
  nginx o erp-api no tienen `SUPABASE_JWT_SECRET`), muestra un aviso **rojo**
  *"Las APIs no están protegidas"*. erp-api sin `SUPABASE_JWT_SECRET` responde
  503 en `/auth/verificar`, y nginx lo trata como error (no deja pasar nada).
- `/auth/v1/*` solo reenvía a GoTrue lo que usa el frontend: `/token`,
  `/logout`, `/recover`, `/user`, `/verify`, `/health` y `/settings`. `/signup`,
  `/otp`, `/magiclink`, `/sso`, `/factors`, `/invite`, `/admin/*`… responden 403
  aunque GoTrue los tenga encendidos. `/auth/v1/token` (inicio de sesión) tiene
  un límite de 10 intentos por minuto por IP (ráfagas de 5; después 429). La IP
  es la real del cliente (`CF-Connecting-IP` del túnel), creída solo si el
  request viene de `KONTARA_PROXY_CONFIABLE` (por defecto loopback y las redes
  privadas de Docker).
- Los servicios del Receptor Electrónico (`/fe/autenticacion/...`,
  `/fe/recepcion/...`, `/fe/aprobacioncomercial/...`) los llaman la DGII y los
  proveedores: **nunca** piden sesión.
- El portal de clientes (Soluciones) se abre sin sesión: la página `/p/<token>`
  del frontend llama `/api-erp/portal/publico/<token>/...`, que nginx reenvía
  sin `auth_request` (location propia) y erp-api con `ERP_REQUIRE_AUTH` deja
  pasar. El resto de `/api-erp/portal/*` sí pide sesión, incluida la vista
  previa del usuario (`/portal/vista-previa/<token>`, que no cuenta como
  acceso del cliente). `/p/` y el portal público responden con
  `Referrer-Policy: no-referrer` (el token va en la URL). El enlace queda
  fijado al RNC/cédula que tenía el cliente al crearlo.
- Pagos con tarjeta en línea (fase 7, [`PAGOS.md`](PAGOS.md)): el inicio del
  pago (`/portal/publico/<token>/pagos`), el retorno del banco
  (`/portal/publico/pagos/<ref>/retorno/...`) y el estado del pago están bajo
  la misma ruta pública: no hay excepciones nuevas en nginx ni en
  `ERP_REQUIRE_AUTH`. Iniciar pagos tiene límite por IP en nginx (zona
  `kontara_pagos`) y por enlace en erp-api. Configuración → Pagos en línea y
  el listado de pagos piden sesión; guardar la configuración, además, ser
  administrador.

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
  crea usted), `ENABLE_PHONE_SIGNUP=false` y `ENABLE_ANONYMOUS_USERS=false`.
  nginx ya bloquea `/auth/v1/signup`, `/otp`, `/magiclink`... y Kontara
  rechaza a quien no tenga `app_metadata.kontara`, pero un Supabase con
  registro abierto también lo pueden usar por su propio dominio
  (`API_EXTERNAL_URL`).
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

Ese usuario necesita **acceso a Kontara** (`app_metadata.kontara = true`).
Dos formas (cualquiera de las dos):

- **Recomendada: `KONTARA_ADMIN_EMAILS`.** En el paso 3 agregue
  `KONTARA_ADMIN_EMAILS=<su correo>` (varios separados por comas). Esos
  correos entran aunque no tengan la marca, y en su primer inicio de sesión
  erp-api se la pone sola (`{"kontara": true, "kontara_admin": true}`, con
  `SUPABASE_SERVICE_ROLE_KEY`). Comprobado el acceso, puede quitar la
  variable (o dejarla: esos correos además pueden mandar el correo de prueba
  a otra dirección).
- **A mano en Studio:** Authentication → Users → el usuario → **Edit user** (o
  el menú «…» → *Edit raw app_metadata* según la versión de Studio) → en
  `raw_app_meta_data` agregue `"kontara": true` sin borrar lo que hay, p. ej.
  `{"provider": "email", "providers": ["email"], "kontara": true}` → Save. Si
  su Studio no lo deja editar, en **SQL Editor**:

  ```sql
  update auth.users
  set raw_app_meta_data = coalesce(raw_app_meta_data, '{}'::jsonb) || '{"kontara": true}'::jsonb
  where email = 'su-correo@empresa.do';
  ```

  La marca vale desde el próximo inicio de sesión (o renovación del token).

Los siguientes usuarios se crean desde la propia aplicación (Configuración →
Usuarios) una vez configurado `SUPABASE_SERVICE_ROLE_KEY`: ya salen con la
marca.

**Si ya tenía usuarios de antes de esta versión:** no tienen la marca y desde
este despliegue reciben *"Su usuario no tiene acceso a Kontara"*. Ponga su
correo de administrador en `KONTARA_ADMIN_EMAILS`, entre, y en Configuración →
Usuarios → **Sin acceso a Kontara** pulse **Dar acceso** en cada uno de los
demás.

### 3. Variables de la facturación

Dokploy → recurso Compose de la facturación → **Environment**, agregue:

```env
# Inicio de sesión (las tres juntas)
VITE_SUPABASE_URL=/
VITE_SUPABASE_ANON_KEY=<ANON_KEY de Supabase>
SUPABASE_JWT_SECRET=<JWT_SECRET de Supabase>

# Primer usuario (paso 2): entra sin la marca y erp-api se la pone.
KONTARA_ADMIN_EMAILS=<su correo>

# Configuración → Usuarios desde la aplicación (y la marca del paso 2)
SUPABASE_SERVICE_ROLE_KEY=<SERVICE_ROLE_KEY de Supabase>

# Opcional: cargar el certificado .p12 y guardar contraseñas SMTP/WhatsApp
# desde Configuración. 32 caracteres o más al azar (p. ej. `openssl rand -base64 48`).
# Si lo cambia después, el certificado cargado y las contraseñas guardadas
# dejan de poder leerse (se vuelve a usar certs/emisor.p12).
CERTIFICADOS_CLAVE=<texto largo al azar>

# Recomendado: secreto de las llamadas internas entre servicios (paso 4).
# 32 caracteres o más al azar (p. ej. `openssl rand -base64 48`).
KONTARA_TOKEN_INTERNO=<texto largo al azar>
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
  `auth_request` de nginx (`KONTARA_AUTH=on`) y el `ERP_REQUIRE_AUTH` de
  erp-api. Las tres variables van juntas: con el secreto pero sin las
  `VITE_*`, las APIs piden sesión y el frontend no tiene cómo iniciarla; con
  las `VITE_*` pero sin el secreto, `/api*` responde 503 (falla cerrado, ver
  arriba) hasta que lo agregue.
- `CERTIFICADOS_CLAVE` debe tener **32 caracteres o más**: con menos no se
  guarda ningún secreto nuevo (la pantalla lo explica). Lo que ya estaba
  guardado con una clave corta se sigue leyendo. Los secretos se cifran con
  una llave derivada con scrypt y una sal al azar por secreto; los del
  formato anterior se vuelven a cifrar así la próxima vez que se guardan.

**Deploy.** Abra `https://front.acentosdeco.lat`: debe pedir el inicio de
sesión. Compruebe desde fuera:

```bash
curl -i https://front.acentosdeco.lat/api-erp/configuracion/empresa   # 401 {"detail":"Inicie sesión para continuar"}
curl -i https://front.acentosdeco.lat/fe/autenticacion/api/semilla    # 200 (receptor público)
curl -i https://front.acentosdeco.lat/api-erp/portal/publico/xxx      # 404 (portal público: sin 401)
curl -i -X POST https://front.acentosdeco.lat/auth/v1/signup          # 403 (solo las rutas que usa el frontend)
```

### 4. Defensa en profundidad: `ERP_REQUIRE_AUTH` y `REQUIRE_AUTH`

- **erp-api** exige el token por su cuenta (además de nginx) en cuanto existe
  `SUPABASE_JWT_SECRET` (`ERP_REQUIRE_AUTH`, la enciende el compose): está en
  la red `supabase`, donde también viven Studio y los demás servicios del
  recurso de Supabase. Ningún servicio de Kontara llama a erp-api por dentro,
  así que no rompe nada. Quedan abiertos `/health`, `/auth/verificar` (se
  valida solo) y el portal público `/portal/publico/*`. `ERP_REQUIRE_AUTH=false`
  lo apaga.
- `REQUIRE_AUTH=true` hace lo mismo en facturas-api, inventario-api y
  formato-606. Está apagado por defecto. Las llamadas internas (facturas-api →
  inventario-api para el stock; erp-api → facturas-api para el PDF del envío
  automático por correo, el portal de clientes y Kontara Ventas) mandan la
  cabecera `X-Kontara-Interno` con `KONTARA_TOKEN_INTERNO`, que el
  `REQUIRE_AUTH` deja pasar: **configure `KONTARA_TOKEN_INTERNO` (32+
  caracteres, el mismo en todos los servicios: va en el ancla del compose)
  antes de encender `REQUIRE_AUTH`**, o esas llamadas recibirán 401. nginx
  borra `X-Kontara-Interno` de lo que manda el navegador. Con los puertos en
  `127.0.0.1` (abajo) el riesgo que cubre `REQUIRE_AUTH` es pequeño.

### Riesgo aceptado: el token en `localStorage`

El frontend guarda la sesión (access y refresh token) en `localStorage`, como
`supabase-js`. Un XSS en la aplicación podría leerla y usarla hasta que venza
el access token (1 h; ~1 min después de cerrar la sesión si erp-api ve
`auth.sessions`). A cambio no hay CSRF: las APIs exigen la cabecera
`Authorization`, que el navegador no manda solo (la cookie `kontara_sesion`
existe solo para las descargas GET). React escapa todo lo que muestra y la
aplicación no inserta HTML. Cerrar sesión «en todos los dispositivos»
(Seguridad) revoca los refresh tokens.

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
   que pertenezca al RNC del emisor (`EMISOR_RNC` o el de Configuración →
   Empresa): el RNC debe ser exactamente el número de `serialNumber`, `OU` u
   `organizationIdentifier` del titular. Sin RNC configurado no se acepta
   ningún certificado. Si algo falla, el certificado anterior sigue en uso.
2. Lo guarda en el volumen Docker `certificados` (`/app/certs-activos/
   activo.json`): el `.p12` en base64 y su contraseña **cifrada** con una
   llave derivada de `CERTIFICADOS_CLAVE` (que solo vive en las variables de
   entorno). Se escribe en un temporal y se cambia con `os.replace`
   (atómico). erp-api lo monta con escritura; los demás servicios, de solo
   lectura. `CERTIFICADOS_CLAVE` va a todos los módulos (ancla del compose)
   porque todos los que firman la necesitan para abrir el certificado
   cargado: los de la DGII (semilla, envíos, consultas), facturas-api y los
   tres del Receptor Electrónico (firman el ARECF/ACECF que devuelven).
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
  firmado si se pide), una sola vez por factura (`configuracion.envios`): cada
  factura se *reclama* (fila `pendiente`) antes de mandarla, así dos réplicas
  o dos pasadas no la mandan dos veces (además cada pasada toma un advisory
  lock). Un error se reintenta a los 10, 20, 40 y 80 minutos (5 intentos en
  total); después queda en `error` con el motivo. El correo de prueba va al
  usuario con sesión (su correo); solo un administrador (`kontara_admin` o
  `KONTARA_ADMIN_EMAILS`) puede indicar otra dirección.
- **WhatsApp Business:** Meta WhatsApp Cloud API (Phone number ID + token de
  acceso permanente). Sin credenciales queda *No conectado*. Con ellas se
  puede mandar un mensaje de prueba (plantilla aprobada, por defecto
  `hello_world`). El envío automático de cada factura por WhatsApp **todavía no
  está implementado** (requiere una plantilla con documento aprobada por
  Meta). Los mensajes de Kontara Ventas: Meta solo entrega texto libre dentro
  de las 24 h de conversación con el cliente. Registre en Meta una plantilla
  con un parámetro `{{1}}` en el cuerpo y póngala en **Plantilla para
  mensajes** (o `WHATSAPP_PLANTILLA` en erp-api): cada mensaje va como esa
  plantilla con el texto en `{{1}}`. Sin plantilla, si Meta lo rechaza por
  estar fuera de la ventana, el envío queda *"Requiere plantilla"* sin más
  reintentos (envíelo a mano desde Kontara Ventas).

## Usuarios

Configuración → Usuarios lista, crea (con contraseña) o invita (sin
contraseña, requiere SMTP en Supabase) y desactiva/activa usuarios con la API
admin de GoTrue (`/auth/v1/admin/users`), que erp-api llama por la red interna
con `SUPABASE_SERVICE_ROLE_KEY`. Solo lista y administra los usuarios **de
Kontara** (`app_metadata.kontara`); los demás del mismo Supabase Auth aparecen
aparte como *Sin acceso a Kontara*, con **Dar acceso**. Los que se crean o
invitan desde aquí salen con la marca. Nadie puede desactivarse a sí mismo.
Todos los usuarios con sesión pueden administrar usuarios (no hay roles
todavía). Desactivar corta el acceso en ~1 minuto (ver `auth.sessions`
arriba). Sin la llave, la pantalla explica cómo hacerlo en Studio.

## Revertir (rollback)

- **Apagar el inicio de sesión sin redeploy del código:** en Dokploy ponga
  `KONTARA_AUTH=off` (nginx deja de pedir sesión), `ERP_REQUIRE_AUTH=false` y
  borre `VITE_SUPABASE_URL` / `VITE_SUPABASE_ANON_KEY` → **Deploy** (el
  frontend vuelve a abrir sin login, con el aviso amarillo). Quitar solo
  `SUPABASE_JWT_SECRET` ya **no** abre nada: con el frontend compilado con
  login, `/api*` responde 503 (falla cerrado).
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
| `ERP_REQUIRE_AUTH` | erp-api | `true` si hay `SUPABASE_JWT_SECRET` | `REQUIRE_AUTH` propio de erp-api (`false` lo apaga) |
| `KONTARA_ADMIN_EMAILS` | todos los módulos | vacía | Correos (coma) que entran sin `app_metadata.kontara`; erp-api se la pone (paso 2) |
| `KONTARA_TOKEN_INTERNO` | todos los módulos | vacía | Secreto (32+) de las llamadas internas (`X-Kontara-Interno`) con `REQUIRE_AUTH` |
| `KONTARA_PROXY_CONFIABLE` | frontend | loopback + redes privadas | De dónde se cree `CF-Connecting-IP` (límite de intentos de login) |
| `CERTIFICADOS_CLAVE` | todos los módulos | vacía | Cifra (scrypt + sal) la contraseña del `.p12` cargado y las de SMTP/WhatsApp. 32+ caracteres |
| `WHATSAPP_PLANTILLA` | erp-api | vacía | Plantilla de Meta para Kontara Ventas si no hay una en Configuración |
| `RECEPTOR_IP_PUBLICACION` | receptores | `127.0.0.1` | `0.0.0.0` para exponerlos por IP:puerto |
