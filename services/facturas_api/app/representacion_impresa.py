"""Representación Impresa (RI) de un e-CF: PDF generado a partir de la
factura firmada, siguiendo las reglas de la sección 18 del "Informe Técnico
Comprobante Fiscal Electrónico" (Orden y Distribución de la Información en
la RI, código QR, Código de Seguridad) y del formato descrito en "Formato
Comprobante Fiscal Electrónico (e-CF)" (qué campos van en palabras).

Alcance de esta implementación (documentado explícitamente, ver también el
mismo patrón de "aproximación conservadora" en `frontend/src/lib/tipoConfig.ts`):
- Cubre el layout de una sola página, que es el caso real de la enorme
  mayoría de facturas (ver los 10 modelos ilustrativos del Informe Técnico,
  9 de los cuales son de una página). Si el detalle de ítems no cabe en una
  página, la tabla de reportlab se parte automáticamente entre páginas (con
  la fila de encabezado repetida) y el bloque de QR + Código de Seguridad +
  Fecha de Firma se dibuja, como exige la norma, únicamente en la última
  página; el número de página ("Página No. X de Y") sí se repite en todas.
  Lo que NO se implementa son los subtotales POR PÁGINA intermedios (sección
  "Paginación" del Formato e-CF) -- son de obligatoriedad condicional
  (código 2, no 1), y su cálculo exacto requeriría un segundo paso de layout
  fuera del alcance razonable de esta primera versión.
- "Unidad de Medida" se traduce a palabras solo para el subconjunto de
  códigos ya cubierto en `frontend/src/lib/catalogos.ts` (UNIDADES_MEDIDA);
  fuera de ese subconjunto se imprime el código tal cual.
"""
from __future__ import annotations

import io
from typing import Any

