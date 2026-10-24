"""Plataforma (fase 6) de erp-api: GET /auth/verificar (auth_request de
nginx), usuarios de Supabase Auth (API admin de GoTrue, mockeada con respx)
y GET /estado-servicios (healthchecks mockeados)."""
import json
import time

import httpx
import jwt
import pytest
import respx
from fastapi.testclient import TestClient

from services.erp_api.app import estado_servicios, main, plataforma
from services.erp_api.app.config import ErpApiSettings, get_settings

SECRETO = "super-secret-jwt-token-with-at-least-32-characters-long"
SERVICE_KEY = "service-role-key-de-prueba"
GW = "http://api-gw:8000"
YO = "11111111-1111-1111-1111-111111111111"
OTRO = "22222222-2222-2222-2222-222222222222"


def token(secreto: str = SECRETO, **cambios) -> str:
    claims = {"sub": YO, "email": "yo@empresa.do", "aud": "authenticated", "role": "authenticated",
              "exp": int(time.time()) + 3600, "app_metadata": {"provider": "email", "kontara": True}}
    claims.update(cambios)
    return jwt.encode({k: v for k, v in claims.items() if v is not None}, secreto, algorithm="HS256")


PDF = "/api/facturas/0b6d2f0e-1c3a-4c1e-9a55-1f0f3c2d4e5f/representacion-impresa"


@pytest.fixture
def client(monkeypatch):
    async def fake_crear_pool(database_url):
        return _PoolSano()

    async def nada(pool):
        return None

    monkeypatch.setattr(main, "crear_pool", fake_crear_pool)
    monkeypatch.setattr(main, "cerrar_pool", nada)
    monkeypatch.setattr(main, "aplicar_migraciones", nada)
    s = get_settings()
    monkeypatch.setattr(s, "supabase_jwt_secret", SECRETO)
    monkeypatch.setattr(s, "supabase_url", GW)
    monkeypatch.setattr(s, "supabase_service_role_key", SERVICE_KEY)
    monkeypatch.setattr(s, "kontara_admin_emails", "")
    plataforma._MARCADOS.clear()
    with TestClient(main.app) as c:
        yield c


class _Conn:
    async def fetchval(self, query, timeout=None):
        return 1


class _Acquire:
    async def __aenter__(self):
        return _Conn()

    async def __aexit__(self, *exc):
        return False


class _PoolSano:
    def acquire(self, timeout=None):
        return _Acquire()


# --- /auth/verificar ---------------------------------------------------------

def test_verificar_sin_token_401(client):
    r = client.get("/auth/verificar")
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "Bearer"


def test_verificar_con_bearer_valido_204(client):
    r = client.get("/auth/verificar", headers={"Authorization": f"Bearer {token()}"})
    assert r.status_code == 204
    assert r.headers["x-usuario-id"] == YO
    assert r.headers["cache-control"] == "no-store"


def test_verificar_cookie_solo_en_descargas_get(client):
    """nginx manda el método y la ruta originales (X-Original-*): la cookie
    vale para abrir el PDF/XML, nunca para un POST (CSRF) ni otras rutas."""
    client.cookies.set("kontara_sesion", token())
    descarga = {"X-Original-Method": "GET", "X-Original-URI": PDF}
    assert client.get("/auth/verificar", headers=descarga).status_code == 204
    xml = {"X-Original-Method": "GET", "X-Original-URI": "/api-erp/compras/bandeja/0b6d2f0e-1c3a-4c1e-9a55-1f0f3c2d4e5f/xml"}
    assert client.get("/auth/verificar", headers=xml).status_code == 204
    assert client.get("/auth/verificar", headers={**descarga, "X-Original-Method": "POST"}).status_code == 401
    otra = {"X-Original-Method": "GET", "X-Original-URI": "/api-erp/configuracion/usuarios"}
    assert client.get("/auth/verificar", headers=otra).status_code == 401
    # Sin cabeceras de nginx (llamada directa): tampoco.
    assert client.get("/auth/verificar").status_code == 401
    # Con Authorization, cualquier método.
    post = {"X-Original-Method": "POST", "X-Original-URI": "/api/facturas", "Authorization": f"Bearer {token()}"}
    assert client.get("/auth/verificar", headers=post).status_code == 204


def test_verificar_usuario_sin_acceso_a_kontara_401(client):
    r = client.get("/auth/verificar", headers={"Authorization": f"Bearer {token(app_metadata={'provider': 'email'})}"})
    assert r.status_code == 401
    assert "no tiene acceso a Kontara" in r.json()["detail"]
    anonimo = token(is_anonymous=True)
    assert client.get("/auth/verificar", headers={"Authorization": f"Bearer {anonimo}"}).status_code == 401


