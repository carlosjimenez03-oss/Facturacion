"""Configuración → Certificado digital (carga validada del .p12) y
WhatsApp y correo (SMTP y Meta mockeados), con la base mockeada."""
import json

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from libs.ecf_common.auth import certificado_activo
from libs.ecf_common.tests.test_certificado_activo import crear_p12
from services.erp_api.app import configuracion_envio as envio
from services.erp_api.app import configuracion_repository as repo
from services.erp_api.app import main
from services.erp_api.app.config import get_settings


@pytest.fixture
def prefs(monkeypatch):
    """Preferencias en memoria en vez de configuracion.preferencias."""
    datos: dict[str, dict] = {}

    async def leer(pool, clave, por_defecto):
        return {**por_defecto, **datos.get(clave, {})}

    async def guardar(pool, clave, valor):
        datos[clave] = json.loads(json.dumps(valor))

    async def enviados(pool):
        return {"correo": 3, "whatsapp": 0}

    monkeypatch.setattr(envio, "leer_preferencia", leer)
    monkeypatch.setattr(envio, "guardar_preferencia", guardar)
    monkeypatch.setattr(envio, "enviados_del_mes", enviados)
    return datos


@pytest.fixture
def client(monkeypatch, tmp_path, prefs):
    async def fake_crear_pool(database_url):
        return object()

    async def nada(pool):
        return None

    async def sin_empresa(pool):
        return None

    monkeypatch.setattr(main, "crear_pool", fake_crear_pool)
    monkeypatch.setattr(main, "cerrar_pool", nada)
    monkeypatch.setattr(main, "aplicar_migraciones", nada)
    monkeypatch.setattr(repo, "obtener_empresa", sin_empresa)
    monkeypatch.setattr(get_settings(), "emisor_rnc", "131897542")
    monkeypatch.setattr(get_settings(), "certificado_path", str(tmp_path / "emisor.p12"))
    monkeypatch.setenv("CERTIFICADO_ACTIVO_PATH", str(tmp_path / "activos" / "activo.json"))
    monkeypatch.setenv("CERTIFICADOS_CLAVE", "clave-larga-de-prueba")
    with TestClient(main.app) as c:
        yield c


def _subir(client, p12: bytes, contrasena: str, nombre: str = "caribe_2026.p12"):
    return client.post(
        "/configuracion/certificado",
        files={"archivo": (nombre, p12, "application/x-pkcs12")},
        data={"contrasena": contrasena},
    )


def test_certificado_sin_cargar(client):
    cuerpo = client.get("/configuracion/certificado").json()
    assert cuerpo["estado"] == "no_configurado"
    assert cuerpo["carga_disponible"] is True


def test_cargar_certificado_valido(client):
    r = _subir(client, crear_p12("clave-ok"), "clave-ok")
    assert r.status_code == 200, r.text
    cuerpo = r.json()
    assert cuerpo["origen"] == "cargado"
    assert cuerpo["archivo"] == "caribe_2026.p12"
    assert cuerpo["titular"] == "Caribe Verde SRL"
    assert cuerpo["coincide_rnc_emisor"] is True
    assert "clave-ok" not in r.text
    assert certificado_activo.leer_activo()[1] == "clave-ok"


@pytest.mark.parametrize(
    "p12,contrasena,nombre,mensaje",
    [
        (crear_p12("buena"), "mala", "c.p12", "contraseña"),
        (crear_p12("x", rnc="101000000"), "x", "c.p12", "no pertenece al RNC"),
        (crear_p12("x", dias=-1), "x", "c.p12", "venció"),
        (b"%PDF", "x", "factura.pdf", "no es un certificado"),
    ],
    ids=["contrasena", "otro-rnc", "vencido", "no-p12"],
)
def test_cargar_certificado_invalido_no_toca_el_activo(client, p12, contrasena, nombre, mensaje):
    assert _subir(client, crear_p12("previa"), "previa").status_code == 200
    r = _subir(client, p12, contrasena, nombre)
    assert r.status_code == 422
    assert mensaje in r.json()["detail"]
    assert certificado_activo.leer_activo()[1] == "previa"


def test_cargar_certificado_sin_clave_de_cifrado(client, monkeypatch):
    monkeypatch.delenv("CERTIFICADOS_CLAVE")
    r = _subir(client, crear_p12("x"), "x")
    assert r.status_code == 503
    assert "CERTIFICADOS_CLAVE" in r.json()["detail"]


def test_metodo_de_inventario(client):
    assert client.get("/configuracion/inventario").json() == {"metodo": "promedio"}
    assert client.put("/configuracion/inventario", json={"metodo": "peps"}).json() == {"metodo": "peps"}
    assert client.get("/configuracion/inventario").json() == {"metodo": "peps"}
    assert client.put("/configuracion/inventario", json={"metodo": "ueps"}).status_code == 422


