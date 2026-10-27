"""Padrón de RNC de la DGII (docs/PADRON_RNC.md).

- Importación: el listado público `DGII_RNC.zip` (DGII_PADRON_URL) se
  descarga una vez al día (PADRON_ACTIVO, PADRON_CADA_HORAS) o lo sube un
  administrador de la instalación (servidores que no llegan a dgii.gov.do).
  Se lee en streaming (padron_archivo.py), se copia con COPY a una tabla
  temporal y, en la MISMA transacción, se aplican altas, cambios y bajas a
  `configuracion.padron_rnc`: quien consulta ve el padrón anterior completo
  hasta el COMMIT, y si algo falla queda el anterior con el error anotado.
  Un advisory lock de sesión hace que una sola réplica importe a la vez.
- Consulta: `GET /padron/{rnc}` (RNC de 9 dígitos o cédula de 11) para
  llenar razón social, nombre comercial, actividad, estado y régimen en
  Agregar empresa, Contactos y la factura.

El padrón es GLOBAL (sin empresa_id ni RLS): el mismo listado público para
todas las empresas de la instalación. Contrato con el frontend:
`frontend/src/lib/padronApi.ts`.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import shutil
import tempfile
import time
from collections.abc import AsyncIterator
from datetime import date, datetime, timedelta, timezone
from itertools import islice
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from libs.ecf_common.empresa import es_admin_plataforma, modo_sistema

from .config import ErpApiSettings, get_settings
from .padron_archivo import COLUMNAS, ESTADO_ACTIVO, ArchivoPadronError, LectorPadron, limpiar_rnc

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/padron", tags=["padron"])

# Advisory lock (de sesión) de la importación: una réplica a la vez.
LOCK_PADRON = 7_007_022
MB = 1024 * 1024
_LOTE = 5000
# Si el archivo nuevo trae menos de esta parte del padrón actual, se
# rechaza (un archivo cortado dejaría sin datos a medio país).
PROPORCION_MINIMA = 0.5
_MINIMO_PARA_COMPARAR = 1000
_REVISAR_CADA_SEGUNDOS = 900.0
_REINTENTO_TRAS_ERROR = timedelta(hours=1)

SIN_PADRON_DESCARGANDO = (
    "El listado de RNC de la DGII aún se está descargando: intente de nuevo en unos minutos. "
    "Mientras tanto puede completar los datos a mano."
)
SIN_PADRON = (
    "El listado de RNC de la DGII todavía no se ha importado en esta instalación: un administrador puede "
    "actualizarlo o subir el DGII_RNC.zip en Configuración → Padrón de RNC de la DGII. "
    "Mientras tanto puede completar los datos a mano."
)
SOLO_ADMIN_INSTALACION = (
    "Solo un administrador de la instalación puede actualizar el padrón de RNC (es el mismo para todas las empresas)"
)
EN_CURSO = "Ya se está actualizando el padrón de RNC: espere a que termine"


class PadronOcupadoError(Exception):
    """Otra réplica (u otra petición) ya está importando."""


class PadronImportacionError(Exception):
    """La importación falló; el padrón anterior quedó intacto."""


# --- importación ------------------------------------------------------------------------------------

_SQL_TEMPORAL = """
CREATE TEMP TABLE padron_carga (
    n                 bigint GENERATED ALWAYS AS IDENTITY,
    rnc               text NOT NULL,
    razon_social      text NOT NULL,
    nombre_comercial  text,
    actividad         text,
    estado            text,
    regimen           text,
    fecha_inicio      date
) ON COMMIT DROP
"""

# Altas y cambios (la última línea de un RNC repetido gana; una fila igual
# no se toca: el WAL y la hinchazón diarios son solo lo que cambió).
_SQL_ALTAS_Y_CAMBIOS = """
WITH f AS (
    SELECT DISTINCT ON (rnc) rnc, razon_social, nombre_comercial, actividad, estado, regimen, fecha_inicio
    FROM padron_carga ORDER BY rnc, n DESC
), up AS (
    INSERT INTO configuracion.padron_rnc AS p
        (rnc, razon_social, nombre_comercial, actividad, estado, regimen, fecha_inicio, actualizado_en)
    SELECT rnc, razon_social, nombre_comercial, actividad, estado, regimen, fecha_inicio, now() FROM f
    ON CONFLICT (rnc) DO UPDATE SET
        razon_social = EXCLUDED.razon_social,
        nombre_comercial = EXCLUDED.nombre_comercial,
        actividad = EXCLUDED.actividad,
        estado = EXCLUDED.estado,
        regimen = EXCLUDED.regimen,
        fecha_inicio = EXCLUDED.fecha_inicio,
        actualizado_en = now()
    WHERE (p.razon_social, p.nombre_comercial, p.actividad, p.estado, p.regimen, p.fecha_inicio)
          IS DISTINCT FROM (EXCLUDED.razon_social, EXCLUDED.nombre_comercial, EXCLUDED.actividad,
                            EXCLUDED.estado, EXCLUDED.regimen, EXCLUDED.fecha_inicio)
    RETURNING (xmax = 0) AS nueva
)
SELECT count(*) FILTER (WHERE nueva) AS nuevas, count(*) FILTER (WHERE NOT nueva) AS cambiadas FROM up
"""

_SQL_BAJAS = """
DELETE FROM configuracion.padron_rnc p
WHERE NOT EXISTS (SELECT 1 FROM padron_carga c WHERE c.rnc = p.rnc)
"""

_SQL_INTENTO = """
INSERT INTO configuracion.padron_rnc_estado (id, intentado_en) VALUES (1, now())
ON CONFLICT (id) DO UPDATE SET intentado_en = now()
"""

_SQL_EXITO = """
INSERT INTO configuracion.padron_rnc_estado
    (id, importado_en, filas, origen, nuevas, cambiadas, eliminadas, duracion_segundos, intentado_en, error, error_en)
