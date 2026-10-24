"""Verificación del access_token de Supabase Auth (GoTrue, HS256) y el
middleware REQUIRE_AUTH de las APIs."""
import time
from types import SimpleNamespace

import jwt
import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from libs.ecf_common.auth.sesion_supabase import (
    COOKIE_SESION,
    SesionInvalidaError,
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
    }
    claims.update(cambios)
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, secreto, algorithm="HS256")


def test_token_valido_devuelve_el_usuario():
    usuario = verificar_token_supabase(token(), SECRETO)
    assert usuario.id == "8d0f6c1e-4d1b-4c8e-9a1a-0a5f3f1f2b3c"
    assert usuario.correo == "carlos@empresa.do"
    assert usuario.rol == "authenticated"


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


def test_token_de_cabeceras():
    assert token_de_cabeceras("Bearer abc", None) == "abc"
    assert token_de_cabeceras("bearer  abc ", None) == "abc"
    assert token_de_cabeceras("Basic abc", None) is None
    assert token_de_cabeceras(None, f"otra=1; {COOKIE_SESION}=xyz") == "xyz"
    assert token_de_cabeceras("Bearer abc", f"{COOKIE_SESION}=xyz") == "abc"
    assert token_de_cabeceras(None, "otra=1") is None
    assert token_de_cabeceras(None, None) is None


def _app(require_auth: bool) -> TestClient:
    app = FastAPI()
    instalar_verificacion_sesion(
        app, SimpleNamespace(require_auth=require_auth, supabase_jwt_secret=SECRETO), rutas_publicas={"/publica"}
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

    return TestClient(app)


def test_middleware_apagado_no_exige_nada():
    assert _app(False).get("/datos").status_code == 200


def test_middleware_encendido():
    c = _app(True)
    sin = c.get("/datos")
    assert sin.status_code == 401
    assert sin.headers["www-authenticate"] == "Bearer"
    assert c.get("/datos", headers={"Authorization": f"Bearer {token()}"}).status_code == 200
    assert c.get("/datos", cookies={COOKIE_SESION: token()}).status_code == 200
    assert c.get("/datos", headers={"Authorization": f"Bearer {token(exp=1)}"}).status_code == 401
    assert c.get("/health").status_code == 200
    assert c.get("/publica").status_code == 200


def test_middleware_deja_pasar_preflight_cors():
    c = _app(True)
    r = c.options("/datos", headers={"Origin": "https://front.example", "Access-Control-Request-Method": "GET"})
    assert r.status_code == 200
