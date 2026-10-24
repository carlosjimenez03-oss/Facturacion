"""Verificación del access_token de Supabase Auth (GoTrue, HS256), la
autorización de Kontara (app_metadata.kontara / KONTARA_ADMIN_EMAILS), la
cookie solo en descargas (CSRF), las llamadas internas (X-Kontara-Interno),
las sesiones cerradas (auth.sessions) y el middleware REQUIRE_AUTH."""
import asyncio
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import jwt
import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from libs.ecf_common.auth.sesion_supabase import (
    CABECERA_INTERNA,
    COOKIE_SESION,
    SIN_ACCESO,
    RevocacionSesiones,
    SesionInvalidaError,
    UsuarioSesion,
    cabeceras_internas,
    cookie_permitida,
    correos_admin,
    instalar_verificacion_sesion,
    token_de_cabeceras,
    verificar_token_supabase,
)

SECRETO = "super-secret-jwt-token-with-at-least-32-characters-long"


def token(secreto: str = SECRETO, **cambios) -> str:
    claims = {
        "sub": "8d0f6c1e-4d1b-4c8e-9a1a-0a5f3f1f2b3c",
        "email": "carlos@empresa.do",
        "aud": "authenticated",
        "role": "authenticated",
        "exp": int(time.time()) + 3600,
        "iat": int(time.time()),
        "app_metadata": {"provider": "email", "kontara": True},
    }
    claims.update(cambios)
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, secreto, algorithm="HS256")


def test_token_valido_devuelve_el_usuario():
    usuario = verificar_token_supabase(token(session_id="5e8f0c2a-0000-4000-8000-000000000001"), SECRETO)
    assert usuario.id == "8d0f6c1e-4d1b-4c8e-9a1a-0a5f3f1f2b3c"
    assert usuario.correo == "carlos@empresa.do"
    assert usuario.rol == "authenticated"
    assert usuario.sesion_id == "5e8f0c2a-0000-4000-8000-000000000001"
    assert usuario.falta_marca is False and usuario.admin is False


@pytest.mark.parametrize("metadatos", [None, {"provider": "email"}, {"kontara": "true"}, {"kontara": 1}, "kontara"])
def test_sin_la_marca_de_kontara_no_entra(metadatos):
    # Un usuario del mismo GoTrue (registro abierto, OTP, magic link, SSO...)
    # tiene un token perfectamente válido pero no es de Kontara.
    with pytest.raises(SesionInvalidaError) as exc:
        verificar_token_supabase(token(app_metadata=metadatos), SECRETO)
    assert str(exc.value) == SIN_ACCESO


def test_usuario_anonimo_no_entra_aunque_tenga_la_marca():
    with pytest.raises(SesionInvalidaError, match="anónimos"):
        verificar_token_supabase(token(is_anonymous=True), SECRETO)


def test_correos_admin_entran_sin_marca_y_se_avisa_que_falta():
    admins = correos_admin(" Carlos@Empresa.do , otra@x.do,, ")
    assert admins == {"carlos@empresa.do", "otra@x.do"}
    usuario = verificar_token_supabase(token(app_metadata={"provider": "email"}), SECRETO, admins=admins)
    assert usuario.falta_marca is True and usuario.admin is True
    # Acepta también el texto de la variable tal cual.
    assert verificar_token_supabase(token(app_metadata=None), SECRETO, admins="carlos@empresa.do").falta_marca
    with pytest.raises(SesionInvalidaError):
        verificar_token_supabase(token(app_metadata=None, email="intruso@x.do"), SECRETO, admins=admins)


def test_marca_admin():
    usuario = verificar_token_supabase(token(app_metadata={"kontara": True, "kontara_admin": True}), SECRETO)
    assert usuario.admin is True and usuario.falta_marca is False


def test_token_vencido():
    with pytest.raises(SesionInvalidaError, match="expiró"):
        verificar_token_supabase(token(exp=int(time.time()) - 10), SECRETO)


def test_token_firmado_con_otro_secreto():
    with pytest.raises(SesionInvalidaError, match="inválido"):
        verificar_token_supabase(token(secreto="otro-secreto-de-al-menos-32-caracteres!!"), SECRETO)


def test_token_alterado_o_basura():
    t = token()
    alterado = t[:-4] + ("AAAA" if not t.endswith("AAAA") else "BBBB")
    for malo in (alterado, "no-es-un-jwt", "a.b.c"):
        with pytest.raises(SesionInvalidaError):
            verificar_token_supabase(malo, SECRETO)


def test_audiencia_incorrecta():
    with pytest.raises(SesionInvalidaError):
        verificar_token_supabase(token(aud="otra"), SECRETO)


@pytest.mark.parametrize("rol", ["anon", "service_role"])
def test_llaves_anon_y_service_role_no_son_sesion(rol):
    with pytest.raises(SesionInvalidaError, match="role"):
        verificar_token_supabase(token(role=rol), SECRETO)


