"""Envío de comprobantes al cliente (Configuración → WhatsApp y correo) y
las preferencias de la empresa guardadas en `configuracion.preferencias`.

- Correo: SMTP propio de la empresa (host, puerto, usuario, contraseña,
  TLS/SSL). La contraseña se guarda cifrada con CERTIFICADOS_CLAVE (ver
  libs/ecf_common/auth/certificado_activo.py) y nunca se devuelve. Con
  "envío automático" encendido, una tarea de fondo (main.py) manda por
  correo cada factura ACEPTADA por la DGII (incluye la aceptada
  condicional) que tenga correo del comprador y todavía no se haya enviado,
  con su PDF (y el XML firmado si se pide), y lo registra en
  `configuracion.envios` (una sola vez por factura: la reclama antes de
  enviar y reintenta los errores con espera creciente, ver abajo).
- WhatsApp: Meta WhatsApp Cloud API (token de acceso + phone number id de
  la cuenta de WhatsApp Business). Sin credenciales queda "No conectado".
  Con credenciales se puede mandar un mensaje de prueba (plantilla
  aprobada en Meta, por defecto `hello_world`). `plantilla_mensajes` es la
  plantilla aprobada (con un parámetro {{1}} en el cuerpo) con que Kontara
  Ventas manda sus mensajes; sin ella van como texto libre, que Meta solo
  entrega dentro de las 24 h de conversación (soluciones_canales.py). El
  envío automático por WhatsApp de cada factura NO está implementado
  todavía (necesita una plantilla con documento aprobada por Meta): ver
  docs/AUTH.md.
"""
from __future__ import annotations

import asyncio
import json
import logging
import smtplib
import ssl
from email.message import EmailMessage
from typing import Any

import asyncpg
import httpx

from libs.ecf_common.auth import certificado_activo
from libs.ecf_common.auth.sesion_supabase import cabeceras_internas

from .config import ErpApiSettings
from .repository import DatosInvalidosError

logger = logging.getLogger(__name__)

META_GRAPH_URL = "https://graph.facebook.com/v20.0"

CORREO_POR_DEFECTO: dict[str, Any] = {
    "host": "", "puerto": 587, "seguridad": "starttls", "usuario": "", "remitente": "", "cco": "",
    "adjuntar_xml": True, "envio_automatico": False,
}
WHATSAPP_POR_DEFECTO: dict[str, Any] = {
    "phone_number_id": "", "numero": "", "plantilla": "hello_world", "idioma": "en_US", "envio_automatico": False,
    "plantilla_mensajes": "", "idioma_mensajes": "es",
}
INVENTARIO_POR_DEFECTO: dict[str, Any] = {"metodo": "promedio"}


class EnvioError(Exception):
    pass


# --- preferencias ---------------------------------------------------------

async def leer_preferencia(pool: asyncpg.Pool, clave: str, por_defecto: dict[str, Any]) -> dict[str, Any]:
    async with pool.acquire() as conn:
        valor = await conn.fetchval("SELECT valor FROM configuracion.preferencias WHERE clave = $1", clave)
    if isinstance(valor, str):
        valor = json.loads(valor)
    return {**por_defecto, **(valor or {})}


