"""Postgres del modo contingencia: el estado compartido
(`facturacion.contingencia`, una fila) y los lotes de envío
(`facturacion.lotes_contingencia`). Tablas creadas por
app.migraciones.DDL_CONTINGENCIA. La lógica está en app.contingencia."""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import asyncpg

from libs.ecf_common.empresa import empresa_actual

from .repository import _fila_a_dict

# Clave del advisory lock que serializa el envío por lotes entre réplicas
# de facturas_api (distinta de la de migraciones).
_LOCK_ENVIO_LOTES = 7_002_002

_MONTO_TOTAL = """
    CASE WHEN (datos::jsonb #>> '{Encabezado,Totales,MontoTotal}') ~ '^-?[0-9]+(\\.[0-9]+)?$'
         THEN (datos::jsonb #>> '{Encabezado,Totales,MontoTotal}')::numeric ELSE 0 END
"""

# Contadores de un lote a partir de sus facturas: enviada = ya no está en
# 'contingencia'; rechazada = la DGII la rechazó al validarla ('rechazado')
# o al recibirla ('error').
_CONTADORES_LOTE = """
    SELECT lote_id,
           count(*) FILTER (WHERE estado <> 'contingencia') AS enviados,
           count(*) FILTER (WHERE estado = 'aceptado') AS aceptados,
           count(*) FILTER (WHERE estado IN ('rechazado', 'error')) AS rechazados
    FROM facturacion.facturas
    WHERE lote_id = ANY($1::text[])
    GROUP BY lote_id
"""


# --- Estado compartido ---------------------------------------------------------

async def leer_estado(pool: asyncpg.Pool) -> dict[str, Any]:
    async with pool.acquire() as conn:
        fila = await conn.fetchrow("SELECT * FROM facturacion.contingencia WHERE id = 1")
        pendientes = await conn.fetchrow(
            f"""
            SELECT count(*) AS cantidad, COALESCE(sum({_MONTO_TOTAL}), 0) AS monto,
                   min(COALESCE(firmado_en, contingencia_en, creado_en)) AS mas_antigua
            FROM facturacion.facturas WHERE estado = 'contingencia'
            """
        )
    estado = dict(fila) if fila is not None else {
        "activa": False, "desde": None, "errores_consecutivos": 0, "ultimo_intento": None,
        "ultimo_error": None, "ultimo_exito": None, "actualizado_en": None,
    }
    estado.pop("id", None)
    estado["pendientes"] = pendientes["cantidad"]
    estado["monto_pendiente"] = pendientes["monto"]
    estado["mas_antigua"] = pendientes["mas_antigua"]
    return estado


async def registrar_falla(pool: asyncpg.Pool, error: str) -> dict[str, Any]:
    """Un envío o una sonda no llegó a la DGII: entra (o sigue) en contingencia."""
    async with pool.acquire() as conn:
        fila = await conn.fetchrow(
            """
            INSERT INTO facturacion.contingencia AS c
                (id, activa, desde, errores_consecutivos, ultimo_intento, ultimo_error, actualizado_en)
            VALUES (1, true, now(), 1, now(), $1, now())
            ON CONFLICT (empresa_id, id) DO UPDATE SET
                activa = true,
                desde = COALESCE(c.desde, now()),
                errores_consecutivos = c.errores_consecutivos + 1,
                ultimo_intento = now(),
                ultimo_error = $1,
                actualizado_en = now()
            RETURNING *
            """,
            error,
        )
    return dict(fila)


async def registrar_exito(pool: asyncpg.Pool) -> dict[str, Any]:
    """La DGII respondió (envío o sonda): sale de contingencia."""
    async with pool.acquire() as conn:
        fila = await conn.fetchrow(
            """
            INSERT INTO facturacion.contingencia AS c (id, activa, ultimo_intento, ultimo_exito, actualizado_en)
            VALUES (1, false, now(), now(), now())
            ON CONFLICT (empresa_id, id) DO UPDATE SET
                activa = false, desde = NULL, errores_consecutivos = 0,
                ultimo_intento = now(), ultimo_exito = now(), actualizado_en = now()
            RETURNING *
            """
        )
    return dict(fila)


# --- Lotes ---------------------------------------------------------

def _fila_lote(fila: asyncpg.Record) -> dict[str, Any]:
    datos = dict(fila)
    datos["pendientes"] = max(datos["documentos"] - datos["enviados"], 0)
    return datos


