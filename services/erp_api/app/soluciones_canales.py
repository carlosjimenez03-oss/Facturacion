"""Configuración y canales de salida de Soluciones (fase 6): correo (SMTP) y
WhatsApp (WhatsApp Cloud API de Meta), y el cliente HTTP de `facturas_api`
para la representación impresa que se descarga en el portal de clientes.

Los canales salen de Configuración → WhatsApp y correo (lo que el usuario
guardó en `configuracion.preferencias`, ver configuracion_envio.py: SMTP
propio de la empresa y WhatsApp Cloud API con su token cifrado); si allí no
hay nada configurado, de las variables de entorno SMTP_* / WHATSAPP_* de
abajo. `canales_de_envio` es el único punto que decide. Sin ningún canal los
envíos de Kontara Ventas quedan 'pendiente_canal' (nunca se marcan enviados
sin que un canal los haya entregado).
"""
from __future__ import annotations

import asyncio
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from functools import lru_cache
from typing import Any

import asyncpg
import httpx

from libs.ecf_common.settings import EcfBaseSettings, load_settings

from . import configuracion_envio as envio


class SolucionesSettings(EcfBaseSettings):
    # URL interna (red Docker) de facturas_api: el portal público le pide la
    # representación impresa (PDF) de las facturas del cliente.
    facturas_api_url: str = "http://facturas-api:8000"
    facturas_api_timeout_segundos: float = 60.0

    # Correo saliente (opcional; respaldo de Configuración → WhatsApp y correo).
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_usuario: str = ""
    smtp_clave: str = ""
    smtp_remitente: str = ""
    smtp_starttls: bool = True

    # WhatsApp Cloud API (opcional; respaldo de Configuración). Ojo: fuera de la ventana de 24 h de
    # conversación, Meta exige plantillas aprobadas; un texto libre fuera de
    # esa ventana lo rechaza y el envío queda en 'error' con su motivo.
    whatsapp_token: str = ""
    whatsapp_phone_number_id: str = ""
    whatsapp_api_url: str = "https://graph.facebook.com/v20.0"

    # Tarea de fondo de Kontara Ventas: cada cuántas horas revisa qué
    # envíos generar (los duplicados los evita `clave_unica`).
    soluciones_tarea_activa: bool = True
    soluciones_tarea_intervalo_horas: float = 24.0
    soluciones_tarea_espera_inicial_segundos: float = 60.0


@lru_cache
def get_soluciones_settings() -> SolucionesSettings:
    return load_settings(SolucionesSettings)


class CanalError(Exception):
    """El canal no entregó el mensaje; el motivo queda en el envío."""


class FacturasApiError(Exception):
    """facturas_api no respondió o no pudo generar el documento."""


def canales_configurados(settings: SolucionesSettings) -> dict[str, bool]:
    """Canales de las variables de entorno (el respaldo de `canales_de_envio`)."""
    return {
        "correo": bool(settings.smtp_host and settings.smtp_remitente),
        "whatsapp": bool(settings.whatsapp_token and settings.whatsapp_phone_number_id),
    }


@dataclass(frozen=True)
class CanalesEnvio:
    """Canales con los que se entrega cada envío. `correo` / `whatsapp` son
    las preferencias guardadas en Configuración cuando están completas
    (formato de configuracion_envio.py); None = se usan las variables de
    entorno de `settings`."""

    settings: SolucionesSettings
    correo: dict[str, Any] | None = None
    whatsapp: dict[str, Any] | None = None

    def configurados(self) -> dict[str, bool]:
        entorno = canales_configurados(self.settings)
        return {
            "correo": self.correo is not None or entorno["correo"],
            "whatsapp": self.whatsapp is not None or entorno["whatsapp"],
        }

    async def enviar_correo(
        self, para: str, asunto: str, cuerpo: str, adjunto: tuple[str, bytes] | None = None
    ) -> None:
        if self.correo is None:
            await enviar_correo(self.settings, para, asunto, cuerpo, adjunto)
            return
        adjuntos = [(adjunto[0], adjunto[1], "application/pdf")] if adjunto else []
        try:
            await envio.enviar_correo(self.correo, para, asunto, cuerpo, adjuntos, timeout=30)
        except envio.EnvioError as exc:
            raise CanalError(f"SMTP: {exc}") from exc

    async def enviar_whatsapp(self, telefono: str, texto: str) -> None:
        if self.whatsapp is None:
            await enviar_whatsapp(self.settings, telefono, texto)
            return
        try:
            token = envio.secreto(self.whatsapp, "token_cifrado")
        except envio.EnvioError as exc:
            raise CanalError(f"WhatsApp: {exc}") from exc
        await _post_whatsapp(
            envio.META_GRAPH_URL, self.whatsapp["phone_number_id"], token, envio.numero_whatsapp(telefono), texto
        )


