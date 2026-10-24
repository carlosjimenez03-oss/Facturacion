"""Centro de sincronización (Fase 6 · Operación): estado de cada
sincronización con terceros, calculado de los datos (solo lectura):

- Envío de e-CF a la DGII y su resultado: `facturacion.facturas` ('error',
  'enviado') y la auditoría `facturacion.envios_dgii` (de facturas_api,
  recepcion, consulta_resultado, aprobacion_comercial, anulacion_encf...). La
  tarea de fondo de facturas_api (`estado_dgii.bucle_sincronizacion`) no
  guarda cuándo corrió: su última actividad es la última consulta de
  resultado auditada (módulo 'consulta_resultado'), y las facturas 'enviado'
  de más de `DIAS_BUCLE_RESULTADO` días ya no las consulta sola.
- Salidas de inventario de facturas enviadas (`inventario_sincronizado`),
  que la misma tarea de fondo reintenta durante `DIAS_BUCLE_INVENTARIO` días.
- Aprobaciones comerciales (ACECF) enviando / fallidas y su entrega al
  proveedor: `recepcion.ecf_recibidos`.
- Modo contingencia: el estado real que guarda facturas_api
  (`facturacion.contingencia`, las facturas en 'contingencia' y los lotes de
  `facturacion.lotes_contingencia`, ver services/facturas_api/app/
  contingencia.py). Si esas tablas todavía no existen (facturas_api sin
  actualizar) se calcula como antes, de la racha de fallos de la auditoría.

Los reintentos usan los endpoints que ya existen (facturas-api
`/facturas/{id}/actualizar-estado` y `/enviar`, erp-api
`/compras/bandeja/{id}/aprobar|rechazar`) desde el frontend. Lo único que
este módulo escribe es el reintento de la entrega del ACECF al proveedor
(`reintentar_entrega`): las mismas columnas (`enviado_al_emisor`,
`error_envio_emisor`) y la misma función de entrega que
`compras_repository.decidir_aprobacion`.
"""
from __future__ import annotations

import re
import uuid
from collections.abc import Awaitable, Callable
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

import asyncpg

from . import formatos, precalculos
from .repository import _ZONA_RD, DatosInvalidosError, _monto_dgii, hoy
from .tareas_repository import _motivo_dgii

EntregarAlEmisor = Callable[[str, str, bytes], Awaitable[Any]]


class SincronizacionNoEncontradaError(Exception):
    pass


class ConflictoSincronizacionError(Exception):
    pass


class FacturaPapelNoEncontradaError(Exception):
    pass


# Mismos plazos que facturas_api (estado_dgii.DIAS_MAXIMOS_PENDIENTE y el
# `dias` por defecto de repository.listar_inventario_pendiente) y que
# compras_repository._ENVIO_VENCIDO.
DIAS_BUCLE_RESULTADO = 30
DIAS_BUCLE_INVENTARIO = 90
_ENVIO_VENCIDO = "interval '5 minutes'"
_LIMITE_POR_CANAL = 500

# Contingencia: la DGII "no responde" si los últimos UMBRAL_CONTINGENCIA
# envíos/consultas auditados fallaron seguidos y el último fue hace menos de
# HORAS_CONTINGENCIA horas (sin intentos más recientes no se sabe).
UMBRAL_CONTINGENCIA = 3
HORAS_CONTINGENCIA = 24
# Intervalo de la tarea de fondo de facturas-api (sincronizar_estado_cada_segundos).
SEGUNDOS_BUCLE_FACTURAS = 120
MODULOS_DGII = ("recepcion", "consulta_resultado", "recepcion_rfce", "anulacion_encf", "aprobacion_comercial")

MODULOS = {
    "recepcion": "Envío de e-CF",
    "consulta_resultado": "Consulta de resultado",
    "aprobacion_comercial": "Aprobación comercial (ACECF)",
    "anulacion_encf": "Anulación de e-NCF (ANECF)",
    "recepcion_rfce": "Resumen de consumo (RFCE)",
    "receptor_recepcion": "e-CF recibido de proveedor",
    "receptor_aprobacion_comercial": "Aprobación comercial recibida",
    "receptor_autenticacion": "Autenticación (semilla)",
}

_TOTAL = _monto_dgii("{Encabezado,Totales,MontoTotal}")
_CLIENTE = "NULLIF(d #>> '{Encabezado,Comprador,RazonSocialComprador}', '')"

# Condición de facturas_api.repository.listar_inventario_pendiente (sin el
# límite de días: lo que pasa de él ya no se reintenta solo).
_INVENTARIO_PENDIENTE = """
    NOT f.inventario_sincronizado
    AND f.track_id IS NOT NULL
    AND f.estado IN ('enviado', 'aceptado')
    AND CASE WHEN jsonb_typeof(f.items_inventario) = 'array'
             THEN jsonb_array_length(f.items_inventario) > 0 ELSE false END
"""

