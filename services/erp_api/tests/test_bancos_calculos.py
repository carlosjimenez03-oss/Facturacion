"""Lectura de extractos CSV y sugerencias de conciliación (funciones puras)."""
import uuid
from datetime import date
from decimal import Decimal

import pytest

from services.erp_api.app import bancos_calculos as bc
from services.erp_api.app.bancos_calculos import ExtractoInvalidoError

D = Decimal


@pytest.mark.parametrize("texto, esperado", [
    ("1,234.56", D("1234.56")),
    ("1.234,56", D("1234.56")),
    ("RD$ 12,500.00", D("12500.00")),
    ("(1,250.00)", D("-1250.00")),
    ("1250.00-", D("-1250.00")),
    ("-27,000.00", D("-27000.00")),
    ("1250,5", D("1250.5")),
    ("1,250", D("1250")),
    ("", None),
    ("-", None),
])
def test_parsear_monto(texto, esperado):
    assert bc.parsear_monto(texto) == esperado


def test_parsear_monto_con_separador_decimal_indicado():
    assert bc.parsear_monto("1.250", ",") == D("1250")
    assert bc.parsear_monto("1,250", ".") == D("1250")
    with pytest.raises(ValueError):
        bc.parsear_monto("doce")


def test_parsear_fecha_dia_primero_y_formatos():
    assert bc.parsear_fecha("05/10/2026") == date(2026, 10, 5)
    assert bc.parsear_fecha("2026-10-05") == date(2026, 10, 5)
    assert bc.parsear_fecha("05/10/2026 10:42") == date(2026, 10, 5)
    assert bc.parsear_fecha("10/05/2026", "MM/DD/AAAA") == date(2026, 10, 5)
    with pytest.raises(ValueError):
        bc.parsear_fecha("31/02/2026")


def test_extracto_con_debito_credito_titulo_y_punto_y_coma():
    contenido = (
        "Banco Popular Dominicano;;;;;\n"
        "Fecha Posteo;Descripción;No. Referencia;Débito;Crédito;Balance\n"
        "10/10/2026;TRANSF RECIBIDA;TRF-1001;;50,000.00;550,000.00\n"
        "18/10/2026;COMISION;COM-10;1,250.00;;548,750.00\n"
        ";;;Total;;\n"
    ).encode("cp1252")
    extracto = bc.leer_extracto(contenido)
    assert extracto.separador == ";" and extracto.fila_encabezado == 2
    assert extracto.mapeo_por_nombre() == {
        "fecha": "Fecha Posteo", "descripcion": "Descripción", "referencia": "No. Referencia", "debito": "Débito",
        "credito": "Crédito", "monto": None, "saldo": "Balance", "tipo": None,
    }
    assert [(x.fecha, x.monto, x.referencia, x.saldo) for x in extracto.lineas] == [
        (date(2026, 10, 10), D("50000.00"), "TRF-1001", D("550000.00")),
        (date(2026, 10, 18), D("-1250.00"), "COM-10", D("548750.00")),
    ]
    assert extracto.errores == []


def test_extracto_con_monto_unico_y_columna_debito_credito():
    contenido = (
        "Fecha,Concepto,Referencia,Tipo,Monto\n"
        "2026-10-01,DEPOSITO,D-1,C,1500.00\n"
        "2026-10-02,CARGO,C-9,D,25.00\n"
        "2026-10-03,AJUSTE,A-1,,-10.00\n"
    ).encode()
    lineas = bc.leer_extracto(contenido).lineas
    assert [x.monto for x in lineas] == [D("1500.00"), D("-25.00"), D("-10.00")]


def test_extracto_con_mapeo_por_posicion_y_signo_debito_positivo():
    contenido = "a\tb\tc\n01/10/2026\tPago\t500.00\n".encode()
    extracto = bc.leer_extracto(contenido, {"fecha": 0, "descripcion": 1, "monto": 2, "fila_encabezado": 1,
                                            "signo_monto": "debito_positivo"})
    assert extracto.separador == "\t"
    assert extracto.lineas[0].monto == D("-500.00") and extracto.lineas[0].descripcion == "Pago"