def test_sin_exp_se_rechaza():
    with pytest.raises(SesionInvalidaError):
        verificar_token_supabase(token(exp=None), SECRETO)


def test_algoritmo_none_se_rechaza():
    sin_firma = jwt.encode({"sub": "x", "aud": "authenticated", "role": "authenticated",
                            "exp": int(time.time()) + 60}, None, algorithm="none")
    with pytest.raises(SesionInvalidaError):
        verificar_token_supabase(sin_firma, SECRETO)


def test_sin_secreto_configurado_falla_cerrado():
    with pytest.raises(SesionInvalidaError, match="SUPABASE_JWT_SECRET"):
        verificar_token_supabase(token(), "")


PDF = "/facturas/0b6d2f0e-1c3a-4c1e-9a55-1f0f3c2d4e5f/representacion-impresa"
XML = "/api-erp/compras/bandeja/0b6d2f0e-1c3a-4c1e-9a55-1f0f3c2d4e5f/xml"


def test_token_de_cabeceras():
    galleta = f"otra=1; {COOKIE_SESION}=xyz"
    assert token_de_cabeceras("Bearer abc", None) == "abc"
    assert token_de_cabeceras("bearer  abc ", None) == "abc"
    assert token_de_cabeceras("Basic abc", None) is None
    assert token_de_cabeceras(None, galleta, metodo="GET", ruta=PDF) == "xyz"
    assert token_de_cabeceras("Bearer abc", galleta, metodo="POST", ruta="/x") == "abc"
    assert token_de_cabeceras(None, "otra=1", metodo="GET", ruta=PDF) is None
    assert token_de_cabeceras(None, None) is None
    # La cookie sola no vale fuera de las descargas GET/HEAD (CSRF).
    assert token_de_cabeceras(None, galleta) is None
    assert token_de_cabeceras(None, galleta, metodo="POST", ruta=PDF) is None
    assert token_de_cabeceras(None, galleta, metodo="GET", ruta="/configuracion/usuarios") is None


@pytest.mark.parametrize(
    ("metodo", "ruta", "permitida"),
    [
        ("GET", PDF, True),
        ("HEAD", PDF, True),
        ("GET", "/api" + PDF, True),
        ("GET", PDF + "?descarga=1", True),
        ("GET", XML, True),
        ("get", XML, True),
        ("POST", PDF, False),
        ("PUT", XML, False),
        ("DELETE", PDF, False),
        ("GET", "/facturas/x", False),
        ("GET", "/api/facturas/../configuracion/representacion-impresa", False),
        ("GET", "/api/facturas/a%2F..%2Fb/representacion-impresa", False),
        ("GET", "/configuracion/usuarios?r=" + PDF, False),
        ("GET", None, False),
        (None, PDF, False),
    ],
)
def test_cookie_solo_en_descargas(metodo, ruta, permitida):
    assert cookie_permitida(metodo, ruta) is permitida


INTERNO = "token-interno-de-prueba-con-32-caracteres!"


def _app(require_auth: bool, **extra) -> TestClient:
    app = FastAPI()
    instalar_verificacion_sesion(
        app,
        SimpleNamespace(
            require_auth=require_auth,
            supabase_jwt_secret=SECRETO,
            kontara_admin_emails="admin@empresa.do",
            kontara_token_interno=INTERNO,
        ),
        rutas_publicas={"/publica"},
        **extra,
    )
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/publica")
    def publica():
        return {"ok": True}

    @app.get("/datos")
    def datos():
        return {"ok": True}

    @app.post("/datos")
    def crear():
        return {"ok": True}

    @app.get(PDF)
    def pdf():
        return {"ok": True}

    @app.post(PDF)
    def pdf_post():
        return {"ok": True}

    return TestClient(app)


def test_middleware_apagado_no_exige_nada():
    assert _app(False).get("/datos").status_code == 200


def test_middleware_encendido():
    c = _app(True)
    sin = c.get("/datos")
    assert sin.status_code == 401
    assert sin.headers["www-authenticate"] == "Bearer"
    assert c.get("/datos", headers={"Authorization": f"Bearer {token()}"}).status_code == 200
    assert c.post("/datos", headers={"Authorization": f"Bearer {token()}"}).status_code == 200
    assert c.get("/datos", headers={"Authorization": f"Bearer {token(exp=1)}"}).status_code == 401
    assert c.get("/health").status_code == 200
    assert c.get("/publica").status_code == 200


def test_middleware_cookie_solo_para_descargas_get():
    c = _app(True)
    c.cookies.set(COOKIE_SESION, token())
    assert c.get(PDF).status_code == 200
    # Con la cookie sola: ni POST (aunque sea la misma ruta) ni otras rutas.
    assert c.post(PDF).status_code == 401
    assert c.post("/datos").status_code == 401
    assert c.get("/datos").status_code == 401


