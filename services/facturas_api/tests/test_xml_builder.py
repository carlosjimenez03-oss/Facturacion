import pytest

from services.facturas_api.app import xml_builder as xb


def _datos_minimos_31() -> dict:
    return {
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
            "Comprador": {
                "RNCComprador": "101672919",
                "RazonSocialComprador": "Cliente Demo SRL",
            },
            "Totales": {"MontoTotal": "1180.00"},
        },
        "DetallesItems": {
            "Item": [
                {
                    "NumeroLinea": "1",
                    "IndicadorFacturacion": "1",
                    "NombreItem": "Producto X",
                    "IndicadorBienoServicio": "1",
                    "CantidadItem": "1.00",
                    "PrecioUnitarioItem": "1000.0000",
                    "MontoItem": "1000.00",
                }
            ]
        },
    }


@pytest.mark.parametrize("tipo_ecf", sorted(xb.TIPOS_SOPORTADOS))
def test_xsd_carga_sin_error(tipo_ecf):
    """Cada XSD bundleado debe poder cargarse como schema (detecta typos/rutas rotas)."""
    xb._cargar_schema(tipo_ecf)


def test_construir_xml_ecf_agrega_fecha_hora_firma():
    xml_bytes = xb.construir_xml_ecf(31, _datos_minimos_31())
    assert b"<FechaHoraFirma>" in xml_bytes
    assert b"<ECF>" in xml_bytes


def test_construir_xml_ecf_tipo_no_soportado():
    with pytest.raises(ValueError):
        xb.construir_xml_ecf(99, {})


def test_generar_y_validar_ok_tipo_31():
    xml_bytes, errores = xb.generar_y_validar(31, _datos_minimos_31())
    assert errores == []
    assert b"MontoTotal" in xml_bytes


def test_generar_y_validar_detecta_campo_requerido_faltante():
    datos = _datos_minimos_31()
    datos["Encabezado"]["Totales"] = {}  # falta MontoTotal (requerido)
    _, errores = xb.generar_y_validar(31, datos)
    assert errores, "debía reportar error por MontoTotal faltante"


def test_generar_y_validar_detecta_orden_incorrecto():
    """xs:sequence exige orden; invertir dos campos del mismo nivel debe fallar."""
    datos = _datos_minimos_31()
    encabezado = datos["Encabezado"]
    # IdDoc debe ir antes que Emisor según el XSD -- los invertimos.
    reordenado = {"Emisor": encabezado["Emisor"], "IdDoc": encabezado["IdDoc"]}
    reordenado.update({k: v for k, v in encabezado.items() if k not in ("Emisor", "IdDoc")})
    datos["Encabezado"] = reordenado
    _, errores = xb.generar_y_validar(31, datos)
    assert errores, "debía reportar error por orden incorrecto de elementos"


def test_construir_xml_lista_produce_elementos_repetidos():
    datos = _datos_minimos_31()
    datos["DetallesItems"]["Item"].append(
        {
            "NumeroLinea": "2",
            "IndicadorFacturacion": "1",
            "NombreItem": "Producto Y",
            "IndicadorBienoServicio": "1",
            "CantidadItem": "1.00",
            "PrecioUnitarioItem": "180.0000",
            "MontoItem": "180.00",
        }
    )
    xml_bytes = xb.construir_xml_ecf(31, datos)
    assert xml_bytes.count(b"<NumeroLinea>") == 2


def test_validar_contra_xsd_requiere_signature():
    """El XML sin firmar NO debe validar contra `validar_contra_xsd` (falta <Signature>)."""
    xml_bytes = xb.construir_xml_ecf(31, _datos_minimos_31())
    errores = xb.validar_contra_xsd(31, xml_bytes)
    assert errores, "el XSD exige un xs:any final (Signature) que aún no existe"


def test_validar_contra_xsd_pasa_con_signature_presente():
    from lxml import etree

    xml_bytes = xb.construir_xml_ecf(31, _datos_minimos_31())
    root = etree.fromstring(xml_bytes)
    etree.SubElement(root, "Signature")
    xml_con_signature = etree.tostring(root)
    assert xb.validar_contra_xsd(31, xml_con_signature) == []
