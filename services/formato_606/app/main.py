"""Formato 606 -- Compras de Bienes y Servicios (Norma General 07-2018/05-2019).

Lee y permite clasificar los renglones que `facturas_api` (comprobantes
autoemitidos tipo 41/43/47) y `receptor_recepcion` (e-CF recibidos de
proveedores) auto-registran en `compras606.registros`, valida un periodo
completo y genera el archivo de texto pipe-delimited que se remite por la
Oficina Virtual. Ver docs/dgii/FORMATO_606.md.
"""
import asyncio
import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

from libs.ecf_common.auth.sesion_supabase import instalar_verificacion_sesion
from libs.ecf_common.compras606 import validar_periodo
from libs.ecf_common.db import cerrar_pool, crear_pool
from libs.ecf_common.empresa import instalar_empresa, rnc_emisor

from . import repository
from .config import Formato606Settings, get_settings
from .generador import construir_archivo_606
from .repository import RegistroNoEditableError, RegistroNoEncontradoError
from .schemas import (
    ErrorValidacion,
    Registro606,
    RegistroActualizar,
    RegistroCrearManual,
    ValidacionResponse,
)
from .validador import validar_registro

settings = get_settings()
logging.basicConfig(level=settings.log_level)
logger = logging.getLogger(__name__)


_POOL_REINTENTOS = 10
_POOL_ESPERA_SEGUNDOS = 3


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Mismo patrón de reintento con espera que facturas_api/inventario_api:
    # `depends_on: condition: service_healthy` solo se respeta en el `up`
    # inicial de Docker Compose.
    pool = None
    for intento in range(1, _POOL_REINTENTOS + 1):
        pool = await crear_pool(settings.database_url)
        if pool is not None:
            break
        logger.warning(
            "No se pudo conectar a la base de datos (intento %s/%s), reintentando en %ss...",
            intento,
            _POOL_REINTENTOS,
            _POOL_ESPERA_SEGUNDOS,
        )
        await asyncio.sleep(_POOL_ESPERA_SEGUNDOS)
    if pool is None:
        raise RuntimeError(
            "formato_606 requiere DATABASE_URL configurado y una base de datos "
            "alcanzable -- los renglones del 606 viven únicamente en Postgres."
        )
    app.state.db_pool = pool
    yield
    await cerrar_pool(app.state.db_pool)


app = FastAPI(title=settings.app_name, debug=settings.debug, lifespan=lifespan)
app.state.db_pool = None

# Empresa de cada request (multiempresa, docs/MULTIEMPRESA.md); por dentro
# de la verificación de sesión (se instala antes) para ver al usuario.
instalar_empresa(app, settings)

# REQUIRE_AUTH=true: exige la sesión de Supabase Auth (ver docs/AUTH.md).
instalar_verificacion_sesion(app, settings)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_methods=["*"],
    allow_headers=["*"],
    # El frontend lee el nombre de archivo real de /periodos/{periodo}/generar
    # desde este header (fetch + blob, para poder mostrar errores de
    # validación inline en vez de navegar a una respuesta cruda) -- sin
    # exponerlo explícitamente, un origen cruzado (dev server en otro puerto)
    # no puede leerlo vía `response.headers.get()` y el nombre cae al
    # fallback genérico sin el RNC.
    expose_headers=["Content-Disposition"],
)


def _pool():
    return app.state.db_pool


def _parse_id(registro_id: str) -> uuid.UUID:
    try:
        return uuid.UUID(registro_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="id de registro inválido") from exc


@app.get("/health")
def health(settings: Formato606Settings = Depends(get_settings)) -> dict:
    return {"status": "ok", "app_env": settings.app_env}


@app.get("/periodos")
async def listar_periodos():
    return await repository.listar_periodos(_pool())


@app.get("/registros", response_model=list[Registro606])
async def listar_registros(periodo: str = Query(..., description="AAAAMM")):
    return await repository.listar_por_periodo(_pool(), periodo)


@app.get("/registros/{registro_id}", response_model=Registro606)
async def obtener_registro(registro_id: str):
    try:
        return await repository.obtener(_pool(), _parse_id(registro_id))
    except RegistroNoEncontradoError as exc:
        raise HTTPException(status_code=404, detail="Registro no encontrado") from exc


@app.post("/registros", response_model=Registro606, status_code=201)
async def crear_registro(payload: RegistroCrearManual):
    return await repository.crear_manual(_pool(), payload.model_dump())


@app.put("/registros/{registro_id}", response_model=Registro606)
async def actualizar_registro(registro_id: str, payload: RegistroActualizar):
    try:
        return await repository.actualizar_clasificacion(
            _pool(), _parse_id(registro_id), payload.model_dump(exclude_unset=True)
        )
    except RegistroNoEncontradoError as exc:
        raise HTTPException(status_code=404, detail="Registro no encontrado") from exc


@app.delete("/registros/{registro_id}", status_code=204)
async def eliminar_registro(registro_id: str):
    try:
        await repository.eliminar_manual(_pool(), _parse_id(registro_id))
    except RegistroNoEncontradoError as exc:
        raise HTTPException(status_code=404, detail="Registro no encontrado") from exc
    except RegistroNoEditableError as exc:
        raise HTTPException(
            status_code=409,
            detail="Solo se pueden eliminar registros agregados manualmente -- los que vienen "
            "de un e-CF se corrigen desde su origen",
        ) from exc


async def _validar_periodo_interno(periodo: str) -> tuple[list[dict], list[ErrorValidacion]]:
    if not validar_periodo(periodo):
        raise HTTPException(status_code=422, detail="Periodo inválido (formato AAAAMM, >= 201805)")
    registros = await repository.listar_por_periodo(_pool(), periodo)
    errores: list[ErrorValidacion] = []
    for registro in registros:
        for mensaje in validar_registro(registro):
            errores.append(
                ErrorValidacion(registro_id=registro["id"], ncf=registro["ncf"], mensaje=mensaje)
            )
    return registros, errores


@app.post("/periodos/{periodo}/validar", response_model=ValidacionResponse)
async def validar_periodo_endpoint(periodo: str):
    registros, errores = await _validar_periodo_interno(periodo)
    return ValidacionResponse(valido=not errores, total_registros=len(registros), errores=errores)


@app.get("/periodos/{periodo}/generar")
async def generar_archivo(
    periodo: str, settings: Formato606Settings = Depends(get_settings)
) -> Response:
    """Genera el TXT pipe-delimited (`DGII_F_606_<RNC>_<periodo>.TXT`), solo
    si el periodo no tiene errores de validación pendientes -- igual que el
    botón "Generar Archivo" de la plantilla oficial, que exige "Validar"
    (sin errores) antes de habilitarse."""
    registros, errores = await _validar_periodo_interno(periodo)
    if errores:
        detalle = "; ".join(f"{e.ncf}: {e.mensaje}" for e in errores[:10])
        raise HTTPException(
            status_code=409,
            detail=f"El periodo tiene {len(errores)} error(es) de validación pendientes: {detalle}",
        )
    if not registros:
        raise HTTPException(status_code=404, detail=f"No hay registros para el periodo {periodo}")

    # RNC de la empresa del request (la 1: EMISOR_RNC, como siempre).
    rnc = await rnc_emisor(app.state.db_pool, settings.emisor_rnc)
    contenido = construir_archivo_606(rnc=rnc, periodo=periodo, registros=registros)
    nombre_archivo = f"DGII_F_606_{rnc}_{periodo}.TXT"
    return Response(
        content=contenido,
        media_type="text/plain",
        headers={"Content-Disposition": f'attachment; filename="{nombre_archivo}"'},
    )