def test_extracto_errores_por_fila_y_sin_encabezado():
    contenido = "Fecha,Descripcion,Monto\n32/10/2026,X,10\n01/10/2026,Y,diez\n02/10/2026,Z,0\n".encode()
    extracto = bc.leer_extracto(contenido)
    assert extracto.lineas == []
    assert [e["fila"] for e in extracto.errores] == [2, 3, 4]
    with pytest.raises(ExtractoInvalidoError, match="encabezado"):
        bc.leer_extracto(b"uno,dos\n1,2\n")
    with pytest.raises(ExtractoInvalidoError, match="columna"):
        bc.leer_extracto(b"Fecha,Monto\n01/10/2026,1\n", {"descripcion": "Detalle"})


def test_huella_estable_y_distingue_lineas_identicas():
    contenido = b"Fecha,Descripcion,Monto\n01/10/2026,Cargo,-5\n01/10/2026,Cargo,-5\n"
    primera = bc.leer_extracto(contenido, cuenta="c1").lineas
    segunda = bc.leer_extracto(contenido, cuenta="c1").lineas
    assert [x.huella for x in primera] == [x.huella for x in segunda]
    assert primera[0].huella != primera[1].huella
    assert bc.leer_extracto(contenido, cuenta="c2").lineas[0].huella != primera[0].huella


def _linea(fecha, monto, referencia=None, descripcion=""):
    return {"id": uuid.uuid4(), "fecha": fecha, "monto": D(monto), "referencia": referencia, "descripcion": descripcion}


def _mov(fecha, monto, referencia=None, origen="manual", descripcion=""):
    return {"origen": origen, "id": uuid.uuid4(), "fecha": fecha, "monto": D(monto), "referencia": referencia,
            "descripcion": descripcion}


def test_sugerencias_por_monto_fecha_y_referencia():
    l1 = _linea(date(2026, 10, 21), "-22520", "TRF-88190", "PAGO PROVEEDOR")
    l2 = _linea(date(2026, 10, 10), "50000", None, "TRANSF RECIBIDA TRF1001")
    l3 = _linea(date(2026, 10, 19), "-150", "SMS-77")
    m1 = _mov(date(2026, 10, 20), "-22520", "TRF-88190", "pago")
    m2 = _mov(date(2026, 10, 10), "50000", "TRF-1001", "cobro")
    m_lejos = _mov(date(2026, 10, 1), "-150")
    sugerencias = bc.sugerir_conciliacion([l1, l2, l3], [m1, m2, m_lejos], dias=3)
    pares = {(s["linea"]["id"], s["movimiento"]["id"]) for s in sugerencias}
    assert pares == {(l1["id"], m1["id"]), (l2["id"], m2["id"])}
    por_linea = {s["linea"]["id"]: s for s in sugerencias}
    assert por_linea[l1["id"]]["puntaje"] == 140 and "referencia coincide" in por_linea[l1["id"]]["motivo"]
    assert por_linea[l2["id"]]["puntaje"] == 150


def test_sugerencias_uno_a_uno_prefiere_la_referencia():
    linea = _linea(date(2026, 10, 5), "-1000", "CHK-1")
    sin_ref = _mov(date(2026, 10, 5), "-1000")
    con_ref = _mov(date(2026, 10, 6), "-1000", "CHK-1")
    sugerencias = bc.sugerir_conciliacion([linea], [sin_ref, con_ref])
    assert len(sugerencias) == 1 and sugerencias[0]["movimiento"]["id"] == con_ref["id"]


def test_enmascarar_numero():
    assert bc.enmascarar_numero("001-234471-1") == "****4711"
    assert bc.enmascarar_numero(None) is None