@respx.mock
def test_verificar_admin_por_correo_entra_y_se_marca_una_vez(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "kontara_admin_emails", "Yo@Empresa.do")
    marca = respx.put(f"{GW}/auth/v1/admin/users/{YO}").mock(
        return_value=httpx.Response(200, json={"id": YO, "email": "yo@empresa.do",
                                               "app_metadata": {"kontara": True, "kontara_admin": True}})
    )
    sin_marca = token(app_metadata={"provider": "email"})
    for _ in range(3):
        assert client.get("/auth/verificar", headers={"Authorization": f"Bearer {sin_marca}"}).status_code == 204
    assert marca.call_count == 1
    assert json.loads(marca.calls[0].request.content) == {"app_metadata": {"kontara": True, "kontara_admin": True}}


@respx.mock
def test_verificar_admin_por_correo_si_gotrue_falla_reintenta(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "kontara_admin_emails", "yo@empresa.do")
    marca = respx.put(f"{GW}/auth/v1/admin/users/{YO}").mock(return_value=httpx.Response(500, json={"msg": "caído"}))
    sin_marca = token(app_metadata=None)
    assert client.get("/auth/verificar", headers={"Authorization": f"Bearer {sin_marca}"}).status_code == 204
    assert client.get("/auth/verificar", headers={"Authorization": f"Bearer {sin_marca}"}).status_code == 204
    assert marca.call_count == 2


def test_verificar_sesion_cerrada_401(client, monkeypatch):
    async def cerrada(pool, usuario):
        return False

    monkeypatch.setattr(plataforma.REVOCACION, "vigente", cerrada)
    r = client.get("/auth/verificar", headers={"Authorization": f"Bearer {token(session_id='x')}"})
    assert r.status_code == 401 and "cerró" in r.json()["detail"]


@pytest.mark.parametrize(
    ("entorno", "esperado"),
    [
        ({}, False),
        ({"ERP_REQUIRE_AUTH": ""}, False),
        ({"ERP_REQUIRE_AUTH": "true"}, True),
        ({"REQUIRE_AUTH": "true"}, True),
        ({"REQUIRE_AUTH": "true", "ERP_REQUIRE_AUTH": "false"}, False),
        ({"REQUIRE_AUTH": "false", "ERP_REQUIRE_AUTH": "true"}, True),
    ],
)
def test_erp_require_auth(monkeypatch, entorno, esperado):
    monkeypatch.delenv("REQUIRE_AUTH", raising=False)
    monkeypatch.delenv("ERP_REQUIRE_AUTH", raising=False)
    for clave, valor in entorno.items():
        monkeypatch.setenv(clave, valor)
    assert ErpApiSettings().require_auth is esperado


@pytest.mark.parametrize(
    "malo",
    [
        token(exp=int(time.time()) - 5),
        token(secreto="secreto-equivocado-de-al-menos-32-caracteres"),
        token(role="anon"),
        "basura",
    ],
    ids=["vencido", "otro-secreto", "anon-key", "basura"],
)
def test_verificar_tokens_invalidos_401(client, malo):
    assert client.get("/auth/verificar", headers={"Authorization": f"Bearer {malo}"}).status_code == 401


def test_verificar_sin_secreto_falla_cerrado(client, monkeypatch):
    # 503 (no 401): nginx no deja pasar nada y el frontend lo muestra en rojo.
    monkeypatch.setattr(get_settings(), "supabase_jwt_secret", "")
    r = client.get("/auth/verificar", headers={"Authorization": f"Bearer {token()}"})
    assert r.status_code == 503
    assert "SUPABASE_JWT_SECRET" in r.json()["detail"]
    assert client.get("/auth/verificar").status_code == 503


# --- Usuarios (GoTrue admin) ---------------------------------------------------------

def _gotrue_user(id_=OTRO, email="ana@empresa.do", **cambios) -> dict:
    u = {"id": id_, "email": email, "created_at": "2026-10-01T12:00:00Z", "last_sign_in_at": None,
         "email_confirmed_at": "2026-10-01T12:00:00Z", "banned_until": None, "invited_at": None,
         "app_metadata": {"provider": "email", "kontara": True}}
    u.update(cambios)
    return u


