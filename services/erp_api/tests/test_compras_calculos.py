"""Cálculos puros de Compras (services/erp_api/app/compras_calculos.py) y el
cliente HTTP de aprobacion_comercial."""
from datetime import date, datetime
from decimal import Decimal

import httpx
import pytest
from lxml import etree

from services.erp_api.app import aprobacion_client, compras_calculos
from services.erp_api.app.compras_calculos import CamposPago606, Pago

D = Decimal


# --- ACECF ---------------------------------------------------------

def _acecf(**cambios):
    datos = dict(
        rnc_emisor="131234567", encf="E310000008734", fecha_emision=date(2026, 10, 5), monto_total=D("118000"),
        rnc_comprador="131897542", aprobado=True, motivo_rechazo=None, fecha_hora=datetime(2026, 10, 23, 9, 5, 7),
    )
    datos.update(cambios)
    return etree.fromstring(compras_calculos.construir_acecf(**datos))


def test_acecf_aprobado():
    root = _acecf()
    assert root.tag == "ACECF"
    detalle = root.find("DetalleAprobacionComercial")
    assert [e.tag for e in detalle] == [
        "Version", "RNCEmisor", "eNCF", "FechaEmision", "MontoTotal", "RNCComprador", "Estado",
        "FechaHoraAprobacionComercial",
    ]
    assert detalle.findtext("FechaEmision") == "05-10-2026"
    assert detalle.findtext("MontoTotal") == "118000.00"
    assert detalle.findtext("Estado") == "1"
    assert detalle.findtext("FechaHoraAprobacionComercial") == "23-10-2026 09:05:07"


def test_acecf_rechazado_lleva_el_motivo():
    root = _acecf(aprobado=False, motivo_rechazo="Cantidad no coincide")
    assert root.findtext(".//Estado") == "2"
    assert root.findtext(".//DetalleMotivoRechazo") == "Cantidad no coincide"


# --- Pagos -> 606 ---------------------------------------------------------

BASE = CamposPago606(fecha_pago=None, forma_pago=None)


def test_sin_pagos_vuelve_a_la_base():
    base = CamposPago606(fecha_pago="20261001", forma_pago=4, itbis_retenido=D("10"))
    assert compras_calculos.campos_pago_606(base, []) == base


def test_pago_con_retenciones_define_fecha_y_montos():
    pagos = [
        Pago(fecha=date(2026, 11, 2), metodo="cheque"),
        Pago(fecha=date(2026, 10, 20), metodo="transferencia", retencion_isr=D("6000"), retencion_itbis=D("5400"),
             tipo_retencion_isr=2),
    ]
    assert compras_calculos.campos_pago_606(BASE, pagos) == CamposPago606(
        fecha_pago="20261020", forma_pago=2, itbis_retenido=D("5400"), tipo_retencion_isr=2,
        monto_retencion_renta=D("6000"),
    )


def test_pagos_sin_retenciones_usan_el_ultimo_y_conservan_la_base():
    base = CamposPago606(fecha_pago=None, forma_pago=4, itbis_retenido=D("100"), tipo_retencion_isr=3,
                         monto_retencion_renta=D("50"))
    pagos = [Pago(fecha=date(2026, 10, 1), metodo="efectivo"), Pago(fecha=date(2026, 10, 9), metodo="tarjeta")]
    nuevo = compras_calculos.campos_pago_606(base, pagos)
    assert nuevo.fecha_pago == "20261009"
    assert nuevo.forma_pago == compras_calculos.FORMA_PAGO_MIXTO
    assert (nuevo.itbis_retenido, nuevo.tipo_retencion_isr, nuevo.monto_retencion_renta) == (D("100"), 3, D("50"))


def test_retencion_solo_itbis_no_lleva_tipo_isr():
    pago = Pago(fecha=date(2026, 10, 1), metodo="efectivo", retencion_itbis=D("10"), tipo_retencion_isr=2)
    assert compras_calculos.campos_pago_606(BASE, [pago]).tipo_retencion_isr is None


@pytest.mark.parametrize(
    "metodos,forma", [({"efectivo"}, 1), ({"cheque", "transferencia"}, 2), ({"tarjeta"}, 3), ({"efectivo", "cheque"}, 7)]
)
def test_forma_pago_606(metodos, forma):
    assert compras_calculos.forma_pago_606(metodos) == forma


