from datetime import date
from decimal import Decimal

import asyncpg
import pytest

from libs.ecf_common import ecf_recibidos
from libs.ecf_common.ecf_recibidos import (
    campos_ecf_recibido,
    datos_606_desde_ecf,
    fecha_dgii_a_date,
    items_ecf,
    parsear_xml,
    registrar_ecf_recibido,
)

XML = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    "<ECF><Encabezado>"
    "<IdDoc><TipoeCF>31</TipoeCF><eNCF>E310000008734</eNCF><FechaLimitePago>04-11-2026</FechaLimitePago></IdDoc>"
    "<Emisor><RNCEmisor>131234567</RNCEmisor><RazonSocialEmisor>Transporte Cibao Express, SRL</RazonSocialEmisor>"
    "<FechaEmision>05-10-2026</FechaEmision></Emisor>"
    "<Comprador><RNCComprador>131897542</RNCComprador></Comprador>"
    "<Totales><TotalITBIS>18000.00</TotalITBIS><MontoTotal>118000.00</MontoTotal></Totales>"
    "</Encabezado><DetallesItems>"
    "<Item><NumeroLinea>1</NumeroLinea><IndicadorFacturacion>1</IndicadorFacturacion><NombreItem>Flete</NombreItem>"
    "<IndicadorBienoServicio>2</IndicadorBienoServicio><CantidadItem>12</CantidadItem>"
    "<PrecioUnitarioItem>5000</PrecioUnitarioItem><MontoItem>60000.00</MontoItem></Item>"
    "<Item><NombreItem>Cemento</NombreItem><IndicadorBienoServicio>1</IndicadorBienoServicio>"
    "<MontoItem>40000.00</MontoItem></Item>"
    "</DetallesItems></ECF>"
).encode("utf-8")


def test_fecha_dgii_a_date():
    assert fecha_dgii_a_date("05-10-2026") == date(2026, 10, 5)
    assert fecha_dgii_a_date("31-02-2026") is None
    assert fecha_dgii_a_date("2026-10-05") is None
    assert fecha_dgii_a_date(None) is None


def test_campos_ecf_recibido():
    campos = campos_ecf_recibido(parsear_xml(XML))
    assert campos == {
        "rnc_emisor": "131234567",
        "razon_social_emisor": "Transporte Cibao Express, SRL",
        "rnc_comprador": "131897542",
        "encf": "E310000008734",
        "tipo_ecf": 31,
        "fecha_emision": date(2026, 10, 5),
        "fecha_limite_pago": date(2026, 11, 4),
        "monto_total": Decimal("118000.00"),
        "itbis_total": Decimal("18000.00"),
    }


def test_tipo_ecf_sale_del_encf_si_falta_tipoecf():
    xml = b"<ECF><Encabezado><IdDoc><eNCF>E450000000001</eNCF></IdDoc></Encabezado></ECF>"
    assert campos_ecf_recibido(parsear_xml(xml))["tipo_ecf"] == 45


def test_items_ecf():
    items = items_ecf(parsear_xml(XML))
    assert items[0] == {
        "linea": 1,
        "descripcion": "Flete",
        "cantidad": Decimal("12"),
        "precio_unitario": Decimal("5000"),
        "monto": Decimal("60000.00"),
        "bien_o_servicio": "servicio",
        "indicador_facturacion": "1",
    }
    assert items[1]["linea"] == 2 and items[1]["bien_o_servicio"] == "bien"
    assert items[1]["cantidad"] == Decimal("0")


def test_datos_606_desde_ecf():
    assert datos_606_desde_ecf(parsear_xml(XML)) == {
        "rnc_cedula": "131234567",
        "ncf": "E310000008734",
        "ncf_modificado": None,
        "fecha_comprobante": "20261005",
        "monto_servicios": Decimal("60000.00"),
        "monto_bienes": Decimal("40000.00"),
        "itbis_facturado": Decimal("18000.00"),
    }
    assert datos_606_desde_ecf(parsear_xml(b"<ECF><eNCF>E310000000001</eNCF></ECF>")) is None


def test_parsear_xml_no_resuelve_entidades():
    xml = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e "PELIGRO">]><ECF><eNCF>&e;</eNCF></ECF>'
    assert "PELIGRO" not in (parsear_xml(xml).findtext(".//eNCF") or "")


class _Conn:
    def __init__(self, error=None):
        self.error = error
        self.llamadas = []

    async def execute(self, query, *args):
        if self.error is not None:
            raise self.error
        self.llamadas.append((query, args))


class _Acquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *exc):
        return False


class _Pool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        return _Acquire(self.conn)


async def test_registrar_ecf_recibido_inserta_idempotente():
    conn = _Conn()
    await registrar_ecf_recibido(_Pool(conn), XML, parsear_xml(XML))
    query, args = conn.llamadas[0]
    assert "INSERT INTO recepcion.ecf_recibidos" in query
    assert "ON CONFLICT (rnc_emisor, encf)" in query
    assert "compras606.registros" in query  # enlaza su renglón del 606
    assert args[0] == "131234567" and args[3] == "E310000008734"
    assert args[9] == XML.decode("utf-8")


async def test_registrar_ecf_recibido_sin_pool_no_hace_nada():
    await registrar_ecf_recibido(None, XML, parsear_xml(XML))


@pytest.mark.parametrize("error", [asyncpg.UndefinedTableError("no existe"), RuntimeError("boom")])
async def test_registrar_ecf_recibido_nunca_lanza(error, caplog):
    await registrar_ecf_recibido(_Pool(_Conn(error)), XML, parsear_xml(XML))
    assert caplog.records  # queda en el log


async def test_registrar_ecf_recibido_sin_encf_no_inserta():
    conn = _Conn()
    xml = b"<ECF><RNCEmisor>131234567</RNCEmisor></ECF>"
    await registrar_ecf_recibido(_Pool(conn), xml, ecf_recibidos.parsear_xml(xml))
    assert conn.llamadas == []
