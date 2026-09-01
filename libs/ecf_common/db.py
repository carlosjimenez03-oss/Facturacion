"""Auditoría de envíos a la DGII en PostgreSQL.

Best-effort y opcional: si `DATABASE_URL` está vacío o la base de datos no
está disponible, estas funciones son no-op. Un fallo al auditar NUNCA debe
interrumpir la comunicación real con la DGII (el envío del e-CF/RFCE/
aprobación/anulación ya sucedió; perder el registro de auditoría es un
problema menor comparado con reportar un error al llamador por algo que en
realidad sí se completó).

El esquema (`facturacion.envios_dgii`) se crea vía `db/init/001_schema.sql`,
ejecutado automáticamente por el contenedor de Postgres en su primer arranque
(`docker-entrypoint-initdb.d`). Ese mismo esquema se expone de solo lectura
a través de PostgREST (rol `web_anon`); las escrituras solo ocurren desde
este módulo (vía asyncpg), nunca a través de la API REST de PostgREST.
"""
import json
import logging
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)


async def crear_pool(database_url: str) -> asyncpg.Pool | None:
    """Crea el pool de conexiones si `database_url` está configurado.

    Retorna None (sin lanzar) si `database_url` está vacío o si la conexión
    falla — el módulo sigue funcionando sin auditoría en ese caso.
    """
    if not database_url:
        return None
    try:
        return await asyncpg.create_pool(database_url, min_size=1, max_size=5)
    except Exception:
        logger.exception("No se pudo conectar a la base de datos de auditoría")
        return None


async def cerrar_pool(pool: asyncpg.Pool | None) -> None:
    if pool is not None:
        await pool.close()


async def registrar_envio(
    pool: asyncpg.Pool | None,
    *,
    modulo: str,
    ambiente: str,
    rnc_emisor: str,
    encf: str | None = None,
    track_id: str | None = None,
    estado: str | None = None,
    codigo: str | None = None,
    respuesta: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    """Inserta una fila de auditoría en facturacion.envios_dgii.

    No lanza excepciones: cualquier fallo se registra en el log y se ignora.
    """
    if pool is None:
        return
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO facturacion.envios_dgii
                    (modulo, ambiente, rnc_emisor, encf, track_id, estado, codigo, respuesta, error)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                """,
                modulo,
                ambiente,
                rnc_emisor,
                encf,
                track_id,
                estado,
                codigo,
                json.dumps(respuesta) if respuesta is not None else None,
                error,
            )
    except Exception:
        logger.exception("No se pudo registrar el envío en la base de datos de auditoría")
