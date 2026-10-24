"""Rutas de Soluciones (services/erp_api/app/soluciones.py) con el
repositorio y facturas_api mockeados. Las consultas se verifican contra un
Postgres real en test_soluciones_postgres.py."""
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from services.erp_api.app import main, soluciones
from services.erp_api.app import soluciones_repository as repo
from services.erp_api.app.repository import DatosInvalidosError
from services.erp_api.app.soluciones_canales import CanalesEnvio, FacturasApiError, get_soluciones_settings
from services.erp_api.app.soluciones_repository import ConflictoSolucionesError, NoEncontradoError

AHORA = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)
TOKEN = "A" * 43


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
    monkeypatch.setattr(get_soluciones_settings(), "soluciones_tarea_activa", False)
    monkeypatch.setattr(get_soluciones_settings(), "smtp_host", "")
    # Sin nada en Configuración → WhatsApp y correo: solo las variables de entorno.
    monkeypatch.setattr(soluciones, "canales_de_envio", _async(lambda pool, settings: CanalesEnvio(settings)))
    with TestClient(main.app) as c:
        yield c


def _async(valor=None, error=None):
    async def f(*args, **kwargs):
        if error is not None:
            raise error
        return valor(*args, **kwargs) if callable(valor) else valor

    return f


def test_planes_con_precio_mensual_y_anual(client):
    r = client.get("/planes")
    assert r.status_code == 200
    planes = {p["clave"]: p for p in r.json()}
    assert planes["pyme"]["precio_mensual"] == "4450.00"
    assert planes["pyme"]["precio_anual"] == "44500.00"
    assert planes["pyme"]["popular"] is True
    assert planes["empresarial"]["ecf_mes"] is None


def test_suscripcion_para_la_tarjeta_del_menu(client, monkeypatch):
    monkeypatch.setattr(repo, "obtener_suscripcion", _async({
        "plan": None, "plan_nombre": None, "periodicidad": None, "estado": "prueba",
        "prueba_inicio": date(2026, 10, 24), "prueba_fin": date(2026, 11, 7), "dias_restantes": 15,
        "proxima_factura": None, "plan_limites": "pyme", "solicitud_pendiente": None,
    }))
    r = client.get("/suscripcion")
    assert r.status_code == 200
    assert r.json()["dias_restantes"] == 15 and r.json()["estado"] == "prueba"


def _solicitud(**cambios):
    return {
        "id": str(uuid.uuid4()), "plan": "pyme", "periodicidad": "mensual", "forma_pago": "transferencia",
        "subtotal": Decimal("4450"), "itbis": Decimal("801"), "total": Decimal("5251"), "contacto_nombre": None,
        "contacto_correo": None, "contacto_telefono": None, "notas": None, "estado": "pendiente", "creado_en": AHORA,
    } | cambios


def test_elegir_plan_no_cobra_y_explica_el_pago(client, monkeypatch):
    monkeypatch.setattr(repo, "crear_solicitud_plan", _async(lambda pool, **c: _solicitud(forma_pago=c["forma_pago"])))
    r = client.post("/suscripcion/solicitudes", json={"plan": "pyme", "forma_pago": "transferencia"})
    assert r.status_code == 201
    assert r.json()["solicitud"]["total"] == "5251.00"
    assert any("transferencia" in i for i in r.json()["instrucciones"])
    r = client.post("/suscripcion/solicitudes", json={"plan": "pyme", "forma_pago": "tarjeta"})
    assert any("todavía no está disponible" in i for i in r.json()["instrucciones"])
    assert client.post("/suscripcion/solicitudes", json={"plan": "oro"}).status_code == 422


def _enlace(**cambios):
    return {
        "id": str(uuid.uuid4()), "cliente_id": str(uuid.uuid4()), "cliente_nombre": "Cliente Uno, SRL",
        "cliente_rnc": "131234567", "cliente_correo": None, "cliente_telefono": None,
        "metodos_pago": ["transferencia"], "expira_en": AHORA, "revocado_en": None, "ultimo_acceso_en": None,
        "accesos": 0, "estado": "activo", "creado_en": AHORA,
    } | cambios


def test_crear_enlace_devuelve_el_token_una_vez_y_traduce_errores(client, monkeypatch):
    monkeypatch.setattr(repo, "crear_enlace", _async(lambda pool, **k: _enlace(token=TOKEN)))
    r = client.post("/portal/enlaces", json={"cliente_id": str(uuid.uuid4()), "metodos_pago": ["tarjeta", "transferencia"]})
    assert r.status_code == 201 and r.json()["token"] == TOKEN
    monkeypatch.setattr(repo, "crear_enlace", _async(error=DatosInvalidosError("El cliente necesita RNC o cédula")))
    assert client.post("/portal/enlaces", json={"cliente_id": str(uuid.uuid4())}).status_code == 422
    monkeypatch.setattr(repo, "listar_enlaces", _async([_enlace()]))
    lista = client.get("/portal/enlaces").json()
    assert lista[0]["token"] is None
    assert client.post("/portal/enlaces/no-uuid/revocar").status_code == 422


