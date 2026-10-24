"""Auditoría de envíos a la DGII en PostgreSQL.

Best-effort y opcional: si `DATABASE_URL` está vacío o la base de datos no
está disponible, estas funciones son no-op. Un fallo al auditar NUNCA debe
interrumpir la comunicación real con la DGII (el envío del e-CF/RFCE/
aprobación/anulación ya sucedió; perder el registro de auditoría es un
problema menor comparado con reportar un error al llamador por algo que en
realidad sí se completó).

El esquema (`facturacion.envios_dgii`) se crea vía `db/init/001_schema.sql`:
en desarrollo local lo ejecuta el contenedor de Postgres en su primer
arranque (`docker-entrypoint-initdb.d`); en producción la base es Supabase
(ver docs/SUPABASE.md). Las escrituras solo ocurren desde este módulo.
"""
import json
import logging
from typing import Any
from urllib.parse import urlsplit

import asyncpg

logger = logging.getLogger(__name__)


def opciones_pool(database_url: str) -> dict[str, Any]:
    """Opciones extra de asyncpg según el destino de la conexión.

    El pooler de Supabase (Supavisor) en modo transacción -- puerto 6543 --
    no admite sentencias preparadas con nombre entre transacciones, así que
    hay que desactivar la caché de sentencias de asyncpg. En modo sesión
    (puerto 5432) o contra un Postgres directo no hace falta.
    """
    puerto = urlsplit(database_url).port
    if puerto == 6543:
        return {"statement_cache_size": 0}
    return {}


async def crear_pool(database_url: str, *, max_size: int = 5) -> asyncpg.Pool | None:
    """Crea el pool de conexiones si `database_url` está configurado.

    `max_size` pequeño para los módulos que solo leen el ambiente DGII
    activo: cada conexión cuenta contra el pooler de Supabase.

    Retorna None (sin lanzar) si `database_url` está vacío o si la conexión
    falla — el módulo sigue funcionando sin auditoría en ese caso.
    """
    if not database_url:
        return None
    try:
        return await asyncpg.create_pool(database_url, min_size=1, max_size=max_size, **opciones_pool(database_url))
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
