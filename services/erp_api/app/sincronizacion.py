"""Rutas del Centro de sincronización de erp-api (Fase 6 · Operación):
resumen por canal, lo pendiente de sincronizar con su acción de reintento y
el historial de la auditoría de envíos a la DGII. Contrato con el frontend:
`frontend/src/lib/operacionApi.ts`. Ver docs/ERP_API.md, sección "Operación".

Los reintentos de la DGII y de la aprobación comercial los hace el frontend
con los endpoints que ya existen (facturas-api y /compras/bandeja); aquí solo
están el reintento de la entrega del ACECF al proveedor, el modo
contingencia calculado (en el resumen), el historial de envíos por día y el
registro de facturas emitidas en papel.
"""
import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from libs.ecf_common.empresa import rnc_emisor

from . import sincronizacion_repository
from .compras import _entregar
from .config import ErpApiSettings, get_settings
from .operacion_schemas import (
    CanalSincronizacion,
    EnviosDia,
    EventoSincronizacion,
    FacturaPapel,
    FacturaPapelPayload,
    ItemSincronizacion,
    ResultadoEntrega,
    ResultadoEvento,
    ResumenSincronizacion,
)
from .repository import DatosInvalidosError
from .sincronizacion_repository import (
    ConflictoSincronizacionError,
    FacturaPapelNoEncontradaError,
    SincronizacionNoEncontradaError,
)

router = APIRouter(prefix="/sincronizacion", tags=["sincronizacion"])


def _pool(request: Request):
    return request.app.state.db_pool


async def _llamar(funcion, *args, **kwargs):
    try:
        return await funcion(*args, **kwargs)
    except DatosInvalidosError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SincronizacionNoEncontradaError as exc:
        raise HTTPException(status_code=404, detail="e-CF recibido no encontrado") from exc
    except FacturaPapelNoEncontradaError as exc:
        raise HTTPException(status_code=404, detail="Factura en papel no encontrada") from exc
    except ConflictoSincronizacionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/resumen", response_model=ResumenSincronizacion)
async def resumen(request: Request):
    return await _llamar(sincronizacion_repository.resumen, _pool(request))


@router.get("/pendientes", response_model=list[ItemSincronizacion])
async def pendientes(request: Request, canal: CanalSincronizacion | None = None):
    return await _llamar(sincronizacion_repository.listar_pendientes, _pool(request), canal=canal)


@router.get("/historial", response_model=list[EventoSincronizacion])
async def historial(
    request: Request,
    modulo: str | None = None,
    resultado: ResultadoEvento | None = None,
    q: str | None = None,
    desde: date | None = None,
    hasta: date | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    return await _llamar(
        sincronizacion_repository.historial,
        _pool(request),
        modulo=modulo,
        resultado=resultado,
        q=q,
        desde=desde,
        hasta=hasta,
        limite=limit,
        offset=offset,
    )


@router.post("/entregas/{ecf_id}/reintentar", response_model=ResultadoEntrega)
async def reintentar_entrega(request: Request, ecf_id: str, settings: ErpApiSettings = Depends(get_settings)):
    try:
        uid = uuid.UUID(ecf_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="id inválido") from exc
    return await _llamar(
        sincronizacion_repository.reintentar_entrega,
        _pool(request),
        uid,
        entregar=_entregar(settings, await rnc_emisor(_pool(request), settings.emisor_rnc)),
    )


@router.get("/envios-por-dia", response_model=list[EnviosDia])
async def envios_por_dia(request: Request, dias: int = Query(7, ge=1, le=90)):
    return await _llamar(sincronizacion_repository.envios_por_dia, _pool(request), dias=dias)


# --- Facturas en papel (contingencia) ---------------------------------------------------------

def _uuid(valor: str) -> uuid.UUID:
    try:
        return uuid.UUID(valor)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="id inválido") from exc


@router.get("/facturas-papel", response_model=list[FacturaPapel])
async def listar_facturas_papel(
    request: Request, desde: date | None = None, hasta: date | None = None, q: str | None = None
):
    return await _llamar(sincronizacion_repository.listar_facturas_papel, _pool(request), desde=desde, hasta=hasta, q=q)


@router.post("/facturas-papel", response_model=FacturaPapel, status_code=201)
async def crear_factura_papel(request: Request, payload: FacturaPapelPayload):
    return await _llamar(sincronizacion_repository.crear_factura_papel, _pool(request), **payload.model_dump())


@router.put("/facturas-papel/{factura_id}", response_model=FacturaPapel)
async def actualizar_factura_papel(request: Request, factura_id: str, payload: FacturaPapelPayload):
    return await _llamar(
        sincronizacion_repository.actualizar_factura_papel, _pool(request), _uuid(factura_id), **payload.model_dump()
    )


@router.delete("/facturas-papel/{factura_id}", status_code=204)
async def eliminar_factura_papel(request: Request, factura_id: str):
    await _llamar(sincronizacion_repository.eliminar_factura_papel, _pool(request), _uuid(factura_id))
