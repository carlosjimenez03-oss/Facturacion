"""Rutas de Compras de erp-api (fase 3): bandeja de entrada de e-CF
recibidos (aprobación comercial), facturas de compra y cuentas por pagar,
pagos a proveedores con retenciones, comprobantes E41/E43 y retenciones del
mes con su certificado. Contrato con el frontend:
`frontend/src/lib/comprasApi.ts`. Ver docs/ERP_API.md, sección "Compras".
"""
import uuid
from datetime import date
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response

from libs.ecf_common.compras606 import TIPOS_BIENES_SERVICIOS
from libs.ecf_common.ecf_recibidos import bytes_xml
from libs.ecf_common.empresa import razon_social, rnc_emisor

from . import compras_calculos, compras_repository
from .aprobacion_client import AprobacionComercialError, entregar_acecf_al_emisor, enviar_aprobacion_comercial
from .compras_repository import CompraNoEncontradaError, ConflictoComprasError
from .config import ErpApiSettings, get_settings
from .impuestos_repository import ConflictoImpuestosError
from .repository import DatosInvalidosError, hoy
from .schemas import (
    CertificadoRetencion,
    ComprobanteCompra,
    CuentaPorPagar,
    EcfRecibido,
    EcfRecibidoResumen,
    EstadoAprobacion,
    EstadoCuentaPagar,
    FacturaCompra,
    FacturaCompraActualizar,
    FacturaCompraPayload,
    PagoProveedor,
    PagoProveedorPayload,
    RechazoPayload,
    RetencionesMes,
    SugerenciaRetencion,
    VincularPayload,
)

router = APIRouter(prefix="/compras", tags=["compras"])


def _pool(request: Request):
    return request.app.state.db_pool


def _uuid(valor: str, nombre: str = "id") -> uuid.UUID:
    try:
        return uuid.UUID(valor)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"{nombre} inválido") from exc


