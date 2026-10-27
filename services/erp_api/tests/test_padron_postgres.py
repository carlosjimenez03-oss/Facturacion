"""Padrón de RNC de la DGII contra un Postgres REAL (padron.py): migración
(tabla global, sin RLS, con SELECT para kontara_app / kontara_todas),
importación (altas, cambios y bajas en una transacción: quien lee ve el
padrón anterior hasta el COMMIT; un fallo deja el anterior y anota el
error), advisory lock (una réplica a la vez), descarga, archivo subido y
las rutas de consulta.

Se salta sin `OPERACION_TEST_DATABASE_URL` (DSN con permiso para crear bases).
"""
import asyncio
import os
import tempfile
import threading
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import asyncpg
import httpx
import pytest

from libs.ecf_common import empresa as emp
from libs.ecf_common.db import crear_pool
from services.erp_api.app import empresas, padron
from services.erp_api.app.config import ErpApiSettings, get_settings
from services.erp_api.app.migraciones import aplicar_migraciones

DSN = os.environ.get("OPERACION_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="sin OPERACION_TEST_DATABASE_URL (Postgres real)")

REPO = Path(__file__).resolve().parents[3]


def _settings(**cambios) -> ErpApiSettings:
    return ErpApiSettings(**{"padron_activo": False, "padron_primera_espera_segundos": 0, **cambios})


@pytest.fixture
async def base():
    emp.invalidar_cache()
    nombre = f"padron_{uuid.uuid4().hex[:10]}"
    admin = await asyncpg.connect(DSN)
    await admin.execute(f"CREATE DATABASE {nombre} TEMPLATE template0 ENCODING 'UTF8'")
    await admin.close()
    dsn = DSN.rsplit("/", 1)[0] + f"/{nombre}"
    conn = await asyncpg.connect(dsn)
    for archivo in sorted((REPO / "db" / "init").glob("*.sql")):
        await conn.execute(archivo.read_text(encoding="utf-8"))
    await conn.close()
    pool = await crear_pool(dsn)
    await aplicar_migraciones(pool, ambiente_defecto="test")
    yield pool, dsn
    await pool.close()
    emp.invalidar_cache()
    admin = await asyncpg.connect(DSN)
    await admin.execute(f"DROP DATABASE IF EXISTS {nombre} WITH (FORCE)")
    await admin.close()


def _linea(rnc: str, nombre: str, estado="ACTIVO", regimen="NORMAL", comercial="") -> str:
    return f"{rnc}|{nombre}|{comercial}|COMERCIO AL POR MENOR| | | | |02/01/2005|{estado}|{regimen}"


def _zip(ruta: Path, lineas: list[str]) -> Path:
    with zipfile.ZipFile(ruta, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("DGII_RNC.TXT", ("\r\n".join(lineas) + "\r\n").encode("cp1252"))
    return ruta


def _muchas(n: int, prefijo="1") -> list[str]:
    return [_linea(f"{prefijo}{i:08d}", f"CONTRIBUYENTE {i} SRL") for i in range(n)]


async def _filas(dsn) -> dict[str, tuple]:
    conn = await asyncpg.connect(dsn)
    try:
        return {r["rnc"]: (r["razon_social"], r["estado"]) for r in await conn.fetch("SELECT * FROM configuracion.padron_rnc")}
    finally:
        await conn.close()


async def _estado(pool) -> dict:
    async with pool.acquire() as conn:
        return await padron.leer_estado(conn)


# --- migración ---------------------------------------------------------------------------------------

async def test_tablas_globales_sin_rls_y_con_select_para_los_roles(base):
    pool, dsn = base
    conn = await asyncpg.connect(dsn)
    try:
        for tabla in ("configuracion.padron_rnc", "configuracion.padron_rnc_estado"):
            fila = await conn.fetchrow(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE oid = $1::regclass", tabla
            )
            assert tuple(fila) == (False, False), tabla
            assert await conn.fetchval("SELECT count(*) FROM pg_policies WHERE schemaname || '.' || tablename = $1", tabla) == 0
            for rol in ("kontara_app", "kontara_todas"):
                assert await conn.fetchval("SELECT has_table_privilege($1, $2, 'SELECT')", rol, tabla), (rol, tabla)
            columnas = {r["column_name"] for r in await conn.fetch(
                "SELECT column_name FROM information_schema.columns WHERE table_schema || '.' || table_name = $1", tabla
            )}
            assert "empresa_id" not in columnas
    finally:
        await conn.close()
    # La migración es idempotente con el padrón ya cargado.
    await aplicar_migraciones(pool, ambiente_defecto="test")


# --- importación -------------------------------------------------------------------------------------

async def test_importa_y_luego_aplica_altas_cambios_y_bajas(base, tmp_path):
    pool, dsn = base
    s = _settings()
    a = _zip(tmp_path / "a.zip", [
        _linea("101000001", "PEÑA & ASOCIADOS SRL", comercial="PEÑA"),
        _linea("101000002", "ZAPATERIA LA BOTA SRL"),
        _linea("00100000001", "JUAN PÉREZ"),
        _linea("101000002", "ZAPATERIA LA BOTA (REPETIDO, GANA EL ÚLTIMO) SRL"),
    ])
    r = await padron.importar(pool, s, archivo=a, origen="prueba A")
    assert (r["filas"], r["nuevas"], r["cambiadas"], r["eliminadas"]) == (3, 3, 0, 0)
    filas = await _filas(dsn)
    assert filas["101000001"] == ("PEÑA & ASOCIADOS SRL", "ACTIVO")
    assert filas["101000002"][0] == "ZAPATERIA LA BOTA (REPETIDO, GANA EL ÚLTIMO) SRL"

    b = _zip(tmp_path / "b.zip", [
        _linea("101000001", "PEÑA & ASOCIADOS SRL", comercial="PEÑA"),  # igual: no se toca
        _linea("101000002", "ZAPATERIA LA BOTA SRL", estado="SUSPENDIDO"),
        _linea("131000003", "NUEVA EMPRESA SAS", regimen="RST"),
    ])
    conn = await asyncpg.connect(dsn)
    antes = await conn.fetchval("SELECT actualizado_en FROM configuracion.padron_rnc WHERE rnc = '101000001'")
    r = await padron.importar(pool, s, archivo=b, origen="prueba B")
    assert (r["filas"], r["nuevas"], r["cambiadas"], r["eliminadas"]) == (3, 1, 1, 1)
    assert await conn.fetchval("SELECT actualizado_en FROM configuracion.padron_rnc WHERE rnc = '101000001'") == antes
    await conn.close()
    assert await _filas(dsn) == {
        "101000001": ("PEÑA & ASOCIADOS SRL", "ACTIVO"),
        "101000002": ("ZAPATERIA LA BOTA SRL", "SUSPENDIDO"),
        "131000003": ("NUEVA EMPRESA SAS", "ACTIVO"),
    }
    estado = await _estado(pool)
    assert estado["filas"] == 3 and estado["origen"] == "prueba B" and estado["error"] is None
    assert estado["importado_en"] is not None and estado["en_curso"] is False


async def test_quien_lee_ve_el_padron_anterior_hasta_el_commit(base, tmp_path, monkeypatch):
    pool, dsn = base
    s = _settings()
    await padron.importar(pool, s, archivo=_zip(tmp_path / "a.zip", [_linea("101000001", "VIEJA SRL"), _linea("101000002", "SE VA SRL")]))
    llego, seguir = asyncio.Event(), asyncio.Event()

    async def pausa():
        llego.set()
        await seguir.wait()

    monkeypatch.setattr(padron, "_antes_de_confirmar", pausa)
    nuevo = _zip(tmp_path / "b.zip", [_linea("101000001", "NUEVA SRL"), _linea("131000009", "LLEGA SRL")])
    tarea = asyncio.create_task(padron.importar(pool, s, archivo=nuevo))
    await asyncio.wait_for(llego.wait(), 30)
    # Todo aplicado pero sin COMMIT: otra conexión (y la API) ve el anterior completo.
    assert await _filas(dsn) == {"101000001": ("VIEJA SRL", "ACTIVO"), "101000002": ("SE VA SRL", "ACTIVO")}
    erp = _app(pool, s)
    async with _cliente(erp) as cliente:
        assert (await cliente.get("/padron/101000001")).json()["razon_social"] == "VIEJA SRL"
        assert (await cliente.get("/padron/131000009")).status_code == 404
        estado = (await cliente.get("/padron/estado")).json()
        assert estado["en_curso"] is True
        # Una segunda importación (otra réplica, otro botón) no entra.
        assert (await cliente.post("/padron/actualizar")).status_code == 409
    with pytest.raises(padron.PadronOcupadoError):
        await padron.importar(pool, s, archivo=nuevo)
    seguir.set()
    await asyncio.wait_for(tarea, 30)
    assert await _filas(dsn) == {"101000001": ("NUEVA SRL", "ACTIVO"), "131000009": ("LLEGA SRL", "ACTIVO")}
    assert (await _estado(pool))["en_curso"] is False


async def test_otra_replica_con_el_lock_no_deja_importar(base, tmp_path):
    pool, dsn = base
    otra = await asyncpg.connect(dsn)
    try:
        assert await otra.fetchval("SELECT pg_try_advisory_lock($1)", padron.LOCK_PADRON)
        assert (await _estado(pool))["en_curso"] is True
        with pytest.raises(padron.PadronOcupadoError):
            await padron.importar(pool, _settings(), archivo=_zip(tmp_path / "a.zip", _muchas(3)))
        assert await _filas(dsn) == {}
        # actualizar_si_toca (la tarea diaria) lo deja pasar en silencio.
        assert await padron.actualizar_si_toca(pool, _settings()) is None
    finally:
        await otra.close()
    assert (await _estado(pool))["en_curso"] is False
    # El lock se suelta también cuando la importación falla.
    with pytest.raises(padron.PadronImportacionError):
        await padron.importar(pool, _settings(), archivo=tmp_path / "no-existe.zip")
    r = await padron.importar(pool, _settings(), archivo=_zip(tmp_path / "b.zip", _muchas(3)))
    assert r["filas"] == 3


@pytest.mark.parametrize("caso", ["danado", "incompleto", "formato", "vacio"])
async def test_un_fallo_conserva_el_padron_anterior_y_anota_el_error(base, tmp_path, caso):
    pool, dsn = base
    s = _settings()
    await padron.importar(pool, s, archivo=_zip(tmp_path / "a.zip", _muchas(2000)), origen="bueno")
    bueno = await _estado(pool)
    malo = tmp_path / "malo.zip"
    if caso == "danado":
        datos = _zip(tmp_path / "x.zip", _muchas(3000, prefijo="2")).read_bytes()
        malo.write_bytes(datos[: len(datos) // 2])
    elif caso == "incompleto":
        _zip(malo, _muchas(500))  # la cuarta parte: se rechaza
    elif caso == "formato":
        _zip(malo, _muchas(1500, prefijo="3") + ["xx;formato;nuevo"] * 1600)  # falla al FINAL del COPY
    else:
        malo.write_bytes(b"")
    with pytest.raises(padron.PadronImportacionError) as info:
        await padron.importar(pool, s, archivo=malo, origen="malo")
    filas = await _filas(dsn)
    assert len(filas) == 2000 and all(r.startswith("1") for r in filas)
    estado = await _estado(pool)
    assert estado["importado_en"] == bueno["importado_en"] and estado["origen"] == "bueno" and estado["filas"] == 2000
    assert estado["error"] == str(info.value) and estado["error_en"] is not None
    assert estado["intentado_en"] > bueno["importado_en"]
    if caso == "incompleto":
        assert "parece incompleto" in estado["error"]
    # Y la próxima buena limpia el error.
    await padron.importar(pool, s, archivo=_zip(tmp_path / "c.zip", _muchas(2001)))
    assert (await _estado(pool))["error"] is None


def test_toca_importar():
    ahora = datetime(2026, 10, 27, 12, tzinfo=timezone.utc)
    h = timedelta(hours=1)
    assert padron.toca_importar({}, ahora, 24)
    assert not padron.toca_importar({"en_curso": True}, ahora, 24)
    # Nunca importado, un intento fallido hace 10 minutos: espera la hora.
    assert not padron.toca_importar({"intentado_en": ahora - h / 6}, ahora, 24)
    assert padron.toca_importar({"intentado_en": ahora - 2 * h}, ahora, 24)
    # Importado hace 3 horas: no; hace 25: sí (y si el último intento falló hace poco, espera).
    assert not padron.toca_importar({"importado_en": ahora - 3 * h, "intentado_en": ahora - 3 * h}, ahora, 24)
    assert padron.toca_importar({"importado_en": ahora - 25 * h, "intentado_en": ahora - 25 * h}, ahora, 24)
    assert not padron.toca_importar({"importado_en": ahora - 25 * h, "intentado_en": ahora - h / 2}, ahora, 24)
    # PADRON_CADA_HORAS menor que la hora de reintento.
    assert padron.toca_importar({"importado_en": ahora - h, "intentado_en": ahora - h}, ahora, 0.5)


# --- descarga ----------------------------------------------------------------------------------------

class _Servidor:
    """HTTP local que responde lo que diga `self.respuesta` (código, tipo, cuerpo)."""

    def __init__(self):
        servidor = self
        self.respuesta = (200, "application/zip", b"")
        self.pedidos = 0

        class Manejador(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                servidor.pedidos += 1
                codigo, tipo, cuerpo = servidor.respuesta
                self.send_response(codigo)
                self.send_header("Content-Type", tipo)
                self.send_header("Content-Length", str(len(cuerpo)))
                self.end_headers()
                self.wfile.write(cuerpo)

            def log_message(self, *args):
                pass

        self.http = ThreadingHTTPServer(("127.0.0.1", 0), Manejador)
        self.url = f"http://127.0.0.1:{self.http.server_port}"
        threading.Thread(target=self.http.serve_forever, daemon=True).start()

    def cerrar(self):
        self.http.shutdown()
        self.http.server_close()


@pytest.fixture
def servidor(monkeypatch):
    # El proxy del entorno no debe meterse con 127.0.0.1.
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    s = _Servidor()
    yield s
    s.cerrar()


async def test_descarga_diaria_desde_la_url_configurada(base, tmp_path, servidor):
    pool, dsn = base
    servidor.respuesta = (200, "application/x-zip-compressed", _zip(tmp_path / "d.zip", _muchas(5)).read_bytes())
    s = _settings(dgii_padron_url=f"{servidor.url}/DGII_RNC.zip", padron_activo=True)
    r = await padron.actualizar_si_toca(pool, s)
    assert r["filas"] == 5 and r["origen"] == s.dgii_padron_url
    # Recién importado: la tarea diaria no vuelve a descargar.
    assert await padron.actualizar_si_toca(pool, s) is None
    assert servidor.pedidos == 1


@pytest.mark.parametrize(
    ("respuesta", "texto"),
    [
        ((403, "text/html", b"<html>bloqueado</html>"), "respondió 403"),
        ((200, "text/html; charset=utf-8", b"<html>mantenimiento</html>"), "página web"),
        ((200, "application/zip", b"x" * (2 * 1024 * 1024)), "pasa de 1 MB"),
    ],
)
async def test_descarga_fallida_queda_anotada_con_ayuda(base, servidor, respuesta, texto):
    pool, dsn = base
    servidor.respuesta = respuesta
    s = _settings(dgii_padron_url=f"{servidor.url}/DGII_RNC.zip", padron_max_mb=1)
    with pytest.raises(padron.PadronImportacionError):
        await padron.importar(pool, s)
    estado = await _estado(pool)
    assert texto in estado["error"] and estado["importado_en"] is None
    if respuesta[0] != 200 or "html" in respuesta[1]:
        assert "súbalo en Configuración" in estado["error"]


async def test_sin_conexion_a_la_dgii(base):
    pool, _ = base
    s = _settings(dgii_padron_url="http://127.0.0.1:9/DGII_RNC.zip")
    with pytest.raises(padron.PadronImportacionError, match="No se pudo descargar"):
        await padron.importar(pool, s)


async def test_bucle_importa_al_arrancar_si_esta_vacio(base, tmp_path, servidor, monkeypatch):
    pool, _ = base
    servidor.respuesta = (200, "application/zip", _zip(tmp_path / "d.zip", _muchas(4)).read_bytes())
    s = _settings(dgii_padron_url=f"{servidor.url}/x.zip", padron_activo=True)
    monkeypatch.setattr(padron, "_REVISAR_CADA_SEGUNDOS", 0.05)
    tarea = asyncio.create_task(padron.bucle_actualizacion(pool, s))
    for _ in range(200):
        if (await _estado(pool)).get("filas") == 4:
            break
        await asyncio.sleep(0.05)
    tarea.cancel()
    with pytest.raises(asyncio.CancelledError):
        await tarea
    assert (await _estado(pool))["filas"] == 4
    assert servidor.pedidos == 1


# --- rutas -------------------------------------------------------------------------------------------

def _app(pool, s):
    from services.erp_api.app import main as erp

    erp.app.state.db_pool = pool
    erp.app.dependency_overrides[get_settings] = lambda: s
    return erp.app


def _cliente(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://kontara")


@pytest.fixture
def limpiar_app():
    yield
    from services.erp_api.app import main as erp

    erp.app.dependency_overrides.clear()
    erp.app.state.db_pool = None


async def _esperar_tareas():
    await asyncio.gather(*list(padron._TAREAS))


async def test_consulta_por_rnc_y_cedula(base, tmp_path, limpiar_app):
    pool, dsn = base
    s = _settings(padron_activo=True)
    app = _app(pool, s)
    async with _cliente(app) as cliente:
        r = await cliente.get("/padron/101000001")
        assert r.status_code == 503 and r.json()["detail"] == padron.SIN_PADRON_DESCARGANDO
        app.dependency_overrides[get_settings] = lambda: _settings(padron_activo=False)
        assert (await cliente.get("/padron/101000001")).json()["detail"] == padron.SIN_PADRON

        await padron.importar(pool, s, archivo=_zip(tmp_path / "a.zip", [
            _linea("101000001", "COMPAÑÍA ÑOÑA SRL", comercial="ÑOÑA"),
            _linea("00100000001", "JUAN PÉREZ", estado="DADO DE BAJA", regimen="RST"),
        ]))
        r = await cliente.get("/padron/1-01-00000-1")
        assert r.status_code == 200
        datos = r.json()
        assert datos | {"padron_actualizado_en": None} == {
            "rnc": "101000001", "razon_social": "COMPAÑÍA ÑOÑA SRL", "nombre_comercial": "ÑOÑA",
            "actividad": "COMERCIO AL POR MENOR", "estado": "ACTIVO", "regimen": "NORMAL",
            "fecha_inicio": "2005-01-02", "activo": True, "fuente": "padron", "padron_actualizado_en": None,
        }
        assert datos["padron_actualizado_en"]
        r = await cliente.get("/padron/001-0000000-1")
        assert (r.json()["estado"], r.json()["activo"]) == ("DADO DE BAJA", False)
        r = await cliente.get("/padron/131999999")
        assert r.status_code == 404 and r.json()["detail"].startswith("El RNC 131999999 no aparece en el padrón")
        assert (await cliente.get("/padron/00199999999")).json()["detail"].startswith("La cédula 00199999999")
        assert (await cliente.get("/padron/12345")).status_code == 422
        # Global: cualquier empresa ve el mismo padrón (sin RLS).
        await empresas.crear_empresa(pool, empresas.NuevaEmpresa(rnc="131000002", razon_social="Segunda SRL"), None)
        emp.invalidar_cache()
        r = await cliente.get("/padron/101000001", headers={"X-Kontara-Empresa": "2"})
        assert r.status_code == 200 and r.json()["razon_social"] == "COMPAÑÍA ÑOÑA SRL"


async def test_agregar_empresa_verifica_con_el_padron(base, tmp_path, limpiar_app, servidor, monkeypatch):
    pool, _ = base
    s = _settings(consulta_directorio_url=servidor.url)
    app = _app(pool, s)
    async with _cliente(app) as cliente:
        r = (await cliente.get("/empresas/consulta-rnc/101000001")).json()
        assert r["padron"] is None and r["padron_detalle"] == padron.SIN_PADRON
        await padron.importar(pool, s, archivo=_zip(tmp_path / "a.zip", [
            _linea("101000001", "COMPAÑÍA ÑOÑA SRL", comercial="ÑOÑA", estado="SUSPENDIDO"),
        ]))
        # Sin certificado no se llama al directorio: se explica, sin "respondió 500".
        r = (await cliente.get("/empresas/consulta-rnc/101000001")).json()
        assert r["padron"]["razon_social"] == "COMPAÑÍA ÑOÑA SRL" and r["padron"]["estado"] == "SUSPENDIDO"
        assert r["nombre"] == "COMPAÑÍA ÑOÑA SRL" and r["padron_detalle"] is None
        assert (r["consultado"], r["detalle"]) == (False, empresas.SIN_CERTIFICADO_DIRECTORIO)
        assert servidor.pedidos == 0
        # Con certificado, si el directorio falla: mensaje claro.
        monkeypatch.setattr(empresas, "_hay_certificado", lambda _s: True)
        servidor.respuesta = (500, "application/json", b'{"detail": "sin token"}')
        r = (await cliente.get("/empresas/consulta-rnc/101000001")).json()
        assert (r["consultado"], r["detalle"]) == (False, empresas.DIRECTORIO_NO_RESPONDE)
        servidor.respuesta = (200, "application/json", b'[{"rnc": "101000001", "nombre": "NONA"}]')
        r = (await cliente.get("/empresas/consulta-rnc/101000001")).json()
        assert (r["consultado"], r["emisor_electronico"], r["nombre"]) == (True, True, "COMPAÑÍA ÑOÑA SRL")
        r = (await cliente.get("/empresas/consulta-rnc/131000009")).json()
        assert r["padron"] is None and "no aparece en el padrón" in r["padron_detalle"]


async def test_subir_el_zip_y_actualizar_ahora(base, tmp_path, limpiar_app, servidor, monkeypatch):
    pool, dsn = base
    s = _settings(dgii_padron_url=f"{servidor.url}/DGII_RNC.zip", padron_max_mb=1)
    app = _app(pool, s)
    async with _cliente(app) as cliente:
        estado = (await cliente.get("/padron/estado")).json()
        assert estado["importado_en"] is None and estado["puede_actualizar"] is True
        assert estado["url"] == s.dgii_padron_url and estado["max_mb"] == 1

        cuerpo = _zip(tmp_path / "subido.zip", _muchas(7)).read_bytes()
        r = await cliente.post("/padron/archivo", content=cuerpo,
                               headers={"Content-Type": "application/zip", "X-Nombre-Archivo": "DGII_RNC%20(1).zip"})
        assert r.status_code == 202 and r.json()["en_curso"] is True
        await _esperar_tareas()
        estado = (await cliente.get("/padron/estado")).json()
        assert (estado["filas"], estado["origen"], estado["en_curso"]) == (7, "Archivo subido: DGII_RNC (1).zip", False)

        # Límites: tamaño y tipo.
        r = await cliente.post("/padron/archivo", content=b"PK\x03\x04" + b"0" * (2 * 1024 * 1024))
        assert r.status_code == 413
        r = await cliente.post("/padron/archivo", content=b"%PDF-1.4 no es un zip")
        assert r.status_code == 422 and "no es el DGII_RNC.zip" in r.json()["detail"]
        assert (await cliente.post("/padron/archivo", content=b"")).status_code == 422
        # Un ZIP que no es el padrón: la importación falla y queda el anterior.
        malo = tmp_path / "malo.zip"
        with zipfile.ZipFile(malo, "w") as zf:
            zf.writestr("otra.txt", "nada que ver\n" * 50)
        assert (await cliente.post("/padron/archivo", content=malo.read_bytes())).status_code == 202
        await _esperar_tareas()
        estado = (await cliente.get("/padron/estado")).json()
        assert estado["filas"] == 7 and "no trae ningún contribuyente" in estado["error"]
        assert not list(Path(tempfile.gettempdir()).glob("kontara-padron-subido-*"))

        # Actualizar ahora: descarga de la URL.
        servidor.respuesta = (200, "application/zip", _zip(tmp_path / "d.zip", _muchas(9)).read_bytes())
        assert (await cliente.post("/padron/actualizar")).status_code == 202
        await _esperar_tareas()
        estado = (await cliente.get("/padron/estado")).json()
        assert (estado["filas"], estado["origen"], estado["error"]) == (9, s.dgii_padron_url, None)

        # Solo el administrador de la instalación (el padrón es de todas las empresas).
        async def no_es_admin(*_a, **_k):
            return False

        monkeypatch.setattr(padron, "es_admin_plataforma", no_es_admin)
        assert (await cliente.post("/padron/actualizar")).status_code == 403
        assert (await cliente.post("/padron/archivo", content=cuerpo)).status_code == 403
        assert (await cliente.get("/padron/estado")).json()["puede_actualizar"] is False
        assert (await cliente.get("/padron/101000001")).status_code in (200, 404)