def test_portal_publico_404_si_el_token_no_vale(client, monkeypatch):
    monkeypatch.setattr(repo, "portal_publico", _async(error=NoEncontradoError("x")))
    r = client.get(f"/portal/publico/{TOKEN}")
    assert r.status_code == 404 and "revocado" in r.json()["detail"]


def test_portal_publico_devuelve_facturas_y_saldo(client, monkeypatch):
    monkeypatch.setattr(repo, "portal_publico", _async({
        "empresa": "Demo, SRL", "cliente_nombre": "Cliente Uno", "cliente_rnc": "131234567", "expira_en": AHORA,
        "metodos_pago": ["transferencia", "tarjeta"], "transferencia": {"banco": "Popular", "numero_cuenta": "1"},
        "saldo_pendiente": Decimal("3500"),
        "facturas": [{"id": str(uuid.uuid4()), "encf": "E310000000011", "tipo_ecf": 31, "fecha_emision": date(2026, 10, 1),
                      "total": Decimal("5000"), "pendiente": Decimal("3500"), "vence": date(2026, 10, 20),
                      "estado_pago": "vencida"}],
    }))
    r = client.get(f"/portal/publico/{TOKEN}")
    assert r.status_code == 200
    assert r.json()["saldo_pendiente"] == "3500.00"
    assert r.json()["facturas"][0]["estado_pago"] == "vencida"


def test_vista_previa_ya_no_esta_en_la_ruta_publica(client, monkeypatch):
    """`?vista_previa=true` en la ruta pública dejaba mirar sin contar el
    acceso: ahora se ignora; la vista previa del usuario es /portal/vista-previa
    (fuera de /portal/publico/, así que nginx y ERP_REQUIRE_AUTH piden sesión)."""
    registros = []

    async def portal(pool, token, *, empresa_configurada="", registrar_acceso=True):
        registros.append(registrar_acceso)
        raise NoEncontradoError("x")

    monkeypatch.setattr(repo, "portal_publico", portal)
    client.get(f"/portal/publico/{TOKEN}?vista_previa=true")
    client.get(f"/portal/vista-previa/{TOKEN}")
    assert registros == [True, False]
    assert not main.RUTA_PORTAL_PUBLICO.startswith("/portal/vista-previa")
    assert not f"/portal/vista-previa/{TOKEN}".startswith(main.RUTA_PORTAL_PUBLICO)


def test_pdf_del_portal_solo_para_facturas_del_cliente(client, monkeypatch):
    fid = str(uuid.uuid4())
    llamadas = []

    async def factura(pool, token, factura_id):
        if str(factura_id) != fid:
            raise NoEncontradoError("ajena")
        return {"id": fid, "encf": "E310000000010", "xml_firmado": None}

    async def ri(settings, factura_id, *, firmada):
        llamadas.append((factura_id, firmada))
        return b"%PDF-1.4 demo"

    monkeypatch.setattr(repo, "factura_del_portal", factura)
    monkeypatch.setattr(soluciones, "representacion_impresa", ri)
    r = client.get(f"/portal/publico/{TOKEN}/facturas/{fid}/pdf")
    assert r.status_code == 200 and r.content == b"%PDF-1.4 demo"
    assert r.headers["content-type"] == "application/pdf"
    assert llamadas == [(fid, False)]
    assert client.get(f"/portal/publico/{TOKEN}/facturas/{uuid.uuid4()}/pdf").status_code == 404
    assert client.get(f"/portal/publico/{TOKEN}/facturas/no-uuid/pdf").status_code == 404

    monkeypatch.setattr(soluciones, "representacion_impresa", _async(error=FacturasApiError("caído")))
    assert client.get(f"/portal/publico/{TOKEN}/facturas/{fid}/pdf").status_code == 502


def test_xml_del_portal(client, monkeypatch):
    fid = str(uuid.uuid4())
    monkeypatch.setattr(repo, "factura_del_portal", _async({"id": fid, "encf": "E310000000010", "xml_firmado": "<ECF/>"}))
    r = client.get(f"/portal/publico/{TOKEN}/facturas/{fid}/xml")
    assert r.status_code == 200 and r.content == b"<ECF/>"
    assert 'filename="E310000000010.xml"' in r.headers["content-disposition"]


