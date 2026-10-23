"""Rutas de Compras (services/erp_api/app/compras.py) con el repositorio
mockeado. Las consultas se verifican contra un Postgres real (ver
docs/ERP_API.md, "Compras")."""
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from services.erp_api.app import compras, compras_repository, main
from services.erp_api.app.aprobacion_client import AprobacionComercialError
from services.erp_api.app.compras_repository import CompraNoEncontradaError, ConflictoComprasError
from services.erp_api.app.config import get_settings
from services.erp_api.app.impuestos_repository import ConflictoImpuestosError
from services.erp_api.app.repository import DatosInvalidosError

AHORA = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def client(monkeypatch):
    async def fake_crear_pool(database_url):
        return object()

    async def fake_cerrar_pool(pool):
        return None

    async def fake_aplicar_migraciones(pool):
        return None

    monkeypatch.setattr(main, "crear_pool", fake_crear_pool)
    monkeypatch.setattr(main, "cerrar_pool", fake_cerrar_pool)
    monkeypatch.setattr(main, "aplicar_migraciones", fake_aplicar_migraciones)
    monkeypatch.setattr(get_settings(), "emisor_rnc", "1-31-89754-2")
    monkeypatch.setattr(get_settings(), "aprobacion_comercial_url", "http://aprobacion-comercial:8000")
    with TestClient(main.app) as c:
        yield c


def _ecf(**cambios) -> dict:
    datos = {
        "id": str(uuid.uuid4()), "rnc_emisor": "131234567", "razon_social_emisor": "Transporte Cibao Express, SRL",
        "rnc_comprador": "131897542", "encf": "E310000008734", "tipo_ecf": 31, "fecha_emision": date(2026, 10, 5),
        "fecha_limite_pago": None, "monto_total": Decimal("118000"), "itbis_total": Decimal("18000"),
        "recibido_en": AHORA, "estado_aprobacion": "pendiente", "motivo_rechazo": None, "aprobado_en": None,
        "error_aprobacion": None, "compra606_id": None, "compra_inventario_id": None,
    }
    datos.update(cambios)
    return datos


def _detalle(**cambios) -> dict:
    datos = {
        "items": [{"linea": 1, "descripcion": "Flete", "cantidad": Decimal("12"), "precio_unitario": Decimal("5000"),
                   "monto": Decimal("60000"), "bien_o_servicio": "servicio", "indicador_facturacion": "1"}],
        "subtotal": Decimal("100000"), "xml": "<ECF/>", "respuesta_dgii": None,
    }
    return _ecf(**{**datos, **cambios})


def _cuenta(**cambios) -> dict:
    datos = {
        "registro606_id": str(uuid.uuid4()), "ncf": "E310000008734", "tipo_ecf": 31, "periodo": "202610",
        "origen": "receptor_recepcion", "proveedor_rnc": "131234567", "proveedor_nombre": "Transporte Cibao Express, SRL",
        "contacto_id": None, "compra_inventario_id": None, "ecf_recibido_id": None, "estado_aprobacion": "aprobado",
        "factura_id": None, "concepto": None, "tipo_bienes_servicios": 2, "fecha_comprobante": date(2026, 10, 5),
        "vence": date(2026, 11, 4), "monto_facturado": Decimal("100000"), "itbis_facturado": Decimal("18000"),
        "total": Decimal("118000"), "notas_credito": Decimal("0"), "tipo_retencion_isr": None,
        "tipo_retencion_isr_label": None, "retencion_isr": Decimal("0"), "retencion_itbis": Decimal("0"),
        "retenciones": Decimal("0"), "pagado": Decimal("0"), "pendiente": Decimal("118000"), "estado": "pendiente",
        "dias_vencida": 0, "fecha_pago": None, "forma_pago": None, "retencion_isr_ecf": Decimal("0"),
        "retencion_itbis_ecf": Decimal("0"),
    }
    datos.update(cambios)
    return datos


def _pago(**cambios) -> dict:
    datos = {
        "id": str(uuid.uuid4()), "registro606_id": str(uuid.uuid4()), "ncf": "E310000008734",
        "proveedor_rnc": "131234567", "proveedor_nombre": "Transporte Cibao Express, SRL", "fecha": date(2026, 10, 20),
        "monto": Decimal("106600"), "metodo": "transferencia", "cuenta_origen": None, "referencia": None, "notas": None,
        "tipo_retencion_isr": 2, "tipo_retencion_isr_label": "Honorarios por servicios independientes",
        "retencion_isr": Decimal("6000"), "retencion_itbis": Decimal("5400"), "anulado": False, "anulado_en": None,
        "creado_en": AHORA,
    }
    datos.update(cambios)
    return datos