async def guardar_preferencia(pool: asyncpg.Pool, clave: str, valor: dict[str, Any]) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO configuracion.preferencias (clave, valor) VALUES ($1, $2::jsonb)
            ON CONFLICT (clave) DO UPDATE SET valor = EXCLUDED.valor, actualizado_en = now()
            """,
            clave,
            json.dumps(valor),
        )


def _con_secreto(actual: dict[str, Any], campo_cifrado: str, nuevo: str | None, borrar: bool = False) -> dict[str, Any]:
    """Cifra el secreto nuevo; None/"" conserva el que había."""
    valor = dict(actual)
    if borrar:
        valor.pop(campo_cifrado, None)
    elif nuevo:
        try:
            valor[campo_cifrado] = certificado_activo.cifrar(nuevo)
        except certificado_activo.CifradoNoConfiguradoError as exc:
            raise EnvioError(
                f"Para guardar contraseñas y tokens configure CERTIFICADOS_CLAVE en Dokploy ({exc}; ver docs/AUTH.md)"
            ) from exc
    elif valor.get(campo_cifrado):
        # Secreto guardado con el formato anterior: se cifra de nuevo con
        # scrypt y sal al guardar (si no se puede, se deja como estaba).
        try:
            valor[campo_cifrado] = certificado_activo.recifrar_si_antiguo(valor[campo_cifrado])
        except certificado_activo.CifradoNoConfiguradoError:
            pass
    return valor


def secreto(valor: dict[str, Any], campo_cifrado: str) -> str:
    """Descifra un secreto guardado (contraseña SMTP, token de WhatsApp); "" si no hay."""
    cifrado = valor.get(campo_cifrado)
    if not cifrado:
        return ""
    try:
        return certificado_activo.descifrar(cifrado)
    except certificado_activo.CifradoNoConfiguradoError as exc:
        raise EnvioError(str(exc)) from exc


def correo_publico(valor: dict[str, Any]) -> dict[str, Any]:
    publico = {k: valor.get(k, v) for k, v in CORREO_POR_DEFECTO.items()}
    publico["contrasena_configurada"] = bool(valor.get("contrasena_cifrada"))
    publico["configurado"] = bool(valor.get("host") and valor.get("remitente"))
    return publico


def whatsapp_publico(valor: dict[str, Any]) -> dict[str, Any]:
    publico = {k: valor.get(k, v) for k, v in WHATSAPP_POR_DEFECTO.items()}
    publico["token_configurado"] = bool(valor.get("token_cifrado"))
    publico["conectado"] = bool(valor.get("token_cifrado") and valor.get("phone_number_id"))
    return publico


async def guardar_correo(pool: asyncpg.Pool, campos: dict[str, Any]) -> dict[str, Any]:
    actual = await leer_preferencia(pool, "correo", CORREO_POR_DEFECTO)
    nuevo = {**actual, **{k: v for k, v in campos.items() if k in CORREO_POR_DEFECTO}}
    if nuevo["seguridad"] not in ("starttls", "ssl", "ninguna"):
        raise DatosInvalidosError("seguridad debe ser starttls, ssl o ninguna")
    nuevo = _con_secreto(nuevo, "contrasena_cifrada", campos.get("contrasena"))
    await guardar_preferencia(pool, "correo", nuevo)
    return nuevo


async def guardar_whatsapp(pool: asyncpg.Pool, campos: dict[str, Any], *, desconectar: bool = False) -> dict[str, Any]:
    actual = await leer_preferencia(pool, "whatsapp", WHATSAPP_POR_DEFECTO)
    nuevo = {**actual, **{k: v for k, v in campos.items() if k in WHATSAPP_POR_DEFECTO}}
    nuevo = _con_secreto(nuevo, "token_cifrado", campos.get("token"), borrar=desconectar)
    if desconectar:
        nuevo["envio_automatico"] = False
    await guardar_preferencia(pool, "whatsapp", nuevo)
    return nuevo


async def enviados_del_mes(pool: asyncpg.Pool) -> dict[str, int]:
    async with pool.acquire() as conn:
        filas = await conn.fetch(
            """
            SELECT canal, count(*) AS n FROM configuracion.envios
            WHERE estado = 'enviado' AND factura_id IS NOT NULL
              AND enviado_en >= date_trunc('month', now() AT TIME ZONE 'America/Santo_Domingo')
            GROUP BY canal
            """
        )
    cuenta = {"correo": 0, "whatsapp": 0}
    cuenta.update({f["canal"]: int(f["n"]) for f in filas})
    return cuenta


async def registrar_envio(
    pool: asyncpg.Pool, canal: str, factura_id, destino: str, error: str | None = None
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO configuracion.envios (canal, factura_id, destino, estado, error)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (canal, factura_id) WHERE factura_id IS NOT NULL DO NOTHING
            """,
            canal,
            factura_id,
            destino,
            "error" if error else "enviado",
            error,
        )


