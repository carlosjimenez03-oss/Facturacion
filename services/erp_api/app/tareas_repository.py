"""Mis tareas (Fase 6 · Operación): tareas manuales y tareas automáticas
calculadas de los datos reales de cada módulo.

Las tareas automáticas NO se guardan ni se duplican: en cada lectura se
calculan con consultas de solo lectura sobre las tablas de los demás módulos
-- las mismas condiciones que usan sus pantallas:

- Declaraciones por vencer / vencidas: `impuestos_repository.listar_declaraciones`.
- e-CF de venta rechazados o con error, y los que siguen 'enviado' (en
  proceso en la DGII): `facturacion.facturas` (de facturas_api).
- e-CF recibidos pendientes de aprobación comercial: `recepcion.ecf_recibidos`.
- Cuentas por cobrar vencidas y por pagar por vencer/vencidas: los mismos
  CTE que /cuentas-por-cobrar y /compras/cuentas-por-pagar.
- Cotizaciones abiertas por vencer: `ventas.cotizaciones`.
- Lotes por vencer/vencidos con existencia y productos bajo el mínimo:
  `inventario.*` (de inventario_api), leídos por SQL con los mismos criterios
  que `lotes_repository.alertas_lotes` y `repository.estado_stock` de
  inventario_api -- solo lectura, nunca se escribe en ese esquema.
- Salidas de inventario de facturas enviadas sin sincronizar: la condición de
  `listar_inventario_pendiente` de facturas_api.
- Facturas en papel de una contingencia sin su e-CF de reemplazo:
  `operacion.facturas_papel`.

Lo único que se escribe vive en el esquema `operacion` (ver migraciones.py):
las tareas manuales y, por la clave estable de cada tarea automática
('declaracion:it1:202609', 'cxc_vencida:<factura_id>'...), si el usuario la
completó o la pospuso. Cuando la condición desaparece, ese estado se borra al
listar: si la condición vuelve a darse, la tarea reaparece pendiente.
"""
from __future__ import annotations

import hashlib
import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import asyncpg

from . import impuestos_repository, precalculos
from .compras_repository import _SELECT_CXP_CON_ESTADO
from .repository import _SELECT_CXC_CON_ESTADO, DatosInvalidosError, _monto_dgii, hoy

logger = logging.getLogger(__name__)


class TareaNoEncontradaError(Exception):
    """Tarea manual inexistente, o automática cuyo motivo ya no existe (404)."""


# Ventanas de las tareas automáticas.
DIAS_DECLARACION_POR_VENCER = 30  # declaraciones pendientes que vencen dentro de...
DIAS_DECLARACION_VENCIDA = 180  # ...y vencidas hace como mucho (las más viejas ya no se recuerdan)
DIAS_FACTURA_RECHAZADA = 90  # e-CF rechazados / con error en los últimos...
HORAS_EN_PROCESO = 1  # 'enviado' sin resultado de la DGII desde hace más de...
DIAS_CXP_POR_VENCER = 7
DIAS_COTIZACION_POR_VENCER = 3
DIAS_LOTE_POR_VENCER = 30  # = dias_alerta_vencimiento por defecto de inventario_api
MINUTOS_INVENTARIO_EN_CURSO = 10  # salida recién enviada sin error: facturas_api la está registrando

_ORDEN_ESTADO = {"pendiente": 0, "pospuesta": 1, "completada": 2}
_ORDEN_PRIORIDAD = {"alta": 0, "media": 1, "baja": 2}

_NOMBRE_DECLARACION = {
    "606": "Enviar el Formato 606",
    "607": "Enviar el Formato 607",
    "608": "Enviar el Formato 608",
    "it1": "Declarar y pagar el IT-1",
    "ir17": "Presentar y pagar el IR-17",
    "ir2": "Presentar el IR-2",
}


def _enlace_declaracion(formulario: str, periodo: str) -> str:
    if formulario == "ir2":
        return f"/impuestos/precalculo/ir2?anio={periodo}"
    if formulario in ("it1", "ir17"):
        return f"/impuestos/precalculo/{formulario}?periodo={periodo}"
    return f"/impuestos/formatos/{formulario}?periodo={periodo}"


def _cantidad(valor: Decimal | None) -> str:
    """Existencia para un texto, sin ceros de más: 3.0000 -> "3"."""
    return f"{Decimal(valor or 0).normalize():f}"