# --- Bandeja ---------------------------------------------------------

def test_listar_bandeja_pasa_filtros_y_serializa(client, monkeypatch):
    recibido = {}

    async def fake(pool, **kwargs):
        recibido.update(kwargs)
        return [_ecf()]

    monkeypatch.setattr(compras_repository, "listar_ecf_recibidos", fake)
    r = client.get("/compras/bandeja?estado=pendiente&q=cibao&periodo=202610")
    assert r.status_code == 200
    assert recibido == {"estado": "pendiente", "q": "cibao", "periodo": "202610", "limite": 200, "offset": 0}
    assert r.json()[0]["monto_total"] == "118000.00"
    assert r.json()[0]["fecha_emision"] == "2026-10-05"


def test_listar_bandeja_estado_invalido(client):
    assert client.get("/compras/bandeja?estado=aprobada").status_code == 422


def test_detalle_bandeja_404_e_id_invalido(client, monkeypatch):
    async def fake(pool, ecf_id):
        raise CompraNoEncontradaError(str(ecf_id))

    monkeypatch.setattr(compras_repository, "obtener_ecf_recibido", fake)
    assert client.get(f"/compras/bandeja/{uuid.uuid4()}").status_code == 404
    assert client.get("/compras/bandeja/abc").status_code == 422


def test_detalle_bandeja(client, monkeypatch):
    async def fake(pool, ecf_id):
        return _detalle()

    monkeypatch.setattr(compras_repository, "obtener_ecf_recibido", fake)
    cuerpo = client.get(f"/compras/bandeja/{uuid.uuid4()}").json()
    assert cuerpo["items"][0]["monto"] == "60000.00" and cuerpo["subtotal"] == "100000.00" and cuerpo["xml"] == "<ECF/>"


def test_descargar_xml(client, monkeypatch):
    async def fake(pool, ecf_id):
        return "131234567E310000008734.xml", "<ECF>ñ</ECF>"

    monkeypatch.setattr(compras_repository, "xml_ecf_recibido", fake)
    r = client.get(f"/compras/bandeja/{uuid.uuid4()}/xml")
    assert r.status_code == 200
    assert 'filename="131234567E310000008734.xml"' in r.headers["content-disposition"]
    assert r.text == "<ECF>ñ</ECF>"


def test_descargar_xml_con_la_codificacion_que_declara(client, monkeypatch):
    xml = '<?xml version="1.0" encoding="ISO-8859-1"?><ECF>Peña</ECF>'

    async def fake(pool, ecf_id):
        return "131234567E310000008734.xml", xml

    monkeypatch.setattr(compras_repository, "xml_ecf_recibido", fake)
    r = client.get(f"/compras/bandeja/{uuid.uuid4()}/xml")
    assert r.headers["content-type"] == "text/xml; charset=ISO-8859-1"
    assert r.content == xml.encode("latin-1")


def test_aprobar_envia_por_aprobacion_comercial_y_entrega_al_emisor(client, monkeypatch):
    recibido = {}
    enviados = []
    entregados = []

    async def fake_enviar(url, *, encf, xml, timeout):
        enviados.append((url, encf, xml))
        return {"estado": "Aceptado"}

    async def fake_entregar(url, *, rnc_emisor, nombre_archivo, xml, timeout):
        entregados.append((url, rnc_emisor, nombre_archivo, xml, timeout))
        return "https://proveedor/fe/aprobacioncomercial/api/ecf"

    async def fake_decidir(pool, ecf_id, *, aprobado, motivo, rnc_comprador, enviar, entregar):
        recibido.update(aprobado=aprobado, motivo=motivo, rnc_comprador=rnc_comprador)
        await enviar("E310000008734", b"<ACECF/>")
        await entregar("131234567", "E310000008734", b"<ACECF>firmado</ACECF>")
        return _detalle(
            estado_aprobacion="aprobado", aprobado_en=AHORA, respuesta_dgii={"estado": "Aceptado"},
            enviado_al_emisor=True, firma_valida=True, firma_detalle="Firmado por X (SN 131234567)",
        )

    monkeypatch.setattr(compras, "enviar_aprobacion_comercial", fake_enviar)
    monkeypatch.setattr(compras, "entregar_acecf_al_emisor", fake_entregar)
    monkeypatch.setattr(compras_repository, "decidir_aprobacion", fake_decidir)
    monkeypatch.setattr(get_settings(), "consulta_directorio_url", "http://consulta-directorio:8000")
    r = client.post(f"/compras/bandeja/{uuid.uuid4()}/aprobar")
    assert r.status_code == 200
    assert r.json()["estado_aprobacion"] == "aprobado"
    assert r.json()["enviado_al_emisor"] is True and r.json()["firma_valida"] is True
    assert r.json()["envio_en_curso"] is False
    assert recibido == {"aprobado": True, "motivo": None, "rnc_comprador": "131897542"}
    assert enviados == [("http://aprobacion-comercial:8000", "E310000008734", b"<ACECF/>")]
    # El archivo va con NUESTRO RNC (el del comprador que aprueba) + e-NCF.
    assert entregados == [
        ("http://consulta-directorio:8000", "131234567", "131897542E310000008734.xml", b"<ACECF>firmado</ACECF>", 30.0)
    ]


