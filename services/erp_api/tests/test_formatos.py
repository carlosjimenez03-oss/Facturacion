"""Formatos 607/608 (services/erp_api/app/formatos.py): mapeo de un e-CF a
los 23 campos del 607, regla de facturas de consumo < RD$250,000, archivos
TXT con el layout exacto de las plantillas oficiales y rangos ANECF."""
from datetime import date
from decimal import Decimal

from services.erp_api.app import formatos


def _ecf(tipo=31, encf="E310000000001", fecha="05-10-2026", items=None, totales=None, id_doc=None,
         comprador=None, referencia=None):
    datos = {
        "Encabezado": {
            "IdDoc": {"TipoeCF": str(tipo), "eNCF": encf, "TipoIngresos": "01", "TipoPago": "1", **(id_doc or {})},
            "Emisor": {"RNCEmisor": "131897542", "FechaEmision": fecha},
            "Comprador": comprador if comprador is not None else {
                "RNCComprador": "101-67291-9", "RazonSocialComprador": "Ferretería Ozama SRL"},
            "Totales": totales or {},
        },
        "DetallesItems": {"Item": items or []},
    }
    if referencia:
        datos["InformacionReferencia"] = referencia
    return {"id": "f-1", "tipo_ecf": tipo, "encf": encf, "datos": datos}


def _item(indicador, monto):
    return {"IndicadorFacturacion": indicador, "MontoItem": monto}


def test_fila_607_credito_fiscal_con_formas_de_pago():
    factura = _ecf(
        items=[_item("1", "10000.00"), _item("4", "500.00")],
        totales={"MontoGravadoTotal": "10000.00", "MontoExento": "500.00", "TotalITBIS": "1800.00",
                 "MontoTotal": "12300.00"},
        id_doc={"TipoPago": "2", "TablaFormasPago": {"FormaDePago": [
            {"FormaPago": "1", "MontoPago": "2300.00"}, {"FormaPago": "4", "MontoPago": "10000.00"}]}},
    )

    fila = formatos.fila_607(factura)

    assert fila["rnc_cedula"] == "101672919"
    assert fila["tipo_id"] == 1
    assert fila["tipo_ingreso"] == "01"
    assert fila["fecha_comprobante"] == date(2026, 10, 5)
    assert fila["monto_facturado"] == Decimal("10500.00")
    assert fila["itbis_facturado"] == Decimal("1800.00")
    assert fila["efectivo"] == Decimal("2300.00")
    assert fila["venta_credito"] == Decimal("10000.00")
    assert fila["cliente_nombre"] == "Ferretería Ozama SRL"
    assert formatos.errores_fila_607(fila) == ["NCF inválido para el 607 (debe tener 11 o 19 posiciones)"]


def test_fila_607_sin_tabla_de_formas_de_pago_usa_tipo_pago():
    credito = _ecf(items=[_item("1", "100")], totales={"TotalITBIS": "18", "MontoTotal": "118"},
                   id_doc={"TipoPago": "2"})
    contado = _ecf(items=[_item("1", "100")], totales={"TotalITBIS": "18", "MontoTotal": "118"})

    assert formatos.fila_607(credito)["venta_credito"] == Decimal("118.00")
    assert formatos.fila_607(contado)["efectivo"] == Decimal("118.00")


def test_fila_607_nota_de_credito_y_cedula_y_pasaporte():
    nota = _ecf(tipo=34, encf="E340000000001", items=[_item("1", "200")],
                totales={"TotalITBIS": "36", "MontoTotal": "236"},
                comprador={"RNCComprador": "00112345678"},
                referencia={"NCFModificado": "E310000000001", "CodigoModificacion": "3"})
    extranjero = _ecf(tipo=46, comprador={"IdentificadorExtranjero": "AB 123456"},
                      items=[_item("3", "5000")], totales={"MontoTotal": "5000"})

    fila_nota = formatos.fila_607(nota)
    fila_ext = formatos.fila_607(extranjero)

    assert (fila_nota["tipo_id"], fila_nota["ncf_modificado"]) == (2, "E310000000001")
    assert all(fila_nota[c] == 0 for c in formatos.COLUMNAS_FORMA_PAGO)
    assert (fila_ext["rnc_cedula"], fila_ext["tipo_id"]) == ("AB123456", 3)
    assert fila_ext["monto_facturado"] == Decimal("5000.00")


def test_desglose_itbis_incluido_y_totales_explicitos():
    incluido = _ecf(items=[_item("1", "118.00")], id_doc={"IndicadorMontoGravado": "1"})
    explicito = _ecf(totales={"MontoGravadoI1": "1000", "MontoGravadoI2": "500", "TotalITBIS1": "180",
                              "TotalITBIS2": "80", "MontoTotal": "1760"})

    assert formatos.desglose_por_tasa(incluido["datos"])["g18"] == Decimal("100.00")
    d = formatos.desglose_por_tasa(explicito["datos"])
    assert (d["g18"], d["g16"], d["i18"], d["i16"]) == (1000, 500, 180, 80)


def test_impuestos_adicionales_propina_y_selectivo():
    factura = _ecf(items=[_item("1", "1000")], totales={
        "TotalITBIS": "180", "MontoTotal": "1330", "ImpuestosAdicionales": {"ImpuestoAdicional": [
            {"TipoImpuesto": "001", "OtrosImpuestosAdicionales": "100"},
            {"TipoImpuesto": "006", "MontoImpuestoSelectivoConsumoEspecifico": "30",
             "MontoImpuestoSelectivoConsumoAdvalorem": "20"}]}})

    fila = formatos.fila_607(factura)

    assert (fila["monto_propina_legal"], fila["impuesto_selectivo_consumo"]) == (100, 50)


