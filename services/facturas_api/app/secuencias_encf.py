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
- Con `clave_idempotencia` (el UUID que el POS le pone a cada venta hecha
  sin conexión con el servidor), la creación es idempotente: un reintento
  con la misma clave -- p. ej. la sincronización que se cortó después de
  crear la factura pero antes de recibir la respuesta -- no crea otra ni
  consume otro e-NCF, sino que lanza `FacturaYaCreadaError` con la
  existente (POST /facturas la devuelve con 200). Un advisory lock por clave
  serializa dos reintentos simultáneos, y lo respalda el índice único
  parcial de `clave_idempotencia` (migraciones.DDL_IDEMPOTENCIA).
- `asegurar_indice_encf_unico` (lifespan de main.py) crea el índice único
  parcial `(rnc_emisor, ambiente, encf) WHERE encf IS NOT NULL` -- solo si
  no hay duplicados previos: si los hay, lo registra en el log y sigue sin
  índice (nunca impide el arranque); la comprobación de arriba sigue
  funcionando. Con el nuevo creado, borra el anterior `(rnc_emisor, encf)`.

**Por ambiente (fase 7).** Las secuencias que autoriza la DGII son distintas
en TesteCF, CerteCF y eCF (se solicitan en cada ambiente): la secuencia, el
"mayor ya usado" y la unicidad del e-NCF son del ambiente de la factura (el
activo al crearla, ver app.ambiente). Así, pasar de pruebas a producción
empieza la numeración de producción desde su `desde`, sin chocar con los
e-NCF de pruebas.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import asyncpg

from libs.ecf_common.ambiente import nombre as nombre_ambiente
from libs.ecf_common.empresa import modo_sistema, todas_las_empresas

from . import repository

logger = logging.getLogger(__name__)

ZONA = ZoneInfo("America/Santo_Domingo")
# Distinta de _LOCK_MIGRACIONES (7_002_001) y de las de erp_api.
_LOCK_INDICE = 7_002_002
INDICE_ENCF_UNICO = "facturas_rnc_ambiente_encf_unico_idx"
# Índice de antes de la fase 7 (sin ambiente): se borra al crear el nuevo.
INDICE_ENCF_UNICO_ANTERIOR = "facturas_rnc_encf_unico_idx"
DDL_INDICE_ENCF_UNICO = f"""
CREATE UNIQUE INDEX IF NOT EXISTS {INDICE_ENCF_UNICO}
    ON facturacion.facturas (rnc_emisor, ambiente, encf) WHERE encf IS NOT NULL
"""


class EncfNoDisponibleError(Exception):
    """No se puede asignar o usar ese e-NCF (el mensaje va al usuario, 409)."""


class FacturaYaCreadaError(Exception):
    """Ya existe la factura de esa clave de idempotencia (un reintento): se
    devuelve esa en vez de crear otra."""

    def __init__(self, factura: dict[str, Any]):
        super().__init__(factura["id"])
        self.factura = factura


class ClaveIdempotenciaReusadaError(Exception):
    """La clave de idempotencia ya es de una factura distinta (otro tipo de
    e-CF): el cliente reusó una clave que no es de esta venta (409)."""


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


async def _secuencia_bloqueada(conn: asyncpg.Connection, tipo_ecf: int, ambiente: str) -> asyncpg.Record | None:
    # to_regclass: sin el esquema de erp_api (todavía) no hay secuencias, y
    # una consulta fallida abortaría la transacción. Una base anterior a la
    # fase 7 (sin la columna `ambiente`) tiene una secuencia por tipo.
    if await conn.fetchval("SELECT to_regclass('configuracion.secuencias')") is None:
        return None
    por_ambiente = await conn.fetchval(
        """
        SELECT EXISTS (SELECT 1 FROM information_schema.columns
                       WHERE table_schema = 'configuracion' AND table_name = 'secuencias' AND column_name = 'ambiente')
        """
    )
    if not por_ambiente:
        return await conn.fetchrow("SELECT * FROM configuracion.secuencias WHERE tipo_ecf = $1 FOR UPDATE", tipo_ecf)
    return await conn.fetchrow(
        "SELECT * FROM configuracion.secuencias WHERE tipo_ecf = $1 AND ambiente = $2 FOR UPDATE", tipo_ecf, ambiente
    )