async def canales_de_envio(pool: asyncpg.Pool, settings: SolucionesSettings) -> CanalesEnvio:
    """Canales efectivos: Configuración → WhatsApp y correo primero; si un
    canal no está completo allí (o el esquema `configuracion` aún no existe),
    el de las variables de entorno."""
    try:
        correo = await envio.leer_preferencia(pool, "correo", envio.CORREO_POR_DEFECTO)
        whatsapp = await envio.leer_preferencia(pool, "whatsapp", envio.WHATSAPP_POR_DEFECTO)
    except (asyncpg.UndefinedTableError, asyncpg.InvalidSchemaNameError):
        return CanalesEnvio(settings)
    return CanalesEnvio(
        settings,
        correo=correo if envio.correo_publico(correo)["configurado"] else None,
        whatsapp=whatsapp if envio.whatsapp_publico(whatsapp)["conectado"] else None,
    )


def _enviar_correo_sync(
    settings: SolucionesSettings, para: str, asunto: str, cuerpo: str, adjunto: tuple[str, bytes] | None
) -> None:
    mensaje = EmailMessage()
    mensaje["From"] = settings.smtp_remitente
    mensaje["To"] = para
    mensaje["Subject"] = asunto
    mensaje.set_content(cuerpo)
    if adjunto:
        nombre, contenido = adjunto
        mensaje.add_attachment(contenido, maintype="application", subtype="pdf", filename=nombre)
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as smtp:
        if settings.smtp_starttls:
            smtp.starttls()
        if settings.smtp_usuario:
            smtp.login(settings.smtp_usuario, settings.smtp_clave)
        smtp.send_message(mensaje)


async def enviar_correo(
    settings: SolucionesSettings, para: str, asunto: str, cuerpo: str, adjunto: tuple[str, bytes] | None = None
) -> None:
    try:
        await asyncio.to_thread(_enviar_correo_sync, settings, para, asunto, cuerpo, adjunto)
    except (OSError, smtplib.SMTPException) as exc:
        raise CanalError(f"SMTP: {exc}") from exc


async def enviar_whatsapp(settings: SolucionesSettings, telefono: str, texto: str) -> None:
    await _post_whatsapp(
        settings.whatsapp_api_url, settings.whatsapp_phone_number_id, settings.whatsapp_token, telefono, texto
    )


async def _post_whatsapp(api_url: str, phone_number_id: str, token: str, telefono: str, texto: str) -> None:
    url = f"{api_url.rstrip('/')}/{phone_number_id}/messages"
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            respuesta = await client.post(
                url,
                headers={"Authorization": f"Bearer {token}"},
                json={"messaging_product": "whatsapp", "to": telefono, "type": "text", "text": {"body": texto}},
            )
    except httpx.HTTPError as exc:
        raise CanalError(f"WhatsApp: {exc}") from exc
    if respuesta.status_code >= 400:
        raise CanalError(f"WhatsApp {respuesta.status_code}: {respuesta.text[:300]}")


async def representacion_impresa(settings: SolucionesSettings, factura_id: str, *, firmada: bool) -> bytes:
    """PDF de la RI desde facturas_api. Igual que la ficha de la factura en
    el frontend: si aún no hay vista previa firmada, se firma primero
    (POST /firmar no cambia el estado de una factura ya enviada)."""
    base = settings.facturas_api_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=settings.facturas_api_timeout_segundos) as client:
            if not firmada:
                r = await client.post(f"{base}/facturas/{factura_id}/firmar")
                if r.status_code >= 400:
                    raise FacturasApiError(f"No se pudo preparar el PDF: {r.text[:300]}")
            r = await client.get(f"{base}/facturas/{factura_id}/representacion-impresa")
    except httpx.HTTPError as exc:
        raise FacturasApiError(f"No se pudo contactar facturas-api: {exc}") from exc
    if r.status_code >= 400:
        raise FacturasApiError(f"No se pudo generar el PDF: {r.text[:300]}")
    return r.content