def _frase(texto: str) -> str:
    """Texto con punto final, para encadenarlo con otra oración."""
    return texto if texto.endswith((".", "!", "?")) else f"{texto}."


def _huella(ids: list[str]) -> str:
    """Clave de una tarea que agrupa varios documentos: cambia si cambia el conjunto."""
    return hashlib.sha1(",".join(sorted(ids)).encode()).hexdigest()[:12]


def _motivo_dgii(respuesta: str | None, error: str | None) -> str | None:
    """Primer mensaje de la respuesta de la DGII guardada en la factura (o el error)."""
    if respuesta:
        try:
            datos = json.loads(respuesta)
        except ValueError:
            datos = None
        if isinstance(datos, dict):
            lista = next((datos[k] for k in datos if k.lower() in ("mensajes", "errores", "messages")), None)
            for item in lista if isinstance(lista, list) else []:
                if isinstance(item, str) and item.strip():
                    return item.strip()
                if isinstance(item, dict):
                    valor = next(
                        (item[k] for k in item if k.lower() in ("valor", "mensaje", "descripcion") and item[k]), None
                    )
                    if valor:
                        return str(valor).strip()
    return (error or "").strip() or None


def _tarea(
    *,
    clave: str,
    tipo: str,
    categoria: str,
    titulo: str,
    prioridad: str,
    descripcion: str | None = None,
    vence: date | None = None,
    enlace: str | None = None,
    monto: Decimal | None = None,
    creado_en: Any = None,
) -> dict[str, Any]:
    return {
        "id": clave,
        "origen": "automatica",
        "tipo": tipo,
        "categoria": categoria,
        "titulo": titulo,
        "descripcion": descripcion,
        "prioridad": prioridad,
        "vence": vence,
        "enlace": enlace,
        "monto": monto,
        "creado_en": creado_en,
    }


# --- Fuentes de las tareas automáticas ---------------------------------------------------------

@dataclass
class _Contexto:
    pool: asyncpg.Pool
    conn: asyncpg.Connection
    hoy: date
    emisor_100_electronico: bool
    tareas: list[dict[str, Any]] = field(default_factory=list)


async def _declaraciones(ctx: _Contexto) -> None:
    for anio in sorted({ctx.hoy.year - 1, ctx.hoy.year}):
        for d in await impuestos_repository.listar_declaraciones(
            ctx.pool, str(anio), emisor_100_electronico=ctx.emisor_100_electronico
        ):
            dias = (d["vence"] - ctx.hoy).days
            if d["estado"] == "vencida":
                if -dias > DIAS_DECLARACION_VENCIDA:
                    continue
            elif d["estado"] != "pendiente" or dias > DIAS_DECLARACION_POR_VENCER:
                continue
            formulario, periodo = d["formulario"], d["periodo"]
            de = f"del ejercicio {periodo}" if formulario == "ir2" else f"de {precalculos.nombre_periodo(periodo)}"
            ctx.tareas.append(_tarea(
                clave=f"declaracion:{formulario}:{periodo}",
                tipo="declaracion",
                categoria="dgii",
                titulo=f"{_NOMBRE_DECLARACION[formulario]} {de}",
                descripcion=(
                    "Venció sin marcarse como presentada: preséntela (con recargos) o márquela si ya la presentó."
                    if d["estado"] == "vencida"
                    else "Márquela como presentada en Impuestos al terminar."
                ),
                prioridad="alta" if d["estado"] == "vencida" or dias <= 5 else "media",
                vence=d["vence"],
                enlace=_enlace_declaracion(formulario, periodo),
            ))


_CLIENTE = "NULLIF(d #>> '{Encabezado,Comprador,RazonSocialComprador}', '')"
_TOTAL = _monto_dgii("{Encabezado,Totales,MontoTotal}")


