"""Rutas de Contabilidad de erp-api (fase 5): catálogo de cuentas, cuentas
por defecto de los asientos automáticos, asientos (manuales y generados
desde los documentos), reportes y activos fijos con su depreciación.
Contrato con el frontend: `frontend/src/lib/contabilidadApi.ts`. Ver
docs/ERP_API.md, sección "Contabilidad".
"""
import uuid
from datetime import date
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request

from . import contabilidad_repository as repo
from .contabilidad_repository import ConflictoContabilidadError, ContabilidadNoEncontradaError
from .repository import DatosInvalidosError, hoy
from .schemas import (
    ActivoFijo,
    ActivoFijoActualizar,
    ActivoFijoPayload,
    AnularAsientoPayload,
    Asiento,
    AsientoPayload,
    AsientoResumen,
    BajaActivoPayload,
    BalanceGeneral,
    Balanza,
    CuentaContable,
    CuentaContableActualizar,
    CuentaContablePayload,
    CuentaPorDefecto,
    CuentasPorDefectoPayload,
    DepreciacionFiscal,
    DepreciacionPayload,
    DepreciacionPeriodo,
    EstadoAsiento,
    EstadoResultados,
    GenerarAsientosPayload,
    LibroDiario,
    Mayor,
    OrigenAsiento,
    ResultadoDepreciacion,
    ResultadoGeneracion,
    ResumenContabilidad,
    TablaDepreciacion,
)

router = APIRouter(prefix="/contabilidad", tags=["contabilidad"])


def _pool(request: Request):
    return request.app.state.db_pool


def _uuid(valor: str, nombre: str = "id") -> uuid.UUID:
    try:
        return uuid.UUID(valor)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"{nombre} inválido") from exc


async def _llamar(funcion, *args, no_encontrado: str = "No encontrado", **kwargs):
    try:
        return await funcion(*args, **kwargs)
    except DatosInvalidosError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ContabilidadNoEncontradaError as exc:
        raise HTTPException(status_code=404, detail=no_encontrado) from exc
    except ConflictoContabilidadError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _rango(desde: date | None, hasta: date | None) -> tuple[date, date]:
    """Sin fechas: el mes en curso (hora RD)."""
    hoy_rd = hoy()
    return desde or hoy_rd.replace(day=1), hasta or hoy_rd


_CUENTA_NO_ENCONTRADA = "Cuenta contable no encontrada"
_ASIENTO_NO_ENCONTRADO = "Asiento no encontrado"
_ACTIVO_NO_ENCONTRADO = "Activo fijo no encontrado"


# --- Catálogo ---------------------------------------------------------

@router.get("/cuentas", response_model=list[CuentaContable])
async def listar_cuentas(
    request: Request,
    hasta: date | None = None,
    q: str | None = None,
    solo_movimiento: bool = False,
    activa: bool | None = None,
):
    """Árbol aplanado (por código) con el saldo de cada cuenta al `hasta`."""
    return await _llamar(
        repo.listar_cuentas, _pool(request), hasta=hasta, q=q, solo_movimiento=solo_movimiento, activa=activa
    )


@router.post("/cuentas", response_model=CuentaContable, status_code=201)
async def crear_cuenta(request: Request, payload: CuentaContablePayload):
    return await _llamar(repo.crear_cuenta, _pool(request), **payload.model_dump())


@router.get("/cuentas/{cuenta_id}", response_model=CuentaContable)
async def obtener_cuenta(request: Request, cuenta_id: str):
    return await _llamar(repo.obtener_cuenta, _pool(request), _uuid(cuenta_id), no_encontrado=_CUENTA_NO_ENCONTRADA)


@router.put("/cuentas/{cuenta_id}", response_model=CuentaContable)
async def actualizar_cuenta(request: Request, cuenta_id: str, payload: CuentaContableActualizar):
    return await _llamar(
        repo.actualizar_cuenta, _pool(request), _uuid(cuenta_id), payload.model_dump(exclude_unset=True),
        no_encontrado=_CUENTA_NO_ENCONTRADA,
    )


@router.delete("/cuentas/{cuenta_id}", status_code=204)
async def eliminar_cuenta(request: Request, cuenta_id: str):
    await _llamar(repo.eliminar_cuenta, _pool(request), _uuid(cuenta_id), no_encontrado=_CUENTA_NO_ENCONTRADA)