# --- correo ---------------------------------------------------------

def _enviar_smtp(config: dict[str, Any], contrasena: str, mensaje: EmailMessage, timeout: float) -> None:
    host, puerto = config["host"], int(config["puerto"] or 587)
    contexto = ssl.create_default_context()
    if config["seguridad"] == "ssl":
        servidor: smtplib.SMTP = smtplib.SMTP_SSL(host, puerto, timeout=timeout, context=contexto)
    else:
        servidor = smtplib.SMTP(host, puerto, timeout=timeout)
    with servidor:
        if config["seguridad"] == "starttls":
            servidor.starttls(context=contexto)
        if config.get("usuario"):
            servidor.login(config["usuario"], contrasena)
        servidor.send_message(mensaje)


async def enviar_correo(
    config: dict[str, Any],
    destino: str,
    asunto: str,
    cuerpo: str,
    adjuntos: list[tuple[str, bytes, str]] = (),
    timeout: float = 20.0,
) -> None:
    if not (config.get("host") and config.get("remitente")):
        raise EnvioError("Configure el servidor SMTP y el remitente antes de enviar")
    mensaje = EmailMessage()
    mensaje["From"] = config["remitente"]
    mensaje["To"] = destino
    if config.get("cco"):
        mensaje["Bcc"] = config["cco"]
    mensaje["Subject"] = asunto
    mensaje.set_content(cuerpo)
    for nombre, contenido, tipo in adjuntos:
        principal, _, sub = tipo.partition("/")
        mensaje.add_attachment(contenido, maintype=principal, subtype=sub, filename=nombre)
    contrasena = secreto(config, "contrasena_cifrada")
    try:
        await asyncio.to_thread(_enviar_smtp, config, contrasena, mensaje, timeout)
    except (smtplib.SMTPException, OSError) as exc:
        raise EnvioError(f"El servidor de correo rechazó el envío: {type(exc).__name__}: {exc}") from exc


# --- WhatsApp (Meta Cloud API) ---------------------------------------------------------

def numero_whatsapp(numero: str) -> str:
    """Solo dígitos; un número dominicano de 10 dígitos lleva delante el 1."""
    destino = "".join(c for c in numero if c.isdigit())
    return "1" + destino if len(destino) == 10 else destino


async def enviar_whatsapp_prueba(config: dict[str, Any], numero: str, timeout: float = 15.0) -> dict[str, Any]:
    token = secreto(config, "token_cifrado")
    if not (token and config.get("phone_number_id")):
        raise EnvioError("WhatsApp no está conectado: falta el token o el phone number id de Meta")
    destino = numero_whatsapp(numero)
    cuerpo = {
        "messaging_product": "whatsapp",
        "to": destino,
        "type": "template",
        "template": {"name": config.get("plantilla") or "hello_world", "language": {"code": config.get("idioma") or "en_US"}},
    }
    async with httpx.AsyncClient(timeout=timeout) as cliente:
        try:
            r = await cliente.post(
                f"{META_GRAPH_URL}/{config['phone_number_id']}/messages",
                headers={"Authorization": f"Bearer {token}"},
                json=cuerpo,
            )
        except httpx.HTTPError as exc:
            raise EnvioError(f"Meta no responde: {exc}") from exc
    if r.status_code >= 400:
        try:
            detalle = r.json().get("error", {}).get("message") or r.text
        except ValueError:
            detalle = r.text
        raise EnvioError(f"Meta rechazó el mensaje: {detalle}")
    return r.json()