async def _facturas_con_problema(ctx: _Contexto) -> None:
    filas = await ctx.conn.fetch(
        f"""
        SELECT f.id, f.tipo_ecf, f.encf, f.estado, f.error, f.dgii_respuesta::text AS respuesta,
               f.actualizado_en, {_CLIENTE} AS cliente, {_TOTAL} AS total
        FROM facturacion.facturas f
        CROSS JOIN LATERAL (SELECT f.datos::jsonb AS d) x
        WHERE f.estado IN ('rechazado', 'error')
          AND f.actualizado_en >= now() - make_interval(days => $1)
        ORDER BY f.actualizado_en
        """,
        DIAS_FACTURA_RECHAZADA,
    )
    for f in filas:
        rechazada = f["estado"] == "rechazado"
        documento = f["encf"] or "sin e-NCF"
        motivo = _motivo_dgii(f["respuesta"], f["error"])
        cliente = f" a {f['cliente']}" if f["cliente"] else ""
        ctx.tareas.append(_tarea(
            clave=f"{'factura_rechazada' if rechazada else 'factura_error'}:{f['id']}",
            tipo="factura_rechazada" if rechazada else "factura_error",
            categoria="ecf",
            titulo=(
                f"Corregir la factura rechazada {documento}{cliente}"
                if rechazada
                else f"Revisar el envío con error de {documento}{cliente}"
            ),
            descripcion=(
                (f"La DGII la rechazó: {_frase(motivo)}" if motivo else "La DGII la rechazó.")
                + " Emita un nuevo e-CF con los datos corregidos."
                if rechazada
                else (f"Error: {_frase(motivo)}" if motivo else "El envío a la DGII falló.")
                + " Reintente desde el Centro de sincronización o el detalle de la factura."
            ),
            prioridad="alta",
            vence=None,
            enlace=f"/facturas/{f['id']}",
            monto=f["total"],
            creado_en=f["actualizado_en"],
        ))


async def _en_proceso_dgii(ctx: _Contexto) -> None:
    fila = await ctx.conn.fetchrow(
        f"""
        SELECT array_agg(f.id::text) AS ids, count(*) AS cantidad, sum({_TOTAL}) AS total,
               min(f.actualizado_en) AS desde,
               bool_or(f.actualizado_en < now() - interval '24 hours') AS hay_viejas
        FROM facturacion.facturas f
        CROSS JOIN LATERAL (SELECT f.datos::jsonb AS d) x
        WHERE f.estado = 'enviado' AND f.actualizado_en < now() - make_interval(hours => $1)
        """,
        HORAS_EN_PROCESO,
    )
    if not fila or not fila["cantidad"]:
        return
    n = fila["cantidad"]
    ctx.tareas.append(_tarea(
        clave=f"dgii_en_proceso:{_huella(fila['ids'])}",
        tipo="dgii_en_proceso",
        categoria="ecf",
        titulo=(
            f"{n} e-CF siguen en proceso en la DGII" if n > 1 else "1 e-CF sigue en proceso en la DGII"
        ),
        descripcion=(
            f"Enviados sin resultado de aceptación desde hace más de {HORAS_EN_PROCESO} h. Consulte su estado "
            "desde el Centro de sincronización."
        ),
        prioridad="alta" if fila["hay_viejas"] else "media",
        enlace="/sincronizacion",
        monto=fila["total"],
        creado_en=fila["desde"],
    ))


async def _ecf_por_aprobar(ctx: _Contexto) -> None:
    filas = await ctx.conn.fetch(
        """
        SELECT id, encf, razon_social_emisor, rnc_emisor, monto_total, recibido_en, error_aprobacion
        FROM recepcion.ecf_recibidos
        WHERE estado_aprobacion IN ('pendiente', 'enviando')
        ORDER BY recibido_en
        """
    )
    for f in filas:
        proveedor = f["razon_social_emisor"] or f["rnc_emisor"]
        ctx.tareas.append(_tarea(
            clave=f"ecf_por_aprobar:{f['id']}",
            tipo="ecf_por_aprobar",
            categoria="compras",
            titulo=f"Aprobar o rechazar el e-CF {f['encf']} de {proveedor}",
            descripcion=(
                f"El último envío de la aprobación comercial falló: {f['error_aprobacion']}"
                if f["error_aprobacion"]
                else "Revise el comprobante recibido y envíe la aprobación comercial (ACECF) a la DGII."
            ),
            prioridad="alta" if f["error_aprobacion"] else "media",
            enlace=f"/bandeja/{f['id']}",
            monto=f["monto_total"],
            creado_en=f["recibido_en"],
        ))