# Resultado de un renglón de facturacion.envios_dgii.
_RESULTADO_EVENTO = """
    CASE WHEN e.error IS NOT NULL THEN 'error'
         WHEN e.codigo = '2' OR e.estado ILIKE 'rechaz%' THEN 'rechazado'
         WHEN e.codigo = '3' OR e.estado ILIKE '%proceso%' THEN 'en_proceso'
         ELSE 'ok' END
"""


# --- Contingencia ---------------------------------------------------------

# Plazo de la DGII para enviar los e-CF emitidos en contingencia por falta de
# conectividad (facturas_api: contingencia_horas_limite). Lotes que se
# muestran: los abiertos, los que esperan resultados y los del último día.
HORAS_LIMITE_CONTINGENCIA = 72
_LOTES_VISIBLES = 10

# Contadores de un lote de facturas_api, en vivo de sus facturas (mismo
# criterio que facturas_api.contingencia_repository._CONTADORES_LOTE).
_LOTES = """
    SELECT l.id, l.documentos, l.estado, l.ultimo_error, l.creado_en, l.iniciado_en, l.completado_en,
           COALESCE(c.enviados, 0) AS enviados, COALESCE(c.aceptados, 0) AS aceptados,
           COALESCE(c.rechazados, 0) AS rechazados
    FROM facturacion.lotes_contingencia l
    LEFT JOIN LATERAL (
        SELECT count(*) FILTER (WHERE f.estado <> 'contingencia') AS enviados,
               count(*) FILTER (WHERE f.estado = 'aceptado') AS aceptados,
               count(*) FILTER (WHERE f.estado IN ('rechazado', 'error')) AS rechazados
        FROM facturacion.facturas f WHERE f.lote_id = l.id
    ) c ON true
"""


async def _contingencia(conn: asyncpg.Connection) -> dict[str, Any]:
    """Modo contingencia: el estado real de facturas_api si sus tablas
    existen; si no, el calculado de la auditoría (`_contingencia_calculada`)."""
    hay_tablas = await conn.fetchval(
        "SELECT to_regclass('facturacion.contingencia') IS NOT NULL"
        " AND to_regclass('facturacion.lotes_contingencia') IS NOT NULL"
    )
    if not hay_tablas:
        return await _contingencia_calculada(conn)
    estado = await conn.fetchrow("SELECT * FROM facturacion.contingencia WHERE id = 1")
    pendientes = await conn.fetchrow(
        f"""
        SELECT count(*) AS cantidad, COALESCE(sum({_TOTAL}), 0) AS monto,
               min(COALESCE(f.firmado_en, f.contingencia_en, f.creado_en)) AS mas_antigua
        FROM facturacion.facturas f
        CROSS JOIN LATERAL (SELECT f.datos::jsonb AS d) x
        WHERE f.estado = 'contingencia'
        """
    )
    lotes = await conn.fetch(
        f"""
        {_LOTES}
        WHERE l.estado <> 'completado' OR l.completado_en >= now() - interval '1 day'
           OR COALESCE(c.aceptados, 0) + COALESCE(c.rechazados, 0) < l.documentos
        ORDER BY l.creado_en DESC, l.id DESC
        LIMIT $1
        """,
        _LOTES_VISIBLES,
    )
    activa = bool(estado and estado["activa"])
    inicio_papel = (estado["desde"] if activa else None) or _inicio_dia(hoy().replace(day=1))
    papel = await conn.fetchval("SELECT count(*) FROM operacion.facturas_papel WHERE creado_en >= $1", inicio_papel)
    ultimo_intento = estado["ultimo_intento"] if estado else None
    proximo = None
    if (activa or pendientes["cantidad"]) and ultimo_intento is not None:
        proximo = ultimo_intento + timedelta(seconds=SEGUNDOS_BUCLE_FACTURAS)
    mas_antigua = pendientes["mas_antigua"]
    return {
        "activa": activa,
        "desde": estado["desde"] if activa else None,
        "errores_consecutivos": estado["errores_consecutivos"] if estado else 0,
        "ultimo_intento": ultimo_intento,
        "ultimo_error": estado["ultimo_error"] if activa else None,
        "proximo_intento": proximo,
        "pendientes": pendientes["cantidad"],
        "monto_pendiente": pendientes["monto"],
        "facturas_papel": papel,
        "lotes": [_lote(lote) for lote in lotes],
        "fuente": "facturas_api",
        "ultimo_exito": estado["ultimo_exito"] if estado else None,
        "mas_antigua": mas_antigua,
        "vence_en": mas_antigua + timedelta(hours=HORAS_LIMITE_CONTINGENCIA) if mas_antigua else None,
        "horas_limite": HORAS_LIMITE_CONTINGENCIA,
    }