def test_middleware_rechaza_usuario_sin_marca_y_acepta_correo_admin():
    c = _app(True)
    sin_marca = c.get("/datos", headers={"Authorization": f"Bearer {token(app_metadata={})}"})
    assert sin_marca.status_code == 401 and sin_marca.json()["detail"] == SIN_ACCESO
    admin = token(app_metadata={}, email="Admin@Empresa.do")
    assert c.get("/datos", headers={"Authorization": f"Bearer {admin}"}).status_code == 200


def test_middleware_acepta_llamadas_internas():
    c = _app(True)
    assert cabeceras_internas(INTERNO) == {CABECERA_INTERNA: INTERNO}
    assert c.post("/datos", headers=cabeceras_internas(INTERNO)).status_code == 200
    assert c.post("/datos", headers={CABECERA_INTERNA: INTERNO[:-1] + "x"}).status_code == 401
    assert c.post("/datos", headers={CABECERA_INTERNA: ""}).status_code == 401


def test_token_interno_corto_no_sirve():
    assert cabeceras_internas("corto") == {}
    assert cabeceras_internas("") == {}
    app = FastAPI()
    instalar_verificacion_sesion(
        app, SimpleNamespace(require_auth=True, supabase_jwt_secret=SECRETO, kontara_token_interno="corto")
    )

    @app.get("/datos")
    def datos():
        return {"ok": True}

    assert TestClient(app).get("/datos", headers={CABECERA_INTERNA: "corto"}).status_code == 401


def test_middleware_al_verificar_y_revocacion():
    vistos = []

    async def al_verificar(request, usuario):
        vistos.append(usuario.id)

    class Revocadas(RevocacionSesiones):
        async def vigente(self, pool, usuario):
            return usuario.sesion_id != "cerrada"

    c = _app(True, al_verificar=al_verificar, revocacion=Revocadas())
    assert c.get("/datos", headers={"Authorization": f"Bearer {token(session_id='abierta')}"}).status_code == 200
    cerrada = c.get("/datos", headers={"Authorization": f"Bearer {token(session_id='cerrada')}"})
    assert cerrada.status_code == 401 and "cerró" in cerrada.json()["detail"]
    assert vistos == ["8d0f6c1e-4d1b-4c8e-9a1a-0a5f3f1f2b3c"]


# --- RevocacionSesiones (auth.sessions de Supabase) ---------------------------------------------------------

class _ConnSesiones:
    def __init__(self, filas, error=None):
        self.filas, self.error, self.consultas = filas, error, 0

    async def fetchrow(self, query, sesion_id):
        self.consultas += 1
        if self.error:
            raise self.error
        return self.filas.get(sesion_id)

    async def fetchval(self, query, sesion_id):
        self.consultas += 1
        return 1 if sesion_id in self.filas else None


class _PoolSesiones:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        return self

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *exc):
        return False


def _usuario(sesion_id):
    return UsuarioSesion(id="u", correo=None, rol="authenticated", expira=0, sesion_id=sesion_id)


def test_revocacion_consulta_auth_sessions_con_cache():
    ahora = datetime.now(timezone.utc)
    conn = _ConnSesiones({
        "viva": {"not_after": None, "banned_until": None, "deleted_at": None},
        "baneado": {"not_after": None, "banned_until": ahora + timedelta(days=36500), "deleted_at": None},
        "vencida": {"not_after": ahora - timedelta(seconds=1), "banned_until": None, "deleted_at": None},
        "borrado": {"not_after": None, "banned_until": None, "deleted_at": ahora},
        "desbaneado": {"not_after": None, "banned_until": ahora - timedelta(days=1), "deleted_at": None},
    })
    r = RevocacionSesiones(ttl=60)
    pool = _PoolSesiones(conn)

    async def correr():
        return {s: await r.vigente(pool, _usuario(s))
                for s in ("viva", "baneado", "vencida", "borrado", "desbaneado", "cerrada")}

    assert asyncio.run(correr()) == {
        "viva": True, "baneado": False, "vencida": False, "borrado": False, "desbaneado": True, "cerrada": False,
    }
    consultas = conn.consultas
    asyncio.run(correr())
    assert conn.consultas == consultas  # 60 s de caché


def test_revocacion_sin_session_id_o_sin_pool_no_consulta():
    r = RevocacionSesiones()
    assert asyncio.run(r.vigente(None, _usuario("x"))) is True
    assert asyncio.run(r.vigente(_PoolSesiones(_ConnSesiones({})), _usuario(None))) is True


def test_revocacion_se_apaga_si_no_hay_esquema_auth():
    conn = _ConnSesiones({}, error=RuntimeError('relation "auth.sessions" does not exist'))
    r = RevocacionSesiones()
    assert asyncio.run(r.vigente(_PoolSesiones(conn), _usuario("x"))) is True
    assert r.desactivada is True
    assert asyncio.run(r.vigente(_PoolSesiones(conn), _usuario("y"))) is True
    assert conn.consultas == 1


def test_middleware_deja_pasar_preflight_cors():
    c = _app(True)
    r = c.options("/datos", headers={"Origin": "https://front.example", "Access-Control-Request-Method": "GET"})
    assert r.status_code == 200
