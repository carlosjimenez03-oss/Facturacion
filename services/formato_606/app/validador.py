"""Validación de un renglón del Formato 606 antes de poder generar el
archivo -- mismas reglas que `clsFormato606Validator`/`clsValidator` de la
plantilla oficial de Excel de la DGII. Ver docs/dgii/FORMATO_606.md.
"""
from __future__ import annotations

from typing import Any

from libs.ecf_common.compras606 import (
    FORMAS_PAGO,
    TIPOS_BIENES_SERVICIOS,
    TIPOS_RETENCION_ISR,
    validar_fecha,
    validar_ncf,
    validar_rnc_cedula,
)

_CAMPOS_MONTO = (
    "monto_servicios",
    "monto_bienes",
    "itbis_facturado",
    "itbis_retenido",
    "itbis_proporcionalidad",
    "itbis_costo",
    "itbis_percibido",
    "monto_retencion_renta",
    "isr_percibido",
    "impuesto_selectivo_consumo",
    "otros_impuestos",
    "monto_propina_legal",
)


def validar_registro(registro: dict[str, Any]) -> list[str]:
    """Retorna la lista de mensajes de error de `registro` (vacía si es
    válido). No valida contra la base de datos (NCF duplicado ya lo impide
    el índice único de compras606.registros al insertar)."""
    errores: list[str] = []

    if not validar_rnc_cedula(registro["rnc_cedula"]):
        errores.append("RNC o Cédula inválido (debe ser numérico, de 9 u 11 dígitos)")

    if registro.get("tipo_bienes_servicios") not in TIPOS_BIENES_SERVICIOS:
        errores.append("Tipo de Bienes y Servicios Comprados es obligatorio (1-11)")

    if not validar_ncf(registro["ncf"]):
        errores.append("NCF inválido")

    ncf_modificado = registro.get("ncf_modificado")
    if ncf_modificado and not validar_ncf(ncf_modificado):
        errores.append("NCF o Documento Modificado inválido")

    if not validar_fecha(registro["fecha_comprobante"]):
        errores.append("Fecha Comprobante inválida (debe ser AAAAMMDD)")

    fecha_pago = registro.get("fecha_pago")
    if fecha_pago and not validar_fecha(fecha_pago):
        errores.append("Fecha Pago inválida (debe ser AAAAMMDD)")

    # "Siempre que se llene [ITBIS Retenido / Tipo de Retención en ISR /
    # Monto Retención Renta] debe haber completado la casilla 7 (fecha pago)"
    # -- ver docs/dgii/FORMATO_606.md, campos 12, 17 y 18.
    requiere_fecha_pago = (
        registro.get("itbis_retenido")
        or registro.get("tipo_retencion_isr") is not None
        or registro.get("monto_retencion_renta")
    )
    if requiere_fecha_pago and not fecha_pago:
        errores.append(
            "Si tiene ITBIS Retenido, Tipo de Retención en ISR o Monto Retención Renta, "
            "debe especificar la Fecha de Pago"
        )

    tipo_retencion_isr = registro.get("tipo_retencion_isr")
    if tipo_retencion_isr is not None and tipo_retencion_isr not in TIPOS_RETENCION_ISR:
        errores.append("Tipo de Retención en ISR debe ser 1-9")

    if registro.get("forma_pago") not in FORMAS_PAGO:
        errores.append("Forma de Pago es obligatoria (1-7)")

    for campo in _CAMPOS_MONTO:
        valor = registro.get(campo)
        if valor is not None and valor < 0:
            errores.append(f"{campo} no puede ser negativo")

    return errores