def test_rechazar_exige_motivo(client, monkeypatch):
    recibido = {}

    async def fake_decidir(pool, ecf_id, *, aprobado, motivo, rnc_comprador, enviar, entregar):
        recibido.update(aprobado=aprobado, motivo=motivo)
        return _detalle(estado_aprobacion="rechazado", motivo_rechazo=motivo)

    monkeypatch.setattr(compras_repository, "decidir_aprobacion", fake_decidir)
    assert client.post(f"/compras/bandeja/{uuid.uuid4()}/rechazar", json={"motivo": "  "}).status_code == 422
    r = client.post(f"/compras/bandeja/{uuid.uuid4()}/rechazar", json={"motivo": " No coincide con la OC "})
    assert r.status_code == 200
    assert recibido == {"aprobado": False, "motivo": "No coincide con la OC"}


@pytest.mark.parametrize(
    "error,status",
    [
        (AprobacionComercialError("DGII caída"), 502),
        (ConflictoComprasError("ya fue aprobado"), 409),
        (ConflictoComprasError("no requiere aprobación comercial"), 409),
        (CompraNoEncontradaError("x"), 404),
        (DatosInvalidosError("motivo"), 422),
    ],
)
def test_aprobar_traduce_errores(client, monkeypatch, error, status):
    async def fake_decidir(pool, ecf_id, **kwargs):
        raise error

    monkeypatch.setattr(compras_repository, "decidir_aprobacion", fake_decidir)
    r = client.post(f"/compras/bandeja/{uuid.uuid4()}/aprobar")
    assert r.status_code == status


def test_vincular_solo_pasa_lo_enviado(client, monkeypatch):
    recibido = []
    compra = str(uuid.uuid4())

    async def fake(pool, ecf_id, campos):
        recibido.append(campos)
        return _detalle(compra606_id=str(uuid.uuid4()), compra_inventario_id=campos.get("compra_inventario_id"))

    monkeypatch.setattr(compras_repository, "vincular_ecf_recibido", fake)
    assert client.post(f"/compras/bandeja/{uuid.uuid4()}/vincular", json={}).status_code == 200
    assert client.post(f"/compras/bandeja/{uuid.uuid4()}/vincular", json={"compra_inventario_id": compra}).status_code == 200
    assert client.post(f"/compras/bandeja/{uuid.uuid4()}/vincular", json={"compra_inventario_id": None}).status_code == 200
    assert recibido == [{}, {"compra_inventario_id": uuid.UUID(compra)}, {"compra_inventario_id": None}]


# --- Cuentas por pagar, facturas y pagos ---------------------------------------------------------

def test_cuentas_por_pagar(client, monkeypatch):
    recibido = {}

    async def fake(pool, **kwargs):
        recibido.update(kwargs)
        return [_cuenta()]

    monkeypatch.setattr(compras_repository, "listar_cuentas_por_pagar", fake)
    r = client.get("/compras/cuentas-por-pagar?estado=todas&proveedor_rnc=1-31-23456-7")
    assert r.status_code == 200
    assert recibido == {"estado": "todas", "proveedor_rnc": "1-31-23456-7", "q": None, "periodo": None}
    assert r.json()[0]["pendiente"] == "118000.00" and r.json()[0]["vence"] == "2026-11-04"
    assert client.get("/compras/cuentas-por-pagar?estado=cobrada").status_code == 422