async def _llamar(funcion, *args, no_encontrado: str = "No encontrado", **kwargs):
    """Traduce los errores del repositorio a HTTP: 422 datos inválidos, 404
    no encontrado, 409 conflicto (estado o declaración ya presentada), 502
    si el módulo de aprobación comercial / la DGII falla."""
    try:
        return await funcion(*args, **kwargs)
    except DatosInvalidosError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except CompraNoEncontradaError as exc:
        raise HTTPException(status_code=404, detail=no_encontrado) from exc
    except (ConflictoComprasError, ConflictoImpuestosError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except AprobacionComercialError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


_ECF_NO_ENCONTRADO = "e-CF recibido no encontrado"
_FACTURA_NO_ENCONTRADA = "Factura de compra no encontrada"


# --- Bandeja de entrada ---------------------------------------------------------

@router.get("/bandeja", response_model=list[EcfRecibidoResumen])
async def listar_bandeja(
    request: Request,
    estado: EstadoAprobacion | None = None,
    q: str | None = None,
    periodo: str | None = None,
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    """`periodo` = AAAAMM de la fecha de emisión."""
    return await _llamar(
        compras_repository.listar_ecf_recibidos, _pool(request), estado=estado, q=q, periodo=periodo,
        limite=limit, offset=offset,
    )


@router.get("/bandeja/{ecf_id}", response_model=EcfRecibido)
async def obtener_bandeja(request: Request, ecf_id: str):
    return await _llamar(
        compras_repository.obtener_ecf_recibido, _pool(request), _uuid(ecf_id), no_encontrado=_ECF_NO_ENCONTRADO
    )


@router.get("/bandeja/{ecf_id}/xml")
async def descargar_xml_bandeja(request: Request, ecf_id: str):
    nombre, xml = await _llamar(
        compras_repository.xml_ecf_recibido, _pool(request), _uuid(ecf_id), no_encontrado=_ECF_NO_ENCONTRADO
    )
    contenido, codificacion = bytes_xml(xml)  # con la codificación que declara el XML
    return Response(
        content=contenido,
        media_type=f"text/xml; charset={codificacion}",
        headers={"Content-Disposition": f'attachment; filename="{nombre}"'},
    )


def _enviar(settings: ErpApiSettings):
    async def enviar(encf: str, xml: bytes) -> dict:
        return await enviar_aprobacion_comercial(
            settings.aprobacion_comercial_url, encf=encf, xml=xml,
            timeout=settings.aprobacion_comercial_timeout_segundos,
        )

    return enviar


def _entregar(settings: ErpApiSettings, rnc_comprador: str):
    async def entregar(rnc_emisor: str, encf: str, xml: bytes) -> str:
        return await entregar_acecf_al_emisor(
            settings.consulta_directorio_url, rnc_emisor=rnc_emisor, nombre_archivo=f"{rnc_comprador}{encf}.xml",
            xml=xml, timeout=settings.entrega_emisor_timeout_segundos,
        )

    return entregar


async def _decidir(request: Request, ecf_id: str, settings: ErpApiSettings, *, aprobado: bool, motivo: str | None):
    # Somos el comprador: la empresa del request (la 1: EMISOR_RNC).
    rnc_comprador = await rnc_emisor(_pool(request), settings.emisor_rnc)
    return await _llamar(
        compras_repository.decidir_aprobacion,
        _pool(request),
        _uuid(ecf_id),
        aprobado=aprobado,
        motivo=motivo,
        rnc_comprador=rnc_comprador,
        enviar=_enviar(settings),
        entregar=_entregar(settings, rnc_comprador),
        no_encontrado=_ECF_NO_ENCONTRADO,
    )


@router.post("/bandeja/{ecf_id}/aprobar", response_model=EcfRecibido)
async def aprobar_bandeja(request: Request, ecf_id: str, settings: ErpApiSettings = Depends(get_settings)):
    return await _decidir(request, ecf_id, settings, aprobado=True, motivo=None)


@router.post("/bandeja/{ecf_id}/rechazar", response_model=EcfRecibido)
async def rechazar_bandeja(
    request: Request, ecf_id: str, payload: RechazoPayload, settings: ErpApiSettings = Depends(get_settings)
):
    return await _decidir(request, ecf_id, settings, aprobado=False, motivo=payload.motivo)


@router.post("/bandeja/{ecf_id}/vincular", response_model=EcfRecibido)
async def vincular_bandeja(request: Request, ecf_id: str, payload: VincularPayload):
    return await _llamar(
        compras_repository.vincular_ecf_recibido,
        _pool(request),
        _uuid(ecf_id),
        payload.model_dump(exclude_unset=True),
        no_encontrado=_ECF_NO_ENCONTRADO,
    )


# --- Cuentas por pagar y facturas de compra ---------------------------------------------------------

@router.get("/cuentas-por-pagar", response_model=list[CuentaPorPagar])
async def listar_cuentas_por_pagar(
    request: Request,
    estado: EstadoCuentaPagar | Literal["todas"] | None = None,
    proveedor_rnc: str | None = None,
    q: str | None = None,
    periodo: str | None = None,
):
    """Sin `estado`: lo que falta por pagar (pendientes + vencidas)."""
    return await _llamar(
        compras_repository.listar_cuentas_por_pagar, _pool(request), estado=estado, proveedor_rnc=proveedor_rnc,
        q=q, periodo=periodo,
    )


@router.post("/facturas", response_model=FacturaCompra, status_code=201)
async def crear_factura_compra(request: Request, payload: FacturaCompraPayload):
    return await _llamar(compras_repository.crear_factura_compra, _pool(request), **payload.model_dump())


@router.get("/facturas/{registro_id}", response_model=FacturaCompra)
async def obtener_factura_compra(request: Request, registro_id: str):
    return await _llamar(
        compras_repository.obtener_factura_compra, _pool(request), _uuid(registro_id),
        no_encontrado=_FACTURA_NO_ENCONTRADA,
    )


@router.put("/facturas/{registro_id}", response_model=FacturaCompra)
async def actualizar_factura_compra(request: Request, registro_id: str, payload: FacturaCompraActualizar):
    return await _llamar(
        compras_repository.actualizar_factura_compra, _pool(request), _uuid(registro_id),
        payload.model_dump(exclude_unset=True), no_encontrado=_FACTURA_NO_ENCONTRADA,
    )


@router.delete("/facturas/{registro_id}", status_code=204)
async def eliminar_factura_compra(request: Request, registro_id: str):
    await _llamar(
        compras_repository.eliminar_factura_compra, _pool(request), _uuid(registro_id),
        no_encontrado=_FACTURA_NO_ENCONTRADA,
    )


# --- Pagos a proveedores ---------------------------------------------------------

@router.get("/pagos", response_model=list[PagoProveedor])
async def listar_pagos(
    request: Request,
    registro606_id: str | None = None,
    periodo: str | None = None,
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    rid = _uuid(registro606_id, "registro606_id") if registro606_id else None
    return await _llamar(
        compras_repository.listar_pagos, _pool(request), registro606_id=rid, periodo=periodo, limite=limit,
        offset=offset,
    )


@router.post("/pagos", response_model=PagoProveedor, status_code=201)
async def registrar_pago(request: Request, payload: PagoProveedorPayload):
    return await _llamar(
        compras_repository.registrar_pago, _pool(request), **payload.model_dump(),
        no_encontrado=_FACTURA_NO_ENCONTRADA,
    )


@router.post("/pagos/{pago_id}/anular", response_model=PagoProveedor)
async def anular_pago(request: Request, pago_id: str):
    return await _llamar(
        compras_repository.anular_pago, _pool(request), _uuid(pago_id), no_encontrado="Pago no encontrado"
    )


# --- Comprobantes E41 / E43 ---------------------------------------------------------

@router.get("/comprobantes", response_model=list[ComprobanteCompra])
async def listar_comprobantes(
    request: Request,
    tipo_ecf: int | None = Query(None, description="41 o 43; sin él, ambos"),
    estado: str | None = None,
    q: str | None = None,
    periodo: str | None = None,
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    return await _llamar(
        compras_repository.listar_comprobantes_compra, _pool(request), tipo_ecf=tipo_ecf, estado=estado, q=q,
        periodo=periodo, limite=limit, offset=offset,
    )


# --- Retenciones ---------------------------------------------------------

@router.get("/retenciones", response_model=RetencionesMes)
async def retenciones_del_mes(request: Request, periodo: str):
    return await _llamar(compras_repository.retenciones_del_mes, _pool(request), periodo)


@router.get("/retenciones/certificado", response_model=CertificadoRetencion)
async def certificado_retencion(
    request: Request, periodo: str, rnc: str, settings: ErpApiSettings = Depends(get_settings)
):
    return await _llamar(
        compras_repository.certificado_retencion,
        _pool(request),
        periodo,
        rnc,
        agente_rnc=await rnc_emisor(_pool(request), settings.emisor_rnc),
        agente_nombre=await razon_social(_pool(request), settings.emisor_razon_social),
        no_encontrado="No hay retenciones a ese proveedor en el período",
    )


@router.get("/retenciones/sugerencia", response_model=SugerenciaRetencion)
async def sugerir_retenciones(
    persona: Literal["fisica", "juridica"],
    tipo_bienes_servicios: int,
    monto: Decimal = Query(..., ge=0),
    itbis: Decimal = Query(Decimal("0"), ge=0),
    emisor_electronico: bool = True,
    fecha_pago: date | None = None,
):
    """Retenciones sugeridas para la pantalla "Nueva factura de compra"
    (sin base de datos; ver compras_calculos.sugerir_retenciones)."""
    if tipo_bienes_servicios not in TIPOS_BIENES_SERVICIOS:
        raise HTTPException(status_code=422, detail="Tipo de bienes y servicios inválido (1-11)")
    return compras_calculos.sugerir_retenciones(
        persona=persona,
        tipo_bienes_servicios=tipo_bienes_servicios,
        emisor_electronico=emisor_electronico,
        monto=monto,
        itbis=itbis,
        periodo_pago=(fecha_pago or hoy()).strftime("%Y%m"),
    )