def _lote(fila: Any) -> dict[str, Any]:
    """Lote de facturas_api -> contrato del frontend: 'completado' con
    rechazos se muestra como 'con_rechazos'; `pendientes` = sin enviar."""
    estado = fila["estado"]
    if estado == "completado" and fila["rechazados"]:
        estado = "con_rechazos"
    return {
        "id": fila["id"],
        "documentos": fila["documentos"],
        "enviados": fila["enviados"],
        "aceptados": fila["aceptados"],
        "rechazados": fila["rechazados"],
        "pendientes": max(fila["documentos"] - fila["enviados"], 0),
        "estado": estado,
        "creado_en": fila["creado_en"],
        "iniciado_en": fila["iniciado_en"],
        "completado_en": fila["completado_en"],
        "ultimo_error": fila["ultimo_error"],
    }


async def _contingencia_calculada(conn: asyncpg.Connection) -> dict[str, Any]:
    """Respaldo para un facturas_api sin las tablas de contingencia: racha
    de fallos seguidos en los últimos envíos/consultas a la DGII; sin lotes,
    y los pendientes son los e-CF cuyo envío falló sin trackId."""
    eventos = await conn.fetch(
        """
        SELECT creado_en, error FROM facturacion.envios_dgii
        WHERE modulo = ANY($1::text[]) AND creado_en >= now() - make_interval(hours => $2)
        ORDER BY creado_en DESC, id DESC
        LIMIT 200
        """,
        list(MODULOS_DGII),
        HORAS_CONTINGENCIA,
    )
    racha = 0
    for evento in eventos:
        if evento["error"] is None:
            break
        racha += 1
    activa = racha >= UMBRAL_CONTINGENCIA
    desde = eventos[racha - 1]["creado_en"] if activa else None
    pendientes = await conn.fetchrow(
        f"""
        SELECT count(*) AS cantidad, COALESCE(sum({_TOTAL}), 0) AS monto
        FROM facturacion.facturas f
        CROSS JOIN LATERAL (SELECT f.datos::jsonb AS d) x
        WHERE f.estado = 'error' AND f.track_id IS NULL AND f.xml_generado IS NOT NULL
        """
    )
    consulta = await conn.fetchrow(
        """
        SELECT (SELECT max(creado_en) FROM facturacion.envios_dgii WHERE modulo = 'consulta_resultado') AS ultima,
               EXISTS (SELECT 1 FROM facturacion.facturas WHERE estado = 'enviado' AND track_id IS NOT NULL) AS hay
        """
    )
    inicio_papel = desde or _inicio_dia(hoy().replace(day=1))
    papel = await conn.fetchval("SELECT count(*) FROM operacion.facturas_papel WHERE creado_en >= $1", inicio_papel)
    proximo = None
    if consulta["hay"] and consulta["ultima"] is not None:
        proximo = consulta["ultima"] + timedelta(seconds=SEGUNDOS_BUCLE_FACTURAS)
    return {
        "activa": activa,
        "desde": desde,
        "errores_consecutivos": racha,
        "ultimo_intento": eventos[0]["creado_en"] if eventos else None,
        "ultimo_error": eventos[0]["error"] if racha else None,
        "proximo_intento": proximo,
        "pendientes": pendientes["cantidad"],
        "monto_pendiente": pendientes["monto"],
        "facturas_papel": papel,
        "lotes": [],
        "fuente": "calculada",
    }


async def envios_por_dia(pool: asyncpg.Pool, *, dias: int = 7) -> list[dict[str, Any]]:
    """Historial de envíos a la DGII por día (hora de RD), lo más reciente primero."""
    async with pool.acquire() as conn:
        filas = await conn.fetch(
            """
            SELECT (creado_en AT TIME ZONE 'America/Santo_Domingo')::date AS fecha,
                   count(DISTINCT encf) FILTER (WHERE modulo = 'recepcion') AS documentos,
                   count(DISTINCT encf) FILTER (WHERE modulo = 'consulta_resultado' AND codigo IN ('1', '4'))
                       AS aceptados,
                   count(DISTINCT encf) FILTER (WHERE modulo = 'consulta_resultado' AND codigo = '2') AS rechazados,
                   count(*) FILTER (WHERE error IS NOT NULL) AS errores
            FROM facturacion.envios_dgii
            WHERE creado_en >= $1 AND modulo IN ('recepcion', 'consulta_resultado')
            GROUP BY 1
            ORDER BY 1 DESC
            """,
            _inicio_dia(hoy() - timedelta(days=dias - 1)),
        )
    resultado = []
    for f in filas:
        if f["errores"]:
            estado = "con_errores"
        elif f["rechazados"]:
            estado = "con_rechazos"
        else:
            estado = "completado" if f["documentos"] or f["aceptados"] else "sin_envios"
        resultado.append(dict(f) | {"origen": "con_fallas" if f["errores"] else "en_linea", "resultado": estado})
    return resultado


# --- Resumen ---------------------------------------------------------