def test_crear_factura_compra(client, monkeypatch):
    recibido = {}

    async def fake(pool, **campos):
        recibido.update(campos)
        return _cuenta(origen="compras", pagos=[])

    monkeypatch.setattr(compras_repository, "crear_factura_compra", fake)
    r = client.post("/compras/facturas", json={
        "proveedor_rnc": "001-0987654-3", "ncf": "E310000000214", "fecha_comprobante": "2026-10-18",
        "tipo_bienes_servicios": 2, "monto_servicios": "85000", "itbis_facturado": "15300"})
    assert r.status_code == 201
    assert recibido["fecha_comprobante"] == date(2026, 10, 18) and recibido["monto_servicios"] == Decimal("85000")
    assert recibido["monto_bienes"] == 0 and recibido["forma_pago"] is None
    assert r.json()["pagos"] == []


def test_crear_factura_compra_duplicada_409(client, monkeypatch):
    async def fake(pool, **campos):
        raise ConflictoComprasError("El NCF ya está registrado")

    monkeypatch.setattr(compras_repository, "crear_factura_compra", fake)
    r = client.post("/compras/facturas", json={
        "proveedor_rnc": "131234567", "ncf": "E310000000214", "fecha_comprobante": "2026-10-18",
        "tipo_bienes_servicios": 2})
    assert r.status_code == 409


def test_actualizar_factura_compra_parcial(client, monkeypatch):
    recibido = {}

    async def fake(pool, registro_id, campos):
        recibido.update(campos)
        return _cuenta(pagos=[_pago()])

    monkeypatch.setattr(compras_repository, "actualizar_factura_compra", fake)
    r = client.put(f"/compras/facturas/{uuid.uuid4()}", json={"tipo_bienes_servicios": 2, "vence": None})
    assert r.status_code == 200
    assert recibido == {"tipo_bienes_servicios": 2, "vence": None}
    assert r.json()["pagos"][0]["retencion_isr"] == "6000.00"


def test_eliminar_factura_compra(client, monkeypatch):
    async def fake(pool, registro_id):
        raise ConflictoComprasError("tiene pagos")

    monkeypatch.setattr(compras_repository, "eliminar_factura_compra", fake)
    assert client.delete(f"/compras/facturas/{uuid.uuid4()}").status_code == 409


def test_registrar_pago(client, monkeypatch):
    recibido = {}
    registro = str(uuid.uuid4())

    async def fake(pool, **campos):
        recibido.update(campos)
        return _pago(registro606_id=registro)

    monkeypatch.setattr(compras_repository, "registrar_pago", fake)
    r = client.post("/compras/pagos", json={
        "registro606_id": registro, "fecha": "2026-10-20", "monto": "106600", "metodo": "transferencia",
        "tipo_retencion_isr": 2, "retencion_isr": "6000", "retencion_itbis": "5400"})
    assert r.status_code == 201
    assert recibido == {
        "registro606_id": uuid.UUID(registro), "fecha": date(2026, 10, 20), "monto": Decimal("106600"),
        "metodo": "transferencia", "cuenta_origen": None, "referencia": None, "notas": None,
        "tipo_retencion_isr": 2, "retencion_isr": Decimal("6000"), "retencion_itbis": Decimal("5400"),
    }
    assert r.json()["monto"] == "106600.00"


@pytest.mark.parametrize(
    "error,status",
    [(DatosInvalidosError("supera lo pendiente"), 422), (ConflictoComprasError("ya tiene retenciones"), 409),
     (ConflictoImpuestosError("IT-1 presentado"), 409), (CompraNoEncontradaError("x"), 404)],
)
def test_registrar_pago_errores(client, monkeypatch, error, status):
    async def fake(pool, **campos):
        raise error

    monkeypatch.setattr(compras_repository, "registrar_pago", fake)
    r = client.post("/compras/pagos", json={"registro606_id": str(uuid.uuid4()), "monto": "1", "metodo": "efectivo"})
    assert r.status_code == status
    assert r.json()["detail"]


def test_registrar_pago_metodo_invalido(client):
    r = client.post("/compras/pagos", json={"registro606_id": str(uuid.uuid4()), "monto": "1", "metodo": "bitcoin"})
    assert r.status_code == 422


def test_anular_pago(client, monkeypatch):
    async def fake(pool, pago_id):
        return _pago(id=str(pago_id), anulado=True, anulado_en=AHORA)

    monkeypatch.setattr(compras_repository, "anular_pago", fake)
    r = client.post(f"/compras/pagos/{uuid.uuid4()}/anular")
    assert r.status_code == 200 and r.json()["anulado"] is True


