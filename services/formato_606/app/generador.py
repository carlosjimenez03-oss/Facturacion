"""Arma el archivo de texto del Formato 606 -- formato exacto (delimitador,
orden de campos, formato de fecha/monto) derivado de la macro
`GenerarArchivo` (módulo `modServicios`) de la plantilla oficial de Excel de
la DGII. Ver docs/dgii/FORMATO_606.md.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any


def _fmt_monto(valor: Decimal) -> str:
    return f"{valor:.2f}"


def _fmt_codigo(valor: int | None) -> str:
    return f"{valor:02d}" if valor is not None else ""


def construir_linea_detalle(registro: dict[str, Any]) -> str:
    """Los 23 campos, en el orden exacto que exige el archivo (ver
    docs/dgii/FORMATO_606.md, tabla del formato de texto)."""
    monto_facturado = registro["monto_servicios"] + registro["monto_bienes"]
    itbis_adelantar = registro["itbis_facturado"] - registro["itbis_costo"]
    campos = [
        registro["rnc_cedula"],
        str(registro["tipo_id"]),
        _fmt_codigo(registro["tipo_bienes_servicios"]),
        registro["ncf"],
        registro.get("ncf_modificado") or "",
        registro["fecha_comprobante"],
        registro.get("fecha_pago") or "",
        _fmt_monto(registro["monto_servicios"]),
        _fmt_monto(registro["monto_bienes"]),
        _fmt_monto(monto_facturado),
        _fmt_monto(registro["itbis_facturado"]),
        _fmt_monto(registro["itbis_retenido"]),
        _fmt_monto(registro["itbis_proporcionalidad"]),
        _fmt_monto(registro["itbis_costo"]),
        _fmt_monto(itbis_adelantar),
        _fmt_monto(registro["itbis_percibido"]),
        _fmt_codigo(registro.get("tipo_retencion_isr")),
        _fmt_monto(registro["monto_retencion_renta"]),
        _fmt_monto(registro["isr_percibido"]),
        _fmt_monto(registro["impuesto_selectivo_consumo"]),
        _fmt_monto(registro["otros_impuestos"]),
        _fmt_monto(registro["monto_propina_legal"]),
        _fmt_codigo(registro["forma_pago"]),
    ]
    return "|".join(campos)


def construir_archivo_606(*, rnc: str, periodo: str, registros: list[dict[str, Any]]) -> bytes:
    """`DGII_F_606_<RNC>_<periodo>.TXT` -- línea de encabezado
    `606|RNC|periodo|cantidad` seguida de una línea por comprobante.
    Terminador de línea CRLF (como la plantilla oficial de Excel, que corre
    en Windows), sin salto de línea final (réplica exacta de la macro, que
    imprime la última línea sin `\\r\\n`)."""
    encabezado = f"606|{rnc}|{periodo}|{len(registros)}"
    lineas = [encabezado] + [construir_linea_detalle(registro) for registro in registros]
    return "\r\n".join(lineas).encode("utf-8")
