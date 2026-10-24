"""Configuración → Certificado digital (carga validada del .p12) y
WhatsApp y correo (SMTP y Meta mockeados), con la base mockeada."""
import json
import time

import jwt

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
    monkeypatch.setenv("CERTIFICADOS_CLAVE", "clave-larga-de-prueba-con-mas-de-32-caracteres")
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


def test_cargar_certificado_con_clave_corta(client, monkeypatch):
    monkeypatch.setenv("CERTIFICADOS_CLAVE", "corta")
    r = _subir(client, crear_p12("x"), "x")
    assert r.status_code == 503
    assert "al menos 32" in r.json()["detail"]
    assert client.get("/configuracion/certificado").json()["carga_disponible"] is False


def test_cargar_certificado_sin_rnc_de_empresa(client, monkeypatch):
    # Sin RNC no hay cómo saber si el certificado es de la empresa: se rechaza.
    monkeypatch.setattr(get_settings(), "emisor_rnc", "")
    r = _subir(client, crear_p12("x"), "x")
    assert r.status_code == 422
    assert "RNC de la empresa" in r.json()["detail"]
    assert certificado_activo.leer_activo() is None


def test_secretos_del_formato_anterior_se_recifran_al_guardar(client, prefs):
    antiguo = certificado_activo._fernet_antiguo(certificado_activo._clave()).encrypt(b"smtp-vieja").decode()
    prefs["correo"] = {"host": "smtp.caribe.do", "remitente": "f@caribe.do", "contrasena_cifrada": antiguo}
    assert client.put("/configuracion/envio/correo", json={"host": "smtp.caribe.do", "remitente": "f@caribe.do"}).status_code == 200
    nuevo = prefs["correo"]["contrasena_cifrada"]
    assert nuevo.startswith("v2:") and certificado_activo.descifrar(nuevo) == "smtp-vieja"


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


SECRETO = "super-secret-jwt-token-with-at-least-32-characters-long"


def _token(correo: str, **app_metadata) -> str:
    return jwt.encode(
        {"sub": "11111111-1111-1111-1111-111111111111", "email": correo, "aud": "authenticated", "role": "authenticated",
         "exp": int(time.time()) + 600, "app_metadata": {"kontara": True, **app_metadata}},
        SECRETO,
        algorithm="HS256",
    )


def test_correo_de_prueba_solo_al_usuario_con_sesion(client, monkeypatch):
    """Con sesión, el SMTP de la empresa solo le escribe al propio usuario
    (no sirve para mandarle correos a cualquiera); un administrador puede
    indicar otra dirección."""
    monkeypatch.setattr(get_settings(), "supabase_jwt_secret", SECRETO)
    monkeypatch.setattr(get_settings(), "kontara_admin_emails", "")
    enviados = []
    monkeypatch.setattr(envio, "_enviar_smtp", lambda config, contrasena, mensaje, timeout: enviados.append(mensaje["To"]))
    client.put("/configuracion/envio/correo", json={"host": "smtp.caribe.do", "remitente": "f@caribe.do"})
    yo = {"Authorization": f"Bearer {_token('Yo@Caribe.do')}"}
    assert client.post("/configuracion/envio/correo/prueba", json={"destino": "yo@caribe.do"}, headers=yo).status_code == 200
    r = client.post("/configuracion/envio/correo/prueba", json={"destino": "victima@otra.do"}, headers=yo)
    assert r.status_code == 403
    assert "su propio correo" in r.json()["detail"]
    admin = {"Authorization": f"Bearer {_token('jefe@caribe.do', kontara_admin=True)}"}
    assert client.post("/configuracion/envio/correo/prueba", json={"destino": "otro@caribe.do"}, headers=admin).status_code == 200
    assert enviados == ["yo@caribe.do", "otro@caribe.do"]


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


class _ConnLock:
    def __init__(self, libre=True):
        self.libre, self.consultas = libre, []

    async def fetchval(self, query, *args):
        self.consultas.append(query)
        return self.libre

    async def execute(self, query, *args):
        self.consultas.append(query)


class _PoolLock:
    def __init__(self, libre=True):
        self.conn = _ConnLock(libre)

    def acquire(self):
        return self

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *exc):
        return False


def _envio_automatico(monkeypatch, pendientes_lista, *, reclamo=1):
    monkeypatch.setenv("CERTIFICADOS_CLAVE", "clave-larga-de-prueba-con-mas-de-32-caracteres")
    config = {**envio.CORREO_POR_DEFECTO, "host": "smtp.x.do", "remitente": "f@x.do", "envio_automatico": True}

    async def leer(pool, clave, por_defecto):
        return config

    async def pendientes(pool, limite=20):
        return pendientes_lista

    eventos = []

    async def reclamar(pool, canal, fid, destino):
        eventos.append(("reclamar", fid))
        return reclamo

    async def terminar(pool, canal, fid, intento, error=None):
        eventos.append(("terminar", fid, intento, error))

    monkeypatch.setattr(envio, "leer_preferencia", leer)
    monkeypatch.setattr(envio, "facturas_por_enviar", pendientes)
    monkeypatch.setattr(envio, "reclamar_envio", reclamar)
    monkeypatch.setattr(envio, "terminar_envio", terminar)
    return config, eventos


