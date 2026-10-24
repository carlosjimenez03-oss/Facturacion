"""Reglas puras de Soluciones (services/erp_api/app/soluciones_calculos.py) y
la entrega de envíos por canal (soluciones_tareas.entregar_pendientes) con
canales falsos."""
from datetime import date
from decimal import Decimal

import pytest

from libs.ecf_common.auth.signing import firmar_xml
from libs.ecf_common.environments import DGIIEnvironment
from services.erp_api.app import soluciones_calculos as calc
from services.erp_api.app import soluciones_tareas
from services.erp_api.app.repository import DatosInvalidosError
from services.erp_api.app.soluciones_canales import CanalError, SolucionesSettings, canales_configurados

D = Decimal


def test_precio_anual_son_diez_meses_mas_itbis():
    assert calc.precio_plan("emprendedor", "mensual") == {"subtotal": D("1950.00"), "itbis": D("351.00"), "total": D("2301.00")}
    assert calc.precio_plan("empresarial", "anual")["subtotal"] == D("98500.00")
    with pytest.raises(DatosInvalidosError):
        calc.precio_plan("pyme", "semanal")


def test_estado_de_la_suscripcion():
    fila = {"plan": None, "prueba_inicio": date(2026, 10, 10), "prueba_fin": date(2026, 10, 24)}
    assert calc.estado_suscripcion(fila, date(2026, 10, 10))["dias_restantes"] == 15
    assert calc.estado_suscripcion(fila, date(2026, 10, 24))["dias_restantes"] == 1
    vencida = calc.estado_suscripcion(fila, date(2026, 10, 25))
    assert (vencida["estado"], vencida["dias_restantes"], vencida["plan_limites"]) == ("prueba_vencida", 0, "pyme")
    activa = calc.estado_suscripcion(fila | {"plan": "emprendedor"}, date(2026, 12, 1))
    assert (activa["estado"], activa["plan_nombre"], activa["dias_restantes"]) == ("activa", "Emprendedor", None)


def test_porcentaje_y_bytes():
    assert calc.porcentaje_uso(2184, 2000) == 100
    assert calc.porcentaje_uso(4, 5) == 80
    assert calc.porcentaje_uso(10, None) is None
    assert calc.porcentaje_uso(None, 5) is None
    assert calc.formato_bytes(int(1.2 * 1024**3)) == "1.2 GB"
    assert calc.formato_bytes(2048) == "2 KB"


def test_token_aleatorio_y_solo_hash():
    token, h = calc.generar_token()
    otro, _ = calc.generar_token()
    assert token != otro and len(token) == 43
    assert h == calc.hash_token(token) and len(h) == 64 and token not in h
    assert calc.token_con_forma_valida(token)
    assert not calc.token_con_forma_valida("corto")
    assert not calc.token_con_forma_valida("x" * 40 + "'; --")


def test_plantilla_deja_las_variables_desconocidas():
    texto = calc.renderizar_plantilla("Hola {cliente}, {desconocida} {monto}", {"cliente": "Ana", "monto": "1,000.00"})
    assert texto == "Hola Ana, {desconocida} 1,000.00"


def test_telefono_whatsapp_y_correo():
    assert calc.telefono_whatsapp("(809) 555-0101") == "18095550101"
    assert calc.telefono_whatsapp("+1 829 555 0101") == "18295550101"
    assert calc.telefono_whatsapp("555-0101") is None
    assert calc.telefono_whatsapp(None) is None
    assert calc.correo_valido("pagos@cliente.do") and not calc.correo_valido("pagos@")
    assert calc.canales_de("ambos") == ["whatsapp", "correo"]


def test_pagos_mixtos_cambio_solo_en_efectivo():
    assert calc.validar_pagos(D("1180"), [{"metodo": "efectivo", "monto": D("2000")}]) == {
        "recibido": D("2000.00"), "cambio": D("820.00")
    }
    assert calc.validar_pagos(
        D("1180"), [{"metodo": "tarjeta", "monto": D("1000")}, {"metodo": "efectivo", "monto": D("200")}]
    )["cambio"] == D("20.00")
    with pytest.raises(DatosInvalidosError, match="cambio"):
        calc.validar_pagos(D("1180"), [{"metodo": "tarjeta", "monto": D("1200")}])
    with pytest.raises(DatosInvalidosError, match="Faltan"):
        calc.validar_pagos(D("1180"), [{"metodo": "transferencia", "monto": D("1000")}])
    with pytest.raises(DatosInvalidosError):
        calc.validar_pagos(D("1180"), [])