async def resumen(pool: asyncpg.Pool) -> dict[str, Any]:
    async with pool.acquire() as conn:
        contingencia = await _contingencia(conn)
        f = await conn.fetchrow(
            f"""
            SELECT
                count(*) FILTER (WHERE f.estado = 'enviado') AS en_proceso,
                COALESCE(sum({_TOTAL}) FILTER (WHERE f.estado = 'enviado'), 0) AS monto_en_proceso,
                min(f.actualizado_en) FILTER (WHERE f.estado = 'enviado') AS en_proceso_desde,
                count(*) FILTER (
                    WHERE f.estado = 'enviado' AND f.actualizado_en < now() - make_interval(days => $1)
                ) AS fuera_de_bucle,
                count(*) FILTER (WHERE f.estado = 'error') AS errores_envio,
                count(*) FILTER (WHERE f.estado = 'contingencia') AS en_contingencia,
                count(*) FILTER (WHERE {_INVENTARIO_PENDIENTE}) AS inventario_pendientes,
                count(*) FILTER (WHERE {_INVENTARIO_PENDIENTE} AND f.inventario_error IS NOT NULL) AS inventario_errores
            FROM facturacion.facturas f
            CROSS JOIN LATERAL (SELECT f.datos::jsonb AS d) x
            WHERE f.estado IN ('enviado', 'error', 'aceptado', 'contingencia')
            """,
            DIAS_BUCLE_RESULTADO,
        )
        e = await conn.fetchrow(
            f"""
            SELECT count(*) AS eventos,
                   count(*) FILTER (WHERE r.resultado = 'error') AS errores,
                   count(*) FILTER (WHERE r.resultado = 'rechazado') AS rechazos
            FROM facturacion.envios_dgii e
            CROSS JOIN LATERAL (SELECT {_RESULTADO_EVENTO} AS resultado) r
            WHERE e.creado_en >= now() - interval '7 days'
            """
        )
        ultimos = {
            modulo: await conn.fetchval(
                "SELECT max(creado_en) FROM facturacion.envios_dgii WHERE modulo = $1", modulo
            )
            for modulo in ("recepcion", "consulta_resultado", "aprobacion_comercial")
        }
        a = await conn.fetchrow(
            f"""
            SELECT
                count(*) FILTER (
                    WHERE estado_aprobacion = 'enviando' AND envio_iniciado_en > now() - {_ENVIO_VENCIDO}
                ) AS en_curso,
                count(*) FILTER (
                    WHERE estado_aprobacion = 'enviando'
                      AND (envio_iniciado_en IS NULL OR envio_iniciado_en <= now() - {_ENVIO_VENCIDO})
                ) AS atascadas,
                count(*) FILTER (WHERE estado_aprobacion = 'pendiente' AND error_aprobacion IS NOT NULL) AS fallidas,
                count(*) FILTER (
                    WHERE estado_aprobacion IN ('aprobado', 'rechazado') AND NOT enviado_al_emisor
                      AND error_envio_emisor IS NOT NULL
                ) AS entregas_fallidas,
                max(aprobado_en) AS ultima_aprobacion
            FROM recepcion.ecf_recibidos
            """
        )

    def canal(canal_: str, titulo: str, descripcion: str, pendientes: int, errores: int, ultima, detalle=None):
        estado = "error" if errores else ("pendiente" if pendientes else "ok")
        return {
            "canal": canal_, "titulo": titulo, "descripcion": descripcion, "pendientes": pendientes,
            "errores": errores, "estado": estado, "ultima_actividad": ultima, "detalle": detalle,
        }

    canales = [
        canal(
            "dgii_envio", "Envío de e-CF a la DGII",
            "Emitidos en contingencia (se envían solos al reconectar) y envíos que fallaron",
            f["en_contingencia"], f["errores_envio"], ultimos["recepcion"],
            "Los emitidos en contingencia se envían por lotes en cuanto la DGII responde; los que fallaron, "
            "reinténtelos o consulte el estado si la DGII llegó a darles un trackId.",
        ),
        canal(
            "dgii_resultado", "Resultado de la DGII", "e-CF enviados que esperan el resultado de su validación",
            f["en_proceso"], f["fuera_de_bucle"], ultimos["consulta_resultado"],
            f"facturas-api consulta solo los enviados de los últimos {DIAS_BUCLE_RESULTADO} días; los más "
            "antiguos se consultan a mano.",
        ),
        canal(
            "inventario", "Salidas de inventario", "Facturas enviadas cuya salida de mercancía no llegó a Inventario",
            f["inventario_pendientes"] - f["inventario_errores"], f["inventario_errores"], None,
            f"Se reintentan solas durante {DIAS_BUCLE_INVENTARIO} días (tarea de fondo de facturas-api).",
        ),
        canal(
            "aprobacion_comercial", "Aprobaciones comerciales", "ACECF de e-CF recibidos enviándose o que fallaron",
            a["en_curso"], a["atascadas"] + a["fallidas"], ultimos["aprobacion_comercial"],
        ),
        canal(
            "entrega_emisor", "Entrega del ACECF al proveedor",
            "Aprobaciones aceptadas por la DGII que no se pudieron entregar al emisor",
            0, a["entregas_fallidas"], a["ultima_aprobacion"],
            "La entrega es complementaria: la DGII ya tiene la aprobación.",
        ),
    ]
    pendientes = sum(c["pendientes"] for c in canales)
    errores = sum(c["errores"] for c in canales)
    return {
        "estado": "error" if errores or contingencia["activa"] else ("atencion" if pendientes else "ok"),
        "contingencia": contingencia,
        "canales": canales,
        "pendientes_total": pendientes,
        "errores_total": errores,
        "en_proceso_dgii": f["en_proceso"],
        "monto_en_proceso": f["monto_en_proceso"],
        "en_proceso_desde": f["en_proceso_desde"],
        "ultimo_envio": ultimos["recepcion"],
        "ultima_consulta_resultado": ultimos["consulta_resultado"],
        "eventos_7d": e["eventos"],
        "errores_7d": e["errores"],
        "rechazos_7d": e["rechazos"],
    }


