"""Rutas de Reportes de erp-api (Fase 6 · Operación): agregaciones de solo
lectura de ventas, ITBIS, compras, antigüedad de saldos, cobros y pagos,
inventario y retenciones. Los períodos son `desde`/`hasta` (YYYY-MM-DD,
ambos incluidos). Contrato con el frontend: `frontend/src/lib/operacionApi.ts`.
Ver docs/ERP_API.md, sección "Operación".
"""
import uuid
from datetime import date
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request

from . import reportes_repository
from .operacion_schemas import (
    ReporteAntiguedad,
    ReporteCobrosPagos,
    ReporteCompras,
    ReporteInventarioValorizado,
    ReporteItbis,
    ReporteMasVendidos,
    ReporteRetenciones,
    ReporteRotacion,
    ReporteVentas,
)
from .repository import DatosInvalidosError

router = APIRouter(prefix="/reportes", tags=["reportes"])


def _pool(request: Request):
    return request.app.state.db_pool


async def _llamar(funcion, *args, **kwargs):
    try:
        return await funcion(*args, **kwargs)
    except DatosInvalidosError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/ventas", response_model=ReporteVentas)
async def ventas(
    request: Request,
    desde: date,
    hasta: date,
    agrupar: Literal["mes", "dia", "cliente", "producto", "tipo_ecf"] = "mes",
    cliente_rnc: str | None = None,
    tipo_ecf: int | None = None,
):
    return await _llamar(
        reportes_repository.reporte_ventas, _pool(request), desde=desde, hasta=hasta, agrupar=agrupar,
        cliente_rnc=cliente_rnc, tipo_ecf=tipo_ecf,
    )


@router.get("/itbis", response_model=ReporteItbis)
async def itbis(request: Request, desde: date, hasta: date):
    return await _llamar(reportes_repository.reporte_itbis, _pool(request), desde=desde, hasta=hasta)


@router.get("/compras", response_model=ReporteCompras)
async def compras(
    request: Request,
    desde: date,
    hasta: date,
    agrupar: Literal["proveedor", "categoria", "mes"] = "proveedor",
    proveedor_rnc: str | None = None,
    tipo_bienes_servicios: int | None = Query(None, ge=1, le=11),
):
    return await _llamar(
        reportes_repository.reporte_compras, _pool(request), desde=desde, hasta=hasta, agrupar=agrupar,
        proveedor_rnc=proveedor_rnc, tipo_bienes_servicios=tipo_bienes_servicios,
    )


@router.get("/antiguedad-cxc", response_model=ReporteAntiguedad)
async def antiguedad_cxc(request: Request, cliente_rnc: str | None = None):
    return await _llamar(reportes_repository.reporte_antiguedad, _pool(request), "cxc", rnc=cliente_rnc)


@router.get("/antiguedad-cxp", response_model=ReporteAntiguedad)
async def antiguedad_cxp(request: Request, proveedor_rnc: str | None = None):
    return await _llamar(reportes_repository.reporte_antiguedad, _pool(request), "cxp", rnc=proveedor_rnc)


@router.get("/cobros-pagos", response_model=ReporteCobrosPagos)
async def cobros_pagos(request: Request, desde: date, hasta: date):
    return await _llamar(reportes_repository.reporte_cobros_pagos, _pool(request), desde=desde, hasta=hasta)


@router.get("/inventario-valorizado", response_model=ReporteInventarioValorizado)
async def inventario_valorizado(
    request: Request,
    almacen_id: str | None = None,
    categoria: str | None = None,
    incluir_sin_existencia: bool = False,
):
    try:
        almacen = uuid.UUID(almacen_id) if almacen_id else None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="almacen_id inválido") from exc
    return await _llamar(
        reportes_repository.reporte_inventario_valorizado, _pool(request), almacen_id=almacen, categoria=categoria,
        incluir_sin_existencia=incluir_sin_existencia,
    )


@router.get("/rotacion-inventario", response_model=ReporteRotacion)
async def rotacion_inventario(request: Request, desde: date, hasta: date, categoria: str | None = None):
    return await _llamar(
        reportes_repository.reporte_rotacion, _pool(request), desde=desde, hasta=hasta, categoria=categoria
    )


@router.get("/mas-vendidos", response_model=ReporteMasVendidos)
async def mas_vendidos(
    request: Request,
    desde: date,
    hasta: date,
    limite: int = Query(20, ge=1, le=200),
    categoria: str | None = None,
):
    return await _llamar(
        reportes_repository.reporte_mas_vendidos, _pool(request), desde=desde, hasta=hasta, limite=limite,
        categoria=categoria,
    )


@router.get("/retenciones", response_model=ReporteRetenciones)
async def retenciones(request: Request, desde: date, hasta: date):
    return await _llamar(reportes_repository.reporte_retenciones, _pool(request), desde=desde, hasta=hasta)