@respx.mock
def test_listar_usuarios(client):
    ruta = respx.get(f"{GW}/auth/v1/admin/users").mock(
        return_value=httpx.Response(200, json={"users": [
            _gotrue_user(), _gotrue_user(YO, "yo@empresa.do", banned_until="2126-01-01T00:00:00Z"),
            _gotrue_user("33333333-3333-3333-3333-333333333333", "ajeno@otra.do", app_metadata={"provider": "email"}),
        ]})
    )
    r = client.get("/configuracion/usuarios")
    assert r.status_code == 200
    cuerpo = r.json()
    assert cuerpo["configurado"] is True
    # Solo los de Kontara; los demás del mismo GoTrue, aparte y "sin acceso".
    assert [u["correo"] for u in cuerpo["usuarios"]] == ["ana@empresa.do", "yo@empresa.do"]
    assert cuerpo["usuarios"][1]["activo"] is False
    assert all(u["acceso"] for u in cuerpo["usuarios"])
    assert [(u["correo"], u["acceso"]) for u in cuerpo["sin_acceso"]] == [("ajeno@otra.do", False)]
    enviado = ruta.calls[0].request
    assert enviado.headers["apikey"] == SERVICE_KEY
    assert enviado.headers["authorization"] == f"Bearer {SERVICE_KEY}"


def test_usuarios_sin_service_role_key(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "supabase_service_role_key", "")
    r = client.get("/configuracion/usuarios")
    assert r.status_code == 200
    assert r.json()["configurado"] is False
    assert "Supabase Studio" in r.json()["mensaje"]
    r = client.post("/configuracion/usuarios", json={"correo": "x@y.do", "contrasena": "12345678"})
    assert r.status_code == 503


@respx.mock
def test_crear_usuario_con_contrasena(client):
    ruta = respx.post(f"{GW}/auth/v1/admin/users").mock(return_value=httpx.Response(200, json=_gotrue_user()))
    r = client.post("/configuracion/usuarios", json={"correo": "Ana@Empresa.do", "contrasena": "clave-segura-1"})
    assert r.status_code == 201
    assert json.loads(ruta.calls[0].request.content) == {
        "email": "ana@empresa.do", "password": "clave-segura-1", "email_confirm": True,
        "app_metadata": {"kontara": True},
    }
    assert r.json()["acceso"] is True


@respx.mock
def test_invitar_usuario_sin_contrasena(client):
    invitado = _gotrue_user(invited_at="2026-10-24T00:00:00Z", email_confirmed_at=None, app_metadata={"provider": "email"})
    ruta = respx.post(f"{GW}/auth/v1/invite").mock(return_value=httpx.Response(200, json=invitado))
    # /invite no recibe app_metadata: la marca se pone justo después.
    marca = respx.put(f"{GW}/auth/v1/admin/users/{OTRO}").mock(
        return_value=httpx.Response(200, json={**invitado, "app_metadata": {"provider": "email", "kontara": True}})
    )
    r = client.post("/configuracion/usuarios", json={"correo": "ana@empresa.do"})
    assert r.status_code == 201
    assert r.json()["invitado"] is True and r.json()["acceso"] is True
    assert ruta.called
    assert json.loads(marca.calls[0].request.content) == {"app_metadata": {"kontara": True}}


@respx.mock
def test_dar_acceso_a_usuario_existente(client):
    marca = respx.put(f"{GW}/auth/v1/admin/users/{OTRO}").mock(return_value=httpx.Response(200, json=_gotrue_user()))
    r = client.post(f"/configuracion/usuarios/{OTRO}/acceso")
    assert r.status_code == 200 and r.json()["acceso"] is True
    assert json.loads(marca.calls[0].request.content) == {"app_metadata": {"kontara": True}}


@respx.mock
def test_no_se_activa_ni_desactiva_a_quien_no_es_de_kontara(client):
    respx.get(f"{GW}/auth/v1/admin/users/{OTRO}").mock(
        return_value=httpx.Response(200, json=_gotrue_user(app_metadata={"provider": "email"}))
    )
    cambio = respx.put(f"{GW}/auth/v1/admin/users/{OTRO}")
    assert client.post(f"/configuracion/usuarios/{OTRO}/activar").status_code == 404
    assert client.post(f"/configuracion/usuarios/{OTRO}/desactivar").status_code == 404
    assert not cambio.called


@respx.mock
def test_error_de_gotrue_se_traduce(client):
    respx.post(f"{GW}/auth/v1/admin/users").mock(
        return_value=httpx.Response(422, json={"code": 422, "msg": "A user with this email address has already been registered"})
    )
    r = client.post("/configuracion/usuarios", json={"correo": "ana@empresa.do", "contrasena": "clave-segura-1"})
    assert r.status_code == 422
    assert "already been registered" in r.json()["detail"]


