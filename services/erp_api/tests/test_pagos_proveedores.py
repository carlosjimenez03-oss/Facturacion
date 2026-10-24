"""Firma de la Página de Pagos de Azul (services/erp_api/app/pagos_proveedores.py).

Azul no publica un vector de prueba. Los vectores de aquí salen de su
documento («E-commerce AZUL · Página de Pagos», 2023-08, ver docs/PAGOS.md):
los campos del formulario de ejemplo del documento (MerchantId 99999999999,
«Comercio prueba», ECommerce, $, orden 1234, 15000, 2057 y las tres URL),
concatenados en el orden de «Manejo de la Autenticación» + AuthKey, en UTF-16LE
(«Unicode») con HMAC-SHA512 y la llave `kontara-clave-de-prueba-no-real`
(inventada: no es de ningún comercio). El resultado esperado se calculó
aparte con OpenSSL, no con este código:

    iconv -f utf-8 -t utf-16le sol.txt -o sol16.bin
    openssl dgst -sha512 -hmac kontara-clave-de-prueba-no-real sol16.bin
"""
from decimal import Decimal

import pytest

from services.erp_api.app import pagos_proveedores as pp

LLAVE = "kontara-clave-de-prueba-no-real"
EJEMPLO_AZUL = {
    "MerchantId": "99999999999",
    "MerchantName": "Comercio prueba",
    "MerchantType": "ECommerce",
    "CurrencyCode": "$",
    "OrderNumber": "1234",
    "Amount": "15000",
    "ITBIS": "2057",
    "ApprovedUrl": "https://comercioprueba.com/aprobada/",
    "DeclinedUrl": "https://comercioprueba.com/declinada/",
    "CancelUrl": "https://comercioprueba.com/cancelada/",
    "UseCustomField1": "0",
    "CustomField1Label": "",
    "CustomField1Value": "",
    "UseCustomField2": "0",
    "CustomField2Label": "",
    "CustomField2Value": "",
}
HASH_SOLICITUD = (
    "21cafceb15fa0cba91a1bda6cc14e2f8e86782af798ad5086ddb44f339e538f5"
    "20030a319b57d593fbbfb274453afa22cee9716178eec565190fda2a41484368"
)
RESPUESTA = {
    "OrderNumber": "1234",
    "Amount": "15000",
    "AuthorizationCode": "OK1234",
    "DateTime": "20230812143015",
    "ResponseCode": "ISO8583",
    "IsoCode": "00",
    "ResponseMessage": "APROBADA",
    "ErrorDescription": "",
    "RRN": "000012345678",
}
HASH_RESPUESTA = (
    "f2ca5291edaf70ddb14dc048438fed0978565d5a677cd75bc78710ea259cf2d2"
    "7a1bfb5ded996d909732e56e0e03fa9ad9b1200aae79f29e2e263b9628c944e4"
)

CONFIG = pp.ConfigProveedor(
    proveedor="azul", ambiente="pruebas", merchant_id="99999999999", merchant_name="Comercio prueba",
    merchant_type="ECommerce", auth_key=LLAVE,
)
URLS = pp.UrlsRetorno(
    aprobada="https://comercioprueba.com/aprobada/",
    declinada="https://comercioprueba.com/declinada/",
    cancelada="https://comercioprueba.com/cancelada/",
)


def test_hash_del_requerimiento_coincide_con_el_vector():
    assert pp.azul_hash_solicitud(EJEMPLO_AZUL, LLAVE) == HASH_SOLICITUD


def test_hash_de_la_respuesta_coincide_con_el_vector():
    assert pp.azul_hash_respuesta(RESPUESTA, LLAVE) == HASH_RESPUESTA


def test_el_formulario_del_ejemplo_lleva_los_campos_y_la_firma_de_azul():
    f = pp.Azul().formulario(CONFIG, pp.IntentoPago(orden="1234", monto=Decimal("150.00"), itbis=Decimal("20.57")), URLS)
    assert f.accion == "https://pruebas.azul.com.do/PaymentPage/"
    assert {k: f.campos[k] for k in EJEMPLO_AZUL} == EJEMPLO_AZUL
    assert f.campos["AuthHash"] == HASH_SOLICITUD
    assert "AuthKey" not in f.campos and LLAVE not in f.campos.values()
    # Orden de los campos del POST = el del documento (y AuthHash después).
    assert list(f.campos)[: len(EJEMPLO_AZUL) + 1] == [*EJEMPLO_AZUL, "AuthHash"]


def test_produccion_usa_el_sitio_principal_y_tiene_alterno():
    config = pp.ConfigProveedor(**{**CONFIG.__dict__, "ambiente": "produccion"})
    f = pp.Azul().formulario(config, pp.IntentoPago(orden="1", monto=Decimal("1"), itbis=Decimal("0")), URLS)
    assert f.accion == "https://pagos.azul.com.do/PaymentPage/Default.aspx"
    assert f.accion_alterna == "https://contpagos.azul.com.do/PaymentPage/Default.aspx"
    assert f.campos["ITBIS"] == "000"


def test_descripcion_va_en_el_campo_personalizado_1():
    f = pp.Azul().formulario(
        CONFIG, pp.IntentoPago(orden="9", monto=Decimal("10"), itbis=Decimal("1.53"), descripcion="E310000000001"), URLS
    )
    assert (f.campos["UseCustomField1"], f.campos["CustomField1Label"], f.campos["CustomField1Value"]) == (
        "1", "Factura", "E310000000001"
    )
    assert f.campos["AuthHash"] == pp.azul_hash_solicitud(f.campos, LLAVE)


