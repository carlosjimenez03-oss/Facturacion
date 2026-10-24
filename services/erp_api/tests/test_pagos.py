"""Rutas de los pagos en línea (services/erp_api/app/pagos.py) con la base
mockeada: qué rutas quedan públicas, quién puede guardar la configuración,
que el AuthKey nunca sale y que un retorno sin firma válida no se aplica.
El flujo completo contra Postgres real está en test_pagos_postgres.py."""
import time
from types import SimpleNamespace

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from libs.ecf_common.auth.sesion_supabase import instalar_verificacion_sesion
from services.erp_api.app import main, pagos
from services.erp_api.app import pagos_repository as repo
from services.erp_api.app.config import get_settings

SECRETO = "secreto-jwt-de-prueba-de-al-menos-32-caracteres"


def _token(correo: str, **app_metadata) -> str:
    return jwt.encode(
        {"sub": "11111111-1111-1111-1111-111111111111", "email": correo, "aud": "authenticated", "role": "authenticated",
         "exp": int(time.time()) + 600, "app_metadata": {"kontara": True, **app_metadata}},
        SECRETO,
        algorithm="HS256",
    )


@pytest.fixture
def guardado(monkeypatch):
    estado = {"valor": {}}

    async def leer(pool, clave, por_defecto):
        return {**por_defecto, **estado["valor"]}

    async def guardar(pool, clave, valor):
        estado["valor"] = valor

    monkeypatch.setattr(pagos.preferencias, "leer_preferencia", leer)
    monkeypatch.setattr(pagos.preferencias, "guardar_preferencia", guardar)
    monkeypatch.setattr(pagos.get_pagos_settings(), "app_env", "test")
    monkeypatch.setattr(pagos.get_pagos_settings(), "azul_auth_key", "")
    return estado


@pytest.fixture
def client(monkeypatch, guardado):
    async def fake_crear_pool(database_url):
        return object()

    async def nada(*args, **kwargs):
        return None

    monkeypatch.setattr(main, "crear_pool", fake_crear_pool)
    monkeypatch.setattr(main, "cerrar_pool", nada)
    monkeypatch.setattr(main, "aplicar_migraciones", nada)
    monkeypatch.setattr(main.soluciones.get_soluciones_settings(), "soluciones_tarea_activa", False)
    monkeypatch.setattr(get_settings(), "supabase_jwt_secret", "")
    with TestClient(main.app) as c:
        yield c


def test_las_unicas_rutas_publicas_son_las_del_portal():
    """ERP_REQUIRE_AUTH deja pasar /portal/publico/*: que no haya caído ahí
    nada más que el portal y el pago (la configuración y el listado de
    pagos piden sesión)."""
    publicas = {
        (metodo, r.path)
        for r in main.app.routes
        if getattr(r, "path", "").startswith(main.RUTA_PORTAL_PUBLICO)
        for metodo in r.methods
    }
    assert publicas == {
        ("GET", "/portal/publico/{token}"),
        ("GET", "/portal/publico/{token}/facturas/{factura_id}/pdf"),
        ("GET", "/portal/publico/{token}/facturas/{factura_id}/xml"),
        ("POST", "/portal/publico/{token}/pagos"),
        ("GET", "/portal/publico/pagos/{referencia}"),
        ("GET", "/portal/publico/pagos/{referencia}/retorno/{resultado}"),
        ("POST", "/portal/publico/pagos/{referencia}/retorno/{resultado}"),
        ("POST", "/portal/publico/pagos/{referencia}/simulado"),
    }


def test_con_require_auth_la_configuracion_pide_sesion_y_el_pago_publico_no(monkeypatch, guardado):
    app = FastAPI()
    app.include_router(pagos.router)
    app.state.db_pool = object()
    ajustes = SimpleNamespace(require_auth=True, supabase_jwt_secret=SECRETO, kontara_admin_emails="", kontara_token_interno="")
    instalar_verificacion_sesion(app, ajustes, es_publica=lambda ruta: ruta.startswith(main.RUTA_PORTAL_PUBLICO))
    c = TestClient(app)
    assert c.get("/configuracion/pagos-en-linea").status_code == 401
    assert c.put("/configuracion/pagos-en-linea", json={}).status_code == 401
    assert c.get("/pagos-en-linea").status_code == 401
    assert c.get("/pagos-en-linea/x/eventos").status_code == 401
    # Público: responde por sí mismo (apagado = 409), nunca 401.
    r = c.post("/portal/publico/" + "A" * 43 + "/pagos", json={"factura_id": "11111111-1111-1111-1111-111111111111"})
    assert r.status_code == 409


def test_solo_un_administrador_guarda_la_configuracion(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "supabase_jwt_secret", SECRETO)
    monkeypatch.setattr(get_settings(), "kontara_admin_emails", "")
    datos = {"activo": False, "proveedor": "simulado"}
    assert client.put("/configuracion/pagos-en-linea", json=datos).status_code == 401
    usuario = {"Authorization": f"Bearer {_token('cajera@caribe.do')}"}
    r = client.put("/configuracion/pagos-en-linea", json=datos, headers=usuario)
    assert r.status_code == 403
    assert client.get("/configuracion/pagos-en-linea", headers=usuario).json()["puede_editar"] is False
    admin = {"Authorization": f"Bearer {_token('jefe@caribe.do', kontara_admin=True)}"}
    r = client.put("/configuracion/pagos-en-linea", json=datos, headers=admin)
    assert r.status_code == 200 and r.json()["puede_editar"] is True


