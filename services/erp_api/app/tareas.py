"""Rutas de Mis tareas de erp-api (Fase 6 · Operación): tareas automáticas
calculadas de los datos de cada módulo + tareas manuales, y el resumen para
la campana de la barra superior. Contrato con el frontend:
`frontend/src/lib/operacionApi.ts`. Ver docs/ERP_API.md, sección "Operación".

Las tareas automáticas se identifican por su clave estable (texto con ':'),
las manuales por su uuid; completar / reabrir / posponer sirven para ambas.
"""
from fastapi import APIRouter, Depends, HTTPException, Request

from . import tareas_repository
from .config import ErpApiSettings, get_settings
from .operacion_schemas import CategoriaTarea, EstadoTarea, PosponerPayload, ResumenTareas, Tarea, TareaPayload
from .repository import DatosInvalidosError
from .tareas_repository import TareaNoEncontradaError

router = APIRouter(prefix="/tareas", tags=["tareas"])


def _pool(request: Request):
    return request.app.state.db_pool


def _emisor(settings: ErpApiSettings) -> dict:
    return {"emisor_100_electronico": settings.emisor_100_electronico}


async def _llamar(funcion, *args, **kwargs):
    """422 datos inválidos, 404 tarea inexistente (o automática ya resuelta)."""
    try:
        return await funcion(*args, **kwargs)
    except DatosInvalidosError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except TareaNoEncontradaError as exc:
        raise HTTPException(
            status_code=404, detail="Tarea no encontrada (si era automática, su motivo ya se resolvió)"
        ) from exc


@router.get("", response_model=list[Tarea])
async def listar_tareas(
    request: Request,
    estado: EstadoTarea | None = None,
    categoria: CategoriaTarea | None = None,
    settings: ErpApiSettings = Depends(get_settings),
):
    return await _llamar(
        tareas_repository.listar_tareas, _pool(request), estado=estado, categoria=categoria, **_emisor(settings)
    )


@router.get("/resumen", response_model=ResumenTareas)
async def resumen_tareas(request: Request, settings: ErpApiSettings = Depends(get_settings)):
    return await _llamar(tareas_repository.resumen_tareas, _pool(request), **_emisor(settings))


@router.post("", response_model=Tarea, status_code=201)
async def crear_tarea(request: Request, payload: TareaPayload):
    return await _llamar(tareas_repository.crear_tarea, _pool(request), **payload.model_dump())


@router.put("/{tarea_id}", response_model=Tarea)
async def actualizar_tarea(request: Request, tarea_id: str, payload: TareaPayload):
    return await _llamar(tareas_repository.actualizar_tarea, _pool(request), tarea_id, **payload.model_dump())


@router.delete("/{tarea_id}", status_code=204)
async def eliminar_tarea(request: Request, tarea_id: str):
    await _llamar(tareas_repository.eliminar_tarea, _pool(request), tarea_id)


@router.post("/{tarea_id}/completar", response_model=Tarea)
async def completar_tarea(request: Request, tarea_id: str, settings: ErpApiSettings = Depends(get_settings)):
    return await _llamar(tareas_repository.completar_tarea, _pool(request), tarea_id, **_emisor(settings))


@router.post("/{tarea_id}/reabrir", response_model=Tarea)
async def reabrir_tarea(request: Request, tarea_id: str, settings: ErpApiSettings = Depends(get_settings)):
    return await _llamar(tareas_repository.reabrir_tarea, _pool(request), tarea_id, **_emisor(settings))


@router.post("/{tarea_id}/posponer", response_model=Tarea)
async def posponer_tarea(
    request: Request, tarea_id: str, payload: PosponerPayload, settings: ErpApiSettings = Depends(get_settings)
):
    return await _llamar(
        tareas_repository.posponer_tarea, _pool(request), tarea_id, payload.hasta, **_emisor(settings)
    )