def test_enviar_correo_sin_smtp_es_409(client):
    r = client.post("/portal/enviar-correo", json={"para": "a@b.do", "asunto": "x", "mensaje": "y"})
    assert r.status_code == 409
    assert client.get("/soluciones/canales").json() == {"correo": False, "whatsapp": False}


def _estado_ventas():
    return {
        "activo": True, "activado_en": AHORA, "prueba_hasta": date(2026, 11, 23), "habilitado": True,
        "ultima_ejecucion": None, "ultimo_resultado": None,
        "automatizaciones": [{
            "clave": "cobranza", "categoria": "Cobranza", "titulo": "Recordatorio de pago amable", "descripcion": "d",
            "variables": ["cliente"], "activa": True, "canal": "whatsapp", "dias": 3, "asunto": "a", "plantilla": "p",
            "activada_en": AHORA, "envios": {"pendiente_canal": 2},
        }],
    }


def test_ventas_estado_activar_y_automatizaciones(client, monkeypatch):
    monkeypatch.setattr(repo, "activar_ventas", _async(_estado_ventas()))
    r = client.post("/ventas-auto/activar", json={"activo": True})
    assert r.status_code == 200 and r.json()["prueba_hasta"] == "2026-11-23"
    assert r.json()["canales"] == {"correo": False, "whatsapp": False}
    assert client.put("/ventas-auto/automatizaciones/spam", json={"activa": True}).status_code == 422
    monkeypatch.setattr(repo, "actualizar_automatizacion",
                        _async(lambda pool, clave, **c: _estado_ventas()["automatizaciones"][0] | {"dias": c["dias"]}))
    assert client.put("/ventas-auto/automatizaciones/cobranza", json={"dias": 5}).json()["dias"] == 5
    assert client.put("/ventas-auto/automatizaciones/cobranza", json={"dias": 900}).status_code == 422


def test_marcar_envio_entregado_por_canal_es_409(client, monkeypatch):
    monkeypatch.setattr(repo, "marcar_envio", _async(error=ConflictoSolucionesError("ya entregado")))
    r = client.post(f"/ventas-auto/envios/{uuid.uuid4()}/marcar", json={"estado": "descartado"})
    assert r.status_code == 409
    assert client.post(f"/ventas-auto/envios/{uuid.uuid4()}/marcar", json={"estado": "enviado"}).status_code == 422


def test_ejecutar_automatizaciones(client, monkeypatch):
    async def ciclo(pool, settings):
        return {"creados": 3, "omitidos_sin_contacto": 1, "por_automatizacion": {"cobranza": 3},
                "entrega": {"enviados": 0, "errores": 0}}

    monkeypatch.setattr(soluciones.soluciones_tareas, "ejecutar_ciclo", ciclo)
    r = client.post("/ventas-auto/ejecutar")
    assert r.status_code == 200 and r.json()["creados"] == 3


def _sesion(**cambios):
    return {
        "id": str(uuid.uuid4()), "caja": "Caja principal", "almacen_id": None, "cajero": "Ana",
        "fondo_inicial": Decimal("2000"), "abierta_en": AHORA, "cerrada_en": None, "estado": "abierta",
        "esperado": {"efectivo": Decimal("2000"), "tarjeta": Decimal("0"), "transferencia": Decimal("0")},
        "contado": None, "diferencias": None, "notas_cierre": None, "cantidad_ventas": 0,
        "total_ventas": Decimal("0"), "ventas": [],
    } | cambios


def test_pos_sesiones(client, monkeypatch):
    monkeypatch.setattr(repo, "sesion_abierta", _async(None))
    r = client.get("/pos/sesiones/abierta")
    assert r.status_code == 200 and r.json() is None
    monkeypatch.setattr(repo, "abrir_sesion", _async(lambda pool, **k: _sesion(fondo_inicial=k["fondo_inicial"])))
    r = client.post("/pos/sesiones", json={"fondo_inicial": "1500", "cajero": "Ana"})
    assert r.status_code == 201 and r.json()["fondo_inicial"] == "1500.00"
    assert client.post("/pos/sesiones", json={"fondo_inicial": "-1"}).status_code == 422
    monkeypatch.setattr(repo, "abrir_sesion", _async(error=ConflictoSolucionesError("ya abierta")))
    assert client.post("/pos/sesiones", json={}).status_code == 409