# --- envío automático de facturas aceptadas ---------------------------------------------------------
#
# Cada factura se RECLAMA antes de mandarla: una fila 'pendiente' en
# configuracion.envios (INSERT ... ON CONFLICT DO NOTHING/UPDATE ...
# RETURNING): si dos pasadas (o dos réplicas) llegan a la vez, solo una
# obtiene la fila y manda el correo. Un error deja la fila en 'error' con su
# intento y la hora del próximo (espera que se duplica: 10, 20, 40, 80 min)
# hasta ENVIO_MAX_INTENTOS; después ya no se reintenta (se ve en el log y en
# la fila). Una fila que quedó 'pendiente' por un reinicio a mitad del envío
# no se reintenta sola: mejor no duplicarle el correo al cliente. Además cada
# pasada toma un advisory lock (pg_try_advisory_lock): con varias réplicas
# corre una sola.

ENVIO_MAX_INTENTOS = 5
ENVIO_ESPERA_BASE_MINUTOS = 10
_LOCK_ENVIO_AUTOMATICO = 7_013_001


async def facturas_por_enviar(pool: asyncpg.Pool, limite: int = 20) -> list[dict[str, Any]]:
    """Aceptadas de los últimos 30 días con correo del comprador que nunca se
    intentaron, o cuyo último intento falló y ya toca reintentar."""
    async with pool.acquire() as conn:
        filas = await conn.fetch(
            """
            SELECT f.id, f.encf, f.tipo_ecf, f.xml_firmado,
                   f.datos->'Encabezado'->'Comprador'->>'CorreoComprador' AS correo,
                   f.datos->'Encabezado'->'Comprador'->>'RazonSocialComprador' AS cliente,
                   f.datos->'Encabezado'->'Totales'->>'MontoTotal' AS total
            FROM facturacion.facturas f
            WHERE f.estado = 'aceptado'
              AND coalesce(f.datos->'Encabezado'->'Comprador'->>'CorreoComprador', '') <> ''
              AND f.actualizado_en >= now() - interval '30 days'
              AND NOT EXISTS (
                  SELECT 1 FROM configuracion.envios e
                  WHERE e.canal = 'correo' AND e.factura_id = f.id
                    AND NOT (e.estado = 'error' AND e.intentos < $2
                             AND (e.proximo_intento IS NULL OR e.proximo_intento <= now()))
              )
            ORDER BY f.actualizado_en
            LIMIT $1
            """,
            limite,
            ENVIO_MAX_INTENTOS,
        )
    return [dict(f) for f in filas]


async def reclamar_envio(pool: asyncpg.Pool, canal: str, factura_id, destino: str) -> int | None:
    """Deja la factura 'pendiente' a nombre de esta pasada y devuelve el
    número de intento, o None si otra pasada ya la tiene, ya se envió o no
    toca reintentar todavía."""
    async with pool.acquire() as conn:
        return await conn.fetchval(
            """
            INSERT INTO configuracion.envios (canal, factura_id, destino, estado, intentos)
            VALUES ($1, $2, $3, 'pendiente', 1)
            ON CONFLICT (canal, factura_id) WHERE factura_id IS NOT NULL DO UPDATE
                SET estado = 'pendiente', intentos = configuracion.envios.intentos + 1,
                    destino = EXCLUDED.destino, error = NULL, proximo_intento = NULL, enviado_en = now()
                WHERE configuracion.envios.estado = 'error'
                  AND configuracion.envios.intentos < $4
                  AND (configuracion.envios.proximo_intento IS NULL OR configuracion.envios.proximo_intento <= now())
            RETURNING intentos
            """,
            canal,
            factura_id,
            destino,
            ENVIO_MAX_INTENTOS,
        )