async def _cxc_vencidas(ctx: _Contexto) -> None:
    filas = await ctx.conn.fetch(
        f"""
        SELECT factura_id, encf, cliente_nombre, vence, pendiente, dias_vencida
        FROM ({_SELECT_CXC_CON_ESTADO}) c
        WHERE estado = 'vencida'
        ORDER BY vence
        """,
        ctx.hoy,
    )
    for f in filas:
        cliente = f["cliente_nombre"] or "cliente sin nombre"
        dias = f["dias_vencida"]
        ctx.tareas.append(_tarea(
            clave=f"cxc_vencida:{f['factura_id']}",
            tipo="cxc_vencida",
            categoria="cobros",
            titulo=f"Cobrar la factura {f['encf']} a {cliente}",
            descripcion=f"Vencida hace {dias} {'día' if dias == 1 else 'días'}: contacte al cliente y registre el cobro.",
            prioridad="alta" if dias > 30 else "media",
            vence=f["vence"],
            enlace=f"/cobros?factura={f['factura_id']}",
            monto=f["pendiente"],
        ))


async def _cxp(ctx: _Contexto) -> None:
    filas = await ctx.conn.fetch(
        f"""
        SELECT registro606_id, ncf, proveedor_nombre, proveedor_rnc, vence, pendiente, estado, dias_vencida
        FROM ({_SELECT_CXP_CON_ESTADO}) c
        WHERE estado = 'vencida' OR (estado = 'pendiente' AND vence <= $1::date + $2::int)
        ORDER BY vence
        """,
        ctx.hoy,
        DIAS_CXP_POR_VENCER,
    )
    for f in filas:
        vencida = f["estado"] == "vencida"
        proveedor = f["proveedor_nombre"] or f["proveedor_rnc"]
        dias = (f["vence"] - ctx.hoy).days
        if vencida:
            cuando = f"Vencida hace {f['dias_vencida']} {'día' if f['dias_vencida'] == 1 else 'días'}"
        else:
            cuando = "Vence hoy" if dias == 0 else f"Vence en {dias} {'día' if dias == 1 else 'días'}"
        ctx.tareas.append(_tarea(
            clave=f"{'cxp_vencida' if vencida else 'cxp_por_vencer'}:{f['registro606_id']}",
            tipo="cxp_vencida" if vencida else "cxp_por_vencer",
            categoria="pagos",
            titulo=f"Pagar la factura {f['ncf']} de {proveedor}",
            descripcion=f"{cuando}: registre el pago (con sus retenciones) en Pagos a proveedores.",
            prioridad="alta" if vencida or dias <= 2 else "media",
            vence=f["vence"],
            enlace=f"/compras/pagos?factura={f['registro606_id']}",
            monto=f["pendiente"],
        ))


async def _cotizaciones(ctx: _Contexto) -> None:
    filas = await ctx.conn.fetch(
        """
        SELECT id, numero, cliente_nombre, vence, total
        FROM ventas.cotizaciones
        WHERE estado = 'abierta' AND vence >= $1::date AND vence <= $1::date + $2::int
        ORDER BY vence, numero
        """,
        ctx.hoy,
        DIAS_COTIZACION_POR_VENCER,
    )
    for f in filas:
        ctx.tareas.append(_tarea(
            clave=f"cotizacion_por_vencer:{f['id']}:{f['vence']:%Y%m%d}",
            tipo="cotizacion_por_vencer",
            categoria="ventas",
            titulo=f"Dar seguimiento a la cotización {f['numero']} de {f['cliente_nombre']}",
            descripcion="Está por vencer: confirme con el cliente, extienda la vigencia o factúrela.",
            prioridad="media" if f["vence"] == ctx.hoy else "baja",
            vence=f["vence"],
            enlace=f"/cotizaciones/{f['id']}",
            monto=f["total"],
        ))