def test_el_auth_key_nunca_se_devuelve(client, guardado, monkeypatch):
    monkeypatch.setenv("CERTIFICADOS_CLAVE", "clave-de-cifrado-de-prueba-larga-32+")
    r = client.put("/configuracion/pagos-en-linea", json={
        "activo": True, "proveedor": "azul", "ambiente": "pruebas", "merchant_id": "39000000001",
        "merchant_name": "Demo", "merchant_type": "ECommerce", "auth_key": "secreto-azul-123",
    })
    assert r.status_code == 200, r.text
    cuerpo = r.text + client.get("/configuracion/pagos-en-linea").text
    assert "secreto-azul-123" not in cuerpo and "auth_key_cifrada" not in cuerpo and "v2:" not in cuerpo
    assert r.json()["auth_key_configurada"] is True and r.json()["listo"] is True
    assert guardado["valor"]["auth_key_cifrada"].startswith("v2:")
    # Guardar sin auth_key conserva la guardada; borrar_auth_key la quita (y ya no se puede activar).
    r = client.put("/configuracion/pagos-en-linea", json={**r.json(), "auth_key": None})
    assert r.json()["auth_key_configurada"] is True
    r = client.put("/configuracion/pagos-en-linea", json={
        "activo": False, "proveedor": "azul", "merchant_id": "39000000001", "merchant_name": "Demo",
        "merchant_type": "ECommerce", "borrar_auth_key": True,
    })
    assert r.json()["auth_key_configurada"] is False and r.json()["faltantes"] == ["AuthKey"]


@pytest.mark.parametrize("cambios, mensaje", [
    ({"merchant_id": "39A"}, "solo dígitos"),
    ({"merchant_name": 'La "Tienda"'}, "comillas"),
])
def test_valida_los_datos_de_azul(client, cambios, mensaje):
    r = client.put("/configuracion/pagos-en-linea", json={"proveedor": "azul", **cambios})
    assert r.status_code == 422 and mensaje in r.json()["detail"]


def test_produccion_exige_url_publica(client, guardado, monkeypatch):
    monkeypatch.setattr(pagos.get_pagos_settings(), "app_env", "production")
    monkeypatch.setattr(pagos.get_pagos_settings(), "azul_auth_key", "llave")
    monkeypatch.setattr(pagos.get_pagos_settings(), "pagos_url_publica", "")
    datos = {"activo": True, "proveedor": "azul", "merchant_id": "1", "merchant_name": "D", "merchant_type": "E"}
    r = client.put("/configuracion/pagos-en-linea", json=datos)
    assert r.status_code == 422 and "PAGOS_URL_PUBLICA" in r.json()["detail"]
    monkeypatch.setattr(pagos.get_pagos_settings(), "pagos_url_publica", "https://front.ejemplo.do/")
    r = client.put("/configuracion/pagos-en-linea", json=datos)
    assert r.status_code == 200
    assert r.json()["auth_key_origen"] == "variable"
    assert r.json()["url_retorno"] == "https://front.ejemplo.do/api-erp/portal/publico/pagos/…/retorno/aprobado"


def test_retorno_sin_firma_valida_no_se_aplica(client, monkeypatch):
    pago = {"id": "11111111-1111-1111-1111-111111111111", "referencia": "R" * 43, "orden": "123", "proveedor": "simulado"}

    async def obtener(pool, referencia):
        return pago

    aplicados, eventos = [], []

    async def aplicar(*args, **kwargs):  # pragma: no cover (no debe llamarse)
        aplicados.append(args)

    async def evento(pool, pago_id, tipo, detalle, ip):
        eventos.append((tipo, detalle))

    monkeypatch.setattr(repo, "obtener_por_referencia", obtener)
    monkeypatch.setattr(repo, "aplicar_retorno", aplicar)
    monkeypatch.setattr(repo, "registrar_evento_suelto", evento)
    r = client.get(
        f"/portal/publico/pagos/{'R' * 43}/retorno/aprobado",
        params={"OrderNumber": "123", "Amount": "100", "IsoCode": "00", "AuthHash": "00" * 64, "CardNumber": "4111"},
        follow_redirects=False,
    )
    assert r.status_code == 303 and r.headers["location"] == f"/p/pago/{'R' * 43}?aviso=firma"
    assert aplicados == []
    assert eventos[0][0] == "firma_invalida" and "CardNumber" not in eventos[0][1]


def test_pago_desconocido_es_404(client):
    assert client.get("/portal/publico/pagos/no-valida").status_code == 404
    assert client.get("/portal/publico/pagos/x/retorno/aprobado", follow_redirects=False).status_code == 404
    assert client.get("/portal/publico/pagos/" + "R" * 43 + "/retorno/otra", follow_redirects=False).status_code == 422
