"""Clave de idempotencia de POST /facturas (POS sin conexión) contra un
Postgres REAL: el reintento devuelve la misma factura sin consumir otro
e-NCF, dos reintentos simultáneos crean una sola, la migración sobre una
base anterior agrega la columna y el índice único, y el endpoint responde
201 la primera vez y 200 en el reintento.

Se salta si no hay `CONTINGENCIA_TEST_DATABASE_URL` (DSN de una base con
permiso para crear bases, p. ej. `postgresql://postgres@127.0.0.1:5690/postgres`).
"""
import asyncio
import os
import uuid
from datetime import date
from pathlib import Path

import asyncpg
import pytest
from fastapi.testclient import TestClient

from services.facturas_api.app import main, repository, secuencias_encf
from services.facturas_api.app.migraciones import aplicar_migraciones

DSN = os.environ.get("CONTINGENCIA_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="sin CONTINGENCIA_TEST_DATABASE_URL (Postgres real)")

REPO = Path(__file__).resolve().parents[3]
HOY = date(2026, 10, 24)
RNC = "131897542"


async def _base(*, hasta: str | None = None):
    nombre = f"idempotencia_{uuid.uuid4().hex[:10]}"
    admin = await asyncpg.connect(DSN)
    await admin.execute(f"CREATE DATABASE {nombre} TEMPLATE template0 ENCODING 'UTF8'")
    await admin.close()
    dsn = DSN.rsplit("/", 1)[0] + f"/{nombre}"
    conn = await asyncpg.connect(dsn)
    for archivo in sorted((REPO / "db" / "init").glob("*.sql")):
        if hasta and archivo.name > hasta:
            continue
        await conn.execute(archivo.read_text(encoding="utf-8"))
    await conn.close()
    return nombre, dsn, await asyncpg.create_pool(dsn, min_size=1, max_size=8)


async def _borrar(nombre, pool):
    await pool.close()
    admin = await asyncpg.connect(DSN)
    await admin.execute(f"DROP DATABASE IF EXISTS {nombre} WITH (FORCE)")
    await admin.close()


@pytest.fixture
async def pool():
    nombre, _dsn, p = await _base()
    await aplicar_migraciones(p)  # sobre db/init completo: no cambia nada
    await p.execute(
        "INSERT INTO configuracion.secuencias (ambiente, tipo_ecf, prefijo, desde, hasta, proximo, fecha_vencimiento)"
        " VALUES ('test', 32, 'E32', 1, 3, 1, NULL), ('test', 31, 'E31', 1, 5000, 1, '2027-12-31')"
    )
    yield p
    await _borrar(nombre, p)


def _datos(tipo: int = 32) -> dict:
    return {"Encabezado": {"Version": "1.0", "IdDoc": {"TipoeCF": str(tipo), "TipoPago": "1"},
                           "Emisor": {"RNCEmisor": RNC}, "Totales": {"MontoTotal": "118.00"}}}


async def _crear(pool, clave, tipo=32):
    return await secuencias_encf.crear_factura(
        pool, tipo_ecf=tipo, ambiente="test", rnc_emisor=RNC, encf=None, asignar_encf=True,
        datos=_datos(tipo), clave_idempotencia=clave, dia=HOY,
        items_inventario=[{"numero_linea": "1", "producto_id": uuid.uuid4(), "cantidad": "1"}],
    )


async def test_reintento_devuelve_la_misma_factura_sin_consumir_otro_encf(pool):
    clave = uuid.uuid4()
    f = await _crear(pool, clave)
    assert f["encf"] == "E320000000001" and str(f["clave_idempotencia"]) == str(clave)
    with pytest.raises(secuencias_encf.FacturaYaCreadaError) as exc:
        await _crear(pool, clave)
    assert exc.value.factura["id"] == f["id"] and exc.value.factura["encf"] == "E320000000001"
    # La siguiente venta (otra clave) recibe el siguiente e-NCF: el reintento no gastó uno.
    assert (await _crear(pool, uuid.uuid4()))["encf"] == "E320000000002"
    # Sin clave, como siempre (Nueva factura): sin idempotencia.
    sin = await secuencias_encf.crear_factura(
        pool, tipo_ecf=32, ambiente="test", rnc_emisor=RNC, encf=None, asignar_encf=True, datos=_datos(), dia=HOY
    )
    assert sin["encf"] == "E320000000003" and sin["clave_idempotencia"] is None
    assert await pool.fetchval("SELECT count(*) FROM facturacion.facturas") == 3
    assert (await repository.obtener_por_clave_idempotencia(pool, clave))["id"] == f["id"]
    assert await repository.obtener_por_clave_idempotencia(pool, uuid.uuid4()) is None


async def test_reintentos_simultaneos_crean_una_sola_factura(pool):
    clave = uuid.uuid4()
    resultados = await asyncio.gather(*[_crear(pool, clave) for _ in range(6)], return_exceptions=True)
    creadas = [r for r in resultados if isinstance(r, dict)]
    repetidas = [r for r in resultados if isinstance(r, secuencias_encf.FacturaYaCreadaError)]
    assert len(creadas) == 1 and len(repetidas) == 5
    assert {r.factura["id"] for r in repetidas} == {creadas[0]["id"]}
    assert await pool.fetchval("SELECT count(*) FROM facturacion.facturas") == 1


async def test_secuencia_agotada_no_se_reintenta_como_repetida(pool):
    """Con la secuencia agotada la venta queda sin factura (409 con el
    motivo); el reintento con la misma clave, tras registrar la secuencia
    nueva, sí la crea."""
    for _ in range(3):
        await _crear(pool, uuid.uuid4())
    clave = uuid.uuid4()
    with pytest.raises(secuencias_encf.EncfNoDisponibleError, match="agotada"):
        await _crear(pool, clave)
    await pool.execute("UPDATE configuracion.secuencias SET hasta = 10 WHERE tipo_ecf = 32")
    assert (await _crear(pool, clave))["encf"] == "E320000000004"


async def test_clave_de_otro_tipo_de_ecf_se_rechaza(pool):
    clave = uuid.uuid4()
    await _crear(pool, clave, tipo=32)
    with pytest.raises(secuencias_encf.ClaveIdempotenciaReusadaError):
        await _crear(pool, clave, tipo=31)


async def test_indice_unico_respalda_la_clave(pool):
    clave = uuid.uuid4()
    await _crear(pool, clave)
    with pytest.raises(asyncpg.UniqueViolationError):
        await pool.execute(
            "INSERT INTO facturacion.facturas (tipo_ecf, ambiente, rnc_emisor, datos, clave_idempotencia)"
            " VALUES (32, 'test', $1, '{}', $2)",
            RNC, clave,
        )


async def test_migracion_sobre_una_base_anterior():
    """Base creada con db/init hasta 017 (despliegue anterior) y facturas
    existentes: la migración agrega la columna y el índice sin tocar datos,
    y correrla de nuevo no cambia nada."""
    nombre, _dsn, p = await _base(hasta="017_contingencia.sql")
    try:
        await p.execute(
            "INSERT INTO facturacion.facturas (tipo_ecf, ambiente, rnc_emisor, encf, estado, datos)"
            " VALUES (31, 'test', $1, 'E310000000001', 'aceptado', '{}')",
            RNC,
        )
        columna = "SELECT 1 FROM information_schema.columns WHERE table_name = 'facturas' AND column_name = 'clave_idempotencia'"
        assert await p.fetchval(columna) is None
        await aplicar_migraciones(p)
        await aplicar_migraciones(p)
        assert await p.fetchval(columna) == 1
        assert await p.fetchval("SELECT to_regclass('facturacion.facturas_clave_idempotencia_idx')") is not None
        fila = await p.fetchrow("SELECT * FROM facturacion.facturas")
        assert fila["estado"] == "aceptado" and fila["clave_idempotencia"] is None
    finally:
        await _borrar(nombre, p)


def test_endpoint_201_y_luego_200_con_la_misma_factura(monkeypatch):
    """POST /facturas real (TestClient + lifespan con la base de la prueba)."""
    nombre, dsn = asyncio.run(_preparar())
    try:
        monkeypatch.setattr(main.settings, "database_url", dsn)
        monkeypatch.setattr(main.settings, "sincronizar_estado_cada_segundos", 0)
        clave = str(uuid.uuid4())
        cuerpo = {"tipo_ecf": 32, "encf": None, "asignar_encf": True, "datos": _datos(), "clave_idempotencia": clave}
        with TestClient(main.app) as client:
            primera = client.post("/facturas", json=cuerpo)
            segunda = client.post("/facturas", json=cuerpo)
            otra = client.post("/facturas", json={**cuerpo, "clave_idempotencia": str(uuid.uuid4())})
            reusada = client.post("/facturas", json={**cuerpo, "tipo_ecf": 31})
        assert primera.status_code == 201 and segunda.status_code == 200
        assert segunda.json()["id"] == primera.json()["id"]
        assert segunda.json()["encf"] == primera.json()["encf"] == "E320000000001"
        assert segunda.json()["clave_idempotencia"] == clave
        assert otra.status_code == 201 and otra.json()["encf"] == "E320000000002"
        assert reusada.status_code == 409 and "otra factura" in reusada.json()["detail"]
    finally:
        asyncio.run(_borrar_por_nombre(nombre))


async def _preparar():
    nombre, dsn, p = await _base()
    await aplicar_migraciones(p)
    await p.execute(
        "INSERT INTO configuracion.secuencias (ambiente, tipo_ecf, prefijo, desde, hasta, proximo)"
        " VALUES ('test', 32, 'E32', 1, 100, 1)"
    )
    await p.close()
    return nombre, dsn


async def _borrar_por_nombre(nombre):
    admin = await asyncpg.connect(DSN)
    await admin.execute(f"DROP DATABASE IF EXISTS {nombre} WITH (FORCE)")
    await admin.close()