def test_listar_pagos(client, monkeypatch):
    recibido = {}

    async def fake(pool, **kwargs):
        recibido.update(kwargs)
        return [_pago()]

    monkeypatch.setattr(compras_repository, "listar_pagos", fake)
    registro = uuid.uuid4()
    assert client.get(f"/compras/pagos?registro606_id={registro}&periodo=202610").status_code == 200
    assert recibido == {"registro606_id": registro, "periodo": "202610", "limite": 200, "offset": 0}
    assert client.get("/compras/pagos?registro606_id=x").status_code == 422


# --- E41/E43 y retenciones ---------------------------------------------------------

def test_comprobantes(client, monkeypatch):
    recibido = {}

    async def fake(pool, **kwargs):
        recibido.update(kwargs)
        return [{
            "id": str(uuid.uuid4()), "tipo_ecf": 41, "encf": "E410000000057", "estado": "aceptado", "dgii_codigo": None,
            "error": None, "proveedor_rnc": "00109876543", "proveedor_nombre": "Rafael Cruz", "concepto": "Diseño",
            "lineas": 1, "fecha_emision": date(2026, 10, 18), "monto": Decimal("85000"), "itbis": Decimal("15300"),
            "total": Decimal("100300"), "itbis_retenido": Decimal("15300"), "isr_retenido": Decimal("8500"),
            "registro606_id": None, "fecha_pago_606": None, "itbis_retenido_606": None, "isr_retenido_606": None,
            "creado_en": AHORA,
        }]

    monkeypatch.setattr(compras_repository, "listar_comprobantes_compra", fake)
    r = client.get("/compras/comprobantes?tipo_ecf=41&periodo=202610")
    assert r.status_code == 200
    assert recibido["tipo_ecf"] == 41 and recibido["periodo"] == "202610"
    assert r.json()[0]["isr_retenido"] == "8500.00"


def test_retenciones_del_mes(client, monkeypatch):
    async def fake(pool, periodo):
        return {
            "periodo": periodo,
            "filas": [{
                "registro606_id": str(uuid.uuid4()), "proveedor_rnc": "131234567", "proveedor_nombre": "Cibao",
                "ncf": "E310000008734", "fecha_comprobante": date(2026, 10, 5), "fecha_pago": date(2026, 10, 20),
                "monto_facturado": Decimal("100000"), "itbis_facturado": Decimal("18000"), "tipo_retencion_isr": 2,
                "tipo_retencion_isr_label": "Honorarios", "isr_retenido": Decimal("6000"),
                "itbis_retenido": Decimal("5400"), "declaraciones": ["ir17", "it1"], "estado": "por_declarar",
            }],
            "proveedores": [{"proveedor_rnc": "131234567", "proveedor_nombre": "Cibao", "comprobantes": 1,
                             "isr_retenido": Decimal("6000"), "itbis_retenido": Decimal("5400")}],
            "totales": {"isr_retenido": Decimal("6000"), "itbis_retenido": Decimal("5400")},
            "it1_presentado": False, "ir17_presentado": False,
        }

    monkeypatch.setattr(compras_repository, "retenciones_del_mes", fake)
    r = client.get("/compras/retenciones?periodo=202610")
    assert r.status_code == 200
    assert r.json()["totales"] == {"isr_retenido": "6000.00", "itbis_retenido": "5400.00"}
    assert client.get("/compras/retenciones").status_code == 422


def test_certificado_pasa_el_agente(client, monkeypatch):
    recibido = {}
    monkeypatch.setattr(get_settings(), "emisor_razon_social", "Kontara Demo, SRL")

    async def fake(pool, periodo, rnc, *, agente_rnc, agente_nombre):
        recibido.update(periodo=periodo, rnc=rnc, agente_rnc=agente_rnc, agente_nombre=agente_nombre)
        raise CompraNoEncontradaError("sin retenciones")

    monkeypatch.setattr(compras_repository, "certificado_retencion", fake)
    r = client.get("/compras/retenciones/certificado?periodo=202610&rnc=131234567")
    assert r.status_code == 404
    assert recibido == {"periodo": "202610", "rnc": "131234567", "agente_rnc": "131897542",
                        "agente_nombre": "Kontara Demo, SRL"}


def test_sugerencia(client):
    r = client.get("/compras/retenciones/sugerencia?persona=juridica&tipo_bienes_servicios=2&monto=1000&itbis=180"
                   "&fecha_pago=2026-10-30")
    assert r.status_code == 200
    assert r.json()["retencion_itbis"] == "54.00" and r.json()["itbis_pct"] == "30.00"
    assert r.json()["neto_a_pagar"] == "1126.00"
    assert client.get("/compras/retenciones/sugerencia?persona=fisica&tipo_bienes_servicios=12&monto=1").status_code == 422