async def _siguiente_numero(
    conn: asyncpg.Connection, tipo_ecf: int, secuencia: asyncpg.Record, ambiente: str
) -> int:
    mayor = await conn.fetchval(
        "SELECT max(substr(encf, 4)::bigint) FROM facturacion.facturas WHERE encf ~ $1 AND ambiente = $2",
        f"^E{tipo_ecf}[0-9]{{10}}$",
        ambiente,
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
    conn: asyncpg.Connection, rnc_emisor: str, ambiente: str, encf: str, excluir: uuid.UUID | None = None
) -> None:
    """Serializa a quien use el mismo e-NCF (aunque todavía no exista el
    índice único) y rechaza el que ya tiene otra factura del emisor en el
    mismo ambiente."""
    await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"encf|{rnc_emisor}|{ambiente}|{encf}")
    otra = await conn.fetchval(
        """
        SELECT id FROM facturacion.facturas
        WHERE rnc_emisor = $1 AND ambiente = $4 AND encf = $2 AND ($3::uuid IS NULL OR id <> $3::uuid)
        LIMIT 1
        """,
        rnc_emisor,
        encf,
        excluir,
        ambiente,
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
    clave_idempotencia: uuid.UUID | None = None,
    dia: date | None = None,
) -> dict[str, Any]:
    encf = None if asignar_encf else normalizar_encf(encf)
    async with pool.acquire() as conn:
        async with conn.transaction():
            if clave_idempotencia is not None:
                # Antes de bloquear la secuencia: un reintento no consume otro e-NCF.
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext($1))", f"factura-idempotencia|{clave_idempotencia}"
                )
                existente = await repository.obtener_por_clave_idempotencia(_UnaConexion(conn), clave_idempotencia)
                if existente is not None:
                    if existente["tipo_ecf"] != tipo_ecf:
                        raise ClaveIdempotenciaReusadaError(
                            f"La clave de idempotencia {clave_idempotencia} ya es de otra factura "
                            f"({existente['encf'] or existente['id']}, tipo {existente['tipo_ecf']})."
                        )
                    raise FacturaYaCreadaError(existente)
            if encf is None:
                secuencia = await _secuencia_bloqueada(conn, tipo_ecf, ambiente)
                if secuencia is None and asignar_encf:
                    raise EncfNoDisponibleError(
                        f"No hay una secuencia E{tipo_ecf} registrada para el ambiente {nombre_ambiente(ambiente)}: "
                        "regístrela en Configuración → Secuencias autorizadas."
                    )
                if secuencia is not None:
                    siguiente = await _siguiente_numero(conn, tipo_ecf, secuencia, ambiente)
                    validar_secuencia(tipo_ecf, secuencia, siguiente, dia or hoy())
                    encf = encf_de(tipo_ecf, siguiente)
                    datos = _con_encf(datos, encf)
            elif (datos.get("Encabezado") or {}).get("IdDoc"):
                datos = _con_encf(datos, encf)
            if encf is not None:
                await _bloquear_y_validar_libre(conn, rnc_emisor, ambiente, encf)
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
                    clave_idempotencia=clave_idempotencia,
                )
            except asyncpg.UniqueViolationError as exc:
                raise EncfNoDisponibleError(f"El e-NCF {encf} ya está usado en otra factura.") from exc


async def validar_encf_para_actualizar(
    pool: asyncpg.Pool, factura_id: uuid.UUID, rnc_emisor: str, encf: str | None, ambiente: str
) -> None:
    """PUT de un borrador: el e-NCF nuevo no puede ser el de otra factura.
    (La carrera con otro request la cierra el índice único.)"""
    encf = normalizar_encf(encf)
    if encf is None:
        return
    async with pool.acquire() as conn:
        async with conn.transaction():
            await _bloquear_y_validar_libre(conn, rnc_emisor, ambiente, encf, excluir=factura_id)


async def asegurar_indice_encf_unico(pool: Any) -> bool:
    """Crea el índice único (rnc_emisor, encf) si no hay duplicados. Nunca
    lanza: un problema aquí no debe impedir que el servicio arranque.
    Como el dueño de la tabla (CREATE INDEX) y mirando todas las empresas."""
    try:
        with modo_sistema(), todas_las_empresas():
            return await _asegurar_indice_encf_unico(pool)
    except Exception:
        logger.exception("No se pudo crear el índice único de e-NCF (facturas-api arranca igual)")
        return False


async def _asegurar_indice_encf_unico(pool: Any) -> bool:
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock($1)", _LOCK_INDICE)
            if await conn.fetchval("SELECT to_regclass($1)", f"facturacion.{INDICE_ENCF_UNICO}") is not None:
                return True
            duplicados = await conn.fetch(
                """
                SELECT rnc_emisor, ambiente, encf, count(*) AS n FROM facturacion.facturas
                WHERE encf IS NOT NULL GROUP BY 1, 2, 3 HAVING count(*) > 1
                ORDER BY 4 DESC LIMIT 10
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
            await conn.execute(f"DROP INDEX IF EXISTS facturacion.{INDICE_ENCF_UNICO_ANTERIOR}")
    logger.info("Índice único de e-NCF por emisor verificado")
    return True
