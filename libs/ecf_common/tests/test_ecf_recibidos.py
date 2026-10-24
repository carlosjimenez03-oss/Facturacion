from datetime import date
from decimal import Decimal

import asyncpg
import pytest

from libs.ecf_common import ecf_recibidos
from libs.ecf_common.auth.signing import firmar_xml
from libs.ecf_common.ecf_recibidos import (
    campos_ecf_recibido,
    datos_606_desde_ecf,
    fecha_dgii_a_date,
    items_ecf,
    parsear_xml,
    registrar_ecf_recibido,
    verificar_firma_ecf,
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
        "impuesto_selectivo_consumo": Decimal("0.00"),
        "otros_impuestos": Decimal("0.00"),
        "monto_propina_legal": Decimal("0.00"),
    }
    assert datos_606_desde_ecf(parsear_xml(b"<ECF><eNCF>E310000000001</eNCF></ECF>")) is None


def _ecf(id_doc="", totales="", items=""):
    return (
        "<ECF><Encabezado>"
        f"<IdDoc><TipoeCF>31</TipoeCF><eNCF>E310000000002</eNCF>{id_doc}</IdDoc>"
        "<Emisor><RNCEmisor>131234567</RNCEmisor><FechaEmision>05-10-2026</FechaEmision></Emisor>"
        f"<Totales>{totales}</Totales></Encabezado><DetallesItems>{items}</DetallesItems></ECF>"
    ).encode()


def _item(indicador, bien_o_servicio, monto):
    return (
        f"<Item><IndicadorFacturacion>{indicador}</IndicadorFacturacion>"
        f"<IndicadorBienoServicio>{bien_o_servicio}</IndicadorBienoServicio><MontoItem>{monto}</MontoItem></Item>"
    )


def test_606_con_itbis_incluido_en_las_lineas():
    # IndicadorMontoGravado=1: MontoItem trae el ITBIS; el 606 va sin ITBIS.
    xml = _ecf(
        "<IndicadorMontoGravado>1</IndicadorMontoGravado>",
        "<TotalITBIS>180.00</TotalITBIS><MontoTotal>1180.00</MontoTotal>",
        _item(1, 2, "708.00") + _item(1, 1, "472.00"),
    )
    datos = datos_606_desde_ecf(parsear_xml(xml))
    assert (datos["monto_servicios"], datos["monto_bienes"], datos["itbis_facturado"]) == (
        Decimal("600.00"), Decimal("400.00"), Decimal("180.00"),
    )
    # Sin MontoTotal, igual se le quita el ITBIS a cada línea.
    sin_total = _ecf("<IndicadorMontoGravado>1</IndicadorMontoGravado>", "", _item(1, 2, "708.00") + _item(4, 1, "100"))
    assert ecf_recibidos.separar_bienes_servicios(parsear_xml(sin_total)) == (Decimal("600.00"), Decimal("100.00"))


def test_606_con_selectivo_otros_impuestos_y_propina():
    # 1000 + 180 ITBIS + 100 propina (código 001) + 20 ISC ad valorem + 5 CDT (otro) = 1305.
    totales = (
        "<TotalITBIS>180.00</TotalITBIS><MontoImpuestoAdicional>125.00</MontoImpuestoAdicional>"
        "<ImpuestosAdicionales>"
        "<ImpuestoAdicional><TipoImpuesto>001</TipoImpuesto><TasaImpuestoAdicional>10</TasaImpuestoAdicional>"
        "<OtrosImpuestosAdicionales>100.00</OtrosImpuestosAdicionales></ImpuestoAdicional>"
        "<ImpuestoAdicional><TipoImpuesto>023</TipoImpuesto><TasaImpuestoAdicional>2</TasaImpuestoAdicional>"
        "<MontoImpuestoSelectivoConsumoAdvalorem>20.00</MontoImpuestoSelectivoConsumoAdvalorem></ImpuestoAdicional>"
        "<ImpuestoAdicional><TipoImpuesto>002</TipoImpuesto><TasaImpuestoAdicional>2</TasaImpuestoAdicional>"
        "<OtrosImpuestosAdicionales>5.00</OtrosImpuestosAdicionales></ImpuestoAdicional>"
        "</ImpuestosAdicionales><MontoTotal>1305.00</MontoTotal>"
    )
    datos = datos_606_desde_ecf(parsear_xml(_ecf("", totales, _item(1, 2, "1000.00"))))
    assert datos["monto_servicios"] == Decimal("1000.00") and datos["monto_bienes"] == Decimal("0.00")
    assert datos["monto_propina_legal"] == Decimal("100.00")
    assert datos["impuesto_selectivo_consumo"] == Decimal("20.00")
    assert datos["otros_impuestos"] == Decimal("5.00")
    total_606 = sum(datos[c] for c in (
        "monto_servicios", "monto_bienes", "itbis_facturado", "impuesto_selectivo_consumo", "otros_impuestos",
        "monto_propina_legal",
    ))
    assert total_606 == Decimal("1305.00")  # el 606 suma exactamente el MontoTotal


