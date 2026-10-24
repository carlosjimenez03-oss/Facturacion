"""Asignación del e-NCF en el servidor y unicidad por emisor.

Hasta la fase 6 el e-NCF lo proponía el frontend (Nueva factura, POS) con
el mayor ya usado + 1 y facturas_api lo guardaba tal cual: dos cajas o dos
pestañas a la vez podían emitir el MISMO e-NCF (la DGII rechaza el segundo,
y queda una venta sin comprobante válido). Ahora:

- `crear_factura` con `encf` vacío o `asignar_encf=True` toma el siguiente
  número de la secuencia autorizada (`configuracion.secuencias`, la que se
  registra en erp-api → Configuración → Secuencias autorizadas) dentro de
  una transacción, con la fila de la secuencia bloqueada (`SELECT ... FOR
  UPDATE`): dos facturas simultáneas del mismo tipo esperan su turno y
  nunca reciben el mismo número. El siguiente es el mismo que muestra
  Configuración (erp_api/configuracion_repository.calcular_secuencia): el
  mayor entre `desde`, `proximo` y el último e-NCF guardado de ese tipo + 1.
  Si la secuencia está desactivada, vencida o agotada responde 409 con el
  motivo. Sin secuencia registrada: con `asignar_encf=True` también 409;
  con `encf` vacío, la factura queda en borrador sin e-NCF, como antes.
- Un e-NCF que manda el cliente se sigue aceptando, pero no si ya existe
  otra factura del mismo emisor con ese e-NCF (409).
- `asegurar_indice_encf_unico` (lifespan de main.py) crea el índice único
  parcial `(rnc_emisor, encf) WHERE encf IS NOT NULL` -- solo si no hay
  duplicados previos: si los hay, lo registra en el log y sigue sin índice
  (nunca impide el arranque); la comprobación de arriba sigue funcionando.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import asyncpg

from . import repository

logger = logging.getLogger(__name__)

ZONA = ZoneInfo("America/Santo_Domingo")
# Distinta de _LOCK_MIGRACIONES (7_002_001) y de las de erp_api.
_LOCK_INDICE = 7_002_002
INDICE_ENCF_UNICO = "facturas_rnc_encf_unico_idx"
DDL_INDICE_ENCF_UNICO = f"""
CREATE UNIQUE INDEX IF NOT EXISTS {INDICE_ENCF_UNICO}
    ON facturacion.facturas (rnc_emisor, encf) WHERE encf IS NOT NULL
