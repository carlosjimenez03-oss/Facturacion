"""Cálculos puros de Compras (fase 3), sin base de datos: el XML de la
aprobación comercial (ACECF), los campos de pago del Formato 606 que salen
de los pagos a proveedores, los períodos de IT-1/IR-17 que un cambio de
retenciones afecta y la sugerencia de retenciones de la factura de compra.
Ver docs/ERP_API.md, sección "Compras".
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from lxml import etree

from .precalculos import PERIODO_LEY_30_26, RETENCIONES_ISR

_CERO = Decimal("0")
_CENTAVO = Decimal("0.01")

ESTADO_ACECF_APROBADO = "1"
ESTADO_ACECF_RECHAZADO = "2"

# Campo 23 del 606 (Forma de Pago) según el método del pago.
_FORMA_PAGO_606 = {"efectivo": 1, "transferencia": 2, "cheque": 2, "tarjeta": 3}
FORMA_PAGO_CREDITO = 4
FORMA_PAGO_MIXTO = 7


def redondear(valor: Decimal) -> Decimal:
    return Decimal(valor).quantize(_CENTAVO, rounding=ROUND_HALF_UP)


def _monto_dgii(valor: Decimal) -> str:
    return f"{redondear(valor):.2f}"


# --- Aprobación comercial (ACECF) ---------------------------------------------------------

def construir_acecf(
    *,
    rnc_emisor: str,
    encf: str,
    fecha_emision: date | None,
    monto_total: Decimal,
    rnc_comprador: str,
    aprobado: bool,
    motivo_rechazo: str | None,
    fecha_hora: datetime,
) -> bytes:
    """Arma el XML (sin firmar) de la aprobación comercial de un e-CF
    recibido; `aprobacion_comercial` lo firma y lo envía a la DGII.

    Estado 1 = aprobado, 2 = rechazado (con DetalleMotivoRechazo). Igual que
    el ARECF de receptor_recepcion (ver su arecf_builder.py), el XSD oficial
    del ACECF no está en el repositorio: los elementos siguen el formato
    "Aprobación Comercial" publicado por la DGII; verificar contra el XSD
    del portal cuando esté disponible.
    """
    root = etree.Element("ACECF")
    detalle = etree.SubElement(root, "DetalleAprobacionComercial")
    etree.SubElement(detalle, "Version").text = "1.0"
    etree.SubElement(detalle, "RNCEmisor").text = rnc_emisor
    etree.SubElement(detalle, "eNCF").text = encf
    if fecha_emision is not None:
        etree.SubElement(detalle, "FechaEmision").text = fecha_emision.strftime("%d-%m-%Y")
    etree.SubElement(detalle, "MontoTotal").text = _monto_dgii(monto_total)
    etree.SubElement(detalle, "RNCComprador").text = rnc_comprador
    etree.SubElement(detalle, "Estado").text = ESTADO_ACECF_APROBADO if aprobado else ESTADO_ACECF_RECHAZADO
    if not aprobado and motivo_rechazo:
        etree.SubElement(detalle, "DetalleMotivoRechazo").text = motivo_rechazo[:250]
    etree.SubElement(detalle, "FechaHoraAprobacionComercial").text = fecha_hora.strftime("%d-%m-%Y %H:%M:%S")
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=False)


# --- Pagos -> campos de pago del 606 ---------------------------------------------------------

@dataclass(frozen=True)
class CamposPago606:
    """Los campos del renglón del 606 que dependen del pago."""

    fecha_pago: str | None  # AAAAMMDD
    forma_pago: int | None
    itbis_retenido: Decimal = _CERO
    tipo_retencion_isr: int | None = None
    monto_retencion_renta: Decimal = _CERO

    @classmethod
    def desde(cls, fila: Any, prefijo: str = "") -> CamposPago606:
        return cls(
            fecha_pago=fila[f"{prefijo}fecha_pago"],
            forma_pago=fila[f"{prefijo}forma_pago"],
            itbis_retenido=Decimal(fila[f"{prefijo}itbis_retenido"] or 0),
            tipo_retencion_isr=fila[f"{prefijo}tipo_retencion_isr"],
            monto_retencion_renta=Decimal(fila[f"{prefijo}monto_retencion_renta"] or 0),
        )

    @property
    def retenciones(self) -> Decimal:
        return self.itbis_retenido + self.monto_retencion_renta


@dataclass(frozen=True)
class Pago:
    fecha: date
    metodo: str
    retencion_isr: Decimal = _CERO
    retencion_itbis: Decimal = _CERO
    tipo_retencion_isr: int | None = None
    monto: Decimal = field(default=_CERO)

    @property
    def tiene_retenciones(self) -> bool:
        return self.retencion_isr > 0 or self.retencion_itbis > 0


def forma_pago_606(metodos: set[str]) -> int:
    formas = {_FORMA_PAGO_606[m] for m in metodos}
    return formas.pop() if len(formas) == 1 else FORMA_PAGO_MIXTO


def campos_pago_606(base: CamposPago606, pagos: list[Pago]) -> CamposPago606:
    """Campos de pago del 606 a partir de los pagos vigentes (no anulados).

    - Sin pagos: los valores que tenía el renglón antes del primer pago
      (`base`), p. ej. retenciones clasificadas a mano en el 606.
    - Fecha de pago: la del pago que lleva las retenciones (es la que decide
      el mes del IT-1/IR-17); si ninguno las lleva, la del último pago.
    - Retenciones: las del pago que las lleva (solo uno puede) o, si
      ninguno, las de `base`.
    - Forma de pago: la del método de los pagos; 7 (mixto) si hay varios.
    """
    if not pagos:
        return base
    con_retenciones = [p for p in pagos if p.tiene_retenciones]
    forma = forma_pago_606({p.metodo for p in pagos})
    if con_retenciones:
        pago = con_retenciones[0]
        return CamposPago606(
            fecha_pago=pago.fecha.strftime("%Y%m%d"),
            forma_pago=forma,
            itbis_retenido=pago.retencion_itbis,
            tipo_retencion_isr=pago.tipo_retencion_isr if pago.retencion_isr > 0 else None,
            monto_retencion_renta=pago.retencion_isr,
        )
    ultimo = max(p.fecha for p in pagos)
    return CamposPago606(
        fecha_pago=ultimo.strftime("%Y%m%d"),
        forma_pago=forma,
        itbis_retenido=base.itbis_retenido,
        tipo_retencion_isr=base.tipo_retencion_isr,
        monto_retencion_renta=base.monto_retencion_renta,
    )


def declaraciones_afectadas(anterior: CamposPago606, nuevo: CamposPago606) -> set[tuple[str, str]]:
    """(formulario, AAAAMM) del IT-1 / IR-17 cuyo total cambia al pasar el
    renglón del 606 de `anterior` a `nuevo`: el IT-1 suma el ITBIS retenido
    y el IR-17 el ISR retenido por mes de fecha de pago."""
    def it1(c: CamposPago606):
        return (c.fecha_pago[:6] if c.fecha_pago else None, c.itbis_retenido)

    def ir17(c: CamposPago606):
        return (c.fecha_pago[:6] if c.fecha_pago else None, c.tipo_retencion_isr, c.monto_retencion_renta)

    afectadas: set[tuple[str, str]] = set()
    if it1(anterior) != it1(nuevo):
        for c in (anterior, nuevo):
            if c.fecha_pago and c.itbis_retenido > 0:
                afectadas.add(("it1", c.fecha_pago[:6]))
    if ir17(anterior) != ir17(nuevo):
        for c in (anterior, nuevo):
            if c.fecha_pago and c.monto_retencion_renta > 0:
                afectadas.add(("ir17", c.fecha_pago[:6]))
    return afectadas


# --- Tipos de comprobante ---------------------------------------------------------

def es_nota_credito(ncf: str) -> bool:
    """Mismo criterio que `_SIGNO_606` de impuestos_repository: E34, B04 o
    el tipo 04 de un NCF de 19 posiciones."""
    ncf = (ncf or "").upper()
    return ncf.startswith(("E34", "B04")) or (len(ncf) == 19 and ncf[9:11] == "04")


def tipo_ecf_de_ncf(ncf: str) -> int | None:
    ncf = (ncf or "").upper()
    if ncf.startswith("E") and len(ncf) == 13 and ncf[1:3].isdigit():
        return int(ncf[1:3])
    return None


def etiqueta_retencion_isr(tipo: int | None) -> str | None:
    definicion = RETENCIONES_ISR.get(tipo) if tipo is not None else None
    return definicion.etiqueta if definicion else None


# --- Sugerencia de retenciones (pantalla "Nueva factura de compra") ---------------------------------------------------------

_SERVICIOS = {2, 5}  # 02 trabajos, suministros y servicios · 05 representación
_ARRENDAMIENTO = 3
_BIENES = {4, 9, 10}  # activos fijos, costo de venta, adquisiciones de activos


def sugerir_retenciones(
    *,
    persona: str,
    tipo_bienes_servicios: int,
    emisor_electronico: bool,
    monto: Decimal,
    itbis: Decimal,
    periodo_pago: str,
) -> dict[str, Any]:
    """Retenciones de ISR e ITBIS que corresponden, según el tipo de
    proveedor y el tipo de gasto del 606 -- es una SUGERENCIA (el usuario
    confirma los montos al registrar el pago):

    - Persona física, servicios (02, 05): ISR de honorarios (tipo 2) e ITBIS
      retenido 100 % (Norma General 02-2005).
    - Persona física, arrendamiento (03): ISR de alquileres (tipo 1) e ITBIS 100 %.
    - Persona física sin e-CF (Comprobante de Compras E41), bienes (04, 09,
      10): ISR de otras rentas presuntas (tipo 4) e ITBIS 100 %.
    - Persona jurídica, servicios (02, 05): ITBIS retenido 30 %, sin ISR.
    - Lo demás: sin retención.

    La tasa de ISR es la de `precalculos.RETENCIONES_ISR` para el mes del
    PAGO (Ley 30-26 desde julio 2026), la misma con la que el IR-17 compara.
    """
    tipo_isr: int | None = None
    pct_itbis = _CERO
    if persona == "fisica":
        if tipo_bienes_servicios in _SERVICIOS:
            tipo_isr, pct_itbis = 2, Decimal("100")
            regla = "Servicios prestados por persona física: retención de ISR (honorarios) e ITBIS retenido 100%."
        elif tipo_bienes_servicios == _ARRENDAMIENTO:
            tipo_isr, pct_itbis = 1, Decimal("100")
            regla = "Arrendamiento pagado a persona física: retención de ISR (alquileres) e ITBIS retenido 100%."
        elif tipo_bienes_servicios in _BIENES and not emisor_electronico:
            tipo_isr, pct_itbis = 4, Decimal("100")
            regla = (
                "Compra de bienes a proveedor informal con Comprobante de Compras (E41): retención de ISR "
                "(otras rentas presuntas) e ITBIS retenido 100%."
            )
        else:
            regla = "Tipo de gasto sin retención para persona física."
    else:
        if tipo_bienes_servicios in _SERVICIOS:
            pct_itbis = Decimal("30")
            regla = "Servicios prestados por persona jurídica: retención del 30% del ITBIS facturado. No aplica ISR."
        else:
            regla = "Compras a persona jurídica de este tipo: no aplica retención."

    pct_isr = _CERO
    if tipo_isr is not None:
        definicion = RETENCIONES_ISR[tipo_isr]
        tasa = definicion.tasa_desde_ley_30_26 if periodo_pago >= PERIODO_LEY_30_26 else definicion.tasa
        pct_isr = tasa * 100
    retencion_isr = redondear(monto * pct_isr / 100)
    retencion_itbis = redondear(itbis * pct_itbis / 100)
    total = redondear(monto + itbis)
    return {
        "tipo_retencion_isr": tipo_isr,
        "tipo_retencion_isr_label": etiqueta_retencion_isr(tipo_isr),
        "isr_pct": redondear(pct_isr),
        "retencion_isr": retencion_isr,
        "itbis_pct": redondear(pct_itbis),
        "retencion_itbis": retencion_itbis,
        "total": total,
        "neto_a_pagar": total - retencion_isr - retencion_itbis,
        "regla": regla,
    }
