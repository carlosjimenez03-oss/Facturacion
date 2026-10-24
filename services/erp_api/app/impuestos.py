"""Rutas de impuestos de erp-api (fase 2): formatos 607 y 608, pre-cálculos
IT-1 / IR-17 / IR-2 y el calendario de declaraciones con su marca de
"presentada". Contrato con el frontend: `frontend/src/lib/impuestosApi.ts`.
Ver docs/ERP_API.md, sección "Impuestos".
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response

from libs.ecf_common.empresa import rnc_emisor

from . import formatos, impuestos_repository
from .config import ErpApiSettings, get_settings
from .impuestos_repository import AnulacionNoEncontradaError, ConflictoImpuestosError
from .repository import DatosInvalidosError
from .schemas import (
    AjustesPayload,
    Anulacion608Payload,
    Declaracion,
    Fila608,
    Formato607,
    Formato608,
    FormularioDeclaracion,
    FormularioPreCalculo,
    MarcarPresentadaPayload,
    PreCalculo,
    TipoAnulacion,
)

router = APIRouter(prefix="/impuestos", tags=["impuestos"])


def _pool(request: Request):
    return request.app.state.db_pool


async def _rnc_emisor(request: Request, settings: ErpApiSettings) -> str:
    """RNC de la empresa del request (la 1: EMISOR_RNC, como siempre)."""
    return await rnc_emisor(_pool(request), settings.emisor_rnc)


def _emisor(settings: ErpApiSettings) -> dict:
    """Configuración del emisor que necesitan el 607/608 y las declaraciones."""
    return {"emisor_100_electronico": settings.emisor_100_electronico}


async def _llamar(funcion, *args, **kwargs):
    """Traduce los errores del repositorio a HTTP: 422 datos/período
    inválidos, 404 no encontrado, 409 conflicto."""
    try:
        return await funcion(*args, **kwargs)
    except DatosInvalidosError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except AnulacionNoEncontradaError as exc:
        raise HTTPException(status_code=404, detail="Anulación no encontrada") from exc
    except ConflictoImpuestosError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _txt(nombre: str, contenido: bytes) -> Response:
    return Response(
        content=contenido,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{nombre}"'},
    )


# --- Formato 607 ---------------------------------------------------------

@router.get("/607", response_model=Formato607)
async def formato_607(request: Request, periodo: str, settings: ErpApiSettings = Depends(get_settings)):
    return await _llamar(impuestos_repository.formato_607, _pool(request), periodo, await _rnc_emisor(request, settings), **_emisor(settings))


@router.get("/607/txt")
async def descargar_607(request: Request, periodo: str, settings: ErpApiSettings = Depends(get_settings)):
    nombre, contenido = await _llamar(
        impuestos_repository.archivo_607, _pool(request), periodo, await _rnc_emisor(request, settings), **_emisor(settings)
    )
    return _txt(nombre, contenido)


# --- Formato 608 ---------------------------------------------------------

@router.get("/608/tipos-anulacion", response_model=list[TipoAnulacion])
async def tipos_anulacion():
    return [{"codigo": codigo, "descripcion": descripcion} for codigo, descripcion in formatos.TIPOS_ANULACION.items()]


@router.get("/608/txt")
async def descargar_608(request: Request, periodo: str, settings: ErpApiSettings = Depends(get_settings)):
    nombre, contenido = await _llamar(
        impuestos_repository.archivo_608, _pool(request), periodo, await _rnc_emisor(request, settings), **_emisor(settings)
    )
    return _txt(nombre, contenido)


@router.get("/608", response_model=Formato608)
async def formato_608(request: Request, periodo: str, settings: ErpApiSettings = Depends(get_settings)):
    return await _llamar(impuestos_repository.formato_608, _pool(request), periodo, await _rnc_emisor(request, settings), **_emisor(settings))


@router.post("/608", response_model=Fila608, status_code=201)
async def agregar_anulacion_608(request: Request, payload: Anulacion608Payload):
    return await _llamar(impuestos_repository.agregar_anulacion_608, _pool(request), **payload.model_dump())


@router.delete("/608/{anulacion_id}", status_code=204)
async def eliminar_anulacion_608(request: Request, anulacion_id: str):
    try:
        uid = uuid.UUID(anulacion_id)
    except ValueError as exc:
        # Las filas que vienen de anulaciones e-NCF no tienen id: solo se
        # pueden borrar las manuales.
        raise HTTPException(status_code=422, detail="id inválido") from exc
    await _llamar(impuestos_repository.eliminar_anulacion_608, _pool(request), uid)


# --- Pre-cálculos ---------------------------------------------------------

@router.get("/precalculo/{formulario}", response_model=PreCalculo)
async def precalculo(request: Request, formulario: FormularioPreCalculo, periodo: str):
    return await _llamar(impuestos_repository.precalculo, _pool(request), formulario, periodo)


@router.put("/precalculo/{formulario}/ajustes", response_model=PreCalculo)
async def guardar_ajustes(request: Request, formulario: FormularioPreCalculo, periodo: str, payload: AjustesPayload):
    return await _llamar(
        impuestos_repository.guardar_ajustes, _pool(request), formulario, periodo, payload.valores
    )


# --- Declaraciones ---------------------------------------------------------

@router.get("/declaraciones", response_model=list[Declaracion])
async def listar_declaraciones(request: Request, anio: str, settings: ErpApiSettings = Depends(get_settings)):
    return await _llamar(impuestos_repository.listar_declaraciones, _pool(request), anio, **_emisor(settings))


@router.post("/declaraciones/{formulario}/{periodo}/presentada", response_model=Declaracion)
async def marcar_presentada(
    request: Request,
    formulario: FormularioDeclaracion,
    periodo: str,
    payload: MarcarPresentadaPayload,
    settings: ErpApiSettings = Depends(get_settings),
):
    return await _llamar(
        impuestos_repository.marcar_presentada,
        _pool(request),
        formulario,
        periodo,
        **payload.model_dump(),
        **_emisor(settings),
    )


@router.delete("/declaraciones/{formulario}/{periodo}/presentada", response_model=Declaracion)
async def desmarcar_presentada(
    request: Request, formulario: FormularioDeclaracion, periodo: str, settings: ErpApiSettings = Depends(get_settings)
):
    return await _llamar(
        impuestos_repository.desmarcar_presentada, _pool(request), formulario, periodo, **_emisor(settings)
    )
