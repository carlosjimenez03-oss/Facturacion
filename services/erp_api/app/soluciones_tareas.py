"""Tarea de fondo de Kontara Ventas: cada `soluciones_tarea_intervalo_horas`
(24 h por defecto) genera la cola de envíos de las automatizaciones activas
y entrega los pendientes por los canales configurados. La arranca el
lifespan del router de Soluciones (`soluciones.py`), después de que
`main.py` creó el pool.

Con varias réplicas, el advisory lock hace que solo una ejecute el ciclo; y
aunque corrieran dos, `clave_unica` evita duplicar mensajes.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import asyncpg

from . import soluciones_repository as repo
from .config import get_settings
from .soluciones_canales import (
    CanalError,
    FacturasApiError,
    RequierePlantillaError,
    SolucionesSettings,
    canales_de_envio,
    representacion_impresa,
)

logger = logging.getLogger(__name__)

_LOCK_TAREA_VENTAS = 7_016_001


async def entregar_pendientes(pool: asyncpg.Pool, settings: SolucionesSettings) -> dict[str, int]:
    """Entrega por SMTP / WhatsApp (los de Configuración o, si no, los de las
    variables de entorno) los envíos de los canales configurados. Sin canal
    no se toca nada: quedan 'pendiente_canal'."""
    salida = await canales_de_envio(pool, settings)
    canales = [c for c, ok in salida.configurados().items() if ok]
    resumen = {"enviados": 0, "errores": 0}
    for envio in await repo.envios_por_entregar(pool, canales):
        error = None
        definitivo = False
        try:
            if envio["canal"] == "whatsapp":
                await salida.enviar_whatsapp(envio["destino"], envio["mensaje"])
            else:
                adjunto = None
                if envio["automatizacion"] == "fidelizacion" and envio["factura_id"]:
                    try:
                        firmada = await repo.xml_firmado_factura(pool, envio["factura_id"]) is not None
                        pdf = await representacion_impresa(settings, envio["factura_id"], firmada=firmada)
                        adjunto = (f"{envio['encf'] or envio['factura_id']}.pdf", pdf)
                    except FacturasApiError as exc:
                        raise CanalError(f"No se pudo adjuntar la representación impresa: {exc}") from exc
                await salida.enviar_correo(envio["destino"], envio["asunto"], envio["mensaje"], adjunto)
        except RequierePlantillaError as exc:
            # Fuera de la ventana de 24 h de WhatsApp: reintentar no sirve.
            error, definitivo = str(exc), True
        except CanalError as exc:
            error = str(exc)
        await repo.registrar_entrega(pool, envio["id"], error=error, definitivo=definitivo)
        resumen["errores" if error else "enviados"] += 1
    return resumen


async def ejecutar_ciclo(pool: asyncpg.Pool, settings: SolucionesSettings) -> dict[str, Any]:
    async with pool.acquire() as conn:
        if not await conn.fetchval("SELECT pg_try_advisory_lock($1)", _LOCK_TAREA_VENTAS):
            return {"creados": 0, "motivo": "Otra réplica está ejecutando las automatizaciones"}
        try:
            resultado = await repo.generar_envios(pool, empresa_configurada=get_settings().emisor_razon_social)
            resultado["entrega"] = await entregar_pendientes(pool, settings)
            return resultado
        finally:
            await conn.execute("SELECT pg_advisory_unlock($1)", _LOCK_TAREA_VENTAS)


async def bucle(pool_de, settings: SolucionesSettings) -> None:
    """`pool_de()` devuelve el pool de la app (puede no existir aún)."""
    await asyncio.sleep(settings.soluciones_tarea_espera_inicial_segundos)
    while True:
        pool = pool_de()
        if isinstance(pool, asyncpg.Pool):
            try:
                resultado = await ejecutar_ciclo(pool, settings)
                logger.info("Kontara Ventas: %s", resultado)
            except Exception:  # la tarea nunca debe morir por un ciclo fallido
                logger.exception("Falló el ciclo de automatizaciones de Kontara Ventas")
        await asyncio.sleep(settings.soluciones_tarea_intervalo_horas * 3600)