async def test_envio_automatico_reclama_y_reintenta_errores(monkeypatch):
    _, eventos = _envio_automatico(monkeypatch, [
        {"id": "f1", "encf": "E310000000001", "correo": "c@x.do", "cliente": "C", "total": "1", "xml_firmado": None},
        {"id": "f2", "encf": "E310000000002", "correo": "d@x.do", "cliente": "D", "total": "2", "xml_firmado": None},
    ])
    monkeypatch.setattr(envio, "_enviar_smtp", lambda *a: None)
    with respx.mock:
        respx.get("http://facturas-api:8000/facturas/f1/representacion-impresa").mock(
            return_value=httpx.Response(200, content=b"%PDF")
        )
        respx.get("http://facturas-api:8000/facturas/f2/representacion-impresa").mock(return_value=httpx.Response(502))
        assert await envio.enviar_facturas_aceptadas(_PoolLock(), get_settings(), "Caribe Verde") == 1
    assert eventos[:3] == [("reclamar", "f1"), ("terminar", "f1", 1, None), ("reclamar", "f2")]
    assert eventos[3][:3] == ("terminar", "f2", 1) and "502" in eventos[3][3]


async def test_envio_automatico_no_manda_lo_que_otro_ya_reclamo(monkeypatch):
    _, eventos = _envio_automatico(
        monkeypatch,
        [{"id": "f1", "encf": "E310000000001", "correo": "c@x.do", "cliente": "C", "total": "1", "xml_firmado": None}],
        reclamo=None,
    )
    with respx.mock(assert_all_called=False) as m:
        pdf = m.get("http://facturas-api:8000/facturas/f1/representacion-impresa")
        assert await envio.enviar_facturas_aceptadas(_PoolLock(), get_settings(), "Caribe Verde") == 0
        assert not pdf.called
    assert eventos == [("reclamar", "f1")]


async def test_envio_automatico_una_pasada_a_la_vez(monkeypatch):
    _, eventos = _envio_automatico(monkeypatch, [{"id": "f1"}])
    pool = _PoolLock(libre=False)
    assert await envio.enviar_facturas_aceptadas(pool, get_settings(), "Caribe Verde") == 0
    assert eventos == []
    assert "pg_try_advisory_lock" in pool.conn.consultas[0]


async def test_envio_automatico_manda_la_cabecera_interna(monkeypatch):
    _envio_automatico(
        monkeypatch,
        [{"id": "f1", "encf": "E310000000001", "correo": "c@x.do", "cliente": "C", "total": "1", "xml_firmado": None}],
    )
    monkeypatch.setattr(envio, "_enviar_smtp", lambda *a: None)
    monkeypatch.setattr(get_settings(), "kontara_token_interno", "token-interno-de-prueba-con-32-caracteres!")
    with respx.mock:
        ruta = respx.get("http://facturas-api:8000/facturas/f1/representacion-impresa").mock(
            return_value=httpx.Response(200, content=b"%PDF")
        )
        assert await envio.enviar_facturas_aceptadas(_PoolLock(), get_settings(), "Caribe Verde") == 1
    assert ruta.calls[0].request.headers["x-kontara-interno"] == "token-interno-de-prueba-con-32-caracteres!"


async def test_envio_automatico_de_aceptadas(monkeypatch):
    monkeypatch.setenv("CERTIFICADOS_CLAVE", "clave-larga-de-prueba-con-mas-de-32-caracteres")
    config = {**envio.CORREO_POR_DEFECTO, "host": "smtp.x.do", "remitente": "f@x.do", "envio_automatico": True,
              "contrasena_cifrada": certificado_activo.cifrar("s")}

    async def leer(pool, clave, por_defecto):
        return config

    async def pendientes(pool, limite=20):
        return [{"id": "f1", "encf": "E310000000001", "correo": "c@x.do", "cliente": "Cliente", "total": "1180.00",
                 "xml_firmado": "<ECF/>"}]

    registros, correos = [], []

    async def reclamar(pool, canal, fid, destino):
        return 1

    async def registrar(pool, canal, fid, intento, error=None):
        registros.append((canal, fid, intento, error))

    def fake_smtp(cfg, contrasena, mensaje, timeout):
        correos.append([p.get_filename() for p in mensaje.iter_attachments()])

    monkeypatch.setattr(envio, "leer_preferencia", leer)
    monkeypatch.setattr(envio, "facturas_por_enviar", pendientes)
    monkeypatch.setattr(envio, "reclamar_envio", reclamar)
    monkeypatch.setattr(envio, "terminar_envio", registrar)
    monkeypatch.setattr(envio, "_enviar_smtp", fake_smtp)
    with respx.mock:
        respx.get("http://facturas-api:8000/facturas/f1/representacion-impresa").mock(
            return_value=httpx.Response(200, content=b"%PDF-1.4")
        )
        assert await envio.enviar_facturas_aceptadas(_PoolLock(), get_settings(), "Caribe Verde") == 1
    assert correos == [["E310000000001.pdf", "E310000000001.xml"]]
    assert registros == [("correo", "f1", 1, None)]

    config["envio_automatico"] = False
    assert await envio.enviar_facturas_aceptadas(object(), get_settings(), "Caribe Verde") == 0