import qrcode
from lxml import etree
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas as pdf_canvas
from reportlab.platypus import (
    HRFlowable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from libs.ecf_common.environments import DGIIEnvironment
from services.consulta_timbre.app.dgii_client import ConsultaTimbreDGIIClient

_DSIG_NS = {"ds": "http://www.w3.org/2000/09/xmldsig#"}

TIPOS_ECF_LABEL: dict[int, str] = {
    31: "Factura de Crédito Fiscal Electrónica",
    32: "Factura de Consumo Electrónica",
    33: "Nota de Débito Electrónica",
    34: "Nota de Crédito Electrónica",
    41: "Compras Electrónico",
    43: "Gastos Menores Electrónico",
    44: "Regímenes Especiales Electrónico",
    45: "Gubernamental Electrónico",
    46: "Comprobante de Exportaciones Electrónico",
    47: "Comprobante para Pagos al Exterior Electrónico",
}

CODIGOS_MODIFICACION_LABEL: dict[str, str] = {
    "1": "Anula el NCF modificado",
    "2": "Corrige texto del comprobante modificado",
    "3": "Corrige montos del NCF modificado",
    "4": "Reemplazo NCF emitido en contingencia",
    "5": "Referencia Factura Consumo Electrónica",
}

# Mismo subconjunto que frontend/src/lib/catalogos.ts (UNIDADES_MEDIDA) --
# mantenerlos en sincronía si se amplía uno u otro.
UNIDADES_MEDIDA_LABEL: dict[str, str] = {
    "43": "Unidad",
    "34": "Pieza",
    "6": "Caja/Cajón",
    "13": "Docena",
    "19": "Hora",
    "26": "Metro",
    "21": "Kilogramo",
    "23": "Libra",
    "24": "Litro",
}

_LINEA_AZUL = colors.HexColor("#4a6b8a")


class RepresentacionImpresaError(Exception):
    """La factura no tiene los datos mínimos (XML firmado) para generar la RI."""


def codigo_seguridad_desde_xml_firmado(xml_firmado: str) -> str:
    """Primeros seis (6) caracteres del `SignatureValue` de la firma digital.

    Según la sección 18.2.3 del Informe Técnico: "Código de Seguridad:
    corresponde a los primeros seis (6) dígitos del hash generado en el
    SignatureValue de la firma digital del e-CF".
    """
    doc = etree.fromstring(xml_firmado.encode("utf-8"))
    nodos = doc.findall(".//ds:SignatureValue", namespaces=_DSIG_NS)
    if not nodos or not nodos[0].text:
        raise RepresentacionImpresaError(
            "El XML firmado no contiene un SignatureValue -- no se puede calcular "
            "el Código de Seguridad."
        )
    valor = "".join(nodos[0].text.split())  # normaliza saltos de línea del base64
    return valor[:6]


def fecha_hora_firma_desde_xml(xml: str) -> str:
    """Extrae `<FechaHoraFirma>` (formato DD-MM-YYYY HH:MM:SS) del XML."""
    doc = etree.fromstring(xml.encode("utf-8"))
    nodos = doc.findall(".//FechaHoraFirma")
    if not nodos or not nodos[0].text:
        raise RepresentacionImpresaError("El XML no contiene FechaHoraFirma.")
    return nodos[0].text


def _generar_imagen_qr(url: str) -> ImageReader:
    qr = qrcode.QRCode(border=1)
    qr.add_data(url)
    qr.make(fit=True)
    imagen = qr.make_image(fill_color="black", back_color="white")
    buffer = io.BytesIO()
    imagen.save(buffer, format="PNG")
    buffer.seek(0)
    return ImageReader(buffer)


def _texto(valor: Any) -> str:
    return "" if valor is None else str(valor)


def _decimal(valor: Any) -> str:
    if valor is None:
        return "0.00"
    try:
        return f"{float(valor):,.2f}"
    except (TypeError, ValueError):
        return _texto(valor)


def _monto_itbis_item(item: dict[str, Any]) -> float:
    """El Formato de e-CF no guarda un monto de ITBIS por línea explícito
    (solo el `IndicadorFacturacion` con la tasa); se recalcula aquí a partir
    del monto del ítem y la tasa que indica el indicador, replicando la
    misma lógica que `frontend/src/lib/format.ts::calcularTotales`."""
    tasas = {"1": 0.18, "2": 0.16, "3": 0.0, "4": 0.0}
    indicador = str(item.get("IndicadorFacturacion") or "")
    tasa = tasas.get(indicador, 0.0)
    try:
        monto = float(item.get("MontoItem") or 0)
    except (TypeError, ValueError):
        return 0.0
    return monto * tasa


def _linea_horizontal() -> HRFlowable:
    return HRFlowable(width="100%", thickness=1, color=_LINEA_AZUL)


def generar_pdf(factura: dict[str, Any], ambiente: DGIIEnvironment) -> bytes:
    """Genera la Representación Impresa (PDF) de una factura firmada.

    `factura` es el dict que devuelve el repositorio (mismas claves que
    `FacturaDetalle`: tipo_ecf, encf, rnc_emisor, datos, xml_generado,
    xml_firmado, ...). Requiere que `xml_firmado` exista.
    """
    xml_firmado = factura.get("xml_firmado")
    if not xml_firmado:
        raise RepresentacionImpresaError(
            "La factura debe estar firmada (POST /facturas/{id}/firmar) antes de "
            "generar su Representación Impresa."
        )

    datos = factura["datos"]
    encabezado = datos.get("Encabezado", {})
    id_doc = encabezado.get("IdDoc", {})
    emisor = encabezado.get("Emisor", {})
    comprador = encabezado.get("Comprador", {}) or {}
    totales = encabezado.get("Totales", {})
    referencia = datos.get("InformacionReferencia") or {}
    items = datos.get("DetallesItems", {}).get("Item", [])

    codigo_seguridad = codigo_seguridad_desde_xml_firmado(xml_firmado)
    fecha_firma = fecha_hora_firma_desde_xml(xml_firmado)

    qr_url = ConsultaTimbreDGIIClient(ambiente).construir_url_ecf(
        rnc_emisor=factura["rnc_emisor"],
        rnc_comprador=_texto(comprador.get("RNCComprador")),
        encf=factura["encf"] or "",
        fecha_emision=_texto(emisor.get("FechaEmision")),
        monto_total=_texto(totales.get("MontoTotal")),
        fecha_firma=fecha_firma,
        codigo_seguridad=codigo_seguridad,
    )
    imagen_qr = _generar_imagen_qr(qr_url)

    estilos = {
        "normal": ParagraphStyle("normal", fontName="Helvetica", fontSize=9, leading=12),
        "negrita": ParagraphStyle("negrita", fontName="Helvetica-Bold", fontSize=9, leading=12),
        "titulo": ParagraphStyle(
            "titulo", fontName="Helvetica-Bold", fontSize=12, leading=15, alignment=2
        ),
        "derecha": ParagraphStyle("derecha", fontName="Helvetica", fontSize=9, alignment=2),
        "derecha_negrita": ParagraphStyle(
            "derecha_negrita", fontName="Helvetica-Bold", fontSize=9, alignment=2
        ),
        "derecha_chico": ParagraphStyle("derecha_chico", fontName="Helvetica", fontSize=8, alignment=2),
    }

    story: list[Any] = []

    # --- Encabezado: emisor (izquierda) / tipo de documento (derecha) ---
    tipo_ecf = int(factura["tipo_ecf"])
    tipo_label = TIPOS_ECF_LABEL.get(tipo_ecf, str(tipo_ecf))

    partes_emisor: list[Any] = []
    if emisor.get("NombreComercial"):
        partes_emisor.append(Paragraph(_texto(emisor["NombreComercial"]), estilos["negrita"]))
    partes_emisor.append(Paragraph(_texto(emisor.get("RazonSocialEmisor")), estilos["negrita"]))
    if emisor.get("Sucursal"):
        partes_emisor.append(Paragraph(_texto(emisor["Sucursal"]), estilos["normal"]))
    partes_emisor.append(Paragraph(f"RNC {_texto(factura['rnc_emisor'])}", estilos["normal"]))
    if emisor.get("DireccionEmisor"):
        partes_emisor.append(
            Paragraph(f"Dirección: {_texto(emisor['DireccionEmisor'])}", estilos["normal"])
        )
    partes_emisor.append(
        Paragraph(f"Fecha Emisión: {_texto(emisor.get('FechaEmision'))}", estilos["normal"])
    )

    partes_doc: list[Any] = [Paragraph(tipo_label, estilos["titulo"])]
    partes_doc.append(Paragraph(f"e-NCF: {_texto(factura['encf'])}", estilos["derecha_negrita"]))
    if id_doc.get("FechaVencimientoSecuencia"):
        partes_doc.append(
            Paragraph(
                f"Fecha Vencimiento: {_texto(id_doc['FechaVencimientoSecuencia'])}",
                estilos["derecha"],
            )
        )
    if referencia.get("NCFModificado"):
        partes_doc.append(
            Paragraph(f"NCF Modificado: {_texto(referencia['NCFModificado'])}", estilos["derecha"])
        )
    if referencia.get("CodigoModificacion"):
        etiqueta = CODIGOS_MODIFICACION_LABEL.get(
            _texto(referencia["CodigoModificacion"]), _texto(referencia["CodigoModificacion"])
        )
        partes_doc.append(Paragraph(etiqueta, estilos["derecha_chico"]))

    story.append(
        Table(
            [[partes_emisor, partes_doc]],
            colWidths=[9 * cm, 8 * cm],
            style=TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]),
        )
    )
    story.append(Spacer(1, 0.3 * cm))
    story.append(_linea_horizontal())
    story.append(Spacer(1, 0.3 * cm))

    # --- Cliente ---
    if comprador.get("RazonSocialComprador") or comprador.get("RNCComprador"):
        if comprador.get("RazonSocialComprador"):
            story.append(
                Paragraph(
                    f"Razón Social Cliente: {_texto(comprador['RazonSocialComprador'])}",
                    estilos["negrita"],
                )
            )
        if comprador.get("RNCComprador"):
            story.append(
                Paragraph(f"RNC Cliente: {_texto(comprador['RNCComprador'])}", estilos["negrita"])
            )
        story.append(Spacer(1, 0.4 * cm))

    # --- Detalle de bienes o servicios ---
    tiene_isc = any(
        item.get("MontoImpuestoSelectivoConsumoEspecifico")
        or item.get("MontoImpuestoSelectivoConsumoAdvalorem")
        for item in items
    )
    tiene_descuento = any(item.get("DescuentoMonto") for item in items)

    encabezados = ["Cantidad", "Descripción", "Unidad de Medida", "Precio"]
    if tiene_isc:
        encabezados.append("ISC")
    encabezados.append("ITBIS")
    if tiene_descuento:
        encabezados.append("Descuento")
    encabezados.append("Valor")

    filas: list[list[Any]] = [encabezados]
    for item in items:
        descripcion = _texto(item.get("NombreItem"))
        if _texto(item.get("IndicadorFacturacion")) == "4":
            descripcion = f"E {descripcion}"
        unidad_codigo = _texto(item.get("UnidadMedida"))
        unidad = UNIDADES_MEDIDA_LABEL.get(unidad_codigo, unidad_codigo)

        fila: list[Any] = [
            _texto(item.get("CantidadItem")),
            Paragraph(descripcion, estilos["normal"]),
            unidad,
            _decimal(item.get("PrecioUnitarioItem")),
        ]
        if tiene_isc:
            isc = float(item.get("MontoImpuestoSelectivoConsumoEspecifico") or 0) + float(
                item.get("MontoImpuestoSelectivoConsumoAdvalorem") or 0
            )
            fila.append(_decimal(isc))
        fila.append(_decimal(_monto_itbis_item(item)))
        if tiene_descuento:
            fila.append(_decimal(item.get("DescuentoMonto")))
        fila.append(_decimal(item.get("MontoItem")))
        filas.append(fila)

    columnas_numero = len(encabezados)
    tabla_items = Table(filas, repeatRows=1)
    tabla_items.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef2f5")),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("ALIGN", (0, 0), (0, -1), "RIGHT"),
                ("ALIGN", (3, 0), (columnas_numero - 1, -1), "RIGHT"),
                ("LINEBELOW", (0, 0), (-1, 0), 0.75, _LINEA_AZUL),
                ("LINEBELOW", (0, -1), (-1, -1), 0.75, _LINEA_AZUL),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    story.append(tabla_items)
    story.append(Spacer(1, 0.4 * cm))

    # --- Totales ---
    filas_totales: list[list[str]] = []
    if totales.get("MontoGravadoTotal"):
        filas_totales.append(["Subtotal Gravado:", _decimal(totales["MontoGravadoTotal"])])
    if totales.get("MontoExento"):
        filas_totales.append(["Subtotal Exento:", _decimal(totales["MontoExento"])])
    if totales.get("MontoImpuestoAdicional"):
        filas_totales.append(["Total ISC:", _decimal(totales["MontoImpuestoAdicional"])])
    if totales.get("TotalITBIS"):
        filas_totales.append(["Total ITBIS:", _decimal(totales["TotalITBIS"])])
    filas_totales.append(["Total:", _decimal(totales.get("MontoTotal"))])

    tabla_totales = Table(filas_totales, colWidths=[4 * cm, 3 * cm])
    tabla_totales.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
                ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("LINEABOVE", (0, -1), (-1, -1), 0.75, _LINEA_AZUL),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]
        )
    )
    story.append(Table([["", tabla_totales]], colWidths=[10.5 * cm, 6.5 * cm]))

    # --- Canvas con numeración y el bloque de QR SOLO en la última página ---
    # (requiere dos pasadas: reportlab no sabe cuántas páginas habrá hasta
    # terminar de fluir el `story`, así que cada página se guarda y se vuelve
    # a dibujar al cerrar el documento, cuando ya se conoce el total).
    class _CanvasConQR(pdf_canvas.Canvas):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._paginas_guardadas: list[dict[str, Any]] = []

        def showPage(self):
            self._paginas_guardadas.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total = len(self._paginas_guardadas)
            for numero, estado in enumerate(self._paginas_guardadas, start=1):
                self.__dict__.update(estado)
                if total > 1:
                    self.setFont("Helvetica", 8)
                    self.drawRightString(
                        letter[0] - 2 * cm, 1.3 * cm, f"Página No. {numero} de {total}"
                    )
                if numero == total:
                    self._dibujar_bloque_qr()
                super().showPage()
            super().save()

        def _dibujar_bloque_qr(self) -> None:
            x = 2 * cm
            y = 1.2 * cm
            tamano = 2.5 * cm
            self.drawImage(imagen_qr, x, y, width=tamano, height=tamano, preserveAspectRatio=True)
            self.setFont("Helvetica", 7.5)
            self.drawString(x + tamano + 0.3 * cm, y + tamano - 0.35 * cm, f"Código de Seguridad: {codigo_seguridad}")
            self.drawString(x + tamano + 0.3 * cm, y + tamano - 0.7 * cm, f"Fecha de Firma Digital: {fecha_firma}")

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        leftMargin=2 * cm,
        rightMargin=2 * cm,
        topMargin=2 * cm,
        bottomMargin=4 * cm,  # espacio reservado para el bloque de QR
    )
    doc.build(story, canvasmaker=_CanvasConQR)
    return buffer.getvalue()
