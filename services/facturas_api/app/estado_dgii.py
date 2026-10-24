"""Resultado de la DGII de los e-CF enviados: 'enviado' -> 'aceptado' | 'rechazado'.

`POST /facturas/{id}/enviar` solo obtiene un trackId; la DGII valida el
comprobante de forma asíncrona y su resultado se consulta por ese trackId a
través de `consulta_resultado` (ver `app.consulta_resultado_client`). Este
módulo traduce esa respuesta al estado de la factura y se usa desde tres
lugares:

- `POST /facturas/{id}/actualizar-estado` (botón "Actualizar estado DGII"
  del frontend).
- La consulta inmediata, best-effort, justo después de un envío exitoso
  (la DGII suele validar en ~200 ms, pero puede responder "En Proceso").
- La tarea de fondo `bucle_sincronizacion`, iniciada en el lifespan, que
  cada `sincronizar_estado_cada_segundos` revisa las facturas que siguen en
  'enviado'.

Los tres pueden coincidir sobre la misma factura: la actualización en
Postgres (`repository.registrar_resultado_consulta`) solo aplica si la fila
sigue en un estado actualizable, así que nunca se pisa un resultado ya
registrado.

Códigos de la DGII (ver services/consulta_resultado/app/schemas.py):
0 No encontrado, 1 Aceptado, 2 Rechazado, 3 En Proceso, 4 Aceptado
Condicional.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from libs.ecf_common.db import registrar_envio

from . import consulta_resultado_client, inventario_client, repository
from .config import FacturasApiSettings
from .consulta_resultado_client import ConsultaResultadoError
from .inventario_client import InventarioApiError

logger = logging.getLogger(__name__)

MODULO_AUDITORIA = "consulta_resultado"

CODIGO_NO_ENCONTRADO = 0
CODIGO_ACEPTADO = 1
CODIGO_RECHAZADO = 2
CODIGO_EN_PROCESO = 3
CODIGO_ACEPTADO_CONDICIONAL = 4

ACEPTADO_CONDICIONAL = "Aceptado Condicional"

# Tope por iteración de la tarea de fondo y antigüedad máxima de un envío
# para seguir consultándolo (pasado ese plazo, queda el botón manual).
LIMITE_POR_ITERACION = 50
DIAS_MAXIMOS_PENDIENTE = 30


def admite_actualizacion(factura: dict[str, Any]) -> bool:
    """Si el estado de la factura todavía puede cambiar según la DGII (mismo
    criterio que `repository._CONDICION_ACTUALIZABLE`)."""
    if not factura.get("track_id"):
        return False
    estado = factura.get("estado")
    if estado in ("enviado", "error"):
        return True
    return estado == "aceptado" and factura.get("dgii_estado") == ACEPTADO_CONDICIONAL


def _codigo(consulta: dict[str, Any]) -> int | None:
    try:
        return int(consulta.get("codigo"))
    except (TypeError, ValueError):
        return None


def _mensajes_rechazo(consulta: dict[str, Any]) -> str:
    partes = []
    for mensaje in consulta.get("mensajes") or []:
        if isinstance(mensaje, dict):
            valor = str(mensaje.get("valor") or "").strip()
            codigo = mensaje.get("codigo")
            if valor:
                partes.append(f"{valor} (código {codigo})" if codigo is not None else valor)
        elif str(mensaje).strip():
            partes.append(str(mensaje).strip())
    return "; ".join(partes) or "Rechazado por la DGII (sin mensajes de validación)"


def interpretar_resultado(consulta: dict[str, Any]) -> tuple[str, str, str | None] | None:
    """(estado, dgii_estado, error) de la factura según la consulta, o None
    si la DGII todavía no tiene un resultado definitivo (En Proceso / No
    encontrado): la factura se queda como está."""
    codigo = _codigo(consulta)
    if codigo == CODIGO_ACEPTADO:
        return "aceptado", consulta.get("estado") or "Aceptado", None
    if codigo == CODIGO_ACEPTADO_CONDICIONAL:
        return "aceptado", ACEPTADO_CONDICIONAL, None
    if codigo == CODIGO_RECHAZADO:
        return "rechazado", consulta.get("estado") or "Rechazado", _mensajes_rechazo(consulta)
    return None


async def actualizar_estado_factura(
    pool,
    factura: dict[str, Any],
    settings: FacturasApiSettings,
    *,
    auditar_sin_resultado: bool = True,
) -> dict[str, Any]:
    """Consulta el resultado del trackId de `factura` y lo aplica.

    Devuelve la factura actualizada, o la misma `factura` si la DGII aún no
    tiene un resultado definitivo. Lanza `ConsultaResultadoError` si
    consulta_resultado no responde (quien llama decide si eso es un 502 o
    solo un aviso en el log).
    """
    track_id = factura["track_id"]
    consulta = await consulta_resultado_client.consultar_resultado(
        settings.consulta_resultado_url, track_id
    )
    codigo = _codigo(consulta)
    resultado = interpretar_resultado(consulta)

    if resultado is not None or auditar_sin_resultado:
        await registrar_envio(
            pool,
            modulo=MODULO_AUDITORIA,
            ambiente=settings.app_env,
            rnc_emisor=factura.get("rnc_emisor") or settings.emisor_rnc,
            encf=factura.get("encf"),
            track_id=track_id,
            estado=consulta.get("estado"),
            codigo=str(codigo) if codigo is not None else None,
            respuesta=consulta,
        )

    if resultado is None:
        logger.info(
            "Factura %s (trackId %s): la DGII aún no tiene resultado (código %s)",
            factura["id"],
            track_id,
            codigo,
        )
        return factura

    estado, dgii_estado, error = resultado
    factura_id = uuid.UUID(str(factura["id"]))
    actualizada = await repository.registrar_resultado_consulta(
        pool,
        factura_id,
        track_id=track_id,
        estado=estado,
        dgii_estado=dgii_estado,
        dgii_codigo=str(codigo),
        consulta=consulta,
        error=error,
    )
    if actualizada is None:
        # Otra petición ya registró un resultado definitivo (o la factura
        # cambió de trackId) entre la lectura y esta actualización.
        return await repository.obtener_factura(pool, factura_id)
    logger.info("Factura %s (trackId %s): %s según la DGII", factura["id"], track_id, dgii_estado)
    return actualizada


async def sincronizar_pendientes(pool, settings: FacturasApiSettings) -> int:
    """Una iteración de la tarea de fondo. Devuelve cuántas facturas
    cambiaron de estado. Si consulta_resultado no responde, corta la
    iteración (no tiene sentido insistir con las demás) y lo deja en el log."""
    pendientes = await repository.listar_pendientes_de_resultado(
        pool, limite=LIMITE_POR_ITERACION, dias=DIAS_MAXIMOS_PENDIENTE
    )
    cambiadas = 0
    for factura in pendientes:
        try:
            actualizada = await actualizar_estado_factura(
                pool, factura, settings, auditar_sin_resultado=False
            )
        except ConsultaResultadoError as exc:
            logger.warning("Sincronización de estados DGII interrumpida: %s", exc)
            break
        except Exception:
            logger.exception("No se pudo actualizar el estado DGII de la factura %s", factura.get("id"))
            continue
        if actualizada.get("estado") != factura.get("estado"):
            cambiadas += 1
    return cambiadas


async def sincronizar_inventario_pendiente(pool, settings: FacturasApiSettings) -> int:
    """Reintenta la salida de mercancía de las facturas ya enviadas cuyo
    registro en inventario_api falló (`inventario_sincronizado = false`).
    Devuelve cuántas quedaron sincronizadas. Si inventario_api no responde,
    corta la iteración y lo intenta en la próxima."""
    pendientes = await repository.listar_inventario_pendiente(pool, limite=LIMITE_POR_ITERACION)
    sincronizadas = 0
    for factura in pendientes:
        fid = uuid.UUID(factura["id"])
        try:
            await inventario_client.registrar_salida_por_factura(
                settings.inventario_api_url,
                factura["id"],
                factura["items_inventario"],
                documento=factura.get("encf"),
            )
        except InventarioApiError as exc:
            await repository.registrar_sincronizacion_inventario(pool, fid, sincronizado=False, error=str(exc))
            logger.warning("Salida de inventario de la factura %s sigue pendiente: %s", factura["id"], exc)
            if "No se pudo contactar" in str(exc):
                break
            continue
        await repository.registrar_sincronizacion_inventario(pool, fid, sincronizado=True, error=None)
        sincronizadas += 1
    return sincronizadas


async def bucle_sincronizacion(pool, settings: FacturasApiSettings) -> None:
    """Tarea de fondo: cada `sincronizar_estado_cada_segundos` consulta las
    facturas pendientes. Cualquier excepción se registra y el bucle sigue;
    solo termina al cancelarse (apagado del servicio, ver `app.main.lifespan`)."""
    intervalo = settings.sincronizar_estado_cada_segundos
    logger.info("Sincronización de estados DGII cada %ss", intervalo)
    while True:
        await asyncio.sleep(intervalo)
        try:
            cambiadas = await sincronizar_pendientes(pool, settings)
            if cambiadas:
                logger.info("Sincronización de estados DGII: %s factura(s) actualizada(s)", cambiadas)
            inventario = await sincronizar_inventario_pendiente(pool, settings)
            if inventario:
                logger.info("Salidas de inventario reintentadas: %s factura(s) sincronizada(s)", inventario)
        except Exception:
            logger.exception("Falló la sincronización de estados DGII; se reintenta en %ss", intervalo)