@router.get("/cuentas-por-defecto", response_model=list[CuentaPorDefecto])
async def listar_cuentas_por_defecto(request: Request):
    return await _llamar(repo.listar_cuentas_por_defecto, _pool(request))


@router.put("/cuentas-por-defecto", response_model=list[CuentaPorDefecto])
async def actualizar_cuentas_por_defecto(request: Request, payload: CuentasPorDefectoPayload):
    return await _llamar(repo.actualizar_cuentas_por_defecto, _pool(request), payload.valores)


# --- Asientos ---------------------------------------------------------

@router.get("/asientos", response_model=list[AsientoResumen])
async def listar_asientos(
    request: Request,
    desde: date | None = None,
    hasta: date | None = None,
    origen: OrigenAsiento | Literal["automatico"] | None = None,
    estado: EstadoAsiento | None = None,
    q: str | None = None,
    cuenta_id: str | None = None,
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    return await _llamar(
        repo.listar_asientos, _pool(request), desde=desde, hasta=hasta, origen=origen, estado=estado, q=q,
        cuenta_id=_uuid(cuenta_id, "cuenta_id") if cuenta_id else None, limite=limit, offset=offset,
    )


@router.post("/asientos", response_model=Asiento, status_code=201)
async def crear_asiento(request: Request, payload: AsientoPayload):
    campos = payload.model_dump()
    return await _llamar(repo.crear_asiento, _pool(request), **campos)


@router.post("/asientos/generar", response_model=ResultadoGeneracion)
async def generar_asientos(request: Request, payload: GenerarAsientosPayload):
    """Crea/actualiza/anula los asientos automáticos de los documentos del período."""
    return await _llamar(repo.generar_asientos, _pool(request), payload.desde, payload.hasta)


@router.get("/asientos/pendientes", response_model=ResultadoGeneracion)
async def pendientes_de_contabilizar(request: Request, desde: date | None = None, hasta: date | None = None):
    """Lo que haría "Generar asientos del período", sin hacerlo, y los
    documentos que no se pueden contabilizar (con el motivo)."""
    d, h = _rango(desde, hasta)
    return await _llamar(repo.pendientes_de_contabilizar, _pool(request), d, h)


@router.get("/asientos/{asiento_id}", response_model=Asiento)
async def obtener_asiento(request: Request, asiento_id: str):
    return await _llamar(repo.obtener_asiento, _pool(request), _uuid(asiento_id), no_encontrado=_ASIENTO_NO_ENCONTRADO)


@router.put("/asientos/{asiento_id}", response_model=Asiento)
async def actualizar_asiento(request: Request, asiento_id: str, payload: AsientoPayload):
    return await _llamar(
        repo.actualizar_asiento, _pool(request), _uuid(asiento_id), **payload.model_dump(),
        no_encontrado=_ASIENTO_NO_ENCONTRADO,
    )


@router.post("/asientos/{asiento_id}/contabilizar", response_model=Asiento)
async def contabilizar_asiento(request: Request, asiento_id: str):
    return await _llamar(
        repo.contabilizar_asiento, _pool(request), _uuid(asiento_id), no_encontrado=_ASIENTO_NO_ENCONTRADO
    )


@router.post("/asientos/{asiento_id}/anular", response_model=Asiento)
async def anular_asiento(request: Request, asiento_id: str, payload: AnularAsientoPayload | None = None):
    return await _llamar(
        repo.anular_asiento, _pool(request), _uuid(asiento_id), payload.motivo if payload else None,
        no_encontrado=_ASIENTO_NO_ENCONTRADO,
    )


@router.delete("/asientos/{asiento_id}", status_code=204)
async def eliminar_asiento(request: Request, asiento_id: str):
    await _llamar(repo.eliminar_asiento, _pool(request), _uuid(asiento_id), no_encontrado=_ASIENTO_NO_ENCONTRADO)


# --- Reportes ---------------------------------------------------------

@router.get("/resumen", response_model=ResumenContabilidad)
async def resumen(request: Request, desde: date | None = None, hasta: date | None = None):
    return await _llamar(repo.resumen, _pool(request), *_rango(desde, hasta))


@router.get("/libro-diario", response_model=LibroDiario)
async def libro_diario(
    request: Request,
    desde: date | None = None,
    hasta: date | None = None,
    limit: int = Query(500, ge=1, le=5000),
    offset: int = Query(0, ge=0),
):
    return await _llamar(repo.libro_diario, _pool(request), *_rango(desde, hasta), limite=limit, offset=offset)


@router.get("/mayor", response_model=Mayor)
async def mayor(request: Request, cuenta_id: str, desde: date | None = None, hasta: date | None = None):
    return await _llamar(
        repo.mayor, _pool(request), _uuid(cuenta_id, "cuenta_id"), *_rango(desde, hasta),
        no_encontrado=_CUENTA_NO_ENCONTRADA,
    )


@router.get("/balanza", response_model=Balanza)
async def balanza(
    request: Request, desde: date | None = None, hasta: date | None = None, nivel: int | None = Query(None, ge=1)
):
    return await _llamar(repo.balanza, _pool(request), *_rango(desde, hasta), nivel=nivel)


@router.get("/estado-resultados", response_model=EstadoResultados)
async def estado_resultados(request: Request, desde: date | None = None, hasta: date | None = None):
    return await _llamar(repo.estado_resultados, _pool(request), *_rango(desde, hasta))


@router.get("/balance-general", response_model=BalanceGeneral)
async def balance_general(request: Request, fecha: date | None = None):
    return await _llamar(repo.balance_general, _pool(request), fecha or hoy())


# --- Activos fijos ---------------------------------------------------------

@router.get("/activos-fijos", response_model=list[ActivoFijo])
async def listar_activos(request: Request, estado: Literal["activo", "depreciado", "baja"] | None = None):
    return await _llamar(repo.listar_activos, _pool(request), estado=estado)


@router.post("/activos-fijos", response_model=ActivoFijo, status_code=201)
async def crear_activo(request: Request, payload: ActivoFijoPayload):
    return await _llamar(repo.crear_activo, _pool(request), **payload.model_dump())


@router.get("/activos-fijos/{activo_id}", response_model=ActivoFijo)
async def obtener_activo(request: Request, activo_id: str):
    return await _llamar(repo.obtener_activo, _pool(request), _uuid(activo_id), no_encontrado=_ACTIVO_NO_ENCONTRADO)


@router.put("/activos-fijos/{activo_id}", response_model=ActivoFijo)
async def actualizar_activo(request: Request, activo_id: str, payload: ActivoFijoActualizar):
    return await _llamar(
        repo.actualizar_activo, _pool(request), _uuid(activo_id), payload.model_dump(exclude_unset=True),
        no_encontrado=_ACTIVO_NO_ENCONTRADO,
    )


@router.delete("/activos-fijos/{activo_id}", status_code=204)
async def eliminar_activo(request: Request, activo_id: str):
    await _llamar(repo.eliminar_activo, _pool(request), _uuid(activo_id), no_encontrado=_ACTIVO_NO_ENCONTRADO)


@router.post("/activos-fijos/{activo_id}/baja", response_model=ActivoFijo)
async def dar_baja_activo(request: Request, activo_id: str, payload: BajaActivoPayload):
    return await _llamar(
        repo.dar_baja_activo, _pool(request), _uuid(activo_id), payload.fecha, no_encontrado=_ACTIVO_NO_ENCONTRADO
    )


@router.get("/activos-fijos/{activo_id}/depreciacion", response_model=TablaDepreciacion)
async def tabla_depreciacion(request: Request, activo_id: str, hasta: str | None = Query(None, pattern=r"^\d{6}$")):
    return await _llamar(
        repo.tabla_depreciacion, _pool(request), _uuid(activo_id), hasta=hasta, no_encontrado=_ACTIVO_NO_ENCONTRADO
    )


@router.get("/depreciacion", response_model=DepreciacionPeriodo)
async def depreciacion_del_periodo(request: Request, periodo: str):
    return await _llamar(repo.depreciacion_del_periodo, _pool(request), periodo)


@router.post("/depreciacion", response_model=ResultadoDepreciacion, status_code=201)
async def registrar_depreciacion(request: Request, payload: DepreciacionPayload):
    return await _llamar(repo.registrar_depreciacion, _pool(request), payload.periodo)


@router.get("/depreciacion/fiscal", response_model=DepreciacionFiscal)
async def depreciacion_fiscal(request: Request, anio: str):
    """Depreciación fiscal del año (Art. 287): la sugerencia del IR-2."""
    return await _llamar(repo.obtener_depreciacion_fiscal, _pool(request), anio)