def test_correo_guarda_sin_devolver_la_contrasena(client, prefs):
    r = client.put("/configuracion/envio/correo", json={
        "host": "smtp.caribe.do", "puerto": 587, "usuario": "f@caribe.do", "contrasena": "smtp-secreta",
        "remitente": "facturacion@caribe.do", "cco": "conta@caribe.do", "envio_automatico": True,
    })
    assert r.status_code == 200
    cuerpo = r.json()
    assert cuerpo["correo"]["contrasena_configurada"] is True
    assert cuerpo["correo"]["configurado"] is True
    assert cuerpo["enviados_mes"]["correo"] == 3
    assert "smtp-secreta" not in r.text
    assert "smtp-secreta" not in json.dumps(prefs)


def test_correo_de_prueba(client, monkeypatch):
    enviados = []

    def fake_smtp(config, contrasena, mensaje, timeout):
        enviados.append((config["host"], contrasena, mensaje["To"], mensaje["Bcc"]))

    monkeypatch.setattr(envio, "_enviar_smtp", fake_smtp)
    client.put("/configuracion/envio/correo", json={"host": "smtp.caribe.do", "contrasena": "smtp-secreta",
                                                     "remitente": "f@caribe.do", "cco": "conta@caribe.do"})
    r = client.post("/configuracion/envio/correo/prueba", json={"destino": "yo@caribe.do"})
    assert r.status_code == 200
    assert enviados == [("smtp.caribe.do", "smtp-secreta", "yo@caribe.do", "conta@caribe.do")]


def test_correo_de_prueba_sin_configurar(client):
    r = client.post("/configuracion/envio/correo/prueba", json={"destino": "yo@caribe.do"})
    assert r.status_code == 422
    assert "SMTP" in r.json()["detail"]


def test_whatsapp_no_conectado_sin_credenciales(client):
    cuerpo = client.get("/configuracion/envio").json()
    assert cuerpo["whatsapp"]["conectado"] is False
    r = client.post("/configuracion/envio/whatsapp/prueba", json={"destino": "809-555-0142"})
    assert r.status_code == 422


@respx.mock
def test_whatsapp_prueba_y_desconectar(client):
    r = client.put("/configuracion/envio/whatsapp", json={"phone_number_id": "1234567890", "numero": "+1 809-555-0142",
                                                           "token": "EAAG-token-meta"})
    assert r.json()["whatsapp"]["conectado"] is True
    assert "EAAG" not in r.text
    ruta = respx.post("https://graph.facebook.com/v20.0/1234567890/messages").mock(
        return_value=httpx.Response(200, json={"messages": [{"id": "wamid.1"}]})
    )
    r = client.post("/configuracion/envio/whatsapp/prueba", json={"destino": "809-555-0142"})
    assert r.status_code == 200
    enviado = ruta.calls[0].request
    assert enviado.headers["authorization"] == "Bearer EAAG-token-meta"
    assert json.loads(enviado.content)["to"] == "18095550142"
    r = client.delete("/configuracion/envio/whatsapp")
    assert r.json()["whatsapp"]["conectado"] is False


async def test_envio_automatico_de_aceptadas(monkeypatch):
    monkeypatch.setenv("CERTIFICADOS_CLAVE", "clave-larga-de-prueba")
    config = {**envio.CORREO_POR_DEFECTO, "host": "smtp.x.do", "remitente": "f@x.do", "envio_automatico": True,
              "contrasena_cifrada": certificado_activo.cifrar("s")}

    async def leer(pool, clave, por_defecto):
        return config

    async def pendientes(pool, limite=20):
        return [{"id": "f1", "encf": "E310000000001", "correo": "c@x.do", "cliente": "Cliente", "total": "1180.00",
                 "xml_firmado": "<ECF/>"}]

    registros, correos = [], []

    async def registrar(pool, canal, fid, destino, error=None):
        registros.append((canal, fid, destino, error))

    def fake_smtp(cfg, contrasena, mensaje, timeout):
        correos.append([p.get_filename() for p in mensaje.iter_attachments()])

    monkeypatch.setattr(envio, "leer_preferencia", leer)
    monkeypatch.setattr(envio, "facturas_por_enviar", pendientes)
    monkeypatch.setattr(envio, "registrar_envio", registrar)
    monkeypatch.setattr(envio, "_enviar_smtp", fake_smtp)
    with respx.mock:
        respx.get("http://facturas-api:8000/facturas/f1/representacion-impresa").mock(
            return_value=httpx.Response(200, content=b"%PDF-1.4")
        )
        assert await envio.enviar_facturas_aceptadas(object(), get_settings(), "Caribe Verde") == 1
    assert correos == [["E310000000001.pdf", "E310000000001.xml"]]
    assert registros == [("correo", "f1", "c@x.do", None)]

    config["envio_automatico"] = False
    assert await envio.enviar_facturas_aceptadas(object(), get_settings(), "Caribe Verde") == 0