async def tomar_lote(pool: asyncpg.Pool, *, tamano: int) -> dict[str, Any] | None:
    """El lote que toca enviar: el más antiguo sin terminar (en cola,
    enviando -- un proceso que se cortó -- o interrumpido) que todavía tenga
    facturas en 'contingencia'; si no hay, uno nuevo con las `tamano`
    facturas en contingencia más antiguas sin lote (por `firmado_en`). Los
    lotes sin terminar que ya no tienen pendientes se cierran como
    completados. None si no queda nada por enviar."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            abiertos = await conn.fetch(
                """
                SELECT l.*, EXISTS (
                    SELECT 1 FROM facturacion.facturas f WHERE f.lote_id = l.id AND f.estado = 'contingencia'
                ) AS con_pendientes
                FROM facturacion.lotes_contingencia l
                WHERE l.estado IN ('en_cola', 'enviando', 'interrumpido')
                ORDER BY l.creado_en, l.id
                FOR UPDATE OF l
                """
            )
            for lote in abiertos:
                if lote["con_pendientes"]:
                    return _fila_lote({k: v for k, v in dict(lote).items() if k != "con_pendientes"})
                await conn.execute(
                    """
                    UPDATE facturacion.lotes_contingencia
                    SET estado = 'completado', completado_en = COALESCE(completado_en, now()), actualizado_en = now()
                    WHERE id = $1
                    """,
                    lote["id"],
                )
            ids = await conn.fetch(
                """
                SELECT id FROM facturacion.facturas
                WHERE estado = 'contingencia' AND lote_id IS NULL
                ORDER BY COALESCE(firmado_en, contingencia_en, creado_en), creado_en, id
                LIMIT $1
                FOR UPDATE SKIP LOCKED
                """,
                tamano,
            )
            if not ids:
                return None
            numero = await conn.fetchval("SELECT nextval('facturacion.lotes_contingencia_seq')")
            lote_id = f"A-{numero:04d}"
            fila = await conn.fetchrow(
                """
                INSERT INTO facturacion.lotes_contingencia (id, documentos, estado)
                VALUES ($1, $2, 'en_cola')
                RETURNING *
                """,
                lote_id,
                len(ids),
            )
            await conn.execute(
                "UPDATE facturacion.facturas SET lote_id = $1 WHERE id = ANY($2::uuid[])",
                lote_id,
                [f["id"] for f in ids],
            )
    return _fila_lote(fila)


async def facturas_pendientes_del_lote(pool: asyncpg.Pool, lote_id: str) -> list[dict[str, Any]]:
    """Las facturas del lote que siguen en 'contingencia', en orden de firma."""
    async with pool.acquire() as conn:
        filas = await conn.fetch(
            """
            SELECT * FROM facturacion.facturas
            WHERE lote_id = $1 AND estado = 'contingencia'
            ORDER BY COALESCE(firmado_en, contingencia_en, creado_en), creado_en, id
            """,
            lote_id,
        )
    return [_fila_a_dict(f) for f in filas]


async def facturas_enviadas_del_lote(pool: asyncpg.Pool, lote_id: str) -> list[dict[str, Any]]:
    """Las del lote que esperan el resultado de la DGII ('enviado' con trackId)."""
    async with pool.acquire() as conn:
        filas = await conn.fetch(
            """
            SELECT id, tipo_ecf, ambiente, rnc_emisor, encf, estado,
                   track_id, dgii_estado, dgii_codigo, error, creado_en, actualizado_en
            FROM facturacion.facturas
            WHERE lote_id = $1 AND estado = 'enviado' AND track_id IS NOT NULL
            ORDER BY actualizado_en
            """,
            lote_id,
        )
    return [dict(f) | {"id": str(f["id"])} for f in filas]


async def marcar_lote(pool: asyncpg.Pool, lote_id: str, *, estado: str, error: str | None = None) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE facturacion.lotes_contingencia
            SET estado = $2,
                ultimo_error = CASE WHEN $2 = 'interrumpido' THEN $3 ELSE ultimo_error END,
                iniciado_en = CASE WHEN $2 = 'enviando' THEN COALESCE(iniciado_en, now()) ELSE iniciado_en END,
                completado_en = CASE WHEN $2 = 'completado' THEN now() ELSE completado_en END,
                actualizado_en = now()
            WHERE id = $1
            """,
            lote_id,
            estado,
            error,
        )


async def recalcular_lotes(pool: asyncpg.Pool, lote_ids: list[str] | None = None) -> None:
    """Recalcula enviados/aceptados/rechazados de los lotes indicados o, sin
    lista, de los abiertos y los que esperan resultados de la DGII."""
    async with pool.acquire() as conn:
        if lote_ids is None:
            lote_ids = [
                f["id"]
                for f in await conn.fetch(
                    """
                    SELECT id FROM facturacion.lotes_contingencia
                    WHERE estado <> 'completado' OR aceptados + rechazados < documentos
                       OR completado_en >= now() - interval '1 day'
                    """
                )
            ]
        if not lote_ids:
            return
        await conn.execute(
            f"""
            UPDATE facturacion.lotes_contingencia l
            SET enviados = c.enviados, aceptados = c.aceptados, rechazados = c.rechazados, actualizado_en = now()
            FROM ({_CONTADORES_LOTE}) c
            WHERE l.id = c.lote_id
              AND (l.enviados, l.aceptados, l.rechazados) IS DISTINCT FROM (c.enviados, c.aceptados, c.rechazados)
            """,
            lote_ids,
        )


async def listar_lotes(pool: asyncpg.Pool, *, limite: int = 20) -> list[dict[str, Any]]:
    async with pool.acquire() as conn:
        filas = await conn.fetch(
            "SELECT * FROM facturacion.lotes_contingencia ORDER BY creado_en DESC, id DESC LIMIT $1",
            limite,
        )
    return [_fila_lote(f) for f in filas]


async def interrumpir_lotes_en_curso(pool: asyncpg.Pool, error: str) -> None:
    """Lotes que quedaron 'enviando' cuando la conexión ya no está."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE facturacion.lotes_contingencia
            SET estado = 'interrumpido', ultimo_error = $1, actualizado_en = now()
            WHERE estado = 'enviando'
            """,
            error,
        )


@asynccontextmanager
async def candado_envio(pool: asyncpg.Pool):
    """Advisory lock de sesión mientras dura un envío por lotes: rinde True
    si se obtuvo, False si otro proceso ya está enviando."""
    async with pool.acquire() as conn:
        # (clave, empresa): cada empresa envía sus lotes por su cuenta.
        empresa_id = empresa_actual()
        obtenido = await conn.fetchval("SELECT pg_try_advisory_lock($1, $2)", _LOCK_ENVIO_LOTES, empresa_id)
        try:
            yield obtenido
        finally:
            if obtenido:
                await conn.execute("SELECT pg_advisory_unlock($1, $2)", _LOCK_ENVIO_LOTES, empresa_id)
