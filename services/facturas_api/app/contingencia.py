"""Modo contingencia: e-CF firmados que no se pudieron enviar a la DGII por
falta de conectividad, y su envío posterior por lotes.

Reglas de la DGII que se aplican (fuentes en docs/dgii/CONTINGENCIA.md):
Decreto 587-24 (Reglamento de la Ley 32-23), arts. 40-43, e Instructivo de
Contingencia de Facturación Electrónica de la DGII. Si el emisor puede
generar y firmar el e-CF pero no transmitirlo (falta de conectividad o
indisponibilidad de la DGII), lo emite igual -- con su e-NCF, firmado --,
entrega al comprador la representación impresa con la leyenda de
contingencia y lo envía a la DGII al restablecerse la comunicación, en un
plazo no mayor de 72 horas. La serie B (papel) solo aplica cuando el
sistema no puede generar el e-CF (contingencia notificada en la Oficina
Virtual, máximo 15 días). `IdDoc.IndicadorEnvioDiferido` NO se usa: es la
modalidad de "envío diferido autorizado" por la DGII, no la contingencia, y
el XML no puede cambiar después de entregado (cambiaría su firma y el
código de seguridad de la representación impresa).

Transiciones de la factura:

    generado/firmado/error (sin trackId) --envío sin conexión--> contingencia
    contingencia --envío en lote, trackId--> enviado -> aceptado | rechazado
    contingencia --la DGII rechaza al recibir (4xx/502)--> error

Estado compartido (`facturacion.contingencia`): lo activa cualquier envío o
sonda que no llega a la DGII y lo desactiva cualquier envío o sonda que sí
llega. Mientras está activo, `POST /facturas/{id}/enviar` hace primero una
sonda corta (`sondear`) y, si la DGII sigue sin responder, deja la factura
en contingencia sin esperar el timeout completo del envío.

Envío por lotes (`enviar_pendientes`, desde la tarea de fondo de
app.estado_dgii y desde `POST /contingencia/reintentar`): con la sonda en
verde toma el lote abierto más antiguo o arma uno nuevo con las
`contingencia_lote_tamano` facturas más antiguas, las envía en orden y sigue
con el flujo normal de cada una (salida de inventario, 606 de los tipos
41/43/47 y consulta del resultado por trackId). Si a mitad del lote se cae
la conexión, el lote queda 'interrumpido' y se retoma en la próxima vuelta;
al terminar sin fallas se sale de contingencia.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

import httpx

from . import contingencia_repository, envio, estado_dgii, repository, representacion_impresa
from .config import FacturasApiSettings
from .consulta_resultado_client import ConsultaResultadoError

logger = logging.getLogger(__name__)

# Leyenda que exige la DGII en la representación impresa de un e-CF emitido
# en contingencia (Instructivo de Contingencia de Facturación Electrónica).
LEYENDA_CONTINGENCIA = representacion_impresa.LEYENDA_CONTINGENCIA

MENSAJE_EMITIDA = "Emitida en contingencia — se enviará automáticamente a la DGII al restablecerse la conexión."


@dataclass
class ResultadoLotes:
    disponible: bool
    detalle: str | None = None
    lotes: list[str] = field(default_factory=list)
    enviados: int = 0
    rechazados: int = 0
    interrumpido: bool = False
    ocupado: bool = False


# --- Sonda ---------------------------------------------------------

async def sondear(settings: FacturasApiSettings) -> tuple[bool, str | None]:
    """Sonda corta de conectividad con la DGII a través de `recepcion`
    (GET /disponibilidad). Un `recepcion` anterior sin la sonda (404) cuenta
    como disponible: se intenta el envío y su resultado decide."""
    try:
        async with httpx.AsyncClient(timeout=settings.timeout_sonda_segundos) as client:
            respuesta = await client.get(f"{settings.recepcion_service_url}/disponibilidad")
    except httpx.HTTPError as exc:
        return False, f"No se pudo contactar recepcion: {exc.__class__.__name__} {exc}".strip()
    if respuesta.status_code == 404:
        return True, None
    if respuesta.status_code >= 500:
        return False, f"recepcion respondió HTTP {respuesta.status_code}"
    try:
        cuerpo = respuesta.json()
    except ValueError:
        return True, None
    return bool(cuerpo.get("disponible", True)), cuerpo.get("detalle")


# --- Estado compartido (best-effort: nunca debe impedir facturar) ---------------------------------------------------------

async def leer_estado_seguro(pool) -> dict[str, Any] | None:
    try:
        return await contingencia_repository.leer_estado(pool)
    except Exception:
        logger.exception("No se pudo leer el estado de contingencia")
        return None


async def registrar_falla_segura(pool, error: str) -> None:
    try:
        await contingencia_repository.registrar_falla(pool, error)
    except Exception:
        logger.exception("No se pudo registrar la falla de conectividad")


async def registrar_exito_seguro(pool) -> None:
    try:
        await contingencia_repository.registrar_exito(pool)
    except Exception:
        logger.exception("No se pudo registrar la salida de contingencia")


def vencimiento(estado: dict[str, Any], settings: FacturasApiSettings):
    """Fecha límite para enviar la factura pendiente más antigua (72 horas)."""
    if not estado.get("mas_antigua"):
        return None
    return estado["mas_antigua"] + timedelta(hours=settings.contingencia_horas_limite)


# --- Envío por lotes ---------------------------------------------------------

_candado_local = asyncio.Lock()
_tarea_en_curso: asyncio.Task | None = None


async def _enviar_lote(pool, settings: FacturasApiSettings, lote: dict[str, Any], resultado: ResultadoLotes) -> bool:
    """Envía las facturas pendientes del lote. False si se cortó la conexión."""
    lote_id = lote["id"]
    resultado.lotes.append(lote_id)
    await contingencia_repository.marcar_lote(pool, lote_id, estado="enviando")
    for factura in await contingencia_repository.facturas_pendientes_del_lote(pool, lote_id):
        fid = uuid.UUID(factura["id"])
        envio_ = await envio.enviar_a_recepcion(settings, factura, firmado=True)
        if envio_.sin_conexion:
            await repository.marcar_contingencia(pool, fid, error=envio_.error or "La DGII no responde")
            await contingencia_repository.marcar_lote(pool, lote_id, estado="interrumpido", error=envio_.error)
            await contingencia_repository.registrar_falla(pool, envio_.error or "La DGII no responde")
            await contingencia_repository.recalcular_lotes(pool, [lote_id])
            resultado.interrumpido = True
            resultado.disponible = False
            resultado.detalle = envio_.error
            logger.warning("Lote %s interrumpido en %s: %s", lote_id, factura.get("encf"), envio_.error)
            return False
        if envio_.exitoso:
            actualizada = await repository.registrar_resultado_envio(
                pool, fid, estado="enviado", track_id=envio_.track_id, dgii_respuesta=envio_.cuerpo, error=envio_.error
            )
            await envio.aplicar_flujo_tras_envio(pool, actualizada, settings)
            resultado.enviados += 1
        else:
            await repository.registrar_resultado_envio(
                pool, fid, estado="error", dgii_respuesta=envio_.cuerpo, error=envio_.error
            )
            resultado.rechazados += 1
            logger.warning("Factura %s del lote %s rechazada al enviarla: %s", factura.get("encf"), lote_id, envio_.error)
        await contingencia_repository.recalcular_lotes(pool, [lote_id])
    await contingencia_repository.marcar_lote(pool, lote_id, estado="completado")
    await _consultar_resultados(pool, settings, lote_id)
    await contingencia_repository.recalcular_lotes(pool, [lote_id])
    return True


async def _consultar_resultados(pool, settings: FacturasApiSettings, lote_id: str) -> None:
    """Consulta una vez el resultado de las facturas enviadas del lote; las
    que sigan "En Proceso" las resuelve la tarea de fondo."""
    for factura in await contingencia_repository.facturas_enviadas_del_lote(pool, lote_id):
        try:
            await estado_dgii.actualizar_estado_factura(pool, factura, settings, auditar_sin_resultado=False)
        except ConsultaResultadoError as exc:
            logger.warning("Consulta de resultados del lote %s pospuesta: %s", lote_id, exc)
            return
        except Exception:
            logger.exception("No se pudo consultar el resultado de la factura %s", factura.get("id"))


async def enviar_pendientes(pool, settings: FacturasApiSettings, *, sonda_hecha: bool = False) -> ResultadoLotes:
    """Una pasada del envío de lo pendiente de contingencia (ver el docstring
    del módulo). Sin pendientes solo sondea si la contingencia está activa,
    para salir de ella en cuanto la DGII responda."""
    if _candado_local.locked():
        return ResultadoLotes(disponible=True, ocupado=True)
    async with _candado_local:
        async with contingencia_repository.candado_envio(pool) as obtenido:
            if not obtenido:
                return ResultadoLotes(disponible=True, ocupado=True)
            return await _enviar_pendientes(pool, settings, sonda_hecha=sonda_hecha)


async def _enviar_pendientes(pool, settings: FacturasApiSettings, *, sonda_hecha: bool) -> ResultadoLotes:
    estado = await contingencia_repository.leer_estado(pool)
    if not sonda_hecha:
        disponible, detalle = await sondear(settings)
        if not disponible:
            await contingencia_repository.registrar_falla(pool, detalle or "La DGII no responde")
            await contingencia_repository.interrumpir_lotes_en_curso(pool, detalle or "La DGII no responde")
            return ResultadoLotes(disponible=False, detalle=detalle)
    resultado = ResultadoLotes(disponible=True)
    if not estado["pendientes"]:
        if estado["activa"]:
            await contingencia_repository.registrar_exito(pool)
            logger.info("La DGII responde de nuevo: fin del modo contingencia")
        return resultado
    tamano = max(1, settings.contingencia_lote_tamano)
    while True:
        lote = await contingencia_repository.tomar_lote(pool, tamano=tamano)
        if lote is None:
            break
        if not await _enviar_lote(pool, settings, lote, resultado):
            return resultado
    await contingencia_repository.registrar_exito(pool)
    logger.info(
        "Contingencia: %s e-CF enviados en %s lote(s) (%s rechazados al recibirlos)",
        resultado.enviados,
        len(resultado.lotes),
        resultado.rechazados,
    )
    return resultado


def lanzar_envio_pendientes(pool, settings: FacturasApiSettings, *, sonda_hecha: bool = False) -> bool:
    """Lanza `enviar_pendientes` en segundo plano (si no hay uno en curso en
    este proceso). Devuelve si se lanzó."""
    global _tarea_en_curso
    if _tarea_en_curso is not None and not _tarea_en_curso.done():
        return False

    async def _correr():
        try:
            await enviar_pendientes(pool, settings, sonda_hecha=sonda_hecha)
        except Exception:
            logger.exception("Falló el envío de lo pendiente de contingencia")

    _tarea_en_curso = asyncio.create_task(_correr())
    return True


def envio_en_curso() -> bool:
    return (_tarea_en_curso is not None and not _tarea_en_curso.done()) or _candado_local.locked()