@pytest.mark.parametrize(
    "monto, texto", [(Decimal("150"), "15000"), (Decimal("17483.21"), "1748321"), (Decimal("0"), "000"),
                     (Decimal("0.5"), "050"), (Decimal("10.005"), "1001")],
)
def test_montos_en_centavos(monto, texto):
    assert pp.a_centavos(monto) == texto


@pytest.mark.parametrize(
    "texto, monto", [("15000", Decimal("150.00")), ("150.00", Decimal("150.00")), ("", None), ("12a", None),
                     ("1,748,321", Decimal("17483.21"))],
)
def test_monto_de_la_respuesta(texto, monto):
    assert pp.desde_centavos(texto) == monto


def test_respuesta_aprobada_valida():
    r = pp.Azul().leer_retorno(CONFIG, RESPUESTA | {"AuthHash": HASH_RESPUESTA.upper()})
    assert r.valido and r.aprobado
    assert (r.orden, r.monto, r.codigo_autorizacion, r.rrn) == ("1234", Decimal("150.00"), "OK1234", "000012345678")


@pytest.mark.parametrize("campo, valor", [
    ("Amount", "1500"),             # monto alterado
    ("IsoCode", "05"),              # resultado alterado
    ("OrderNumber", "1235"),        # otra orden
    ("AuthorizationCode", "OK9999"),
    ("RRN", "000012345679"),
])
def test_cualquier_campo_alterado_invalida_la_firma(campo, valor):
    r = pp.Azul().leer_retorno(CONFIG, RESPUESTA | {campo: valor, "AuthHash": HASH_RESPUESTA})
    assert not r.valido and r.motivo == "AuthHash inválido"
    assert not r.aprobado


def test_sin_firma_o_con_otra_llave_no_vale():
    assert not pp.Azul().leer_retorno(CONFIG, RESPUESTA).valido
    otra = pp.azul_hash_respuesta(RESPUESTA, "otra-llave")
    assert not pp.Azul().leer_retorno(CONFIG, RESPUESTA | {"AuthHash": otra}).valido
    sin_llave = pp.ConfigProveedor(**{**CONFIG.__dict__, "auth_key": ""})
    assert not pp.Azul().leer_retorno(sin_llave, RESPUESTA | {"AuthHash": HASH_RESPUESTA}).valido


def test_declinada_firmada_es_valida_pero_no_aprobada():
    declinada = RESPUESTA | {"IsoCode": "05", "ResponseMessage": "DECLINADA", "AuthorizationCode": ""}
    r = pp.Azul().leer_retorno(CONFIG, declinada | {"AuthHash": pp.azul_hash_respuesta(declinada, LLAVE)})
    assert r.valido and not r.aprobado and r.iso_code == "05"
    error = RESPUESTA | {"ResponseCode": "Error"}
    r = pp.Azul().leer_retorno(CONFIG, error | {"AuthHash": pp.azul_hash_respuesta(error, LLAVE)})
    assert r.valido and not r.aprobado


def test_comparacion_de_hash():
    assert pp.hash_coincide("ABCDEF", "abcdef")
    assert not pp.hash_coincide("", "abc") and not pp.hash_coincide(None, "abc")
    assert not pp.hash_coincide("ñ" * 3, "abc")


def test_validar_config_dice_que_falta():
    assert pp.Azul().validar_config(pp.ConfigProveedor(proveedor="azul")) == [
        "MerchantId", "MerchantName", "MerchantType", "AuthKey"
    ]
    assert pp.Azul().validar_config(CONFIG) == []
    assert pp.Simulado().validar_config(pp.ConfigProveedor(proveedor="simulado")) == []


def test_textos_con_caracteres_prohibidos_por_azul():
    config = pp.ConfigProveedor(**{**CONFIG.__dict__, "merchant_name": 'Mi "Tienda"'})
    with pytest.raises(pp.ConfigPagoInvalidaError):
        pp.Azul().formulario(config, pp.IntentoPago(orden="1", monto=Decimal("1"), itbis=Decimal("0")), URLS)
    with pytest.raises(pp.ConfigPagoInvalidaError):
        pp.Azul().formulario(pp.ConfigProveedor(proveedor="azul"), pp.IntentoPago("1", Decimal("1"), Decimal("0")), URLS)
    with pytest.raises(pp.ConfigPagoInvalidaError):
        pp.Azul().formulario(CONFIG, pp.IntentoPago(orden="1", monto=Decimal("1"), itbis=Decimal("2")), URLS)


def test_simulado_firma_como_azul_con_su_llave_publica():
    urls = pp.UrlsRetorno(aprobada="https://x/a", declinada="https://x/d", cancelada="https://x/c", simulado="https://x/sim")
    f = pp.Simulado().formulario(pp.ConfigProveedor(proveedor="simulado"), pp.IntentoPago("77", Decimal("5"), Decimal("0")), urls)
    assert f.accion == "https://x/sim"
    assert f.campos["AuthHash"] == pp.azul_hash_solicitud(f.campos, pp.LLAVE_SIMULADO)
    respuesta = pp.respuesta_simulada(f.campos, "aprobado", rrn="123456789012", autorizacion="654321", fecha="20261024120000")
    r = pp.Simulado().leer_retorno(pp.ConfigProveedor(proveedor="simulado"), respuesta)
    assert r.valido and r.aprobado and r.monto == Decimal("5.00") and r.codigo_autorizacion == "654321"
    declinada = pp.respuesta_simulada(f.campos, "declinado", rrn="1", autorizacion="1", fecha="x")
    r = pp.Simulado().leer_retorno(pp.ConfigProveedor(proveedor="simulado"), declinada)
    assert r.valido and not r.aprobado
    # Una respuesta del simulado no vale para Azul (otra llave).
    assert not pp.Azul().leer_retorno(CONFIG, respuesta).valido