# --- Pendientes ---------------------------------------------------------

def _item(canal: str, referencia_id: Any, **campos: Any) -> dict[str, Any]:
    datos = {
        "id": f"{canal}:{referencia_id}",
        "canal": canal,
        "referencia_id": str(referencia_id),
        "documento": None, "tipo_ecf": None, "contraparte": None, "fecha": None, "monto": None,
        "estado": "pendiente", "detalle": None, "accion": None, "decision": None, "motivo": None,
        "automatico": False, "enlace": None,
    }
    datos.update(campos)
    return datos


async def _pendientes_facturas(conn: asyncpg.Connection) -> list[dict[str, Any]]:
    filas = await conn.fetch(
        f"""
        SELECT f.id, f.tipo_ecf, f.encf, f.estado, f.track_id, f.dgii_estado, f.error,
               f.dgii_respuesta::text AS respuesta, (f.xml_generado IS NOT NULL) AS tiene_xml,
               f.actualizado_en, {_CLIENTE} AS cliente, {_TOTAL} AS total,
               f.actualizado_en >= now() - make_interval(days => $1) AS en_bucle,
               f.lote_id, COALESCE(f.firmado_en, f.contingencia_en) AS firmado_en
        FROM facturacion.facturas f
        CROSS JOIN LATERAL (SELECT f.datos::jsonb AS d) x
        WHERE f.estado IN ('enviado', 'error', 'contingencia')
        ORDER BY f.actualizado_en
        LIMIT $2
        """,
        DIAS_BUCLE_RESULTADO,
        _LIMITE_POR_CANAL * 2,
    )
    items = []
    for f in filas:
        comun = {
            "documento": f["encf"], "tipo_ecf": f["tipo_ecf"], "contraparte": f["cliente"],
            "fecha": f["actualizado_en"], "monto": f["total"], "enlace": f"/facturas/{f['id']}",
        }
        if f["estado"] == "contingencia":
            items.append(_item(
                "dgii_envio", f["id"], **(comun | {"fecha": f["firmado_en"] or f["actualizado_en"]}),
                estado="pendiente",
                detalle="Emitida en contingencia" + (f" · lote {f['lote_id']}" if f["lote_id"] else "")
                + " · se envía sola al restablecerse la conexión",
                automatico=True,
            ))
        elif f["estado"] == "enviado":
            items.append(_item(
                "dgii_resultado", f["id"], **comun,
                estado="en_curso" if f["en_bucle"] else "error",
                detalle=(
                    f"La DGII responde: {f['dgii_estado']}" if f["dgii_estado"] else "Esperando el resultado de la DGII"
                ) + ("" if f["en_bucle"] else f" · enviado hace más de {DIAS_BUCLE_RESULTADO} días: consúltelo a mano"),
                accion="actualizar_estado",
                automatico=bool(f["en_bucle"]),
            ))
        else:
            accion = "actualizar_estado" if f["track_id"] else ("reenviar" if f["tiene_xml"] else None)
            motivo = _motivo_dgii(f["respuesta"], f["error"])
            items.append(_item(
                "dgii_envio", f["id"], **comun,
                estado="error",
                detalle=motivo or "El envío a la DGII falló",
                accion=accion,
            ))
    return items


async def _pendientes_inventario(conn: asyncpg.Connection) -> list[dict[str, Any]]:
    filas = await conn.fetch(
        f"""
        SELECT f.id, f.tipo_ecf, f.encf, f.inventario_error, f.actualizado_en, {_CLIENTE} AS cliente,
               {_TOTAL} AS total, f.actualizado_en >= now() - make_interval(days => $1) AS en_bucle
        FROM facturacion.facturas f
        CROSS JOIN LATERAL (SELECT f.datos::jsonb AS d) x
        WHERE {_INVENTARIO_PENDIENTE}
        ORDER BY f.actualizado_en
        LIMIT $2
        """,
        DIAS_BUCLE_INVENTARIO,
        _LIMITE_POR_CANAL,
    )
    return [
        _item(
            "inventario", f["id"],
            documento=f["encf"], tipo_ecf=f["tipo_ecf"], contraparte=f["cliente"], fecha=f["actualizado_en"],
            monto=f["total"], enlace=f"/facturas/{f['id']}",
            estado="error" if f["inventario_error"] else "pendiente",
            detalle=(f["inventario_error"] or "Salida de mercancía pendiente de registrar")
            + ("" if f["en_bucle"] else f" · más de {DIAS_BUCLE_INVENTARIO} días: regístrela con un ajuste de salida"),
            automatico=bool(f["en_bucle"]),
        )
        for f in filas
    ]