async def terminar_envio(pool: asyncpg.Pool, canal: str, factura_id, intento: int, error: str | None = None) -> None:
    """'enviado', o 'error' con la hora del próximo intento (None = no más)."""
    espera = ENVIO_ESPERA_BASE_MINUTOS * 2 ** (intento - 1) if error and intento < ENVIO_MAX_INTENTOS else None
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE configuracion.envios
            SET estado = CASE WHEN $3::text IS NULL THEN 'enviado' ELSE 'error' END,
                error = $3::text,
                enviado_en = now(),
                proximo_intento = CASE WHEN $4::int IS NULL THEN NULL ELSE now() + make_interval(mins => $4::int) END
            WHERE canal = $1 AND factura_id = $2 AND estado = 'pendiente'
            """,
            canal,
            factura_id,
            error,
            espera,
        )


async def _enviar_factura(
    cliente: httpx.AsyncClient, settings: ErpApiSettings, config: dict[str, Any], f: dict[str, Any], empresa: str
) -> None:
    adjuntos: list[tuple[str, bytes, str]] = []
    pdf = await cliente.get(f"{settings.facturas_api_url}/facturas/{f['id']}/representacion-impresa")
    pdf.raise_for_status()
    adjuntos.append((f"{f['encf']}.pdf", pdf.content, "application/pdf"))
    if config.get("adjuntar_xml") and f.get("xml_firmado"):
        adjuntos.append((f"{f['encf']}.xml", f["xml_firmado"].encode("utf-8"), "application/xml"))
    await enviar_correo(
        config,
        f["correo"],
        f"{empresa}: comprobante electrónico {f['encf']}",
        f"Hola, {f['cliente'] or 'estimado cliente'}.\n\nLe compartimos su comprobante electrónico "
        f"{f['encf']} por RD$ {f['total'] or ''}, aceptado por la DGII.\n\n— {empresa}",
        adjuntos,
    )


async def enviar_facturas_aceptadas(pool: asyncpg.Pool, settings: ErpApiSettings, empresa: str) -> int:
    """Una pasada del envío automático por correo. Devuelve cuántas se enviaron."""
    config = await leer_preferencia(pool, "correo", CORREO_POR_DEFECTO)
    if not (config.get("envio_automatico") and config.get("host") and config.get("remitente")):
        return 0
    async with pool.acquire() as conn_lock:
        if not await conn_lock.fetchval("SELECT pg_try_advisory_lock($1)", _LOCK_ENVIO_AUTOMATICO):
            return 0
        try:
            return await _pasada_envio(pool, settings, config, empresa)
        finally:
            await conn_lock.execute("SELECT pg_advisory_unlock($1)", _LOCK_ENVIO_AUTOMATICO)


async def _pasada_envio(pool: asyncpg.Pool, settings: ErpApiSettings, config: dict[str, Any], empresa: str) -> int:
    enviadas = 0
    # Llamada interna: pasa el REQUIRE_AUTH de facturas-api sin sesión.
    async with httpx.AsyncClient(timeout=30, headers=cabeceras_internas(settings.kontara_token_interno)) as cliente:
        for f in await facturas_por_enviar(pool):
            intento = await reclamar_envio(pool, "correo", f["id"], f["correo"])
            if intento is None:
                continue
            try:
                await _enviar_factura(cliente, settings, config, f, empresa)
            except (EnvioError, httpx.HTTPError) as exc:
                logger.warning(
                    "No se pudo enviar por correo la factura %s (intento %s de %s): %s",
                    f["encf"], intento, ENVIO_MAX_INTENTOS, exc,
                )
                await terminar_envio(pool, "correo", f["id"], intento, str(exc)[:500] or type(exc).__name__)
                continue
            await terminar_envio(pool, "correo", f["id"], intento)
            enviadas += 1
    return enviadas


async def bucle_envio_automatico(pool: asyncpg.Pool, settings: ErpApiSettings, nombre_empresa) -> None:
    while True:
        await asyncio.sleep(settings.envio_automatico_cada_segundos)
        try:
            await enviar_facturas_aceptadas(pool, settings, await nombre_empresa())
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Falló la pasada de envío automático de comprobantes")
