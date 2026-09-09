import uuid
from decimal import Decimal

from services.formato_606.app.validador import validar_registro


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


def test_registro_completo_no_tiene_errores():
    assert validar_registro(_registro()) == []


def test_rnc_invalido():
    errores = validar_registro(_registro(rnc_cedula="123"))
    assert any("RNC o Cédula" in e for e in errores)


def test_tipo_bienes_faltante_es_obligatorio():
    errores = validar_registro(_registro(tipo_bienes_servicios=None))
    assert any("Tipo de Bienes" in e for e in errores)


def test_tipo_bienes_fuera_de_rango():
    errores = validar_registro(_registro(tipo_bienes_servicios=99))
    assert any("Tipo de Bienes" in e for e in errores)


def test_ncf_invalido():
    errores = validar_registro(_registro(ncf="123"))
    assert any("NCF inválido" in e for e in errores)


def test_ncf_modificado_invalido():
    errores = validar_registro(_registro(ncf_modificado="XYZ"))
    assert any("NCF o Documento Modificado" in e for e in errores)


def test_ncf_modificado_vacio_no_es_error():
    errores = validar_registro(_registro(ncf_modificado=None))
    assert not any("Documento Modificado" in e for e in errores)


def test_fecha_comprobante_invalida():
    errores = validar_registro(_registro(fecha_comprobante="2026011"))
    assert any("Fecha Comprobante" in e for e in errores)


def test_itbis_retenido_sin_fecha_pago_es_error():
    errores = validar_registro(_registro(itbis_retenido=Decimal("18.00"), fecha_pago=None))
    assert any("Fecha de Pago" in e for e in errores)


def test_itbis_retenido_con_fecha_pago_no_es_error():
    errores = validar_registro(_registro(itbis_retenido=Decimal("18.00"), fecha_pago="20260120"))
    assert not any("Fecha de Pago" in e for e in errores)


def test_tipo_retencion_isr_sin_fecha_pago_es_error():
    errores = validar_registro(_registro(tipo_retencion_isr=2, fecha_pago=None))
    assert any("Fecha de Pago" in e for e in errores)


def test_tipo_retencion_isr_fuera_de_rango():
    errores = validar_registro(_registro(tipo_retencion_isr=99, fecha_pago="20260120"))
    assert any("Tipo de Retención en ISR" in e for e in errores)


def test_forma_pago_faltante_es_obligatoria():
    errores = validar_registro(_registro(forma_pago=None))
    assert any("Forma de Pago" in e for e in errores)


def test_forma_pago_fuera_de_rango():
    errores = validar_registro(_registro(forma_pago=99))
    assert any("Forma de Pago" in e for e in errores)


def test_monto_negativo_es_error():
    errores = validar_registro(_registro(monto_servicios=Decimal("-1")))
    assert any("monto_servicios" in e for e in errores)
