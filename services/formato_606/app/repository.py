"""Acceso a Postgres para compras606.registros -- ver
db/init/006_compras606.sql para el esquema y docs/dgii/FORMATO_606.md para
el significado de cada campo.
"""
from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import asyncpg

from libs.ecf_common.compras606 import periodo_de_fecha, tipo_identificacion


class RegistroNoEncontradoError(Exception):
    pass


class RegistroNoEditableError(Exception):
    """Solo los renglones de origen 'manual' se pueden borrar."""


_CAMPOS_ACTUALIZABLES = (
    "tipo_bienes_servicios",
    "fecha_pago",
    "itbis_retenido",
    "itbis_proporcionalidad",
    "itbis_costo",
    "itbis_percibido",
    "tipo_retencion_isr",
    "monto_retencion_renta",
    "isr_percibido",
    "impuesto_selectivo_consumo",
    "otros_impuestos",
    "monto_propina_legal",
    "forma_pago",
)


def _con_calculados(fila: asyncpg.Record) -> dict[str, Any]:
    datos = dict(fila)
    datos["id"] = str(datos["id"])
    datos["monto_facturado"] = datos["monto_servicios"] + datos["monto_bienes"]
    datos["itbis_adelantar"] = datos["itbis_facturado"] - datos["itbis_costo"]
    return datos


async def listar_por_periodo(pool: asyncpg.Pool, periodo: str) -> list[dict[str, Any]]:
    async with pool.acquire() as conn:
        filas = await conn.fetch(
            "SELECT * FROM compras606.registros WHERE periodo = $1 ORDER BY fecha_comprobante, ncf",
            periodo,
        )
    return [_con_calculados(fila) for fila in filas]


async def listar_periodos(pool: asyncpg.Pool) -> list[dict[str, Any]]:
    """Un resumen por periodo (cantidad de registros, pendientes de
    clasificar) -- para la pantalla de selección de periodo."""
    async with pool.acquire() as conn:
        filas = await conn.fetch(
            """
            SELECT
                periodo,
                count(*) AS total,
                count(*) FILTER (
                    WHERE tipo_bienes_servicios IS NULL OR forma_pago IS NULL
                ) AS pendientes
            FROM compras606.registros
            GROUP BY periodo
            ORDER BY periodo DESC
            """
        )
    return [dict(fila) for fila in filas]


async def obtener(pool: asyncpg.Pool, registro_id: uuid.UUID) -> dict[str, Any]:
    async with pool.acquire() as conn:
        fila = await conn.fetchrow("SELECT * FROM compras606.registros WHERE id = $1", registro_id)
    if fila is None:
        raise RegistroNoEncontradoError(str(registro_id))
    return _con_calculados(fila)


async def actualizar_clasificacion(
    pool: asyncpg.Pool, registro_id: uuid.UUID, campos: dict[str, Any]
) -> dict[str, Any]:
    asignaciones = []
    valores: list[Any] = []
    for nombre in _CAMPOS_ACTUALIZABLES:
        if nombre not in campos:
            continue
        valores.append(campos[nombre])
        asignaciones.append(f"{nombre} = ${len(valores)}")
    valores.append(registro_id)
    async with pool.acquire() as conn:
        fila = await conn.fetchrow(
            f"""
            UPDATE compras606.registros
            SET {', '.join(asignaciones)}, actualizado_en = now()
            WHERE id = ${len(valores)}
            RETURNING *
            """,
            *valores,
        )
    if fila is None:
        raise RegistroNoEncontradoError(str(registro_id))
    return _con_calculados(fila)


async def crear_manual(pool: asyncpg.Pool, datos: dict[str, Any]) -> dict[str, Any]:
    rnc_cedula = datos["rnc_cedula"]
    fecha_comprobante = datos["fecha_comprobante"]
    async with pool.acquire() as conn:
        fila = await conn.fetchrow(
            """
            INSERT INTO compras606.registros
                (periodo, origen, referencia_tipo, referencia_id, rnc_cedula, tipo_id,
                 tipo_bienes_servicios, ncf, ncf_modificado, fecha_comprobante, fecha_pago,
                 monto_servicios, monto_bienes, itbis_facturado, itbis_retenido,
                 itbis_proporcionalidad, itbis_costo, itbis_percibido, tipo_retencion_isr,
                 monto_retencion_renta, isr_percibido, impuesto_selectivo_consumo,
                 otros_impuestos, monto_propina_legal, forma_pago)
            VALUES ($1, 'manual', NULL, NULL, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12,
                    $13, $14, $15, $16, $17, $18, $19, $20, $21, $22)
            RETURNING *
            """,
            periodo_de_fecha(fecha_comprobante),
            rnc_cedula,
            tipo_identificacion(rnc_cedula),
            datos.get("tipo_bienes_servicios"),
            datos["ncf"],
            datos.get("ncf_modificado"),
            fecha_comprobante,
            datos.get("fecha_pago"),
            datos.get("monto_servicios", Decimal("0")),
            datos.get("monto_bienes", Decimal("0")),
            datos.get("itbis_facturado", Decimal("0")),
            datos.get("itbis_retenido", Decimal("0")),
            datos.get("itbis_proporcionalidad", Decimal("0")),
            datos.get("itbis_costo", Decimal("0")),
            datos.get("itbis_percibido", Decimal("0")),
            datos.get("tipo_retencion_isr"),
            datos.get("monto_retencion_renta", Decimal("0")),
            datos.get("isr_percibido", Decimal("0")),
            datos.get("impuesto_selectivo_consumo", Decimal("0")),
            datos.get("otros_impuestos", Decimal("0")),
            datos.get("monto_propina_legal", Decimal("0")),
            datos.get("forma_pago"),
        )
    return _con_calculados(fila)


async def eliminar_manual(pool: asyncpg.Pool, registro_id: uuid.UUID) -> None:
    """Solo se pueden borrar renglones agregados a mano -- los que vienen de
    un e-CF (recibido o autoemitido) se corrigen desde su origen, no aquí."""
    async with pool.acquire() as conn:
        origen = await conn.fetchval(
            "SELECT origen FROM compras606.registros WHERE id = $1", registro_id
        )
        if origen is None:
            raise RegistroNoEncontradoError(str(registro_id))
        if origen != "manual":
            raise RegistroNoEditableError(str(registro_id))
        await conn.execute("DELETE FROM compras606.registros WHERE id = $1", registro_id)
