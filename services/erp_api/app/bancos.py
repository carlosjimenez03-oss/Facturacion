"""Rutas de Bancos de erp-api (fase 5): cuentas bancarias, movimientos,
transferencias entre cuentas, importación de extractos (CSV) y
conciliación. Contrato con el frontend: `frontend/src/lib/bancosApi.ts`.
Ver docs/ERP_API.md, sección "Bancos".
"""
import json
import uuid
from datetime import date
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from pydantic import ValidationError

from . import bancos_repository
from .bancos_repository import BancoNoEncontradoError, ConflictoBancosError
from .repository import DatosInvalidosError
from .schemas import (
    CerrarConciliacionPayload,
    Conciliacion,
    ConciliacionAutoPayload,
    ConciliacionCerrada,
    ConciliarPayload,
    CuentaBancaria,
    CuentaBancariaActualizar,
    CuentaBancariaPayload,
    Extracto,
    LineaExtracto,
    MapeoExtracto,
    MovimientoBancario,
    MovimientoDesdeLineaPayload,
    MovimientoPayload,
    MovimientosCuenta,
    PrevisualizacionExtracto,
    ResultadoConciliacionAuto,
    ResultadoImportacion,
    SugerenciaConciliacion,
    Transferencia,
    TransferenciaPayload,
)

router = APIRouter(prefix="/bancos", tags=["bancos"])

# Un estado de cuenta en CSV pesa unos KB; 5 MB sobra.
_TAMANO_MAXIMO_EXTRACTO = 5 * 1024 * 1024


def _pool(request: Request):
    return request.app.state.db_pool


def _uuid(valor: str, nombre: str = "id") -> uuid.UUID:
    try:
        return uuid.UUID(valor)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"{nombre} inválido") from exc


async def _llamar(funcion, *args, no_encontrado: str = "No encontrado", **kwargs):
    """422 datos inválidos, 404 no encontrado, 409 conflicto (conciliación
    cerrada, estado)."""
    try:
        return await funcion(*args, **kwargs)
    except DatosInvalidosError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except BancoNoEncontradoError as exc:
        raise HTTPException(status_code=404, detail=no_encontrado) from exc
    except ConflictoBancosError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


_CUENTA_NO_ENCONTRADA = "Cuenta bancaria no encontrada"
_LINEA_NO_ENCONTRADA = "Línea del extracto no encontrada"


# --- Cuentas ---------------------------------------------------------

@router.get("/cuentas", response_model=list[CuentaBancaria])
async def listar_cuentas(request: Request, activa: bool | None = None):
    return await _llamar(bancos_repository.listar_cuentas, _pool(request), activa=activa)


@router.post("/cuentas", response_model=CuentaBancaria, status_code=201)
async def crear_cuenta(request: Request, payload: CuentaBancariaPayload):
    return await _llamar(bancos_repository.crear_cuenta, _pool(request), **payload.model_dump())


@router.get("/cuentas/{cuenta_id}", response_model=CuentaBancaria)
async def obtener_cuenta(request: Request, cuenta_id: str):
    return await _llamar(
        bancos_repository.obtener_cuenta, _pool(request), _uuid(cuenta_id), no_encontrado=_CUENTA_NO_ENCONTRADA
    )


@router.put("/cuentas/{cuenta_id}", response_model=CuentaBancaria)
async def actualizar_cuenta(request: Request, cuenta_id: str, payload: CuentaBancariaActualizar):
    return await _llamar(
        bancos_repository.actualizar_cuenta, _pool(request), _uuid(cuenta_id), payload.model_dump(exclude_unset=True),
        no_encontrado=_CUENTA_NO_ENCONTRADA,
    )


@router.delete("/cuentas/{cuenta_id}", status_code=204)
async def eliminar_cuenta(request: Request, cuenta_id: str):
    await _llamar(
        bancos_repository.eliminar_cuenta, _pool(request), _uuid(cuenta_id), no_encontrado=_CUENTA_NO_ENCONTRADA
    )


# --- Movimientos ---------------------------------------------------------

@router.get("/cuentas/{cuenta_id}/movimientos", response_model=MovimientosCuenta)
async def listar_movimientos(
    request: Request,
    cuenta_id: str,
    desde: date | None = None,
    hasta: date | None = None,
    conciliado: bool | None = None,
    q: str | None = None,
    limit: int = Query(500, ge=1, le=2000),
    offset: int = Query(0, ge=0),
):
    return await _llamar(
        bancos_repository.listar_movimientos, _pool(request), _uuid(cuenta_id), desde=desde, hasta=hasta,
        conciliado=conciliado, q=q, limite=limit, offset=offset, no_encontrado=_CUENTA_NO_ENCONTRADA,
    )


