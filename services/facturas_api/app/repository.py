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
    for campo in ("datos", "dgii_respuesta", "items_inventario"):
        if isinstance(datos.get(campo), str):
            datos[campo] = json.loads(datos[campo])
    datos["id"] = str(datos["id"])
    return datos


def _items_inventario_json(items_inventario: list[dict[str, Any]] | None) -> str | None:
    if items_inventario is None:
        return None
    return json.dumps(
        [
            {
                **item,
                "producto_id": str(item["producto_id"]),
                "cantidad": str(item["cantidad"]),
                **{
                    campo: str(item[campo])
                    for campo in ("almacen_id", "lote_id")
                    if item.get(campo) is not None
                },
            }
            for item in items_inventario
        ]
    )


async def crear_factura(
    pool: asyncpg.Pool,
    *,
    tipo_ecf: int,
    ambiente: str,
    rnc_emisor: str,
    encf: str | None,
    datos: dict[str, Any],
    items_inventario: list[dict[str, Any]] | None = None,
    vendedor_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    async with pool.acquire() as conn:
        fila = await conn.fetchrow(
            """
            INSERT INTO facturacion.facturas
                (tipo_ecf, ambiente, rnc_emisor, encf, datos, items_inventario, vendedor_id)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            RETURNING *
            """,
            tipo_ecf,
            ambiente,
            rnc_emisor,
            encf,
            json.dumps(datos),
            _items_inventario_json(items_inventario),
            vendedor_id,
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
               track_id, dgii_estado, dgii_codigo, error, creado_en, actualizado_en,
               firmado_en, emitida_en_contingencia, contingencia_en, lote_id
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
    pool: asyncpg.Pool,
    factura_id: uuid.UUID,
    *,
    encf: str | None,
    datos: dict[str, Any],
    items_inventario: list[dict[str, Any]] | None = None,
    vendedor_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """Actualiza los datos editables de una factura en estado 'borrador'."""
    async with pool.acquire() as conn:
        fila = await conn.fetchrow(
            """
            UPDATE facturacion.facturas
            SET encf = $2, datos = $3, items_inventario = $4, vendedor_id = $5, actualizado_en = now()
            WHERE id = $1 AND estado = 'borrador'
            RETURNING *
            """,
            factura_id,
            encf,
            json.dumps(datos),
            _items_inventario_json(items_inventario),
            vendedor_id,
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
            SET xml_generado = $2, estado = 'generado',
                -- La firma anterior es de otro XML (otra FechaHoraFirma):
                -- se vuelve a firmar al enviar (ver app.envio.asegurar_firma).
                xml_firmado = NULL, firmado_en = NULL,
                actualizado_en = now()
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
            SET xml_firmado = $2, firmado_en = now(),
                -- Firmar una factura ya enviada (p. ej. para generar su PDF)
                -- no debe pisar el estado que informó la DGII.
                estado = CASE WHEN estado IN ('borrador', 'generado', 'firmado') THEN 'firmado' ELSE estado END,
                actualizado_en = now()
            WHERE id = $1
            RETURNING *
            """,
            factura_id,
            xml_firmado,
        )
    if fila is None:
        raise FacturaNoEncontradaError(str(factura_id))
    return _fila_a_dict(fila)


async def guardar_firma_envio(
    pool: asyncpg.Pool, factura_id: uuid.UUID, xml_firmado: str
) -> dict[str, Any]:
    """Guarda el XML firmado que se va a enviar a la DGII (ver
    app.envio.asegurar_firma) sin cambiar el estado: si el envío no llega,
    es el que queda en contingencia y el que lleva la representación impresa."""
    async with pool.acquire() as conn:
        fila = await conn.fetchrow(
            """
            UPDATE facturacion.facturas
            SET xml_firmado = $2, firmado_en = now(), actualizado_en = now()
            WHERE id = $1
            RETURNING *
            """,
            factura_id,
            xml_firmado,
        )
    if fila is None:
        raise FacturaNoEncontradaError(str(factura_id))
    return _fila_a_dict(fila)


async def marcar_contingencia(
    pool: asyncpg.Pool, factura_id: uuid.UUID, *, error: str
) -> dict[str, Any]:
    """El envío no llegó a la DGII por conectividad: la factura queda
    'contingencia' (firmada, entregable al comprador con la leyenda,
    pendiente de envío) y la tarea de fondo la enviará en un lote. Solo
    desde estados sin envío a la DGII (nunca pisa un trackId)."""
    async with pool.acquire() as conn:
        fila = await conn.fetchrow(
            """
            UPDATE facturacion.facturas
            SET estado = 'contingencia', emitida_en_contingencia = true,
                contingencia_en = COALESCE(contingencia_en, now()), error = $2,
                intentos_envio = intentos_envio + 1, ultimo_intento_envio = now(), actualizado_en = now()
            WHERE id = $1 AND track_id IS NULL AND xml_firmado IS NOT NULL
              AND estado IN ('generado', 'firmado', 'error', 'contingencia')
            RETURNING *
            """,
            factura_id,
            error,
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
                dgii_respuesta = $6, error = $7, intentos_envio = intentos_envio + 1,
                ultimo_intento_envio = now(), actualizado_en = now()
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


async def registrar_sincronizacion_inventario(
    pool: asyncpg.Pool, factura_id: uuid.UUID, *, sincronizado: bool, error: str | None
) -> None:
    """Deja constancia de si la salida de mercancía (POST a inventario_api
    tras un envío exitoso a la DGII) se registró o falló -- ver
    `app.main.enviar_factura`. Un fallo aquí es best-effort: no debe romper
    la respuesta de /enviar (el e-CF ya se envió, eso ya no se puede
    deshacer), solo se registra para poder detectarlo y resolverlo."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE facturacion.facturas
            SET inventario_sincronizado = $2, inventario_error = $3
            WHERE id = $1
            """,
            factura_id,
            sincronizado,
            error,
        )


# Estados desde los que el resultado de la consulta por trackId puede mover
# la factura: 'enviado' (lo normal), 'error' con trackId (la DGII sí recibió
# el comprobante) y 'aceptado' condicional (la DGII puede reclasificarlo).
# Cualquier otro estado ya es definitivo y nunca se pisa. Debe coincidir con
# `app.estado_dgii.admite_actualizacion`.
_CONDICION_ACTUALIZABLE = """
    track_id IS NOT NULL
    AND (
        estado IN ('enviado', 'error')
        OR (estado = 'aceptado' AND dgii_estado = 'Aceptado Condicional')
    )
"""


async def registrar_resultado_consulta(
    pool: asyncpg.Pool,
    factura_id: uuid.UUID,
    *,
    track_id: str,
    estado: str,
    dgii_estado: str | None,
    dgii_codigo: str | None,
    consulta: dict[str, Any],
    error: str | None,
) -> dict[str, Any] | None:
    """Aplica el resultado de la DGII (consulta por trackId) a la factura.

    Solo actualiza si la fila sigue en un estado actualizable y con el mismo
    trackId consultado -- si otra petición (el botón del frontend, la tarea
    de fondo o la consulta inmediata tras enviar) ya la movió a un estado
    definitivo, no se pisa nada y se devuelve None. Repetir la misma consulta
    deja la factura igual (idempotente).

    `dgii_respuesta` pasa a ser la respuesta de la consulta (es lo que lee el
    panel de rechazo del frontend: `mensajes`, `secuenciaUtilizada`), y la
    respuesta original del envío (`recepcion`: trackId/error/mensaje) se
    conserva bajo la clave `envio` -- también tras consultas sucesivas.
    """
    async with pool.acquire() as conn:
        fila = await conn.fetchrow(
            f"""
            UPDATE facturacion.facturas
            SET estado = $3, dgii_estado = $4, dgii_codigo = $5,
                dgii_respuesta = $6::jsonb || jsonb_build_object(
                    'envio',
                    CASE WHEN jsonb_typeof(dgii_respuesta) = 'object' AND dgii_respuesta ? 'envio'
                         THEN dgii_respuesta -> 'envio'
                         ELSE dgii_respuesta
                    END
                ),
                error = $7, actualizado_en = now()
            WHERE id = $1 AND track_id = $2 AND {_CONDICION_ACTUALIZABLE}
            RETURNING *
            """,
            factura_id,
            track_id,
            estado,
            dgii_estado,
            dgii_codigo,
            json.dumps(consulta),
            error,
        )
    return _fila_a_dict(fila) if fila is not None else None


async def listar_inventario_pendiente(
    pool: asyncpg.Pool, *, limite: int = 50, dias: int = 90
) -> list[dict[str, Any]]:
    """Facturas que llegaron a la DGII (con trackId, no rechazadas) cuya salida
    de mercancía no se pudo registrar en inventario_api (p. ej. inventario_api
    caído o reiniciándose durante un despliegue). La tarea de fondo de
    `app.estado_dgii` las reintenta: la salida por factura es idempotente."""
    async with pool.acquire() as conn:
        filas = await conn.fetch(
            """
            SELECT id, encf, estado, items_inventario
            FROM facturacion.facturas
            WHERE NOT inventario_sincronizado
              AND track_id IS NOT NULL
              AND estado IN ('enviado', 'aceptado')
              AND CASE WHEN jsonb_typeof(items_inventario) = 'array'
                       THEN jsonb_array_length(items_inventario) > 0 ELSE false END
              AND actualizado_en >= now() - make_interval(days => $2)
            ORDER BY actualizado_en ASC
            LIMIT $1
            """,
            limite,
            dias,
        )
    return [_fila_a_dict(fila) for fila in filas]


async def listar_pendientes_de_resultado(
    pool: asyncpg.Pool, *, limite: int = 50, dias: int = 30
) -> list[dict[str, Any]]:
    """Facturas en 'enviado' con trackId enviadas en los últimos `dias`, las
    más antiguas primero -- las que la tarea de fondo de `app.estado_dgii`
    le consulta a la DGII. `actualizado_en` marca el envío: mientras la DGII
    responde "En Proceso" la factura no se toca."""
    async with pool.acquire() as conn:
        filas = await conn.fetch(
            """
            SELECT id, tipo_ecf, ambiente, rnc_emisor, encf, estado,
                   track_id, dgii_estado, dgii_codigo, error, creado_en, actualizado_en
            FROM facturacion.facturas
            WHERE estado = 'enviado'
              AND track_id IS NOT NULL
              AND actualizado_en >= now() - make_interval(days => $2)
            ORDER BY actualizado_en ASC
            LIMIT $1
            """,
            limite,
            dias,
        )
    return [dict(fila) | {"id": str(fila["id"])} for fila in filas]