async def _lotes(ctx: _Contexto) -> None:
    # Mismos criterios que lotes_repository.alertas_lotes de inventario_api:
    # lotes no bloqueados, con existencia en algún almacén, vencidos o que
    # vencen dentro de los días de alerta. Solo lectura.
    filas = await ctx.conn.fetch(
        """
        SELECT l.id, l.numero, l.fecha_vencimiento, p.nombre, p.sku, e.cantidad
        FROM inventario.lotes l
        JOIN inventario.productos p ON p.id = l.producto_id
        JOIN (
            SELECT lote_id, sum(cantidad) AS cantidad
            FROM inventario.existencias_lote
            WHERE cantidad <> 0
            GROUP BY lote_id
        ) e ON e.lote_id = l.id
        WHERE NOT l.bloqueado AND l.fecha_vencimiento IS NOT NULL
          AND l.fecha_vencimiento <= $1::date + $2::int
        ORDER BY l.fecha_vencimiento, p.nombre, l.numero
        """,
        ctx.hoy,
        DIAS_LOTE_POR_VENCER,
    )
    for f in filas:
        vencido = f["fecha_vencimiento"] < ctx.hoy
        dias = (f["fecha_vencimiento"] - ctx.hoy).days
        ctx.tareas.append(_tarea(
            clave=f"{'lote_vencido' if vencido else 'lote_por_vencer'}:{f['id']}:{f['fecha_vencimiento']:%Y%m%d}",
            tipo="lote_vencido" if vencido else "lote_por_vencer",
            categoria="inventario",
            titulo=(
                f"Retirar el lote vencido {f['numero']} de {f['nombre']}"
                if vencido
                else f"Rotar el lote {f['numero']} de {f['nombre']} antes de que venza"
            ),
            descripcion=(
                f"Existencia {_cantidad(f['cantidad'])} · "
                + ("bloquéelo o regístrelo como merma en un ajuste." if vencido else "véndalo primero (FEFO).")
            ),
            prioridad="alta" if vencido or dias <= 7 else "media",
            vence=f["fecha_vencimiento"],
            enlace="/inventario/lotes",
        ))


async def _stock_bajo(ctx: _Contexto) -> None:
    # Mismo criterio que estado_stock de inventario_api ('bajo_minimo' y
    # 'sin_existencia' con mínimo): bienes activos con mínimo configurado.
    filas = await ctx.conn.fetch(
        """
        SELECT id, sku, nombre, stock_actual, stock_minimo
        FROM inventario.productos
        WHERE activo AND tipo = 'bien' AND stock_minimo > 0 AND stock_actual <= stock_minimo
        ORDER BY stock_actual <= 0 DESC, nombre
        """
    )
    for f in filas:
        ctx.tareas.append(_tarea(
            clave=f"stock_bajo:{f['id']}",
            tipo="stock_bajo",
            categoria="inventario",
            titulo=f"Reponer {f['nombre']} ({f['sku']})",
            descripcion=f"Existencia {_cantidad(f['stock_actual'])} · mínimo {_cantidad(f['stock_minimo'])}",
            prioridad="alta" if f["stock_actual"] <= 0 else "media",
            enlace="/inventario/compras/nueva",
        ))


async def _inventario_sin_sincronizar(ctx: _Contexto) -> None:
    # Condición de facturas_api.repository.listar_inventario_pendiente, sin su
    # límite de 90 días (lo que pasa de ahí ya no se reintenta solo).
    fila = await ctx.conn.fetchrow(
        """
        SELECT array_agg(id::text) AS ids, count(*) AS cantidad,
               bool_or(actualizado_en < now() - interval '90 days') AS hay_viejas
        FROM facturacion.facturas
        WHERE NOT inventario_sincronizado
          AND track_id IS NOT NULL
          AND estado IN ('enviado', 'aceptado')
          AND CASE WHEN jsonb_typeof(items_inventario) = 'array'
                   THEN jsonb_array_length(items_inventario) > 0 ELSE false END
          AND (inventario_error IS NOT NULL OR actualizado_en < now() - make_interval(mins => $1))
        """,
        MINUTOS_INVENTARIO_EN_CURSO,
    )
    if not fila or not fila["cantidad"]:
        return
    n = fila["cantidad"]
    ctx.tareas.append(_tarea(
        clave=f"inventario_sin_sincronizar:{_huella(fila['ids'])}",
        tipo="inventario_sin_sincronizar",
        categoria="inventario",
        titulo=(
            f"{n} salidas de inventario sin registrar" if n > 1 else "1 salida de inventario sin registrar"
        ),
        descripcion="Facturas enviadas cuya salida de mercancía no llegó a Inventario. Revíselas en el Centro de "
        "sincronización.",
        prioridad="alta" if fila["hay_viejas"] else "media",
        enlace="/sincronizacion",
    ))