async def _pendientes_recepcion(conn: asyncpg.Connection) -> list[dict[str, Any]]:
    filas = await conn.fetch(
        f"""
        SELECT id, encf, tipo_ecf, razon_social_emisor, rnc_emisor, monto_total, estado_aprobacion,
               decision_en_envio, motivo_rechazo, envio_iniciado_en, error_aprobacion, aprobado_en,
               error_envio_emisor, (acecf_xml IS NOT NULL) AS tiene_acecf, actualizado_en,
               (estado_aprobacion = 'enviando' AND envio_iniciado_en > now() - {_ENVIO_VENCIDO}) AS en_curso
        FROM recepcion.ecf_recibidos
        WHERE estado_aprobacion = 'enviando'
           OR (estado_aprobacion = 'pendiente' AND error_aprobacion IS NOT NULL)
           OR (estado_aprobacion IN ('aprobado', 'rechazado') AND NOT enviado_al_emisor
               AND error_envio_emisor IS NOT NULL)
        ORDER BY actualizado_en
        LIMIT $1
        """,
        _LIMITE_POR_CANAL,
    )
    items = []
    for f in filas:
        comun = {
            "documento": f["encf"], "tipo_ecf": f["tipo_ecf"],
            "contraparte": f["razon_social_emisor"] or f["rnc_emisor"], "monto": f["monto_total"],
            "enlace": f"/bandeja/{f['id']}",
        }
        if f["estado_aprobacion"] == "enviando":
            decision = f["decision_en_envio"]
            items.append(_item(
                "aprobacion_comercial", f["id"], **comun,
                fecha=f["envio_iniciado_en"] or f["actualizado_en"],
                estado="en_curso" if f["en_curso"] else "error",
                detalle=(
                    "Enviándose a la DGII"
                    if f["en_curso"]
                    else "El envío quedó interrumpido (el proceso no terminó): repítalo"
                ),
                accion=None if f["en_curso"] else "reintentar_aprobacion",
                decision=decision,
                motivo=f["motivo_rechazo"] if decision == "rechazado" else None,
            ))
        elif f["estado_aprobacion"] == "pendiente":
            items.append(_item(
                "aprobacion_comercial", f["id"], **comun,
                fecha=f["actualizado_en"], estado="error",
                detalle=f["error_aprobacion"], accion="decidir_aprobacion",
            ))
        else:
            items.append(_item(
                "entrega_emisor", f["id"], **comun,
                fecha=f["aprobado_en"] or f["actualizado_en"], estado="error",
                detalle=f["error_envio_emisor"]
                + ("" if f["tiene_acecf"] else " · no se guardó el ACECF firmado: no se puede reintentar"),
                accion="reintentar_entrega" if f["tiene_acecf"] else None,
                decision=f["estado_aprobacion"],
            ))
    return items


_ORDEN_ESTADO = {"error": 0, "pendiente": 1, "en_curso": 2}


async def listar_pendientes(pool: asyncpg.Pool, *, canal: str | None = None) -> list[dict[str, Any]]:
    """Todo lo que falta sincronizar, los errores primero y luego por antigüedad."""
    async with pool.acquire() as conn:
        items = (
            await _pendientes_facturas(conn) + await _pendientes_inventario(conn) + await _pendientes_recepcion(conn)
        )
    if canal is not None:
        items = [i for i in items if i["canal"] == canal]
    minimo = datetime.min.replace(tzinfo=_ZONA_RD)
    return sorted(items, key=lambda i: (_ORDEN_ESTADO[i["estado"]], i["fecha"] or minimo))


# --- Historial ---------------------------------------------------------

def _inicio_dia(fecha: date) -> datetime:
    return datetime.combine(fecha, time.min, tzinfo=_ZONA_RD)