"""


class EncfNoDisponibleError(Exception):
    """No se puede asignar o usar ese e-NCF (el mensaje va al usuario, 409)."""


def hoy() -> date:
    return datetime.now(ZONA).date()


def encf_de(tipo_ecf: int, numero: int) -> str:
    return f"E{tipo_ecf}{numero:010d}"


def normalizar_encf(encf: str | None) -> str | None:
    valor = (encf or "").strip().upper()
    return valor or None


class _UnaConexion:
    """Un "pool" de una sola conexión: para que repository.crear_factura
    inserte dentro de la transacción que tiene bloqueada la secuencia."""

    def __init__(self, conn: asyncpg.Connection):
        self._conn = conn

    def acquire(self):
        return self

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return False


def _con_encf(datos: dict[str, Any], encf: str) -> dict[str, Any]:
    """El e-NCF va también en Encabezado.IdDoc.eNCF (lo que lleva el XML),
    en su lugar del XSD: justo después de TipoeCF (el orden de las claves es
    el orden de los elementos, ver xml_builder.py)."""
    datos = json.loads(json.dumps(datos))
    encabezado = datos.setdefault("Encabezado", {})
    id_doc = encabezado.get("IdDoc") if isinstance(encabezado.get("IdDoc"), dict) else {}
    if "eNCF" in id_doc:
        id_doc["eNCF"] = encf
    else:
        resto = {k: v for k, v in id_doc.items() if k != "TipoeCF"}
        id_doc = {**({"TipoeCF": id_doc["TipoeCF"]} if "TipoeCF" in id_doc else {}), "eNCF": encf, **resto}
    encabezado["IdDoc"] = id_doc
    return datos


async def _secuencia_bloqueada(conn: asyncpg.Connection, tipo_ecf: int) -> asyncpg.Record | None:
    # to_regclass: sin el esquema de erp_api (todavía) no hay secuencias, y
    # una consulta fallida abortaría la transacción.
    if await conn.fetchval("SELECT to_regclass('configuracion.secuencias')") is None:
        return None
    return await conn.fetchrow(
        "SELECT * FROM configuracion.secuencias WHERE tipo_ecf = $1 FOR UPDATE", tipo_ecf
    )


async def _siguiente_numero(conn: asyncpg.Connection, tipo_ecf: int, secuencia: asyncpg.Record) -> int:
    mayor = await conn.fetchval(
        "SELECT max(substr(encf, 4)::bigint) FROM facturacion.facturas WHERE encf ~ $1",
        f"^E{tipo_ecf}[0-9]{{10}}$",
    )
    return max(int(secuencia["desde"]), int(secuencia["proximo"]), int(mayor or 0) + 1)


def validar_secuencia(tipo_ecf: int, secuencia: asyncpg.Record | dict, siguiente: int, dia: date) -> None:
    prefijo = f"E{tipo_ecf}"
    donde = "actualícela en Configuración → Secuencias autorizadas"
    if not secuencia["activa"]:
        raise EncfNoDisponibleError(f"La secuencia {prefijo} está desactivada: {donde}.")
    vence = secuencia["fecha_vencimiento"]
    if vence is not None and vence < dia:
        raise EncfNoDisponibleError(
            f"La secuencia {prefijo} venció el {vence:%d/%m/%Y}: solicite una nueva en la Oficina Virtual de la DGII y "
            f"{donde}."
        )
    if siguiente > int(secuencia["hasta"]):
        raise EncfNoDisponibleError(
            f"La secuencia {prefijo} está agotada (autorizada hasta {encf_de(tipo_ecf, int(secuencia['hasta']))}): "
            f"solicite una nueva en la Oficina Virtual de la DGII y {donde}."
        )


async def _bloquear_y_validar_libre(
    conn: asyncpg.Connection, rnc_emisor: str, encf: str, excluir: uuid.UUID | None = None
) -> None:
    """Serializa a quien use el mismo e-NCF (aunque todavía no exista el
    índice único) y rechaza el que ya tiene otra factura del emisor."""
    await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"encf|{rnc_emisor}|{encf}")
    otra = await conn.fetchval(
        """
        SELECT id FROM facturacion.facturas
        WHERE rnc_emisor = $1 AND encf = $2 AND ($3::uuid IS NULL OR id <> $3::uuid)
        LIMIT 1
        """,
        rnc_emisor,
        encf,
        excluir,
    )
    if otra is not None:
        raise EncfNoDisponibleError(
            f"El e-NCF {encf} ya está usado en otra factura: use el siguiente de la secuencia (deje que se asigne solo)."
        )


async def crear_factura(
    pool: asyncpg.Pool,
    *,
    tipo_ecf: int,
    ambiente: str,
    rnc_emisor: str,
    encf: str | None,
    datos: dict[str, Any],
    asignar_encf: bool = False,
    items_inventario: list[dict[str, Any]] | None = None,
    vendedor_id: uuid.UUID | None = None,
    dia: date | None = None,
) -> dict[str, Any]:
    encf = None if asignar_encf else normalizar_encf(encf)
    async with pool.acquire() as conn:
        async with conn.transaction():
            if encf is None:
                secuencia = await _secuencia_bloqueada(conn, tipo_ecf)
                if secuencia is None and asignar_encf:
                    raise EncfNoDisponibleError(
                        f"No hay una secuencia E{tipo_ecf} registrada: regístrela en Configuración → Secuencias autorizadas."
                    )
                if secuencia is not None:
                    siguiente = await _siguiente_numero(conn, tipo_ecf, secuencia)
                    validar_secuencia(tipo_ecf, secuencia, siguiente, dia or hoy())
                    encf = encf_de(tipo_ecf, siguiente)
                    datos = _con_encf(datos, encf)
            elif (datos.get("Encabezado") or {}).get("IdDoc"):
                datos = _con_encf(datos, encf)
            if encf is not None:
                await _bloquear_y_validar_libre(conn, rnc_emisor, encf)
            try:
                return await repository.crear_factura(
                    _UnaConexion(conn),
                    tipo_ecf=tipo_ecf,
                    ambiente=ambiente,
                    rnc_emisor=rnc_emisor,
                    encf=encf,
                    datos=datos,
                    items_inventario=items_inventario,
                    vendedor_id=vendedor_id,
                )
            except asyncpg.UniqueViolationError as exc:
                raise EncfNoDisponibleError(f"El e-NCF {encf} ya está usado en otra factura.") from exc


async def validar_encf_para_actualizar(
    pool: asyncpg.Pool, factura_id: uuid.UUID, rnc_emisor: str, encf: str | None
) -> None:
    """PUT de un borrador: el e-NCF nuevo no puede ser el de otra factura.
    (La carrera con otro request la cierra el índice único.)"""
    encf = normalizar_encf(encf)
    if encf is None:
        return
    async with pool.acquire() as conn:
        async with conn.transaction():
            await _bloquear_y_validar_libre(conn, rnc_emisor, encf, excluir=factura_id)


async def asegurar_indice_encf_unico(pool: Any) -> bool:
    """Crea el índice único (rnc_emisor, encf) si no hay duplicados. Nunca
    lanza: un problema aquí no debe impedir que el servicio arranque."""
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("SELECT pg_advisory_xact_lock($1)", _LOCK_INDICE)
                if await conn.fetchval("SELECT to_regclass($1)", f"facturacion.{INDICE_ENCF_UNICO}") is not None:
                    return True
                duplicados = await conn.fetch(
                    """
                    SELECT rnc_emisor, encf, count(*) AS n FROM facturacion.facturas
                    WHERE encf IS NOT NULL GROUP BY 1, 2 HAVING count(*) > 1
                    ORDER BY 3 DESC LIMIT 10
                    """
                )
                if duplicados:
                    logger.warning(
                        "No se crea el índice único de e-NCF: hay e-NCF repetidos en facturacion.facturas (%s). "
                        "Corríjalos (p. ej. borre los borradores repetidos) y reinicie facturas-api; mientras tanto "
                        "los duplicados nuevos se rechazan igual al crear la factura.",
                        ", ".join(f"{d['encf']} ×{d['n']}" for d in duplicados),
                    )
                    return False
                await conn.execute(DDL_INDICE_ENCF_UNICO)
        logger.info("Índice único de e-NCF por emisor verificado")
        return True
    except Exception:
        logger.exception("No se pudo crear el índice único de e-NCF (facturas-api arranca igual)")
        return False