async def _papel_sin_reemplazo(ctx: _Contexto) -> None:
    filas = await ctx.conn.fetch(
        """
        SELECT id, ncf, fecha, cliente_nombre, total, creado_en
        FROM operacion.facturas_papel
        WHERE encf_reemplazo IS NULL
        ORDER BY fecha, ncf
        """
    )
    for f in filas:
        cliente = f" de {f['cliente_nombre']}" if f["cliente_nombre"] else ""
        ctx.tareas.append(_tarea(
            clave=f"papel_sin_reemplazo:{f['id']}",
            tipo="papel_sin_reemplazo",
            categoria="ecf",
            titulo=f"Emitir el e-CF de reemplazo de la factura en papel {f['ncf']}{cliente}",
            descripcion=(
                f"Emitida en papel el {f['fecha']:%d/%m/%Y} durante una contingencia: emita el e-CF que la "
                "referencia (código de modificación 4) y anótelo en el Centro de sincronización."
            ),
            prioridad="media",
            enlace="/sincronizacion",
            monto=f["total"],
            creado_en=f["creado_en"],
        ))


# Cada fuente y los `tipo` de tarea que produce: si una fuente falla (p. ej.
# el esquema de inventario todavía no existe), sus tareas no se muestran y el
# estado guardado de sus tipos NO se borra.
_FUENTES: list[tuple[Callable[[_Contexto], Awaitable[None]], tuple[str, ...]]] = [
    (_declaraciones, ("declaracion",)),
    (_facturas_con_problema, ("factura_rechazada", "factura_error")),
    (_en_proceso_dgii, ("dgii_en_proceso",)),
    (_ecf_por_aprobar, ("ecf_por_aprobar",)),
    (_cxc_vencidas, ("cxc_vencida",)),
    (_cxp, ("cxp_vencida", "cxp_por_vencer")),
    (_cotizaciones, ("cotizacion_por_vencer",)),
    (_lotes, ("lote_vencido", "lote_por_vencer")),
    (_stock_bajo, ("stock_bajo",)),
    (_inventario_sin_sincronizar, ("inventario_sin_sincronizar",)),
    (_papel_sin_reemplazo, ("papel_sin_reemplazo",)),
]


async def tareas_automaticas(
    pool: asyncpg.Pool, *, emisor_100_electronico: bool = True
) -> tuple[list[dict[str, Any]], set[str]]:
    """(tareas automáticas sin estado, tipos cuyas fuentes respondieron)."""
    tipos_ok: set[str] = set()
    async with pool.acquire() as conn:
        ctx = _Contexto(pool=pool, conn=conn, hoy=hoy(), emisor_100_electronico=emisor_100_electronico)
        for fuente, tipos in _FUENTES:
            try:
                await fuente(ctx)
            except (asyncpg.UndefinedTableError, asyncpg.UndefinedColumnError, asyncpg.InvalidSchemaNameError) as exc:
                logger.warning("Tareas automáticas: se omite %s (%s)", fuente.__name__, exc)
                continue
            tipos_ok.update(tipos)
    return ctx.tareas, tipos_ok


# --- Estado y listado ---------------------------------------------------------

def _estado(completada_en: Any, pospuesta_hasta: date | None, hoy_: date) -> str:
    if completada_en is not None:
        return "completada"
    if pospuesta_hasta is not None and pospuesta_hasta >= hoy_:
        return "pospuesta"
    return "pendiente"


def _con_estado(tarea: dict[str, Any], completada_en: Any, pospuesta_hasta: date | None, hoy_: date) -> dict[str, Any]:
    estado = _estado(completada_en, pospuesta_hasta, hoy_)
    tarea.update(
        estado=estado,
        completada_en=completada_en,
        pospuesta_hasta=pospuesta_hasta if estado == "pospuesta" else None,
        vencida=estado != "completada" and tarea["vence"] is not None and tarea["vence"] < hoy_,
    )
    return tarea


def _fila_manual(fila: asyncpg.Record, hoy_: date) -> dict[str, Any]:
    tarea = {
        "id": str(fila["id"]),
        "origen": "manual",
        "tipo": "manual",
        "categoria": "gestion",
        "titulo": fila["titulo"],
        "descripcion": fila["descripcion"],
        "prioridad": fila["prioridad"],
        "vence": fila["vence"],
        "enlace": None,
        "monto": None,
        "creado_en": fila["creado_en"],
    }
    return _con_estado(tarea, fila["completada_en"] if fila["completada"] else None, fila["pospuesta_hasta"], hoy_)


def _orden(tarea: dict[str, Any]) -> tuple:
    return (
        _ORDEN_ESTADO[tarea["estado"]],
        not tarea["vencida"],
        _ORDEN_PRIORIDAD[tarea["prioridad"]],
        tarea["vence"] is None,
        tarea["vence"] or date.max,
        tarea["titulo"],
    )


