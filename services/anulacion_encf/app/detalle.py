"""Detalle de un ANECF (rangos de e-NCF anulados y fecha de la anulación)
para guardarlo junto con la respuesta de la DGII en facturacion.envios_dgii.

La DGII no devuelve qué secuencias se anularon (solo `{rnc, codigo, nombre,
mensajes}`), así que sin esto la auditoría no dice qué e-NCF quedaron
anulados. erp_api lo lee para listar los e-NCF anulados del período en el
Formato 608 (ver services/erp_api/app/impuestos_repository.py).

Estructura según "Formato Anulación de e-NCF v1.0" de la DGII: Encabezado/
FechaHoraAnulacioneNCF (dd-MM-AAAA HH:mm:ss) y DetalleAnulacion/Anulacion/
TipoeCF + TablaRangoSecuenciasAnuladaseNCF con pares SecuenciaeNCFDesde /
SecuenciaeNCFHasta. Se busca por nombre local, sin depender del namespace.
"""
from __future__ import annotations

from typing import Any

from lxml import etree


def _local(elemento: etree._Element) -> str:
    return etree.QName(elemento).localname if isinstance(elemento.tag, str) else ""


def extraer_detalle_anulacion(xml_bytes: bytes) -> dict[str, Any]:
    """{"fecha_hora_anulacion": str | None, "rangos": [{"tipo_ecf", "desde", "hasta"}]}.
    Best-effort: un XML ilegible devuelve rangos vacíos (el envío a la DGII
    no depende de esto)."""
    try:
        raiz = etree.fromstring(xml_bytes, parser=etree.XMLParser(resolve_entities=False, no_network=True))
    except etree.XMLSyntaxError:
        return {"fecha_hora_anulacion": None, "rangos": []}
    fecha = next((e.text for e in raiz.iter() if _local(e) == "FechaHoraAnulacioneNCF"), None)
    rangos: list[dict[str, str | None]] = []
    for anulacion in (e for e in raiz.iter() if _local(e) == "Anulacion"):
        tipo = next((e.text for e in anulacion.iter() if _local(e) == "TipoeCF"), None)
        desde: str | None = None
        for elemento in anulacion.iter():
            nombre = _local(elemento)
            if nombre == "SecuenciaeNCFDesde":
                desde = (elemento.text or "").strip()
            elif nombre == "SecuenciaeNCFHasta" and desde is not None:
                rangos.append({"tipo_ecf": (tipo or "").strip() or None, "desde": desde, "hasta": (elemento.text or "").strip()})
                desde = None
    return {"fecha_hora_anulacion": (fecha or "").strip() or None, "rangos": rangos}
