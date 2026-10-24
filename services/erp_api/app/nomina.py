"""Rutas de Kontara Nómina en erp-api: empresa empleadora, parámetros de ley
(tasas TSS, topes y escala del ISR por fecha de vigencia), empleados (con
importación CSV y prestaciones), períodos de nómina (cálculo, aprobación,
pago y cierre), archivo para el banco y reportes (TSS, IR-3, IR-13, costo
vs pago). Contrato con el frontend: `frontend/src/lib/nominaApi.ts`. Ver
docs/NOMINA.md.

Los esquemas viven aquí (no en `schemas.py`) para que el módulo sea
autocontenido.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from . import nomina_repository as repo
from .nomina_repository import ConflictoNominaError, NominaNoEncontradaError
from .repository import DatosInvalidosError
from .schemas import Monto, _Entrada

router = APIRouter(prefix="/nomina", tags=["nomina"])

TipoPeriodo = Literal["mensual", "quincenal"]
EstadoPeriodo = Literal["borrador", "calculada", "aprobada", "pagada", "cerrada"]
EstadoEmpleado = Literal["activo", "inactivo"]
TipoCuenta = Literal["ahorro", "corriente"]


# --- Esquemas ---------------------------------------------------------

class EmpresaPayload(_Entrada):
    rnc: str | None = None
    razon_social: str | None = None
    categoria: str | None = None
    municipio: str | None = None
    contacto_nombre: str | None = None
    tasa_srl: Decimal = Field(Decimal("0.0110"), ge=0, lt=Decimal("0.1"))
    frecuencia_pago: TipoPeriodo = "mensual"
    dia_pago: int = Field(30, ge=1, le=31)
    banco: str | None = None
    cuenta_origen: str | None = None


class Empresa(BaseModel):
    rnc: str | None = None
    razon_social: str | None = None
    categoria: str | None = None
    municipio: str | None = None
    contacto_nombre: str | None = None
    tasa_srl: Decimal
    frecuencia_pago: TipoPeriodo
    dia_pago: int
    banco: str | None = None
    cuenta_origen: str | None = None
    completa: bool
    actualizado_en: datetime | None = None


class TramoIsr(BaseModel):
    desde: Monto
    tasa: Decimal = Field(..., ge=0, lt=1)
    fijo: Monto = Decimal("0")


class ParametrosPayload(_Entrada):
    salario_minimo_cotizable: Decimal = Field(..., gt=0)
    afp_empleado: Decimal = Field(..., ge=0, lt=1)
    afp_empleador: Decimal = Field(..., ge=0, lt=1)
    sfs_empleado: Decimal = Field(..., ge=0, lt=1)
    sfs_empleador: Decimal = Field(..., ge=0, lt=1)
    srl_empleador: Decimal = Field(..., ge=0, lt=1)
    infotep_empleador: Decimal = Field(..., ge=0, lt=1)
    infotep_bonificacion_empleado: Decimal = Field(..., ge=0, lt=1)
    tope_afp_salarios: Decimal = Field(..., gt=0)
    tope_sfs_salarios: Decimal = Field(..., gt=0)
    tope_srl_salarios: Decimal = Field(..., gt=0)
    escala_isr: list[TramoIsr] = Field(..., min_length=1)
    regalia_tope_exento: Decimal | None = Field(None, ge=0)
    divisor_salario_diario: Decimal = Field(Decimal("23.83"), gt=0)
    horas_jornada: Decimal = Field(Decimal("8"), gt=0)
    recargo_horas_extra: Decimal = Field(Decimal("0.35"), ge=0)
    recargo_horas_extra_exceso: Decimal = Field(Decimal("1.00"), ge=0)
    fuente: str | None = None


class ParametrosNomina(BaseModel):
    vigente_desde: date
    salario_minimo_cotizable: Monto
    afp_empleado: Decimal
    afp_empleador: Decimal
    sfs_empleado: Decimal
    sfs_empleador: Decimal
    srl_empleador: Decimal
    infotep_empleador: Decimal
    infotep_bonificacion_empleado: Decimal
    tope_afp_salarios: Decimal
    tope_sfs_salarios: Decimal
    tope_srl_salarios: Decimal
    escala_isr: list[TramoIsr]
    regalia_tope_exento: Monto | None = None
    divisor_salario_diario: Decimal
    horas_jornada: Decimal
    recargo_horas_extra: Decimal
    recargo_horas_extra_exceso: Decimal
    fuente: str | None = None
    tope_afp: Monto
    tope_sfs: Monto
    tope_srl: Monto
    exento_isr_anual: Monto
    actualizado_en: datetime


class Indicadores(BaseModel):
    fecha: date
    vigente_desde: date
    salario_minimo_cotizable: Monto
    tope_afp: Monto
    tope_sfs: Monto
    tope_srl: Monto
    exento_isr_anual: Monto
    exento_isr_mensual: Monto


class EmpleadoPayload(_Entrada):
    cedula: str = Field(..., min_length=11)
    nombre: str = Field(..., min_length=1)
    cargo: str | None = None
    departamento: str | None = None
    fecha_ingreso: date
    salario_mensual: Decimal = Field(..., gt=0)
    tipo_pago: TipoPeriodo = "mensual"
    banco: str | None = None
    cuenta_bancaria: str | None = None
    tipo_cuenta: TipoCuenta | None = None
    afp: str | None = None
    ars: str | None = None
    correo: str | None = None
    estado: EstadoEmpleado = "activo"
    fecha_salida: date | None = None


class Empleado(BaseModel):
    id: uuid.UUID
    cedula: str
    nombre: str
    cargo: str | None = None
    departamento: str | None = None
    fecha_ingreso: date
    salario_mensual: Monto
    tipo_pago: TipoPeriodo
    banco: str | None = None
    cuenta_bancaria: str | None = None
    tipo_cuenta: TipoCuenta | None = None
    afp: str | None = None
    ars: str | None = None
    correo: str | None = None
    estado: EstadoEmpleado
    fecha_salida: date | None = None
    dias_vacaciones: int
    creado_en: datetime
    actualizado_en: datetime


class ErrorImportacion(BaseModel):
    fila: int
    mensaje: str


class ResultadoImportacion(BaseModel):
    creados: int
    actualizados: int
    errores: list[ErrorImportacion]


class ImportarPayload(BaseModel):
    csv: str = Field(..., min_length=1, max_length=2_000_000)


class PrestacionesEmpleado(BaseModel):
    empleado_id: uuid.UUID
    fecha_salida: date
    desahucio: bool
    meses_servicio: int
    salario_diario: Monto
    dias_preaviso: int
    preaviso: Monto
    dias_cesantia: Decimal
    cesantia: Monto
    dias_vacaciones: int
    vacaciones: Monto
    regalia: Monto
    total: Monto


class PeriodoPayload(_Entrada):
    tipo: TipoPeriodo
    anio: int = Field(..., ge=2000, le=2100)
    mes: int = Field(..., ge=1, le=12)
    quincena: Literal[1, 2] | None = None
    fecha_pago: date | None = None
    notas: str | None = None


class Totales(BaseModel):
    salario: Monto
    horas_extra: Monto
    otros_ingresos: Monto
    bonificacion: Monto
    ingresos_exentos: Monto
    regalia: Monto
    total_ingresos: Monto
    afp: Monto
    sfs: Monto
    isr: Monto
    infotep_empleado: Monto
    otros_descuentos: Monto
    total_descuentos: Monto
    neto: Monto
    afp_empleador: Monto
    sfs_empleador: Monto
    srl: Monto
    infotep: Monto
    total_aportes_empleador: Monto
    costo_total: Monto


class PeriodoResumen(BaseModel):
    id: uuid.UUID
    tipo: TipoPeriodo
    anio: int
    mes: int
    quincena: int | None = None
    nombre: str
    fecha_inicio: date
    fecha_fin: date
    fecha_pago: date
    estado: EstadoPeriodo
    tasa_srl: Decimal | None = None
    notas: str | None = None
    empleados: int
    pagados: int
    totales: Totales
    calculado_en: datetime | None = None
    aprobado_en: datetime | None = None
    pagado_en: datetime | None = None
    cerrado_en: datetime | None = None
    creado_en: datetime


class LineaNomina(BaseModel):
    empleado_id: uuid.UUID
    cedula: str
    nombre: str
    cargo: str | None = None
    departamento: str | None = None
    salario_mensual: Monto
    banco: str | None = None
    cuenta_bancaria: str | None = None
    tipo_cuenta: str | None = None
    horas_extra_35: Decimal
    horas_extra_100: Decimal
    otros_ingresos: Monto
    bonificacion: Monto
    ingresos_exentos: Monto
    regalia: Monto
    otros_descuentos: Monto
    salario: Monto | None = None
    horas_extra: Monto | None = None
    total_ingresos: Monto | None = None
    salario_cotizable: Monto | None = None
    base_isr: Monto | None = None
    afp: Monto | None = None
    sfs: Monto | None = None
    isr: Monto | None = None
    infotep_empleado: Monto | None = None
    total_descuentos: Monto | None = None
    neto: Monto | None = None
    afp_empleador: Monto | None = None
    sfs_empleador: Monto | None = None
    srl: Monto | None = None
    infotep: Monto | None = None
    total_aportes_empleador: Monto | None = None
    costo_total: Monto | None = None
    pagado: bool
    pagado_en: datetime | None = None
    referencia_pago: str | None = None


class Periodo(PeriodoResumen):
    parametros: dict[str, Any] | None = None
    lineas: list[LineaNomina]


class NovedadesPayload(_Entrada):
    horas_extra_35: Decimal = Field(Decimal("0"), ge=0, le=744)
    horas_extra_100: Decimal = Field(Decimal("0"), ge=0, le=744)
    otros_ingresos: Decimal = Field(Decimal("0"), ge=0)
    bonificacion: Decimal = Field(Decimal("0"), ge=0)
    ingresos_exentos: Decimal = Field(Decimal("0"), ge=0)
    regalia: Decimal = Field(Decimal("0"), ge=0)
    otros_descuentos: Decimal = Field(Decimal("0"), ge=0)


class PagoPayload(_Entrada):
    empleado_ids: list[uuid.UUID] | None = None
    referencia: str | None = None


class RegaliaSugerida(BaseModel):
    empleado_id: uuid.UUID
    anio: int
    salario_ordinario_registrado: Monto
    regalia_registrada: Monto
    regalia: Monto


class FilaTss(BaseModel):
    empleado_id: uuid.UUID
    cedula: str
    nombre: str
    salario_cotizable: Monto
    regalia: Monto
    otras_remuneraciones: Monto
    afp: Monto
    sfs: Monto
    afp_empleador: Monto
    sfs_empleador: Monto
    srl: Monto
    infotep: Monto
    total: Monto


class TotalesTss(BaseModel):
    salario_cotizable: Monto
    regalia: Monto
    otras_remuneraciones: Monto
    afp: Monto
    sfs: Monto
    afp_empleador: Monto
    sfs_empleador: Monto
    srl: Monto
    infotep: Monto
    total: Monto


class ReporteTss(BaseModel):
    anio: int
    mes: int
    nombre: str
    nominas_pendientes: int
    tope_afp: Monto
    tope_sfs: Monto
    tope_srl: Monto
    vence: date
    empleados: list[FilaTss]
    totales: TotalesTss


class FilaIr3(BaseModel):
    empleado_id: uuid.UUID
    cedula: str
    nombre: str
    ingresos_gravados: Monto
    ingresos_exentos: Monto
    tss: Monto
    base_isr: Monto
    isr: Monto


class TotalesIr3(BaseModel):
    ingresos_gravados: Monto
    ingresos_exentos: Monto
    tss: Monto
    base_isr: Monto
    isr: Monto


class ReporteIr3(BaseModel):
    anio: int
    mes: int
    nombre: str
    nominas_pendientes: int
    asalariados: int
    retenidos: int
    vence: date
    empleados: list[FilaIr3]
    totales: TotalesIr3


class FilaIr13(BaseModel):
    empleado_id: uuid.UUID
    cedula: str
    nombre: str
    salarios: Monto
    exentos: Monto
    tss: Monto
    renta_neta: Monto
    isr: Monto


class TotalesIr13(BaseModel):
    salarios: Monto
    exentos: Monto
    tss: Monto
    renta_neta: Monto
    isr: Monto


class ReporteIr13(BaseModel):
    anio: int
    nominas_pendientes: int
    empleados: list[FilaIr13]
    totales: TotalesIr13


class CostoPagoMes(BaseModel):
    mes: int
    nombre: str
    costo: Monto
    neto: Monto
    ingresos: Monto
    empleados: int


class PeriodoSugerido(BaseModel):
    tipo: TipoPeriodo
    anio: int
    mes: int
    nombre: str


class ResumenNomina(BaseModel):
    empresa_completa: bool
    contacto_nombre: str | None = None
    empleados_activos: int
    nomina_calculada: bool
    pasos_completados: int
    periodo: PeriodoResumen | None = None
    periodo_sugerido: PeriodoSugerido
    indicadores: Indicadores | None = None


class Aviso(BaseModel):
    id: str
    tipo: Literal["tss", "dgii", "equipo"]
    titulo: str
    detalle: str
    fecha: date | None = None
    enlace: str | None = None


# --- Utilidades ---------------------------------------------------------

def _pool(request: Request):
    return request.app.state.db_pool


def _uuid(valor: str, nombre: str = "id") -> uuid.UUID:
    try:
        return uuid.UUID(valor)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"{nombre} inválido") from exc


async def _llamar(funcion, *args, no_encontrado: str = "No encontrado", **kwargs):
    """422 datos inválidos, 404 no encontrado, 409 conflicto de estado."""
    try:
        return await funcion(*args, **kwargs)
    except DatosInvalidosError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except NominaNoEncontradaError as exc:
        raise HTTPException(status_code=404, detail=no_encontrado) from exc
    except ConflictoNominaError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _csv(nombre: str, contenido: bytes) -> Response:
    return Response(
        content=contenido,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{nombre}"'},
    )


_EMPLEADO_NO_ENCONTRADO = "Empleado no encontrado"
_PERIODO_NO_ENCONTRADO = "Nómina no encontrada"


# --- Empresa y parámetros ---------------------------------------------------------

@router.get("/empresa", response_model=Empresa)
async def obtener_empresa(request: Request):
    return await _llamar(repo.obtener_empresa, _pool(request))


@router.put("/empresa", response_model=Empresa)
async def guardar_empresa(request: Request, payload: EmpresaPayload):
    return await _llamar(repo.guardar_empresa, _pool(request), **payload.model_dump())


@router.get("/parametros", response_model=list[ParametrosNomina])
async def listar_parametros(request: Request):
    return await _llamar(repo.listar_parametros, _pool(request))


@router.get("/parametros/vigentes", response_model=ParametrosNomina)
async def parametros_vigentes(request: Request, fecha: date | None = None):
    return await _llamar(repo.parametros_vigentes, _pool(request), fecha)


@router.put("/parametros/{vigente_desde}", response_model=ParametrosNomina)
async def guardar_parametros(request: Request, vigente_desde: date, payload: ParametrosPayload):
    datos = payload.model_dump()
    datos["escala_isr"] = [t.model_dump() for t in payload.escala_isr]
    return await _llamar(repo.guardar_parametros, _pool(request), vigente_desde, **datos)


@router.delete("/parametros/{vigente_desde}", status_code=204)
async def eliminar_parametros(request: Request, vigente_desde: date):
    await _llamar(repo.eliminar_parametros, _pool(request), vigente_desde, no_encontrado="Parámetros no encontrados")


@router.get("/indicadores", response_model=Indicadores)
async def indicadores(request: Request, fecha: date | None = None):
    return await _llamar(repo.indicadores, _pool(request), fecha)


@router.get("/resumen", response_model=ResumenNomina)
async def resumen(request: Request):
    return await _llamar(repo.resumen, _pool(request))


@router.get("/avisos", response_model=list[Aviso])
async def avisos(request: Request):
    return await _llamar(repo.avisos, _pool(request))


# --- Empleados ---------------------------------------------------------

@router.get("/empleados", response_model=list[Empleado])
async def listar_empleados(
    request: Request, q: str | None = None, estado: EstadoEmpleado | None = None, tipo_pago: TipoPeriodo | None = None
):
    return await _llamar(repo.listar_empleados, _pool(request), q=q, estado=estado, tipo_pago=tipo_pago)


@router.post("/empleados", response_model=Empleado, status_code=201)
async def crear_empleado(request: Request, payload: EmpleadoPayload):
    return await _llamar(repo.crear_empleado, _pool(request), **payload.model_dump())


@router.post("/empleados/importar", response_model=ResultadoImportacion)
async def importar_empleados(request: Request, payload: ImportarPayload):
    """CSV con encabezado: cédula, nombre, salario, fecha ingreso (y
    opcionales cargo, departamento, tipo pago, banco, cuenta, tipo cuenta,
    AFP, ARS, correo). Crea o actualiza por cédula."""
    return await _llamar(repo.importar_empleados, _pool(request), payload.csv)


@router.get("/empleados/{empleado_id}", response_model=Empleado)
async def obtener_empleado(request: Request, empleado_id: str):
    return await _llamar(repo.obtener_empleado, _pool(request), _uuid(empleado_id), no_encontrado=_EMPLEADO_NO_ENCONTRADO)


@router.put("/empleados/{empleado_id}", response_model=Empleado)
async def actualizar_empleado(request: Request, empleado_id: str, payload: EmpleadoPayload):
    return await _llamar(
        repo.actualizar_empleado, _pool(request), _uuid(empleado_id), **payload.model_dump(),
        no_encontrado=_EMPLEADO_NO_ENCONTRADO,
    )


@router.delete("/empleados/{empleado_id}", status_code=204)
async def eliminar_empleado(request: Request, empleado_id: str):
    await _llamar(repo.eliminar_empleado, _pool(request), _uuid(empleado_id), no_encontrado=_EMPLEADO_NO_ENCONTRADO)


@router.get("/empleados/{empleado_id}/prestaciones", response_model=PrestacionesEmpleado)
async def prestaciones(request: Request, empleado_id: str, fecha_salida: date, desahucio: bool = True):
    return await _llamar(
        repo.prestaciones_empleado, _pool(request), _uuid(empleado_id), fecha_salida, desahucio,
        no_encontrado=_EMPLEADO_NO_ENCONTRADO,
    )


# --- Períodos ---------------------------------------------------------

@router.get("/periodos", response_model=list[PeriodoResumen])
async def listar_periodos(request: Request, anio: int | None = Query(None, ge=2000, le=2100), estado: EstadoPeriodo | None = None):
    return await _llamar(repo.listar_periodos, _pool(request), anio=anio, estado=estado)


@router.post("/periodos", response_model=Periodo, status_code=201)
async def crear_periodo(request: Request, payload: PeriodoPayload):
    return await _llamar(repo.crear_periodo, _pool(request), **payload.model_dump())


@router.get("/periodos/{periodo_id}", response_model=Periodo)
async def obtener_periodo(request: Request, periodo_id: str):
    return await _llamar(repo.obtener_periodo, _pool(request), _uuid(periodo_id), no_encontrado=_PERIODO_NO_ENCONTRADO)


@router.delete("/periodos/{periodo_id}", status_code=204)
async def eliminar_periodo(request: Request, periodo_id: str):
    await _llamar(repo.eliminar_periodo, _pool(request), _uuid(periodo_id), no_encontrado=_PERIODO_NO_ENCONTRADO)


@router.post("/periodos/{periodo_id}/calcular", response_model=Periodo)
async def calcular_periodo(request: Request, periodo_id: str):
    return await _llamar(repo.calcular_periodo, _pool(request), _uuid(periodo_id), no_encontrado=_PERIODO_NO_ENCONTRADO)


@router.put("/periodos/{periodo_id}/lineas/{empleado_id}", response_model=Periodo)
async def guardar_novedades(request: Request, periodo_id: str, empleado_id: str, payload: NovedadesPayload):
    return await _llamar(
        repo.guardar_novedades, _pool(request), _uuid(periodo_id), _uuid(empleado_id, "empleado_id"),
        **payload.model_dump(), no_encontrado="El empleado no está en esta nómina",
    )


@router.get("/periodos/{periodo_id}/lineas/{empleado_id}/regalia", response_model=RegaliaSugerida)
async def regalia_sugerida(request: Request, periodo_id: str, empleado_id: str):
    return await _llamar(
        repo.regalia_sugerida, _pool(request), _uuid(periodo_id), _uuid(empleado_id, "empleado_id"),
        no_encontrado=_PERIODO_NO_ENCONTRADO,
    )


@router.post("/periodos/{periodo_id}/aprobar", response_model=Periodo)
async def aprobar_periodo(request: Request, periodo_id: str):
    return await _llamar(repo.aprobar_periodo, _pool(request), _uuid(periodo_id), no_encontrado=_PERIODO_NO_ENCONTRADO)


@router.post("/periodos/{periodo_id}/reabrir", response_model=Periodo)
async def reabrir_periodo(request: Request, periodo_id: str):
    return await _llamar(repo.reabrir_periodo, _pool(request), _uuid(periodo_id), no_encontrado=_PERIODO_NO_ENCONTRADO)


@router.post("/periodos/{periodo_id}/pagar", response_model=Periodo)
async def pagar_periodo(request: Request, periodo_id: str, payload: PagoPayload):
    return await _llamar(
        repo.pagar_periodo, _pool(request), _uuid(periodo_id), empleado_ids=payload.empleado_ids,
        referencia=payload.referencia, no_encontrado=_PERIODO_NO_ENCONTRADO,
    )


@router.post("/periodos/{periodo_id}/cerrar", response_model=Periodo)
async def cerrar_periodo(request: Request, periodo_id: str):
    return await _llamar(repo.cerrar_periodo, _pool(request), _uuid(periodo_id), no_encontrado=_PERIODO_NO_ENCONTRADO)


@router.get("/periodos/{periodo_id}/archivo-banco")
async def archivo_banco(request: Request, periodo_id: str):
    nombre, contenido = await _llamar(
        repo.archivo_banco, _pool(request), _uuid(periodo_id), no_encontrado=_PERIODO_NO_ENCONTRADO
    )
    return _csv(nombre, contenido)


# --- Reportes ---------------------------------------------------------

@router.get("/reportes/tss", response_model=ReporteTss)
async def reporte_tss(request: Request, anio: int, mes: int):
    return await _llamar(repo.reporte_tss, _pool(request), anio, mes)


@router.get("/reportes/tss/csv")
async def archivo_tss(request: Request, anio: int, mes: int):
    nombre, contenido = await _llamar(repo.archivo_tss, _pool(request), anio, mes)
    return _csv(nombre, contenido)


@router.get("/reportes/ir3", response_model=ReporteIr3)
async def reporte_ir3(request: Request, anio: int, mes: int):
    return await _llamar(repo.reporte_ir3, _pool(request), anio, mes)


@router.get("/reportes/ir13", response_model=ReporteIr13)
async def reporte_ir13(request: Request, anio: int):
    return await _llamar(repo.reporte_ir13, _pool(request), anio)


@router.get("/reportes/costo-pago", response_model=list[CostoPagoMes])
async def costo_vs_pago(request: Request, anio: int):
    return await _llamar(repo.costo_vs_pago, _pool(request), anio)