async def listar_tareas(
    pool: asyncpg.Pool,
    *,
    estado: str | None = None,
    categoria: str | None = None,
    emisor_100_electronico: bool = True,
) -> list[dict[str, Any]]:
    """Manuales y automáticas con su estado, las más urgentes primero."""
    automaticas, tipos_ok = await tareas_automaticas(pool, emisor_100_electronico=emisor_100_electronico)
    hoy_ = hoy()
    claves = {t["id"] for t in automaticas}
    async with pool.acquire() as conn:
        guardados = {
            f["clave"]: f
            for f in await conn.fetch("SELECT clave, completada_en, pospuesta_hasta FROM operacion.tareas_automaticas")
        }
        obsoletas = [c for c in guardados if c not in claves and c.split(":", 1)[0] in tipos_ok]
        if obsoletas:
            await conn.execute("DELETE FROM operacion.tareas_automaticas WHERE clave = ANY($1::text[])", obsoletas)
        manuales = await conn.fetch(
            "SELECT id, titulo, descripcion, vence, prioridad, completada, completada_en, pospuesta_hasta, creado_en"
            " FROM operacion.tareas"
        )
    tareas = [_fila_manual(f, hoy_) for f in manuales]
    for tarea in automaticas:
        guardado = guardados.get(tarea["id"])
        tareas.append(_con_estado(
            tarea,
            guardado["completada_en"] if guardado else None,
            guardado["pospuesta_hasta"] if guardado else None,
            hoy_,
        ))
    if estado is not None:
        tareas = [t for t in tareas if t["estado"] == estado]
    if categoria is not None:
        tareas = [t for t in tareas if t["categoria"] == categoria]
    return sorted(tareas, key=_orden)


async def resumen_tareas(pool: asyncpg.Pool, *, emisor_100_electronico: bool = True) -> dict[str, Any]:
    pendientes = await listar_tareas(pool, estado="pendiente", emisor_100_electronico=emisor_100_electronico)
    hoy_ = hoy()
    por_categoria: dict[str, int] = {}
    for t in pendientes:
        por_categoria[t["categoria"]] = por_categoria.get(t["categoria"], 0) + 1
    return {
        "pendientes": len(pendientes),
        "vencidas": sum(1 for t in pendientes if t["vencida"]),
        "vencen_hoy": sum(1 for t in pendientes if t["vence"] == hoy_),
        "alta": sum(1 for t in pendientes if t["prioridad"] == "alta"),
        "por_categoria": por_categoria,
        "proximas": pendientes[:5],
    }


# --- Tareas manuales ---------------------------------------------------------