@respx.mock
def test_desactivar_y_activar_usuario(client):
    respx.get(f"{GW}/auth/v1/admin/users/{OTRO}").mock(return_value=httpx.Response(200, json=_gotrue_user()))
    ruta = respx.put(f"{GW}/auth/v1/admin/users/{OTRO}").mock(
        return_value=httpx.Response(200, json=_gotrue_user(banned_until="2126-01-01T00:00:00Z"))
    )
    r = client.post(f"/configuracion/usuarios/{OTRO}/desactivar", headers={"Authorization": f"Bearer {token()}"})
    assert r.status_code == 200
    assert r.json()["activo"] is False
    assert b"876000h" in ruta.calls[0].request.content
    ruta.mock(return_value=httpx.Response(200, json=_gotrue_user()))
    r = client.post(f"/configuracion/usuarios/{OTRO}/activar")
    assert r.json()["activo"] is True
    assert b'"none"' in ruta.calls[1].request.content


def test_no_puede_desactivarse_a_si_mismo(client):
    r = client.post(f"/configuracion/usuarios/{YO}/desactivar", headers={"Authorization": f"Bearer {token()}"})
    assert r.status_code == 409


def test_usuario_id_invalido(client):
    assert client.post("/configuracion/usuarios/../../otra/desactivar").status_code in (404, 422)
    assert client.post("/configuracion/usuarios/no-es-uuid/activar").status_code == 422


# --- Estado de las soluciones ---------------------------------------------------------

@respx.mock
def test_estado_servicios(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "estado_servicios", {
        "facturas-api": "http://facturas-api:8000",
        "inventario-api": "http://inventario-api:8000",
        "recepcion": "http://recepcion:8000",
        "receptor-recepcion": "http://receptor-recepcion:8000",
    })
    respx.get("http://facturas-api:8000/health").mock(
        return_value=httpx.Response(200, json={"status": "ok", "app_env": "test", "dgii_environment": "testecf"})
    )
    respx.get("http://inventario-api:8000/health").mock(return_value=httpx.Response(500))
    respx.get("http://recepcion:8000/health").mock(side_effect=httpx.ConnectError("refused"))
    respx.get("http://receptor-recepcion:8000/health").mock(side_effect=httpx.ReadTimeout("lento"))
    respx.get(f"{GW}/auth/v1/health").mock(return_value=httpx.Response(200, json={"version": "v2.177.0"}))

    r = client.get("/estado-servicios")
    assert r.status_code == 200
    cuerpo = r.json()
    assert cuerpo["ambiente"] == get_settings().app_env
    por_id = {s["id"]: s for s in cuerpo["servicios"]}
    assert por_id["erp-api"]["estado"] == "ok"
    assert por_id["facturas-api"]["estado"] == "ok"
    assert por_id["facturas-api"]["detalle"] == "test · testecf"
    assert por_id["inventario-api"]["estado"] == "error"
    assert "500" in por_id["inventario-api"]["detalle"]
    assert por_id["recepcion"]["estado"] == "caido"
    assert por_id["receptor-recepcion"]["estado"] == "caido"
    assert "No respondió" in por_id["receptor-recepcion"]["detalle"]
    assert por_id["base-datos"]["estado"] == "ok"
    assert por_id["supabase-auth"]["estado"] == "ok"
    assert por_id["supabase-auth"]["detalle"] == "GoTrue v2.177.0"
    assert por_id["receptor-recepcion"]["grupo"] == "Receptor electrónico"
    # Mismo orden que el catálogo (aplicación primero).
    assert cuerpo["servicios"][0]["id"] == "facturas-api"


@respx.mock
def test_estado_servicios_sin_supabase_configurado(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "estado_servicios", {})
    monkeypatch.setattr(get_settings(), "supabase_service_role_key", "")
    por_id = {s["id"]: s for s in client.get("/estado-servicios").json()["servicios"]}
    assert por_id["supabase-auth"]["estado"] == "no_configurado"


async def test_base_de_datos_caida():
    class PoolRoto:
        def acquire(self, timeout=None):
            raise OSError("connection refused")

    assert (await estado_servicios.consultar_base_datos(PoolRoto(), 1))["estado"] == "caido"
    assert (await estado_servicios.consultar_base_datos(None, 1))["estado"] == "caido"


def test_rutas_de_negocio_no_piden_sesion_sin_require_auth(client, monkeypatch):
    async def fake_listar(pool, **kw):
        return []

    from services.erp_api.app import configuracion_repository
    monkeypatch.setattr(configuracion_repository, "listar_vendedores", fake_listar)
    assert client.get("/configuracion/vendedores").status_code == 200
