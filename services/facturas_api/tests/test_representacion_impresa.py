import uuid

import pytest

from libs.ecf_common.auth.signing import CredencialFirma, firmar_xml
from libs.ecf_common.environments import DGIIEnvironment
from services.facturas_api.app import representacion_impresa as ri
from services.facturas_api.app.xml_builder import construir_xml_ecf


def _datos(**overrides) -> dict:
    base = {
        "Encabezado": {
            "Version": "1.0",
            "IdDoc": {
                "TipoeCF": "31",
                "eNCF": "E310000000001",
                "FechaVencimientoSecuencia": "31-12-2026",
                "TipoIngresos": "01",
                "TipoPago": "1",
            },
            "Emisor": {
                "RNCEmisor": "131234567",
                "RazonSocialEmisor": "Empresa Demo SRL",
                "DireccionEmisor": "Calle Falsa 123",
                "FechaEmision": "02-09-2026",
            },
            "Comprador": {"RNCComprador": "101672919", "RazonSocialComprador": "Cliente Demo SRL"},
            "Totales": {
                "MontoGravadoTotal": "1000.00",
                "TotalITBIS": "180.00",
                "MontoTotal": "1180.00",
            },
        },
        "DetallesItems": {
            "Item": [
                {
                    "NumeroLinea": "1",
                    "IndicadorFacturacion": "1",
                    "NombreItem": "Producto X",
                    "IndicadorBienoServicio": "1",
                    "CantidadItem": "1.00",
                    "UnidadMedida": "43",
                    "PrecioUnitarioItem": "1000.0000",
                    "MontoItem": "1000.00",
                }
            ]
        },
    }
    base.update(overrides)
    return base


@pytest.fixture
def xml_firmado_prueba(credencial_prueba: CredencialFirma) -> str:
    xml_sin_firmar = construir_xml_ecf(31, _datos())
    return firmar_xml(xml_sin_firmar, credencial_prueba).decode("utf-8")


def _factura_firmada(xml_firmado: str, **overrides) -> dict:
    base = {
        "id": str(uuid.uuid4()),
        "tipo_ecf": 31,
        "encf": "E310000000001",
        "rnc_emisor": "131234567",
        "datos": _datos(),
        "xml_firmado": xml_firmado,
    }
    base.update(overrides)
    return base


def test_codigo_seguridad_desde_xml_firmado(xml_firmado_prueba):
    codigo = ri.codigo_seguridad_desde_xml_firmado(xml_firmado_prueba)
    assert len(codigo) == 6


def test_codigo_seguridad_sin_signature_value():
    with pytest.raises(ri.RepresentacionImpresaError):
        ri.codigo_seguridad_desde_xml_firmado("<ECF></ECF>")


def test_fecha_hora_firma_desde_xml(xml_firmado_prueba):
    fecha = ri.fecha_hora_firma_desde_xml(xml_firmado_prueba)
    assert len(fecha) == len("31-12-2026 00:00:00")


def test_fecha_hora_firma_ausente():
    with pytest.raises(ri.RepresentacionImpresaError):
        ri.fecha_hora_firma_desde_xml("<ECF></ECF>")


def test_generar_pdf_sin_firmar_lanza_error():
    factura = _factura_firmada(None)
    factura["xml_firmado"] = None
    with pytest.raises(ri.RepresentacionImpresaError):
        ri.generar_pdf(factura, DGIIEnvironment.TESTECF)


def test_generar_pdf_smoke(xml_firmado_prueba):
    factura = _factura_firmada(xml_firmado_prueba)
    pdf_bytes = ri.generar_pdf(factura, DGIIEnvironment.TESTECF)
    assert pdf_bytes.startswith(b"%PDF")
    assert len(pdf_bytes) > 1000


def test_generar_pdf_con_isc_descuento_y_referencia(xml_firmado_prueba):
    """Ejercita las columnas condicionales (ISC, Descuento) y el bloque de
    'comprobante que modifica' -- todas ramas que no cubre el caso simple."""
    datos = _datos()
    datos["DetallesItems"]["Item"][0]["MontoImpuestoSelectivoConsumoEspecifico"] = "10.00"
    datos["DetallesItems"]["Item"][0]["DescuentoMonto"] = "5.00"
    datos["InformacionReferencia"] = {
        "NCFModificado": "E310000000000",
        "CodigoModificacion": "1",
    }
    factura = _factura_firmada(xml_firmado_prueba, datos=datos)
    pdf_bytes = ri.generar_pdf(factura, DGIIEnvironment.TESTECF)
    assert pdf_bytes.startswith(b"%PDF")