def test_606_descuento_global_se_reparte_por_proporcion():
    # Líneas 600 servicios + 400 bienes, descuento global de 100: monto 900.
    xml = _ecf("", "<TotalITBIS>162.00</TotalITBIS><MontoTotal>1062.00</MontoTotal>",
               _item(1, 2, "600.00") + _item(1, 1, "400.00"))
    assert ecf_recibidos.separar_bienes_servicios(parsear_xml(xml)) == (Decimal("540.00"), Decimal("360.00"))


def test_parsear_xml_no_resuelve_entidades():
    xml = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e "PELIGRO">]><ECF><eNCF>&e;</eNCF></ECF>'
    assert "PELIGRO" not in (parsear_xml(xml).findtext(".//eNCF") or "")


def test_xml_se_guarda_segun_su_codificacion():
    xml = '<?xml version="1.0" encoding="ISO-8859-1"?><ECF><RazonSocialEmisor>Peña</RazonSocialEmisor></ECF>'
    crudo = xml.encode("latin-1")
    texto = ecf_recibidos.texto_xml(crudo, parsear_xml(crudo))
    assert texto == xml
    # Leído desde la base (str) aunque declare ISO-8859-1, y descargado igual que llegó.
    assert parsear_xml(texto).findtext("RazonSocialEmisor") == "Peña"
    assert ecf_recibidos.bytes_xml(texto) == (crudo, "ISO-8859-1")
    utf16 = '<?xml version="1.0" encoding="UTF-16"?><ECF><eNCF>E310000000001</eNCF></ECF>'.encode("utf-16")
    assert ecf_recibidos.texto_xml(utf16, parsear_xml(utf16)).endswith("<eNCF>E310000000001</eNCF></ECF>")
    assert ecf_recibidos.texto_xml(b"<ECF>a\x00b</ECF>") == "<ECF>ab</ECF>"
    assert ecf_recibidos.bytes_xml("<ECF>ñ</ECF>") == ("<ECF>ñ</ECF>".encode(), "utf-8")


def _firmado(credencial, rnc="131234567"):
    xml = (
        f"<ECF><Encabezado><IdDoc><eNCF>E310000000001</eNCF></IdDoc><Emisor><RNCEmisor>{rnc}</RNCEmisor>"
        "<FechaEmision>05-10-2026</FechaEmision></Emisor><Totales><MontoTotal>118.00</MontoTotal></Totales>"
        "</Encabezado></ECF>"
    ).encode()
    return firmar_xml(xml, credencial)


def test_verificar_firma_ecf(credencial_con_rnc):
    firmado = _firmado(credencial_con_rnc("131234567"))
    valida, detalle = verificar_firma_ecf(firmado, "131234567")
    assert valida and "SN 131234567" in detalle
    # Firma íntegra pero de otro contribuyente.
    valida, detalle = verificar_firma_ecf(_firmado(credencial_con_rnc("101010101")), "131234567")
    assert not valida and "no es del RNC emisor" in detalle
    # Alterado después de firmar.
    valida, detalle = verificar_firma_ecf(firmado.replace(b"118.00", b"1.18"), "131234567")
    assert not valida and "inválida" in detalle
    # Sin firma.
    assert verificar_firma_ecf(XML, "131234567")[0] is False


class _Transaccion:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self

    async def __aexit__(self, tipo, exc, tb):
        return False


class _Conn:
    """Registra las sentencias; `insertado` = id que devuelve el INSERT (None
    = ya existía) y `existente` = la fila que devuelve el SELECT ... SKIP
    LOCKED (None = está bloqueada)."""

    def __init__(self, error=None, insertado="nuevo-id", existente=None):
        self.error = error
        self.insertado = insertado
        self.existente = existente
        self.llamadas = []

    def transaction(self):
        return _Transaccion(self)

    async def execute(self, query, *args):
        if self.error is not None:
            raise self.error
        self.llamadas.append((query, args))
        return "UPDATE 1"

    async def fetchval(self, query, *args):
        await self.execute(query, *args)
        return self.insertado

    async def fetchrow(self, query, *args):
        await self.execute(query, *args)
        return self.existente


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