def _id_manual(id_: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(id_)
    except ValueError:
        return None


_COLUMNAS = "id, titulo, descripcion, vence, prioridad, completada, completada_en, pospuesta_hasta, creado_en"


async def crear_tarea(
    pool: asyncpg.Pool, *, titulo: str, descripcion: str | None, vence: date | None, prioridad: str
) -> dict[str, Any]:
    async with pool.acquire() as conn:
        fila = await conn.fetchrow(
            f"""
            INSERT INTO operacion.tareas (titulo, descripcion, vence, prioridad)
            VALUES ($1, $2, $3, $4)
            RETURNING {_COLUMNAS}
            """,
            titulo,
            descripcion or None,
            vence,
            prioridad,
        )
    return _fila_manual(fila, hoy())


async def actualizar_tarea(
    pool: asyncpg.Pool,
    tarea_id: str,
    *,
    titulo: str,
    descripcion: str | None,
    vence: date | None,
    prioridad: str,
) -> dict[str, Any]:
    uid = _id_manual(tarea_id)
    if uid is None:
        raise DatosInvalidosError("Las tareas automáticas no se editan: complétela o pospóngala")
    async with pool.acquire() as conn:
        fila = await conn.fetchrow(
            f"""
            UPDATE operacion.tareas
            SET titulo = $2, descripcion = $3, vence = $4, prioridad = $5, actualizado_en = now()
            WHERE id = $1
            RETURNING {_COLUMNAS}
            """,
            uid,
            titulo,
            descripcion or None,
            vence,
            prioridad,
        )
    if fila is None:
        raise TareaNoEncontradaError(tarea_id)
    return _fila_manual(fila, hoy())


async def eliminar_tarea(pool: asyncpg.Pool, tarea_id: str) -> None:
    uid = _id_manual(tarea_id)
    if uid is None:
        raise DatosInvalidosError(
            "Las tareas automáticas no se eliminan: desaparecen solas cuando se resuelve su motivo"
        )
    async with pool.acquire() as conn:
        borradas = await conn.fetchval(
            "WITH b AS (DELETE FROM operacion.tareas WHERE id = $1 RETURNING 1) SELECT count(*) FROM b", uid
        )
    if not borradas:
        raise TareaNoEncontradaError(tarea_id)


# --- Completar / reabrir / posponer (manuales y automáticas) -------------------------------------

async def _automatica(pool: asyncpg.Pool, clave: str, emisor_100_electronico: bool) -> dict[str, Any]:
    """La tarea automática vigente con esa clave (404 si su motivo ya no existe)."""
    automaticas, _ = await tareas_automaticas(pool, emisor_100_electronico=emisor_100_electronico)
    tarea = next((t for t in automaticas if t["id"] == clave), None)
    if tarea is None:
        raise TareaNoEncontradaError(clave)
    return tarea


async def _cambiar_estado(
    pool: asyncpg.Pool,
    tarea_id: str,
    *,
    completar: bool,
    pospuesta_hasta: date | None,
    emisor_100_electronico: bool,
) -> dict[str, Any]:
    """`completar` True = completada; False con `pospuesta_hasta` = pospuesta;
    False sin fecha = reabierta (pendiente)."""
    uid = _id_manual(tarea_id)
    if uid is not None:
        async with pool.acquire() as conn:
            fila = await conn.fetchrow(
                f"""
                UPDATE operacion.tareas
                SET completada = $2, completada_en = CASE WHEN $2 THEN now() END, pospuesta_hasta = $3,
                    actualizado_en = now()
                WHERE id = $1
                RETURNING {_COLUMNAS}
                """,
                uid,
                completar,
                pospuesta_hasta,
            )
        if fila is None:
            raise TareaNoEncontradaError(tarea_id)
        return _fila_manual(fila, hoy())
    tarea = await _automatica(pool, tarea_id, emisor_100_electronico)
    async with pool.acquire() as conn:
        if not completar and pospuesta_hasta is None:
            await conn.execute("DELETE FROM operacion.tareas_automaticas WHERE clave = $1", tarea_id)
            return _con_estado(tarea, None, None, hoy())
        fila = await conn.fetchrow(
            """
            INSERT INTO operacion.tareas_automaticas (clave, completada_en, pospuesta_hasta)
            VALUES ($1, CASE WHEN $2 THEN now() END, $3)
            ON CONFLICT (empresa_id, clave) DO UPDATE
            SET completada_en = EXCLUDED.completada_en, pospuesta_hasta = EXCLUDED.pospuesta_hasta,
                actualizado_en = now()
            RETURNING completada_en, pospuesta_hasta
            """,
            tarea_id,
            completar,
            pospuesta_hasta,
        )
    return _con_estado(tarea, fila["completada_en"], fila["pospuesta_hasta"], hoy())


async def completar_tarea(pool: asyncpg.Pool, tarea_id: str, *, emisor_100_electronico: bool = True) -> dict[str, Any]:
    return await _cambiar_estado(
        pool, tarea_id, completar=True, pospuesta_hasta=None, emisor_100_electronico=emisor_100_electronico
    )


async def reabrir_tarea(pool: asyncpg.Pool, tarea_id: str, *, emisor_100_electronico: bool = True) -> dict[str, Any]:
    return await _cambiar_estado(
        pool, tarea_id, completar=False, pospuesta_hasta=None, emisor_100_electronico=emisor_100_electronico
    )


async def posponer_tarea(
    pool: asyncpg.Pool, tarea_id: str, hasta: date, *, emisor_100_electronico: bool = True
) -> dict[str, Any]:
    if hasta < hoy():
        raise DatosInvalidosError("La fecha para posponer no puede ser anterior a hoy")
    if hasta > hoy() + timedelta(days=366):
        raise DatosInvalidosError("Solo se puede posponer hasta un año")
    return await _cambiar_estado(
        pool, tarea_id, completar=False, pospuesta_hasta=hasta, emisor_100_electronico=emisor_100_electronico
    )

