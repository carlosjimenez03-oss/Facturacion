import uuid
from decimal import Decimal

from services.formato_606.app.generador import construir_archivo_606, construir_linea_detalle


def _registro(**overrides) -> dict:
    base = {
        "id": uuid.uuid4(),
        "rnc_cedula": "131234567",
        "tipo_id": 1,
        "tipo_bienes_servicios": 2,
        "ncf": "E310000000001",
        "ncf_modificado": None,
        "fecha_comprobante": "20260115",
        "fecha_pago": None,
        "monto_servicios": Decimal("400.00"),
        "monto_bienes": Decimal("600.00"),
        "itbis_facturado": Decimal("180.00"),
        "itbis_retenido": Decimal("0"),
        "itbis_proporcionalidad": Decimal("0"),
        "itbis_costo": Decimal("0"),
        "itbis_percibido": Decimal("0"),
        "tipo_retencion_isr": None,
        "monto_retencion_renta": Decimal("0"),
        "isr_percibido": Decimal("0"),
        "impuesto_selectivo_consumo": Decimal("0"),
        "otros_impuestos": Decimal("0"),
        "monto_propina_legal": Decimal("0"),
        "forma_pago": 2,
    }
    base.update(overrides)
    return base


def test_construir_linea_detalle_tiene_23_campos():
    linea = construir_linea_detalle(_registro())
    assert len(linea.split("|")) == 23


def test_construir_linea_detalle_orden_y_formato_exactos():
    linea = construir_linea_detalle(_registro())
    campos = linea.split("|")
    assert campos[0] == "131234567"  # RNC/Cédula
    assert campos[1] == "1"  # Tipo Id
    assert campos[2] == "02"  # Tipo de Bienes, con cero a la izquierda
    assert campos[3] == "E310000000001"  # NCF
    assert campos[4] == ""  # NCF modificado (vacío)
    assert campos[5] == "20260115"  # Fecha Comprobante
    assert campos[6] == ""  # Fecha Pago (vacía)
    assert campos[7] == "400.00"  # Monto Servicios
    assert campos[8] == "600.00"  # Monto Bienes
    assert campos[9] == "1000.00"  # Total Monto Facturado = 400 + 600
    assert campos[10] == "180.00"  # ITBIS Facturado
    assert campos[14] == "180.00"  # ITBIS por Adelantar = facturado - costo (180-0)
    assert campos[22] == "02"  # Forma de Pago, con cero a la izquierda


def test_construir_linea_detalle_itbis_por_adelantar_resta_el_costo():
    registro = _registro(itbis_facturado=Decimal("180.00"), itbis_costo=Decimal("50.00"))
    campos = construir_linea_detalle(registro).split("|")
    assert campos[14] == "130.00"


def test_construir_linea_detalle_tipo_retencion_ausente_queda_vacio():
    campos = construir_linea_detalle(_registro(tipo_retencion_isr=None)).split("|")
    assert campos[16] == ""


def test_construir_linea_detalle_tipo_retencion_presente_con_cero():
    campos = construir_linea_detalle(_registro(tipo_retencion_isr=2)).split("|")
    assert campos[16] == "02"


def test_construir_archivo_606_encabezado():
    contenido = construir_archivo_606(rnc="131234567", periodo="202601", registros=[_registro()])
    primera_linea = contenido.decode("utf-8").split("\r\n")[0]
    assert primera_linea == "606|131234567|202601|1"


def test_construir_archivo_606_una_linea_por_registro():
    registros = [_registro(ncf="E310000000001"), _registro(ncf="E310000000002")]
    contenido = construir_archivo_606(rnc="131234567", periodo="202601", registros=registros)
    lineas = contenido.decode("utf-8").split("\r\n")
    assert len(lineas) == 3  # encabezado + 2 detalles


def test_construir_archivo_606_sin_salto_de_linea_final():
    contenido = construir_archivo_606(rnc="131234567", periodo="202601", registros=[_registro()])
    assert not contenido.endswith(b"\r\n")
    assert not contenido.endswith(b"\n")


def test_construir_archivo_606_vacio_solo_encabezado():
    contenido = construir_archivo_606(rnc="131234567", periodo="202601", registros=[])
    assert contenido.decode("utf-8") == "606|131234567|202601|0"