def _sentencias(conn):
    return [" ".join(q.split()) for q, _ in conn.llamadas]


async def test_registrar_ecf_recibido_inserta_sin_esperar_candados():
    conn = _Conn()
    await registrar_ecf_recibido(_Pool(conn), XML, parsear_xml(XML))
    sentencias = _sentencias(conn)
    assert sentencias[0] == "SET LOCAL lock_timeout = '2s'"
    assert "INSERT INTO recepcion.ecf_recibidos" in sentencias[2]
    assert "ON CONFLICT DO NOTHING" in sentencias[2]
    args = conn.llamadas[2][1]
    assert args[0] == "131234567" and args[1] == "E310000008734"
    assert args[9] == XML.decode("utf-8")
    assert args[10] is False and "certificado" in args[11]  # sin firma: firma_valida False, con el motivo
    # Enlaza su renglón del 606 solo si ningún otro e-CF lo tiene.
    assert "compras606.registros" in sentencias[3] and "NOT EXISTS" in sentencias[3]


async def test_reenvio_con_la_fila_ocupada_no_espera_ni_actualiza():
    conn = _Conn(insertado=None, existente=None)
    await registrar_ecf_recibido(_Pool(conn), XML, parsear_xml(XML))
    sentencias = _sentencias(conn)
    assert "FOR UPDATE SKIP LOCKED" in sentencias[-1]
    assert not any(s.startswith("UPDATE") for s in sentencias)


async def test_reenvio_solo_completa_el_vinculo_del_606():
    existente = {"id": "e1", "compra606_id": None, "firma_valida": False, "xml": "<otro/>", "estado_aprobacion": "pendiente"}
    conn = _Conn(insertado=None, existente=existente)
    await registrar_ecf_recibido(_Pool(conn), XML, parsear_xml(XML))  # sin firma: no reemplaza
    updates = [s for s in _sentencias(conn) if s.startswith("UPDATE")]
    assert len(updates) == 1 and "SET compra606_id" in updates[0]


async def test_copia_con_firma_valida_reemplaza_a_la_invalida(credencial_con_rnc):
    firmado = _firmado(credencial_con_rnc("131234567"))
    existente = {"id": "e1", "compra606_id": "r1", "firma_valida": False, "xml": "<falso/>", "estado_aprobacion": "pendiente"}
    conn = _Conn(insertado=None, existente=existente)
    await registrar_ecf_recibido(_Pool(conn), firmado, parsear_xml(firmado))
    updates = [(s, a) for s, (_, a) in zip(_sentencias(conn), conn.llamadas) if s.startswith("UPDATE")]
    assert "SET razon_social_emisor" in updates[0][0] and updates[0][1][-2] is True
    assert "UPDATE compras606.registros" in updates[1][0]  # corrige los montos del 606 (si se puede)
    # Ya decidida comercialmente: no se reemplaza.
    conn = _Conn(insertado=None, existente={**existente, "estado_aprobacion": "aprobado"})
    await registrar_ecf_recibido(_Pool(conn), firmado, parsear_xml(firmado))
    assert not any(s.startswith("UPDATE") for s in _sentencias(conn))


async def test_registrar_ecf_recibido_sin_pool_no_hace_nada():
    await registrar_ecf_recibido(None, XML, parsear_xml(XML))


@pytest.mark.parametrize("error", [asyncpg.UndefinedTableError("no existe"), RuntimeError("boom")])
async def test_registrar_ecf_recibido_nunca_lanza(error, caplog):
    await registrar_ecf_recibido(_Pool(_Conn(error)), XML, parsear_xml(XML))
    assert caplog.records  # queda en el log


async def test_aviso_nombra_la_tabla_que_falta(caplog):
    error = asyncpg.UndefinedTableError('relation "compras606.registros" does not exist')
    await registrar_ecf_recibido(_Pool(_Conn(error)), XML, parsear_xml(XML))
    assert "compras606.registros" in caplog.text


async def test_registrar_ecf_recibido_sin_encf_no_inserta():
    conn = _Conn()
    xml = b"<ECF><RNCEmisor>131234567</RNCEmisor></ECF>"
    await registrar_ecf_recibido(_Pool(conn), xml, ecf_recibidos.parsear_xml(xml))
    assert conn.llamadas == []
