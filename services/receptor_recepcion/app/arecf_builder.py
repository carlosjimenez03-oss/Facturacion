"""Construye el ARECF (Acuse de Recibo de e-CF) que `receptor_recepcion`
firma y devuelve al contribuyente que nos envió un e-CF.

Nota (ver docs/dgii/EMISORES_ELECTRONICOS.md, "Recepción (obligatorio)"): el
documento oficial no trae adjunto el XSD del ARECF -- solo un ejemplo. Los
nombres de elemento aquí siguen la misma convención PascalCase que el resto
de XSD de e-CF ya presentes en `services/facturas_api/xsd/`; verificar contra
el XSD oficial del portal de la DGII cuando esté disponible y ajustar si
difiere.
"""
from __future__ import annotations

from datetime import datetime

from lxml import etree

ESTADO_ACEPTADO = "0"


def construir_arecf(*, rnc_emisor: str, rnc_comprador: str, encf: str, estado: str = ESTADO_ACEPTADO) -> bytes:
    """Arma el XML (sin firmar) del acuse de recibo de un e-CF recibido."""
    root = etree.Element("ARECF")
    detalle = etree.SubElement(root, "DetalleAcuseDeRecibo")
    etree.SubElement(detalle, "Version").text = "1.0"
    etree.SubElement(detalle, "RNCEmisor").text = rnc_emisor
    etree.SubElement(detalle, "RNCComprador").text = rnc_comprador
    etree.SubElement(detalle, "eNCF").text = encf
    etree.SubElement(detalle, "Estado").text = estado
    etree.SubElement(detalle, "FechaHoraAcuseRecibo").text = datetime.now().strftime("%d-%m-%Y %H:%M:%S")
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=False)