@router.post("/movimientos", response_model=MovimientoBancario, status_code=201)
async def registrar_movimiento(request: Request, payload: MovimientoPayload):
    return await _llamar(
        bancos_repository.registrar_movimiento, _pool(request), **payload.model_dump(),
        no_encontrado=_CUENTA_NO_ENCONTRADA,
    )


@router.post("/movimientos/{movimiento_id}/anular", response_model=MovimientoBancario)
async def anular_movimiento(request: Request, movimiento_id: str):
    return await _llamar(
        bancos_repository.anular_movimiento, _pool(request), _uuid(movimiento_id),
        no_encontrado="Movimiento no encontrado",
    )


# --- Transferencias ---------------------------------------------------------

@router.get("/transferencias", response_model=list[Transferencia])
async def listar_transferencias(
    request: Request,
    cuenta_id: str | None = None,
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    cid = _uuid(cuenta_id, "cuenta_id") if cuenta_id else None
    return await _llamar(
        bancos_repository.listar_transferencias, _pool(request), cuenta_id=cid, limite=limit, offset=offset
    )


@router.post("/transferencias", response_model=Transferencia, status_code=201)
async def registrar_transferencia(request: Request, payload: TransferenciaPayload):
    return await _llamar(
        bancos_repository.registrar_transferencia, _pool(request), **payload.model_dump(),
        no_encontrado=_CUENTA_NO_ENCONTRADA,
    )


@router.post("/transferencias/{transferencia_id}/anular", response_model=Transferencia)
async def anular_transferencia(request: Request, transferencia_id: str):
    return await _llamar(
        bancos_repository.anular_transferencia, _pool(request), _uuid(transferencia_id),
        no_encontrado="Transferencia no encontrada",
    )


# --- Extractos ---------------------------------------------------------

def _mapeo(texto: str | None) -> dict | None:
    """`mapeo` del formulario multipart: JSON de MapeoExtracto."""
    if not texto:
        return None
    try:
        return MapeoExtracto.model_validate(json.loads(texto)).model_dump(exclude_none=True)
    except (json.JSONDecodeError, ValidationError) as exc:
        raise HTTPException(status_code=422, detail=f"«mapeo» inválido: {exc}") from exc


async def _leer_archivo(archivo: UploadFile) -> bytes:
    contenido = await archivo.read(_TAMANO_MAXIMO_EXTRACTO + 1)
    if len(contenido) > _TAMANO_MAXIMO_EXTRACTO:
        raise HTTPException(status_code=413, detail="El archivo supera 5 MB: exporte un período más corto")
    return contenido


@router.post("/extractos/previsualizar", response_model=PrevisualizacionExtracto)
async def previsualizar_extracto(archivo: UploadFile = File(...), mapeo: str | None = Form(None)):
    """Lee el CSV sin guardarlo: columnas, mapeo detectado, primeras 20 líneas y errores."""
    contenido = await _leer_archivo(archivo)
    try:
        return bancos_repository.previsualizar_extracto(contenido, _mapeo(mapeo))
    except DatosInvalidosError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/cuentas/{cuenta_id}/extractos", response_model=ResultadoImportacion, status_code=201)
async def importar_extracto(
    request: Request,
    cuenta_id: str,
    archivo: UploadFile = File(...),
    mapeo: str | None = Form(None),
    saldo_final: str | None = Form(None),
):
    saldo = None
    if saldo_final not in (None, ""):
        try:
            saldo = Decimal(saldo_final.replace(",", ""))
        except InvalidOperation as exc:
            raise HTTPException(status_code=422, detail="«saldo_final» no es un monto") from exc
    contenido = await _leer_archivo(archivo)
    return await _llamar(
        bancos_repository.importar_extracto, _pool(request), _uuid(cuenta_id), contenido,
        nombre_archivo=archivo.filename, mapeo=_mapeo(mapeo), saldo_final=saldo, no_encontrado=_CUENTA_NO_ENCONTRADA,
    )


@router.get("/cuentas/{cuenta_id}/extractos", response_model=list[Extracto])
async def listar_extractos(request: Request, cuenta_id: str):
    return await _llamar(
        bancos_repository.listar_extractos, _pool(request), _uuid(cuenta_id), no_encontrado=_CUENTA_NO_ENCONTRADA
    )


@router.delete("/extractos/{extracto_id}", status_code=204)
async def eliminar_extracto(request: Request, extracto_id: str):
    await _llamar(
        bancos_repository.eliminar_extracto, _pool(request), _uuid(extracto_id), no_encontrado="Extracto no encontrado"
    )


# --- Conciliación ---------------------------------------------------------

@router.get("/cuentas/{cuenta_id}/conciliacion", response_model=Conciliacion)
async def conciliacion(request: Request, cuenta_id: str, hasta: date | None = None):
    return await _llamar(
        bancos_repository.conciliacion, _pool(request), _uuid(cuenta_id), hasta=hasta,
        no_encontrado=_CUENTA_NO_ENCONTRADA,
    )


@router.get("/cuentas/{cuenta_id}/conciliacion/sugerencias", response_model=list[SugerenciaConciliacion])
async def sugerencias(request: Request, cuenta_id: str, dias: int = Query(3, ge=0, le=31), hasta: date | None = None):
    return await _llamar(
        bancos_repository.sugerencias, _pool(request), _uuid(cuenta_id), dias=dias, hasta=hasta,
        no_encontrado=_CUENTA_NO_ENCONTRADA,
    )


@router.post("/cuentas/{cuenta_id}/conciliacion/auto", response_model=ResultadoConciliacionAuto)
async def conciliar_automatico(request: Request, cuenta_id: str, payload: ConciliacionAutoPayload):
    return await _llamar(
        bancos_repository.conciliar_automatico, _pool(request), _uuid(cuenta_id), dias=payload.dias,
        hasta=payload.hasta, lineas=payload.lineas, no_encontrado=_CUENTA_NO_ENCONTRADA,
    )


@router.post("/extracto-lineas/{linea_id}/conciliar", response_model=LineaExtracto)
async def conciliar_linea(request: Request, linea_id: str, payload: ConciliarPayload):
    return await _llamar(
        bancos_repository.conciliar_linea, _pool(request), _uuid(linea_id), origen=payload.origen,
        movimiento_id=payload.movimiento_id, no_encontrado="Línea o movimiento no encontrado",
    )


@router.post("/extracto-lineas/{linea_id}/desconciliar", response_model=LineaExtracto)
async def desconciliar_linea(request: Request, linea_id: str):
    return await _llamar(
        bancos_repository.desconciliar_linea, _pool(request), _uuid(linea_id), no_encontrado=_LINEA_NO_ENCONTRADA
    )


@router.post("/extracto-lineas/{linea_id}/movimiento", response_model=LineaExtracto, status_code=201)
async def crear_movimiento_desde_linea(request: Request, linea_id: str, payload: MovimientoDesdeLineaPayload):
    return await _llamar(
        bancos_repository.crear_movimiento_desde_linea, _pool(request), _uuid(linea_id), **payload.model_dump(),
        no_encontrado=_LINEA_NO_ENCONTRADA,
    )


@router.get("/cuentas/{cuenta_id}/conciliaciones", response_model=list[ConciliacionCerrada])
async def listar_conciliaciones(request: Request, cuenta_id: str):
    return await _llamar(
        bancos_repository.listar_conciliaciones, _pool(request), _uuid(cuenta_id), no_encontrado=_CUENTA_NO_ENCONTRADA
    )


@router.post("/cuentas/{cuenta_id}/conciliaciones", response_model=ConciliacionCerrada, status_code=201)
async def cerrar_conciliacion(request: Request, cuenta_id: str, payload: CerrarConciliacionPayload):
    return await _llamar(
        bancos_repository.cerrar_conciliacion, _pool(request), _uuid(cuenta_id), hasta=payload.hasta,
        saldo_banco=payload.saldo_banco, notas=payload.notas, no_encontrado=_CUENTA_NO_ENCONTRADA,
    )


@router.delete("/conciliaciones/{conciliacion_id}", status_code=204)
async def reabrir_conciliacion(request: Request, conciliacion_id: str):
    await _llamar(
        bancos_repository.reabrir_conciliacion, _pool(request), _uuid(conciliacion_id),
        no_encontrado="Conciliación no encontrada",
    )