async def historial(
    pool: asyncpg.Pool,
    *,
    modulo: str | None = None,
    resultado: str | None = None,
    q: str | None = None,
    desde: date | None = None,
    hasta: date | None = None,
    limite: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Auditoría de facturacion.envios_dgii, lo más reciente primero."""
    if desde and hasta and desde > hasta:
        raise DatosInvalidosError("La fecha inicial es posterior a la final")
    condiciones = []
    valores: list[Any] = []
    if modulo:
        valores.append(modulo)
        condiciones.append(f"e.modulo = ${len(valores)}")
    if resultado:
        valores.append(resultado)
        condiciones.append(f"r.resultado = ${len(valores)}")
    if q:
        valores.append(f"%{q.strip()}%")
        n = len(valores)
        condiciones.append(f"(e.encf ILIKE ${n} OR e.track_id ILIKE ${n} OR e.error ILIKE ${n})")
    if desde:
        valores.append(_inicio_dia(desde))
        condiciones.append(f"e.creado_en >= ${len(valores)}")
    if hasta:
        valores.append(_inicio_dia(hasta + timedelta(days=1)))
        condiciones.append(f"e.creado_en < ${len(valores)}")
    where = f"WHERE {' AND '.join(condiciones)}" if condiciones else ""
    valores.extend([limite, offset])
    async with pool.acquire() as conn:
        filas = await conn.fetch(
            f"""
            SELECT e.id, e.creado_en, e.modulo, e.ambiente, e.encf, e.track_id, e.estado, e.codigo, e.error,
                   r.resultado
            FROM facturacion.envios_dgii e
            CROSS JOIN LATERAL (SELECT {_RESULTADO_EVENTO} AS resultado) r
            {where}
            ORDER BY e.creado_en DESC, e.id DESC
            LIMIT ${len(valores) - 1} OFFSET ${len(valores)}
            """,
            *valores,
        )
    return [dict(f) | {"modulo_label": MODULOS.get(f["modulo"], f["modulo"])} for f in filas]


# --- Reintento de la entrega del ACECF al proveedor ---------------------------------------------------------

async def reintentar_entrega(pool: asyncpg.Pool, ecf_id: uuid.UUID, *, entregar: EntregarAlEmisor) -> dict[str, Any]:
    """Vuelve a entregar al proveedor el ACECF firmado que ya aceptó la DGII
    (best-effort, igual que en `decidir_aprobacion`): guarda el resultado y
    lo devuelve; un fallo de la entrega no es un error de la petición."""
    async with pool.acquire() as conn:
        fila = await conn.fetchrow(
            "SELECT id, encf, rnc_emisor, estado_aprobacion, acecf_xml, enviado_al_emisor"
            " FROM recepcion.ecf_recibidos WHERE id = $1",
            ecf_id,
        )
    if fila is None:
        raise SincronizacionNoEncontradaError(str(ecf_id))
    if fila["estado_aprobacion"] not in ("aprobado", "rechazado") or not fila["acecf_xml"]:
        raise ConflictoSincronizacionError(
            f"El e-CF {fila['encf']} no tiene una aprobación comercial aceptada por la DGII que entregar"
        )
    if fila["enviado_al_emisor"]:
        raise ConflictoSincronizacionError(f"El ACECF del e-CF {fila['encf']} ya se entregó al proveedor")
    try:
        await entregar(fila["rnc_emisor"], fila["encf"], fila["acecf_xml"].encode("utf-8"))
    except Exception as exc:
        enviado, error = False, str(exc) or exc.__class__.__name__
    else:
        enviado, error = True, None
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE recepcion.ecf_recibidos
            SET enviado_al_emisor = $2, error_envio_emisor = $3, actualizado_en = now()
            WHERE id = $1
            """,
            ecf_id,
            enviado,
            error,
        )
    return {"id": str(fila["id"]), "encf": fila["encf"], "enviado_al_emisor": enviado, "error_envio_emisor": error}


# --- Facturas en papel ---------------------------------------------------------

_COLUMNAS_PAPEL = """
    id, ncf, fecha, cliente_rnc, cliente_nombre, gravado_18, gravado_16, gravado_0, exento, itbis_18, itbis_16,
    total, forma_pago, notas, encf_reemplazo, creado_en
"""
_CAMPOS_PAPEL = (
    "ncf", "fecha", "cliente_rnc", "cliente_nombre", "gravado_18", "gravado_16", "gravado_0", "exento",
    "itbis_18", "itbis_16", "total", "forma_pago", "notas", "encf_reemplazo",
)


def _tipo_ncf(ncf: str) -> str:
    return ncf[1:3] if len(ncf) == 11 else ncf[9:11]


def _fila_papel(fila: asyncpg.Record) -> dict[str, Any]:
    datos = dict(fila)
    datos["id"] = str(datos["id"])
    datos["tipo_comprobante"] = _tipo_ncf(datos["ncf"])
    datos["itbis"] = datos["itbis_18"] + datos["itbis_16"]
    return datos