def test_pos_registrar_venta_y_cerrar(client, monkeypatch):
    sid, fid = str(uuid.uuid4()), str(uuid.uuid4())
    venta = {
        "id": str(uuid.uuid4()), "sesion_id": sid, "factura_id": fid, "encf": "E320000000001", "tipo_ecf": 32,
        "cliente_rnc": None, "cliente_nombre": None, "total": Decimal("1180"),
        "pagos": [{"metodo": "efectivo", "monto": "1500.00", "referencia": None}],
        "recibido": Decimal("1500"), "cambio": Decimal("320"), "creado_en": AHORA,
    }
    monkeypatch.setattr(repo, "registrar_venta", _async(venta))
    r = client.post(f"/pos/sesiones/{sid}/ventas", json={"factura_id": fid, "pagos": [{"metodo": "efectivo", "monto": "1500"}]})
    assert r.status_code == 201 and r.json()["cambio"] == "320.00"
    assert client.post(f"/pos/sesiones/{sid}/ventas", json={"factura_id": fid, "pagos": []}).status_code == 422
    assert client.post(
        f"/pos/sesiones/{sid}/ventas", json={"factura_id": fid, "pagos": [{"metodo": "cheque", "monto": "1"}]}
    ).status_code == 422
    monkeypatch.setattr(repo, "registrar_venta", _async(error=ConflictoSolucionesError("no enviada")))
    assert client.post(
        f"/pos/sesiones/{sid}/ventas", json={"factura_id": fid, "pagos": [{"metodo": "efectivo", "monto": "1"}]}
    ).status_code == 409

    capturado = {}

    async def cerrar(pool, sesion_id, *, contado, notas):
        capturado.update(contado)
        return _sesion(estado="cerrada", contado=contado,
                       diferencias={"efectivo": Decimal("-80"), "tarjeta": Decimal("0"), "transferencia": Decimal("0")})

    monkeypatch.setattr(repo, "cerrar_sesion", cerrar)
    r = client.post(f"/pos/sesiones/{sid}/cerrar", json={"contado": {"efectivo": "1920"}})
    assert r.status_code == 200 and r.json()["diferencias"]["efectivo"] == "-80.00"
    assert capturado == {"efectivo": Decimal("1920"), "tarjeta": Decimal("0"), "transferencia": Decimal("0")}


def test_timbre_sin_xml_firmado(client, monkeypatch):
    monkeypatch.setattr(repo, "factura_para_timbre", _async(
        {"rnc_emisor": "131897542", "encf": "E320000000001", "xml_firmado": None, "datos": {}}))
    r = client.get(f"/pos/facturas/{uuid.uuid4()}/timbre")
    assert r.status_code == 200 and r.json()["codigo_seguridad"] is None and r.json()["encf"] == "E320000000001"
    monkeypatch.setattr(repo, "factura_para_timbre", _async(error=NoEncontradoError("Factura no encontrada")))
    assert client.get(f"/pos/facturas/{uuid.uuid4()}/timbre").status_code == 404


def test_el_lifespan_arranca_y_detiene_la_tarea_diaria(monkeypatch):
    estados = []

    async def bucle(pool_de, settings):
        estados.append("iniciada")
        try:
            await __import__("asyncio").sleep(3600)
        finally:
            estados.append("detenida")

    async def fake_crear_pool(database_url):
        return object()

    async def nada(*a):
        return None

    monkeypatch.setattr(main, "crear_pool", fake_crear_pool)
    monkeypatch.setattr(main, "cerrar_pool", nada)
    monkeypatch.setattr(main, "aplicar_migraciones", nada)
    monkeypatch.setattr(soluciones.soluciones_tareas, "bucle", bucle)
    monkeypatch.setattr(get_soluciones_settings(), "soluciones_tarea_activa", True)
    with TestClient(main.app) as c:
        assert c.get("/health").status_code == 200
    assert estados == ["iniciada", "detenida"]


async def test_usuarios_activos_de_supabase_auth(monkeypatch):
    # Sin SUPABASE_SERVICE_ROLE_KEY no se cuentan ("—" en Planes).
    monkeypatch.setattr(soluciones.gotrue_admin, "configurado", lambda settings: False)
    assert await soluciones._usuarios_activos() is None

    monkeypatch.setattr(soluciones.gotrue_admin, "configurado", lambda settings: True)
    monkeypatch.setattr(
        soluciones.gotrue_admin, "listar_usuarios", _async([{"activo": True}, {"activo": False}, {"activo": True}])
    )
    assert await soluciones._usuarios_activos() == 2

    caido = soluciones.gotrue_admin.GoTrueError(502, "Supabase Auth no responde")
    monkeypatch.setattr(soluciones.gotrue_admin, "listar_usuarios", _async(error=caido))
    assert await soluciones._usuarios_activos() is None
