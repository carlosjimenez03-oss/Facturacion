"""Configuración → Ambiente DGII (fase 7) contra un Postgres REAL: permisos de
administrador, confirmación y certificado para pasar a producción, bloqueo
con facturas en contingencia o lotes abiertos, auditoría, y que
facturas_api y los servicios de la DGII leen el ambiente guardado
(libs/ecf_common/ambiente.py) y crean las facturas con él.

Se salta sin `CONTINGENCIA_TEST_DATABASE_URL` (DSN de una base con permiso
para crear bases).
"""
import os
import time
import uuid
from pathlib import Path

import asyncpg
import httpx
import jwt
import pytest

from libs.ecf_common.ambiente import AmbienteDGII
from services.erp_api.app import ambiente_dgii, main
from services.erp_api.app.config import get_settings
from services.erp_api.app.migraciones import aplicar_migraciones
from services.facturas_api.app import secuencias_encf
from services.facturas_api.app.migraciones import aplicar_migraciones as migrar_facturas

DSN = os.environ.get("CONTINGENCIA_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="sin CONTINGENCIA_TEST_DATABASE_URL (Postgres real)")

REPO = Path(__file__).resolve().parents[3]
SECRETO = "super-secret-jwt-token-with-at-least-32-characters-long"
ADMIN = "11111111-1111-1111-1111-111111111111"


def _token(*, admin: bool, correo="admin@caribe.do") -> str:
    metadatos = {"kontara": True, **({"kontara_admin": True} if admin else {})}
    claims = {"sub": ADMIN if admin else str(uuid.uuid4()), "email": correo, "aud": "authenticated",
              "role": "authenticated", "exp": int(time.time()) + 3600, "app_metadata": metadatos}
    return jwt.encode(claims, SECRETO, algorithm="HS256")


@pytest.fixture
async def pool():
    nombre = f"ambiente_{uuid.uuid4().hex[:10]}"
    admin = await asyncpg.connect(DSN)
    await admin.execute(f"CREATE DATABASE {nombre} TEMPLATE template0 ENCODING 'UTF8'")
    await admin.close()
    dsn = DSN.rsplit("/", 1)[0] + f"/{nombre}"
    conn = await asyncpg.connect(dsn)
    for archivo in sorted((REPO / "db" / "init").glob("*.sql")):
        await conn.execute(archivo.read_text(encoding="utf-8"))
    await conn.close()
    p = await asyncpg.create_pool(dsn, min_size=1, max_size=4)
    await aplicar_migraciones(p)
    await migrar_facturas(p)
    yield p
    await p.close()
    admin = await asyncpg.connect(DSN)
    await admin.execute(f"DROP DATABASE IF EXISTS {nombre} WITH (FORCE)")
    await admin.close()


@pytest.fixture
async def api(pool, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "supabase_jwt_secret", SECRETO)
    monkeypatch.setattr(settings, "app_env", "test")
    monkeypatch.setattr(ambiente_dgii, "ACTIVO", AmbienteDGII("test"))
    certificado = {"estado": "vigente", "coincide_rnc_emisor": True}

    def estado_certificado(path, password, rnc):
        return dict(certificado)

    monkeypatch.setattr(ambiente_dgii.certificado_activo, "estado_certificado", estado_certificado)
    main.app.state.db_pool = pool
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://erp") as cliente:
        cliente.certificado = certificado
        yield cliente
    main.app.state.db_pool = None


def _como(admin=True):
    return {"Authorization": f"Bearer {_token(admin=admin)}"}


async def test_sin_fila_usa_app_env(api):
    r = await api.get("/configuracion/ambiente", headers=_como())
    cuerpo = r.json()
    assert r.status_code == 200
    assert (cuerpo["ambiente"], cuerpo["origen"], cuerpo["ambiente_dgii"]) == ("test", "variables", "testecf")
    assert cuerpo["puede_cambiar"] is True and cuerpo["cambios"] == []
    assert cuerpo["certificado"]["valido"] is True
    # Sin sesión de administrador no puede cambiarlo (y la pantalla lo sabe).
    r = await api.get("/configuracion/ambiente", headers=_como(admin=False))
    assert r.json()["puede_cambiar"] is False and "administrador" in r.json()["motivo_sin_permiso"]


async def test_cambio_a_produccion_con_confirmacion_y_auditoria(api, pool):
    assert (await api.put("/configuracion/ambiente", json={"ambiente": "production", "confirmacion": True},
                          headers=_como(admin=False))).status_code == 403
    sin_sesion = await api.put("/configuracion/ambiente", json={"ambiente": "production", "confirmacion": True})
    assert sin_sesion.status_code == 403 and "Inicie sesión" in sin_sesion.json()["detail"]
    r = await api.put("/configuracion/ambiente", json={"ambiente": "production"}, headers=_como())
    assert r.status_code == 422 and "validez fiscal" in r.json()["detail"]
    assert (await api.put("/configuracion/ambiente", json={"ambiente": "staging"}, headers=_como())).status_code == 422

    api.certificado.update(estado="vencido")
    r = await api.put("/configuracion/ambiente", json={"ambiente": "ecf", "confirmacion": True}, headers=_como())
    assert r.status_code == 409 and "vencido" in r.json()["detail"]
    api.certificado.update(estado="vigente", coincide_rnc_emisor=False)
    r = await api.put("/configuracion/ambiente", json={"ambiente": "ecf", "confirmacion": True}, headers=_como())
    assert r.status_code == 409 and "no pertenece al RNC" in r.json()["detail"]
    assert await pool.fetchval("SELECT count(*) FROM configuracion.ambiente_dgii") == 0

    api.certificado.update(coincide_rnc_emisor=True)
    r = await api.put("/configuracion/ambiente", json={"ambiente": "ecf", "confirmacion": True}, headers=_como())
    assert r.status_code == 200
    cuerpo = r.json()
    assert (cuerpo["ambiente"], cuerpo["origen"], cuerpo["cambiado_por"]) == ("production", "configuracion", "admin@caribe.do")
    assert any("no tiene secuencias" in a for a in cuerpo["avisos"])
    assert [(c["anterior"], c["nuevo"], c["usuario_id"]) for c in cuerpo["cambios"]] == [("test", "production", ADMIN)]

    # Repetir el mismo ambiente no audita nada nuevo; volver a pruebas no
    # pide certificado ni confirmación.
    await api.put("/configuracion/ambiente", json={"ambiente": "production", "confirmacion": True}, headers=_como())
    api.certificado.update(estado="no_configurado")
    r = await api.put("/configuracion/ambiente", json={"ambiente": "test"}, headers=_como())
    assert r.status_code == 200 and r.json()["ambiente"] == "test"
    cambios = await pool.fetch("SELECT anterior, nuevo, usuario_correo FROM configuracion.ambiente_dgii_cambios ORDER BY id")
    assert [(c["anterior"], c["nuevo"]) for c in cambios] == [("test", "production"), ("production", "test")]
    assert {c["usuario_correo"] for c in cambios} == {"admin@caribe.do"}


async def test_bloqueado_con_contingencia_o_lotes_abiertos(api, pool):
    fid = await pool.fetchval(
        "INSERT INTO facturacion.facturas (tipo_ecf, ambiente, rnc_emisor, encf, estado, datos)"
        " VALUES (31, 'test', '131897542', 'E310000000001', 'contingencia', '{}') RETURNING id"
    )
    estado = (await api.get("/configuracion/ambiente", headers=_como())).json()
    assert estado["puede_cambiar"] is False and estado["pendientes_contingencia"] == 1
    r = await api.put("/configuracion/ambiente", json={"ambiente": "qa"}, headers=_como())
    assert r.status_code == 409 and "contingencia" in r.json()["detail"]

    await pool.execute("UPDATE facturacion.facturas SET estado = 'enviado' WHERE id = $1", fid)
    await pool.execute("INSERT INTO facturacion.lotes_contingencia (id, estado) VALUES ('A-0001', 'enviando')")
    r = await api.put("/configuracion/ambiente", json={"ambiente": "qa"}, headers=_como())
    assert r.status_code == 409 and "lote" in r.json()["detail"]

    await pool.execute("UPDATE facturacion.lotes_contingencia SET estado = 'completado'")
    assert (await api.put("/configuracion/ambiente", json={"ambiente": "qa"}, headers=_como())).status_code == 200
    assert await pool.fetchval("SELECT ambiente FROM configuracion.ambiente_dgii") == "qa"


async def test_los_servicios_leen_el_ambiente_guardado(api, pool):
    """facturas_api, recepcion y consulta (AmbienteDGII) toman el ambiente
    de la fila; una factura nueva queda en ese ambiente con su secuencia."""
    lector = AmbienteDGII("test", ttl_segundos=0)
    assert await lector.actual(pool) == "test"
    await api.put("/configuracion/secuencias/31", params={"ambiente": "qa"}, json={"desde": 500, "hasta": 900},
                  headers=_como())
    await api.put("/configuracion/ambiente", json={"ambiente": "qa"}, headers=_como())
    assert await lector.actual(pool) == "qa" and await lector.origen(pool) == "configuracion"

    factura = await secuencias_encf.crear_factura(
        pool, tipo_ecf=31, ambiente=await lector.actual(pool), rnc_emisor="131897542", encf=None,
        datos={"Encabezado": {"IdDoc": {"TipoeCF": "31"}}}, asignar_encf=True,
    )
    assert (factura["ambiente"], factura["encf"]) == ("qa", "E310000000500")
    # Configuración muestra las secuencias del ambiente activo y lo que va usado de cada una.
    secuencias = (await api.get("/configuracion/secuencias", headers=_como())).json()
    e31 = next(s for s in secuencias if s["tipo_ecf"] == 31)
    assert (e31["ambiente"], e31["siguiente_encf"], e31["usados"]) == ("qa", "E310000000501", 1)
    pruebas = (await api.get("/configuracion/secuencias", params={"ambiente": "test"}, headers=_como())).json()
    assert next(s for s in pruebas if s["tipo_ecf"] == 31)["configurada"] is False
    estado = (await api.get("/configuracion/ambiente", headers=_como())).json()
    assert estado["secuencias"]["qa"] == 1 and estado["facturas"]["qa"] == 1
    # Datos de la empresa y estado de las soluciones muestran el activo.
    assert (await api.get("/configuracion/empresa", headers=_como())).json()["ambiente_dgii"] == "certecf"