def _campos_papel(campos: dict[str, Any]) -> dict[str, Any]:
    """Valida y completa una factura en papel: NCF serie B (no e-NCF), RNC
    del cliente obligatorio en crédito fiscal (01), ITBIS por tasa (18 % / 16 %
    del gravado si no se indica) y total."""
    ncf = re.sub(r"\s", "", campos["ncf"]).upper()
    if formatos.es_encf(ncf):
        raise DatosInvalidosError("Un e-NCF no es una factura en papel: regístrelo como e-CF")
    if not formatos.validar_ncf_607(ncf):
        raise DatosInvalidosError(
            "NCF inválido: debe tener 11 posiciones (B + tipo + 8 dígitos) o 19 (formato anterior)"
        )
    rnc = re.sub(r"[^0-9]", "", campos.get("cliente_rnc") or "") or None
    if rnc is not None and len(rnc) not in (9, 11):
        raise DatosInvalidosError("El RNC o cédula del cliente debe tener 9 u 11 dígitos")
    if _tipo_ncf(ncf) == "01" and rnc is None:
        raise DatosInvalidosError("Una factura de crédito fiscal (B01) requiere el RNC o cédula del cliente")
    if campos["fecha"] > hoy():
        raise DatosInvalidosError("La fecha de emisión no puede ser futura")
    montos = {
        k: precalculos.redondear(Decimal(campos.get(k) or 0)) for k in ("gravado_18", "gravado_16", "gravado_0", "exento")
    }
    for clave, base, tasa in (("itbis_18", "gravado_18", "0.18"), ("itbis_16", "gravado_16", "0.16")):
        valor = campos.get(clave)
        montos[clave] = precalculos.redondear(Decimal(valor) if valor is not None else montos[base] * Decimal(tasa))
    total = sum(montos.values(), Decimal("0"))
    if total <= 0:
        raise DatosInvalidosError("Indique los montos de la factura")
    encf = re.sub(r"\s", "", campos.get("encf_reemplazo") or "").upper() or None
    if encf is not None and not formatos.es_encf(encf):
        raise DatosInvalidosError("El e-CF de reemplazo debe ser un e-NCF (E + 12 dígitos)")
    return {
        "ncf": ncf,
        "fecha": campos["fecha"],
        "cliente_rnc": rnc,
        "cliente_nombre": (campos.get("cliente_nombre") or "").strip() or None,
        **montos,
        "total": total,
        "forma_pago": campos.get("forma_pago") or 1,
        "notas": (campos.get("notas") or "").strip() or None,
        "encf_reemplazo": encf,
    }


async def listar_facturas_papel(
    pool: asyncpg.Pool, *, desde: date | None = None, hasta: date | None = None, q: str | None = None
) -> list[dict[str, Any]]:
    condiciones = []
    valores: list[Any] = []
    if desde:
        valores.append(desde)
        condiciones.append(f"fecha >= ${len(valores)}")
    if hasta:
        valores.append(hasta)
        condiciones.append(f"fecha <= ${len(valores)}")
    if q:
        valores.append(f"%{q.strip()}%")
        n = len(valores)
        condiciones.append(f"(ncf ILIKE ${n} OR cliente_nombre ILIKE ${n} OR cliente_rnc ILIKE ${n})")
    where = f"WHERE {' AND '.join(condiciones)}" if condiciones else ""
    async with pool.acquire() as conn:
        filas = await conn.fetch(
            f"SELECT {_COLUMNAS_PAPEL} FROM operacion.facturas_papel {where}"
            " ORDER BY fecha DESC, creado_en DESC LIMIT 500",
            *valores,
        )
    return [_fila_papel(f) for f in filas]


async def crear_factura_papel(pool: asyncpg.Pool, **campos: Any) -> dict[str, Any]:
    datos = _campos_papel(campos)
    parametros = ", ".join(f"${i}" for i in range(1, len(_CAMPOS_PAPEL) + 1))
    async with pool.acquire() as conn:
        try:
            fila = await conn.fetchrow(
                f"INSERT INTO operacion.facturas_papel ({', '.join(_CAMPOS_PAPEL)}) VALUES ({parametros})"
                f" RETURNING {_COLUMNAS_PAPEL}",
                *(datos[c] for c in _CAMPOS_PAPEL),
            )
        except asyncpg.UniqueViolationError as exc:
            raise ConflictoSincronizacionError(f"El NCF {datos['ncf']} ya está registrado") from exc
    return _fila_papel(fila)


async def actualizar_factura_papel(pool: asyncpg.Pool, factura_id: uuid.UUID, **campos: Any) -> dict[str, Any]:
    datos = _campos_papel(campos)
    asignaciones = ", ".join(f"{c} = ${i}" for i, c in enumerate(_CAMPOS_PAPEL, start=2))
    async with pool.acquire() as conn:
        try:
            fila = await conn.fetchrow(
                f"UPDATE operacion.facturas_papel SET {asignaciones}, actualizado_en = now() WHERE id = $1"
                f" RETURNING {_COLUMNAS_PAPEL}",
                factura_id,
                *(datos[c] for c in _CAMPOS_PAPEL),
            )
        except asyncpg.UniqueViolationError as exc:
            raise ConflictoSincronizacionError(f"El NCF {datos['ncf']} ya está registrado") from exc
    if fila is None:
        raise FacturaPapelNoEncontradaError(str(factura_id))
    return _fila_papel(fila)


async def eliminar_factura_papel(pool: asyncpg.Pool, factura_id: uuid.UUID) -> None:
    async with pool.acquire() as conn:
        borradas = await conn.fetchval(
            "WITH b AS (DELETE FROM operacion.facturas_papel WHERE id = $1 RETURNING 1) SELECT count(*) FROM b",
            factura_id,
        )
    if not borradas:
        raise FacturaPapelNoEncontradaError(str(factura_id))