def test_esperado_y_diferencias_del_arqueo():
    ventas = [
        {"pagos": [{"metodo": "efectivo", "monto": "1500.00"}], "cambio": "320.00"},
        {"pagos": [{"metodo": "tarjeta", "monto": "4000"}, {"metodo": "transferencia", "monto": "100"}], "cambio": "0"},
    ]
    esperado = calc.esperado_en_caja(D("2000"), ventas)
    assert esperado == {"efectivo": D("3180.00"), "tarjeta": D("4000.00"), "transferencia": D("100.00")}
    dif = calc.diferencias_arqueo(esperado, {"efectivo": D("3200"), "tarjeta": D("4000")})
    assert dif == {"efectivo": D("20.00"), "tarjeta": D("0.00"), "transferencia": D("-100.00")}


def test_timbre_desde_el_xml_firmado(credencial_prueba):
    xml = (
        "<ECF><Encabezado><IdDoc><eNCF>E320000000001</eNCF></IdDoc></Encabezado>"
        "<FechaHoraFirma>24-10-2026 10:00:00</FechaHoraFirma></ECF>"
    ).encode()
    firmado = firmar_xml(xml, credencial_prueba).decode()
    datos = {"Encabezado": {"Emisor": {"FechaEmision": "24-10-2026"}, "Totales": {"MontoTotal": "1180.00"}}}
    t = calc.timbre_desde_xml(firmado, rnc_emisor="131897542", encf="E320000000001", datos=datos,
                              ambiente=DGIIEnvironment.TESTECF)
    assert len(t["codigo_seguridad"]) == 6
    assert f"codigoseguridad={t['codigo_seguridad']}" in t["url_qr"].replace("%2B", "+").replace("%2F", "/")
    assert t["qr_svg"].startswith("<svg")
    assert calc.timbre_desde_xml(None, rnc_emisor="1", encf="E32", datos={}, ambiente=DGIIEnvironment.TESTECF)["qr_svg"] is None
    assert calc.timbre_desde_xml("<ECF/>", rnc_emisor="1", encf="E32", datos={}, ambiente=DGIIEnvironment.TESTECF)["codigo_seguridad"] is None


def test_canales_configurados():
    assert canales_configurados(SolucionesSettings()) == {"correo": False, "whatsapp": False}
    s = SolucionesSettings(smtp_host="smtp.ejemplo.do", smtp_remitente="no-responder@ejemplo.do",
                           whatsapp_token="t", whatsapp_phone_number_id="123")
    assert canales_configurados(s) == {"correo": True, "whatsapp": True}


async def test_entregar_pendientes_sin_canal_no_toca_nada(monkeypatch):
    async def cola(pool, canales):
        assert canales == []
        return []

    monkeypatch.setattr(soluciones_tareas.repo, "envios_por_entregar", cola)
    assert await soluciones_tareas.entregar_pendientes(object(), SolucionesSettings()) == {"enviados": 0, "errores": 0}


async def test_entregar_pendientes_registra_exito_y_error(monkeypatch):
    envios = [
        {"id": "a", "canal": "whatsapp", "destino": "18095550101", "mensaje": "Hola", "asunto": "x",
         "automatizacion": "cobranza", "factura_id": None, "encf": None},
        {"id": "b", "canal": "correo", "destino": "c@c.do", "mensaje": "Hola", "asunto": "x",
         "automatizacion": "motivar", "factura_id": None, "encf": None},
    ]
    registrados = {}

    async def cola(pool, canales):
        assert canales == ["correo", "whatsapp"]
        return envios

    async def registrar(pool, envio_id, *, error):
        registrados[envio_id] = error

    async def whatsapp_ok(settings, tel, texto):
        return None

    async def correo_falla(settings, para, asunto, cuerpo, adjunto):
        raise CanalError("SMTP: 550 buzón inexistente")

    monkeypatch.setattr(soluciones_tareas.repo, "envios_por_entregar", cola)
    monkeypatch.setattr(soluciones_tareas.repo, "registrar_entrega", registrar)
    monkeypatch.setattr(soluciones_tareas, "enviar_whatsapp", whatsapp_ok)
    monkeypatch.setattr(soluciones_tareas, "enviar_correo", correo_falla)
    s = SolucionesSettings(smtp_host="h", smtp_remitente="r@r.do", whatsapp_token="t", whatsapp_phone_number_id="1")
    assert await soluciones_tareas.entregar_pendientes(object(), s) == {"enviados": 1, "errores": 1}
    assert registrados == {"a": None, "b": "SMTP: 550 buzón inexistente"}