def test_armar_607_resume_consumo_menor_al_tope_y_resta_notas():
    consumo = _ecf(tipo=32, encf="E320000000001", comprador={}, items=[_item("1", "1000")],
                   totales={"TotalITBIS": "180", "MontoTotal": "1180"})
    consumo_grande = _ecf(tipo=32, encf="E320000000002", comprador={}, items=[_item("1", "300000")],
                          totales={"TotalITBIS": "54000", "MontoTotal": "354000"})
    factura = _ecf(items=[_item("1", "1000")], totales={"TotalITBIS": "180", "MontoTotal": "1180"})
    nota = _ecf(tipo=34, encf="E340000000001", items=[_item("1", "100")],
                totales={"TotalITBIS": "18", "MontoTotal": "118"})

    filas, totales, advertencias = formatos.armar_607([consumo, consumo_grande, factura, nota])

    assert [f["ncf"] for f in filas] == ["E320000000002", "E310000000001", "E340000000001"]
    assert totales["cantidad"] == 3
    assert totales["monto_facturado"] == Decimal("300900.00")
    assert totales["itbis_facturado"] == Decimal("54162.00")
    assert any("Resumen General de Facturas de Consumo" in a and "1,180.00" in a for a in advertencias)
    assert any("E320000000002" in a and "sin RNC" in a for a in advertencias)


def _fila_b(**cambios):
    fila = formatos.fila_607(_ecf(items=[_item("1", "1000")], totales={"TotalITBIS": "180", "MontoTotal": "1180"},
                                  id_doc={"TablaFormasPago": {"FormaDePago": [
                                      {"FormaPago": "2", "MontoPago": "1180"}]}}))
    fila.update({"ncf": "B0100000001", **cambios})
    return fila


def test_txt_607_layout_oficial():
    fila = _fila_b()
    fila2 = _fila_b(ncf="B0100000002", tipo_ingreso="04", itbis_retenido_terceros=Decimal("54.00"),
                    fecha_retencion=date(2026, 10, 20))

    contenido = formatos.construir_archivo_607(rnc="131897542", periodo="202610", filas=[fila, fila2])

    assert formatos.errores_fila_607(fila) == []
    assert contenido == (
        b"607|131897542|202610|2\r\n"
        b"101672919|1|B0100000001||1|20261005||1000.00|180.00|0.00|0.00|0.00|0.00|0.00|0.00|0.00"
        b"|0.00|1180.00|0.00|0.00|0.00|0.00|0.00\r\n"
        b"101672919|1|B0100000002||4|20261005|20261020|1000.00|180.00|54.00|0.00|0.00|0.00|0.00|0.00|0.00"
        b"|0.00|1180.00|0.00|0.00|0.00|0.00|0.00"
    )
    assert formatos.nombre_archivo("607", "131897542", "202610") == "DGII_F_607_131897542_202610.TXT"


def test_validaciones_607():
    assert "Falta el RNC/cédula del comprador" in formatos.errores_fila_607(_fila_b(rnc_cedula=""))
    sin_pago = _fila_b(cheque_transferencia=Decimal("0"))
    assert "Debe tener al menos una forma de pago (casillas 17 a 23)" in formatos.errores_fila_607(sin_pago)
    retencion = _fila_b(itbis_retenido_terceros=Decimal("10"), fecha_retencion=None)
    assert any("fecha de retención" in e for e in formatos.errores_fila_607(retencion))
    # Nota de crédito (B04) sobre una factura de consumo (B02) bajo el tope: sin identificación.
    nota = _fila_b(ncf="B0400000001", ncf_modificado="B0200000001", rnc_cedula="", cheque_transferencia=Decimal("0"))
    assert formatos.errores_fila_607(nota) == []


def test_txt_608_layout_oficial_y_validacion_ncf():
    filas = [
        {"ncf": "B0100000005", "fecha_comprobante": date(2026, 10, 3), "tipo_anulacion": "04"},
        {"ncf": "B0200000099", "fecha_comprobante": date(2026, 10, 9), "tipo_anulacion": "10"},
    ]

    contenido = formatos.construir_archivo_608(rnc="131897542", periodo="202610", filas=filas)

    assert contenido == b"608|131897542|202610|2\r\nB0100000005|20261003|04\r\nB0200000099|20261009|10"
    assert formatos.validar_ncf_608("B0100000005")
    assert formatos.validar_ncf_608("A010010010100000001")
    assert not formatos.validar_ncf_608("E310000000001")
    assert not formatos.validar_ncf_608("B0500000001")
    assert list(formatos.TIPOS_ANULACION) == [f"{n:02d}" for n in range(1, 11)]


def test_expandir_rango_encf():
    assert formatos.expandir_rango("E310000000009", "E310000000011", 100) == [
        "E310000000009", "E310000000010", "E310000000011"]
    assert formatos.expandir_rango("E310000000001", "E310000000100", 2) == ["E310000000001", "E310000000002"]
    assert formatos.cantidad_rango("E310000000001", "E310000000100") == 100
    assert formatos.expandir_rango("E310000000005", "E320000000009", 10) == []
    assert formatos.expandir_rango("E310000000009", "E310000000001", 10) == []
