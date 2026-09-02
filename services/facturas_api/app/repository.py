"""Acceso a Postgres para `facturacion.facturas` (borrador -> XML -> firmado -> enviado).

A diferencia de `libs.ecf_common.db` (auditoría best-effort, nunca lanza),
este repositorio SÍ requiere una base de datos disponible: las facturas
generadas desde el frontend viven únicamente en Postgres, no hay otro lugar
donde persistirlas. Si `database_url` no está configurado, el servicio no
puede operar (ver `app.main`, que falla rápido en el lifespan si el pool es
None).
"""
from __future__ import annotations

import json
import uuid
from typing import Any

import asyncpg


class FacturaNoEncontradaError(Exception):
    pass


def _fila_a_dict(fila: asyncpg.Record) -> dict[str, Any]:
    datos = dict(fila)
    for campo in ("datos", "dgii_respuesta"):
        if isinstance(datos.get(campo), str):
            datos[campo] = json.loads(datos[campo])
    datos["id"] = str(datos["id"])
    return datos


async def crear_factura(
    pool: asyncpg.Pool,
    *,
    tipo_ecf: int,
    ambiente: str,
    rnc_emisor: str,
    encf: str | None,
    datos: dict[str, Any],
) -> dict[str, Any]:
    async with pool.acquire() as conn:
        fila = await conn.fetchrow(
            """
            INSERT INTO facturacion.facturas (tipo_ecf, ambiente, rnc_emisor, encf, datos)
            VALUES ($1, $2, $3, $4, $5)
            RETURNING *
            """,
            tipo_ecf,
            ambiente,
            rnc_emisor,
            encf,
            json.dumps(datos),
        )
    return _fila_a_dict(fila)


async def listar_facturas(
    pool: asyncpg.Pool,
    *,
    tipo_ecf: int | None = None,
    estado: str | None = None,
    limite: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    condiciones = []
    valores: list[Any] = []
    if tipo_ecf is not None:
        valores.append(tipo_ecf)
        condiciones.append(f"tipo_ecf = ${len(valores)}")
    if estado is not None:
        valores.append(estado)
        condiciones.append(f"estado = ${len(valores)}")
    where = f"WHERE {' AND '.join(condiciones)}" if condiciones else ""
    valores.extend([limite, offset])
    query = f"""
        SELECT id, tipo_ecf, ambiente, rnc_emisor, encf, estado,
               track_id, dgii_estado, dgii_codigo, error, creado_en, actualizado_en
        FROM facturacion.facturas
        {where}
        ORDER BY creado_en DESC
        LIMIT ${len(valores) - 1} OFFSET ${len(valores)}
    """
    async with pool.acquire() as conn:
        filas = await conn.fetch(query, *valores)
    return [dict(fila) | {"id": str(fila["id"])} for fila in filas]


async def obtener_factura(pool: asyncpg.Pool, factura_id: uuid.UUID) -> dict[str, Any]:
    async with pool.acquire() as conn:
        fila = await conn.fetchrow(
            "SELECT * FROM facturacion.facturas WHERE id = $1", factura_id
        )
    if fila is None:
        raise FacturaNoEncontradaError(str(factura_id))
    return _fila_a_dict(fila)


async def actualizar_datos(
    pool: asyncpg.Pool, factura_id: uuid.UUID, *, encf: str | None, datos: dict[str, Any]
) -> dict[str, Any]:
    """Actualiza los datos editables de una factura en estado 'borrador'."""
    async with pool.acquire() as conn:
        fila = await conn.fetchrow(
            """
            UPDATE facturacion.facturas
            SET encf = $2, datos = $3, actualizado_en = now()
            WHERE id = $1 AND estado = 'borrador'
            RETURNING *
            """,
            factura_id,
            encf,
            json.dumps(datos),
        )
    if fila is None:
        raise FacturaNoEncontradaError(str(factura_id))
    return _fila_a_dict(fila)


async def eliminar_factura(pool: asyncpg.Pool, factura_id: uuid.UUID) -> None:
    async with pool.acquire() as conn:
        resultado = await conn.execute(
            "DELETE FROM facturacion.facturas WHERE id = $1 AND estado = 'borrador'",
            factura_id,
        )
    if resultado == "DELETE 0":
        raise FacturaNoEncontradaError(str(factura_id))


async def guardar_xml_generado(
    pool: asyncpg.Pool, factura_id: uuid.UUID, xml_generado: str
) -> dict[str, Any]:
    async with pool.acquire() as conn:
        fila = await conn.fetchrow(
            """
            UPDATE facturacion.facturas
            SET xml_generado = $2, estado = 'generado', actualizado_en = now()
            WHERE id = $1
            RETURNING *
            """,
            factura_id,
            xml_generado,
        )
    if fila is None:
        raise FacturaNoEncontradaError(str(factura_id))
    return _fila_a_dict(fila)


async def guardar_xml_firmado(
    pool: asyncpg.Pool, factura_id: uuid.UUID, xml_firmado: str
) -> dict[str, Any]:
    async with pool.acquire() as conn:
        fila = await conn.fetchrow(
            """
            UPDATE facturacion.facturas
            SET xml_firmado = $2, estado = 'firmado', actualizado_en = now()
            WHERE id = $1
            RETURNING *
            """,
            factura_id,
            xml_firmado,
        )
    if fila is None:
        raise FacturaNoEncontradaError(str(factura_id))
    return _fila_a_dict(fila)


async def registrar_resultado_envio(
    pool: asyncpg.Pool,
    factura_id: uuid.UUID,
    *,
    estado: str,
    track_id: str | None = None,
    dgii_estado: str | None = None,
    dgii_codigo: str | None = None,
    dgii_respuesta: dict[str, Any] | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    async with pool.acquire() as conn:
        fila = await conn.fetchrow(
            """
            UPDATE facturacion.facturas
            SET estado = $2, track_id = $3, dgii_estado = $4, dgii_codigo = $5,
                dgii_respuesta = $6, error = $7, actualizado_en = now()
            WHERE id = $1
            RETURNING *
            """,
            factura_id,
            estado,
            track_id,
            dgii_estado,
            dgii_codigo,
            json.dumps(dgii_respuesta) if dgii_respuesta is not None else None,
            error,
        )
    if fila is None:
        raise FacturaNoEncontradaError(str(factura_id))
    return _fila_a_dict(fila)