def test_declaraciones_afectadas():
    con = CamposPago606(fecha_pago="20261020", forma_pago=2, itbis_retenido=D("5400"), tipo_retencion_isr=2,
                        monto_retencion_renta=D("6000"))
    assert compras_calculos.declaraciones_afectadas(BASE, con) == {("it1", "202610"), ("ir17", "202610")}
    assert compras_calculos.declaraciones_afectadas(con, BASE) == {("it1", "202610"), ("ir17", "202610")}
    # Solo cambia la forma de pago: no toca ninguna declaración.
    assert compras_calculos.declaraciones_afectadas(con, CamposPago606(**{**con.__dict__, "forma_pago": 7})) == set()
    # Mover la fecha de pago de mes afecta a los dos meses.
    movido = CamposPago606(**{**con.__dict__, "fecha_pago": "20261102", "monto_retencion_renta": D("0"),
                              "tipo_retencion_isr": None})
    assert compras_calculos.declaraciones_afectadas(con, movido) == {
        ("it1", "202610"), ("it1", "202611"), ("ir17", "202610"),
    }


@pytest.mark.parametrize(
    "ncf,nota,tipo",
    [("E340000000001", True, 34), ("B0400000001", True, None), ("E310000000001", False, 31),
     ("A010010010400000001", True, None), ("B0100000001", False, None)],
)
def test_tipos_de_comprobante(ncf, nota, tipo):
    assert compras_calculos.es_nota_credito(ncf) is nota
    assert compras_calculos.tipo_ecf_de_ncf(ncf) == tipo


# --- Sugerencia de retenciones ---------------------------------------------------------

def _sugerir(**cambios):
    datos = dict(persona="fisica", tipo_bienes_servicios=2, emisor_electronico=True, monto=D("85000"),
                 itbis=D("15300"), periodo_pago="202610")
    datos.update(cambios)
    return compras_calculos.sugerir_retenciones(**datos)


def test_sugerencia_honorarios_persona_fisica_ley_30_26():
    s = _sugerir()
    assert s["tipo_retencion_isr"] == 2
    assert s["isr_pct"] == D("15.00") and s["retencion_isr"] == D("12750.00")
    assert s["itbis_pct"] == D("100.00") and s["retencion_itbis"] == D("15300.00")
    assert s["neto_a_pagar"] == D("100300.00") - D("12750.00") - D("15300.00")


def test_sugerencia_antes_de_la_ley_30_26():
    assert _sugerir(periodo_pago="202606")["retencion_isr"] == D("8500.00")


def test_sugerencia_alquiler_e_informal():
    assert _sugerir(tipo_bienes_servicios=3)["tipo_retencion_isr"] == 1
    informal = _sugerir(tipo_bienes_servicios=9, emisor_electronico=False, periodo_pago="202606")
    assert informal["tipo_retencion_isr"] == 4 and informal["retencion_isr"] == D("1700.00")
    assert _sugerir(tipo_bienes_servicios=9)["tipo_retencion_isr"] is None  # emisor electrónico: sin retención


def test_sugerencia_persona_juridica():
    s = _sugerir(persona="juridica")
    assert s["tipo_retencion_isr"] is None and s["retencion_isr"] == 0
    assert s["retencion_itbis"] == D("4590.00")
    assert _sugerir(persona="juridica", tipo_bienes_servicios=9)["retencion_itbis"] == 0


# --- Cliente de aprobacion_comercial ---------------------------------------------------------

def _con_transporte(monkeypatch, handler):
    original = httpx.AsyncClient

    def fabrica(**kwargs):
        return original(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(aprobacion_client.httpx, "AsyncClient", fabrica)


async def test_enviar_aprobacion_comercial_ok(monkeypatch):
    recibido = {}

    def handler(request: httpx.Request):
        recibido["url"] = str(request.url)
        recibido["cuerpo"] = request.content
        return httpx.Response(200, json={"mensaje": [], "estado": "Aceptado", "codigo": "1"})

    _con_transporte(monkeypatch, handler)
    respuesta = await aprobacion_client.enviar_aprobacion_comercial(
        "http://aprobacion-comercial:8000/", encf="E310000000001", xml=b"<ACECF/>"
    )
    assert respuesta["estado"] == "Aceptado"
    assert recibido["url"] == "http://aprobacion-comercial:8000/aprobacion"
    assert b'name="encf"' in recibido["cuerpo"] and b"<ACECF/>" in recibido["cuerpo"]


async def test_enviar_aprobacion_comercial_error_de_la_dgii(monkeypatch):
    _con_transporte(monkeypatch, lambda request: httpx.Response(502, json={"detail": "DGII: e-CF no encontrado"}))
    with pytest.raises(aprobacion_client.AprobacionComercialError, match="e-CF no encontrado"):
        await aprobacion_client.enviar_aprobacion_comercial("http://x", encf="E310000000001", xml=b"<ACECF/>")


async def test_enviar_aprobacion_comercial_sin_conexion(monkeypatch):
    def handler(request):
        raise httpx.ConnectError("sin red")

    _con_transporte(monkeypatch, handler)
    with pytest.raises(aprobacion_client.AprobacionComercialError, match="No se pudo contactar"):
        await aprobacion_client.enviar_aprobacion_comercial("http://x", encf="E310000000001", xml=b"<ACECF/>")