VALUES (1, now(), $1, $2, $3, $4, $5, $6, now(), NULL, NULL)
ON CONFLICT (id) DO UPDATE SET
    importado_en = now(), filas = $1, origen = $2, nuevas = $3, cambiadas = $4, eliminadas = $5,
    duracion_segundos = $6, error = NULL, error_en = NULL
"""

_SQL_ERROR = """
INSERT INTO configuracion.padron_rnc_estado (id, intentado_en, error, error_en) VALUES (1, now(), $1, now())
ON CONFLICT (id) DO UPDATE SET error = $1, error_en = now()
"""

_SQL_EN_CURSO = """
SELECT EXISTS (SELECT 1 FROM pg_locks WHERE locktype = 'advisory' AND granted
               AND classid = 0 AND objid = $1 AND objsubid = 1)
"""


async def _por_lotes(lector: LectorPadron) -> AsyncIterator[tuple]:
    """Las filas del archivo para COPY. Leer y descomprimir es trabajo de
    CPU: se hace en un hilo, de a lotes, sin frenar las demás peticiones."""
    filas = lector.filas()

    def siguiente() -> list[tuple]:
        return [f.tupla() for f in islice(filas, _LOTE)]

    while True:
        lote = await asyncio.to_thread(siguiente)
        if not lote:
            return
        for tupla in lote:
            yield tupla


async def _antes_de_confirmar() -> None:
    """Punto de las pruebas: la importación terminó pero no hizo COMMIT."""


async def _cargar(
    conn, archivo: Path, origen: str, *, max_descomprimido: int, proporcion_minima: float
) -> dict[str, Any]:
    inicio = time.monotonic()
    lector = LectorPadron(archivo, max_descomprimido=max_descomprimido)
    async with conn.transaction():
        # Sin límite de la sesión (algunos roles de Supabase lo traen): la
        # primera carga son cientos de miles de filas.
        await conn.execute("SET LOCAL statement_timeout = 0")
        await conn.execute("SET LOCAL lock_timeout = '30s'")
        await conn.execute(_SQL_TEMPORAL)
        await conn.copy_records_to_table("padron_carga", records=_por_lotes(lector), columns=list(COLUMNAS))
        await conn.execute("ANALYZE padron_carga")
        recibidas = await conn.fetchval("SELECT count(DISTINCT rnc) FROM padron_carga")
        actuales = await conn.fetchval("SELECT count(*) FROM configuracion.padron_rnc")
        if actuales >= _MINIMO_PARA_COMPARAR and recibidas < actuales * proporcion_minima:
            raise ArchivoPadronError(
                f"El archivo trae {recibidas:,} contribuyentes y el padrón actual tiene {actuales:,}: parece "
                "incompleto, se conserva el actual"
            )
        cambios = await conn.fetchrow(_SQL_ALTAS_Y_CAMBIOS)
        eliminadas = int((await conn.execute(_SQL_BAJAS)).split()[-1])
        filas = await conn.fetchval("SELECT count(*) FROM configuracion.padron_rnc")
        duracion = round(time.monotonic() - inicio, 1)
        await conn.execute(_SQL_EXITO, filas, origen, cambios["nuevas"], cambios["cambiadas"], eliminadas, duracion)
        await _antes_de_confirmar()
    resultado = {
        "filas": filas,
        "nuevas": cambios["nuevas"],
        "cambiadas": cambios["cambiadas"],
        "eliminadas": eliminadas,
        "lineas": lector.lineas,
        "descartadas": lector.descartadas,
        "duracion_segundos": duracion,
        "origen": origen,
    }
    logger.info("Padrón de RNC importado: %s", resultado)
    return resultado


async def descargar(settings: ErpApiSettings, destino: Path) -> str:
    """DGII_RNC.zip a `destino`, sin pasar de PADRON_MAX_MB. Devuelve el origen."""
    url = settings.dgii_padron_url
    limite = settings.padron_max_mb * MB
    ayuda = (
        "Si este servidor no llega a dgii.gov.do, descargue el DGII_RNC.zip en otro equipo y súbalo en "
        "Configuración → Padrón de RNC de la DGII."
    )
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(settings.padron_timeout_segundos, connect=30.0),
            follow_redirects=True,
            headers={"User-Agent": "Kontara/1.0 (padron de RNC)"},
        ) as cliente:
            async with cliente.stream("GET", url) as respuesta:
                if respuesta.status_code != 200:
                    raise PadronImportacionError(f"La DGII respondió {respuesta.status_code} al descargar {url}. {ayuda}")
                if "text/html" in respuesta.headers.get("content-type", ""):
                    raise PadronImportacionError(
                        f"La DGII respondió una página web en lugar del archivo ({url}): puede estar en "
                        f"mantenimiento o bloqueando la descarga. {ayuda}"
                    )
                recibidos = 0
                with open(destino, "wb") as fh:
                    async for trozo in respuesta.aiter_bytes(1 << 16):
                        recibidos += len(trozo)
                        if recibidos > limite:
                            raise PadronImportacionError(
                                f"El archivo de la DGII pasa de {settings.padron_max_mb} MB (PADRON_MAX_MB)"
                            )
                        fh.write(trozo)
    except httpx.HTTPError as exc:
        raise PadronImportacionError(
            f"No se pudo descargar el padrón de la DGII ({url}): {exc.__class__.__name__}. {ayuda}"
        ) from exc
    return url


async def importar(
    pool,
    settings: ErpApiSettings,
    *,
    archivo: Path | None = None,
    origen: str | None = None,
    proporcion_minima: float = PROPORCION_MINIMA,
) -> dict[str, Any]:
    """Descarga (sin `archivo`) e importa el padrón. PadronOcupadoError si
    otra réplica ya lo está haciendo; PadronImportacionError si falla (el
    padrón anterior queda y el error se anota en padron_rnc_estado)."""
    with modo_sistema():
        async with pool.acquire() as conn:
            if not await conn.fetchval("SELECT pg_try_advisory_lock($1)", LOCK_PADRON):
                raise PadronOcupadoError(EN_CURSO)
            carpeta = Path(tempfile.mkdtemp(prefix="kontara-padron-"))
            try:
                await conn.execute(_SQL_INTENTO)
                try:
                    if archivo is None:
                        archivo = carpeta / "DGII_RNC.zip"
                        origen = await descargar(settings, archivo)
                    return await _cargar(
                        conn,
                        archivo,
                        origen or archivo.name,
                        max_descomprimido=settings.padron_max_descomprimido_mb * MB,
                        proporcion_minima=proporcion_minima,
                    )
                except Exception as exc:
                    mensaje = str(exc) if isinstance(exc, (ArchivoPadronError, PadronImportacionError)) else (
                        f"Error inesperado al importar el padrón: {exc.__class__.__name__}: {exc}"
                    )
                    logger.warning("No se pudo importar el padrón de RNC: %s", mensaje)
                    with contextlib.suppress(Exception):
                        await conn.execute(_SQL_ERROR, mensaje[:1000])
                    raise PadronImportacionError(mensaje) from exc
            finally:
                shutil.rmtree(carpeta, ignore_errors=True)
                with contextlib.suppress(Exception):
                    await conn.execute("SELECT pg_advisory_unlock($1)", LOCK_PADRON)


# --- estado y tarea diaria --------------------------------------------------------------------------

async def leer_estado(conn) -> dict[str, Any]:
    if await conn.fetchval("SELECT to_regclass('configuracion.padron_rnc_estado')") is None:
        fila = None
    else:
        fila = await conn.fetchrow("SELECT * FROM configuracion.padron_rnc_estado WHERE id = 1")
    estado = dict(fila) if fila else {}
    estado.pop("id", None)
    estado["en_curso"] = bool(await conn.fetchval(_SQL_EN_CURSO, LOCK_PADRON))
    return estado


def toca_importar(estado: dict[str, Any], ahora: datetime, cada_horas: float) -> bool:
    """Nunca importado: sí. Un intento hace poco (bien o mal): todavía no
    (tras un error se reintenta cada hora). Si no, cuando el último bueno
    tiene PADRON_CADA_HORAS."""
    if estado.get("en_curso"):
        return False
    intento = estado.get("intentado_en")
    if intento is not None and ahora - intento < min(_REINTENTO_TRAS_ERROR, timedelta(hours=cada_horas)):
        return False
    ultimo = estado.get("importado_en")
    return ultimo is None or ahora - ultimo >= timedelta(hours=cada_horas)


async def actualizar_si_toca(pool, settings: ErpApiSettings) -> dict[str, Any] | None:
    with modo_sistema():
        async with pool.acquire() as conn:
            estado = await leer_estado(conn)
    if not toca_importar(estado, datetime.now(timezone.utc), settings.padron_cada_horas):
        return None
    try:
        return await importar(pool, settings)
    except PadronOcupadoError:
        return None


async def bucle_actualizacion(pool, settings: ErpApiSettings) -> None:
    """Tarea de fondo del lifespan (main.py): la primera vez poco después de
    arrancar (si el padrón está vacío o viejo) y luego cada 15 minutos mira
    si ya toca. Un error queda anotado y no detiene la tarea."""
    await asyncio.sleep(settings.padron_primera_espera_segundos)
    while True:
        try:
            await actualizar_si_toca(pool, settings)
        except asyncio.CancelledError:
            raise
        except PadronImportacionError:
            pass  # ya anotado en padron_rnc_estado
        except Exception:
            logger.exception("Falló la revisión del padrón de RNC")
        await asyncio.sleep(_REVISAR_CADA_SEGUNDOS)


_TAREAS: set[asyncio.Task] = set()


def lanzar(pool, settings: ErpApiSettings, *, archivo: Path | None = None, origen: str | None = None) -> asyncio.Task:
    """Importación en segundo plano (Actualizar ahora / archivo subido):
    puede tardar más que el tiempo de espera de nginx. La pantalla consulta
    /padron/estado hasta que termina."""

    async def correr() -> None:
        try:
            await importar(pool, settings, archivo=archivo, origen=origen)
        except (PadronOcupadoError, PadronImportacionError):
            pass
        except Exception:
            logger.exception("Falló la importación del padrón de RNC")
        finally:
            if archivo is not None:
                shutil.rmtree(archivo.parent, ignore_errors=True)

    tarea = asyncio.create_task(correr())
    _TAREAS.add(tarea)
    tarea.add_done_callback(_TAREAS.discard)
    return tarea


# --- consulta -------------------------------------------------------------------------------------

class Contribuyente(BaseModel):
    rnc: str
    razon_social: str
    nombre_comercial: str | None = None
    actividad: str | None = None
    estado: str | None = None
    regimen: str | None = None
    fecha_inicio: date | None = None
    activo: bool
    fuente: str = "padron"
    # Fecha de la última importación del padrón (de qué día son los datos).
    padron_actualizado_en: datetime | None = None


class EstadoPadron(BaseModel):
    importado_en: datetime | None = None
    filas: int | None = None
    origen: str | None = None
    nuevas: int | None = None
    cambiadas: int | None = None
    eliminadas: int | None = None
    duracion_segundos: float | None = None
    intentado_en: datetime | None = None
    error: str | None = None
    error_en: datetime | None = None
    en_curso: bool = False
    automatico: bool
    cada_horas: float
    url: str
    max_mb: int
    puede_actualizar: bool


class PadronNoDisponibleError(Exception):
    """El padrón no se ha importado todavía (mensaje para el usuario)."""


def _mensaje_sin_padron(estado: dict[str, Any], settings: ErpApiSettings) -> str:
    if estado.get("en_curso") or (settings.padron_activo and not estado.get("error")):
        return SIN_PADRON_DESCARGANDO
    return SIN_PADRON


def texto_no_encontrado(rnc: str, estado: dict[str, Any]) -> str:
    tipo = "La cédula" if len(rnc) == 11 else "El RNC"
    fecha = estado.get("importado_en")
    cuando = f" (actualizado el {fecha:%d/%m/%Y})" if fecha else ""
    return f"{tipo} {rnc} no aparece en el padrón de RNC de la DGII{cuando}."


async def buscar(pool, settings: ErpApiSettings, rnc: str) -> dict[str, Any] | None:
    """El contribuyente o None si no está. PadronNoDisponibleError si el
    padrón todavía no se ha importado."""
    async with pool.acquire() as conn:
        if await conn.fetchval("SELECT to_regclass('configuracion.padron_rnc')") is None:
            raise PadronNoDisponibleError(SIN_PADRON)
        fila = await conn.fetchrow(
            """
            SELECT p.*, e.importado_en AS padron_actualizado_en
            FROM configuracion.padron_rnc p
            LEFT JOIN configuracion.padron_rnc_estado e ON e.id = 1
            WHERE p.rnc = $1
            """,
            rnc,
        )
        if fila is not None:
            datos = dict(fila)
            datos.pop("actualizado_en", None)
            return {**datos, "activo": (datos.get("estado") or "") == ESTADO_ACTIVO, "fuente": "padron"}
        estado = await leer_estado(conn)
        if estado.get("importado_en") is None and not await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM configuracion.padron_rnc)"
        ):
            raise PadronNoDisponibleError(_mensaje_sin_padron(estado, settings))
    return None


def rnc_valido(rnc: str) -> str:
    limpio = limpiar_rnc(rnc)
    if len(limpio) not in (9, 11):
        raise HTTPException(status_code=422, detail="El RNC debe tener 9 dígitos (o 11 si es una cédula)")
    return limpio


async def _estado_respuesta(request: Request, settings: ErpApiSettings) -> dict[str, Any]:
    pool = request.app.state.db_pool
    async with pool.acquire() as conn:
        estado = await leer_estado(conn)
    return {
        **estado,
        "automatico": settings.padron_activo,
        "cada_horas": settings.padron_cada_horas,
        "url": settings.dgii_padron_url,
        "max_mb": settings.padron_max_mb,
        "puede_actualizar": await es_admin_plataforma(pool, getattr(request.state, "usuario", None)),
    }


async def _exigir_admin_instalacion(request: Request) -> None:
    if not await es_admin_plataforma(request.app.state.db_pool, getattr(request.state, "usuario", None)):
        raise HTTPException(status_code=403, detail=SOLO_ADMIN_INSTALACION)


async def _exigir_libre(request: Request) -> None:
    async with request.app.state.db_pool.acquire() as conn:
        if await conn.fetchval(_SQL_EN_CURSO, LOCK_PADRON):
            raise HTTPException(status_code=409, detail=EN_CURSO)


@router.get("/estado", response_model=EstadoPadron)
async def estado(request: Request, settings: ErpApiSettings = Depends(get_settings)):
    """Configuración → Padrón de RNC de la DGII: la última importación."""
    return await _estado_respuesta(request, settings)


@router.post("/actualizar", response_model=EstadoPadron, status_code=202)
async def actualizar_ahora(request: Request, settings: ErpApiSettings = Depends(get_settings)):
    """Descarga e importa ahora (en segundo plano)."""
    await _exigir_admin_instalacion(request)
    await _exigir_libre(request)
    lanzar(request.app.state.db_pool, settings)
    return {**await _estado_respuesta(request, settings), "en_curso": True}


_TIPOS_ARCHIVO = (b"PK\x03\x04",)


@router.post("/archivo", response_model=EstadoPadron, status_code=202)
async def subir_archivo(request: Request, settings: ErpApiSettings = Depends(get_settings)):
    """Sube el DGII_RNC.zip (o el TXT) en el cuerpo del request (binario,
    sin multipart) y lo importa en segundo plano. Para servidores que no
    llegan a dgii.gov.do."""
    await _exigir_admin_instalacion(request)
    await _exigir_libre(request)
    limite = settings.padron_max_mb * MB
    declarado = request.headers.get("content-length")
    if declarado and declarado.isdigit() and int(declarado) > limite:
        raise HTTPException(status_code=413, detail=f"El archivo pasa de {settings.padron_max_mb} MB")
    nombre = (request.headers.get("x-nombre-archivo") or "DGII_RNC.zip").replace("\\", "/").rsplit("/", 1)[-1]
    nombre = unquote(nombre)[:120] or "DGII_RNC.zip"
    carpeta = Path(tempfile.mkdtemp(prefix="kontara-padron-subido-"))
    destino = carpeta / "padron.zip"
    try:
        recibidos = 0
        with open(destino, "wb") as fh:
            async for trozo in request.stream():
                recibidos += len(trozo)
                if recibidos > limite:
                    raise HTTPException(status_code=413, detail=f"El archivo pasa de {settings.padron_max_mb} MB")
                fh.write(trozo)
        if recibidos == 0:
            raise HTTPException(status_code=422, detail="El archivo está vacío")
        with open(destino, "rb") as fh:
            inicio = fh.read(4096)
        if not inicio.startswith(_TIPOS_ARCHIVO) and (b"|" not in inicio or b"\x00" in inicio[:512]):
            raise HTTPException(
                status_code=422,
                detail=f"«{nombre}» no es el DGII_RNC.zip: suba el ZIP tal como lo descarga de la DGII (o su TXT)",
            )
    except BaseException:
        shutil.rmtree(carpeta, ignore_errors=True)
        raise
    lanzar(request.app.state.db_pool, settings, archivo=destino, origen=f"Archivo subido: {nombre}")
    return {**await _estado_respuesta(request, settings), "en_curso": True}


@router.get("/{rnc}", response_model=Contribuyente)
async def consultar(request: Request, rnc: str, settings: ErpApiSettings = Depends(get_settings)):
    """Datos del padrón de RNC de la DGII para un RNC (9) o cédula (11)."""
    limpio = rnc_valido(rnc)
    pool = request.app.state.db_pool
    try:
        encontrado = await buscar(pool, settings, limpio)
    except PadronNoDisponibleError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if encontrado is None:
        async with pool.acquire() as conn:
            estado_actual = await leer_estado(conn)
        raise HTTPException(status_code=404, detail=texto_no_encontrado(limpio, estado_actual))
    return encontrado
