"""Configuración (fase 6) contra un Postgres REAL: empresa, secuencias con
e-NCF ya usados en facturacion.facturas, vendedores (incluida la columna
vendedor_id de facturas_api), preferencias y envíos.

Se salta si no hay `COMPRAS_TEST_DATABASE_URL` (igual que
test_compras_postgres.py): cada prueba crea una base nueva, carga
`db/init/*.sql` y aplica las migraciones de erp-api y facturas_api.
"""
import os
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path

import asyncpg
import pytest

from services.erp_api.app import configuracion_envio as envio
from services.erp_api.app import configuracion_repository as repo
from services.erp_api.app.migraciones import aplicar_migraciones
from services.erp_api.app.repository import DatosInvalidosError
from services.facturas_api.app import repository as facturas_repo
from services.facturas_api.app.migraciones import aplicar_migraciones as migrar_facturas

DSN = os.environ.get("COMPRAS_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="sin COMPRAS_TEST_DATABASE_URL (Postgres real)")

REPO = Path(__file__).resolve().parents[3]
HOY = date(2026, 10, 24)


@pytest.fixture
async def pool():
    nombre = f"config_{uuid.uuid4().hex[:10]}"
    admin = await asyncpg.connect(DSN)
    await admin.execute(f"CREATE DATABASE {nombre} TEMPLATE template0 ENCODING 'UTF8'")
    await admin.close()
    dsn = DSN.rsplit("/", 1)[0] + f"/{nombre}"
    conn = await asyncpg.connect(dsn)
    for archivo in sorted((REPO / "db" / "init").glob("*.sql")):
        await conn.execute(archivo.read_text(encoding="utf-8"))
    await conn.close()
    p = await asyncpg.create_pool(dsn, min_size=1, max_size=3)
    # Dos veces: las migraciones deben ser idempotentes sobre db/init.
    for _ in range(2):
        await aplicar_migraciones(p)
        await migrar_facturas(p)
    yield p
    await p.close()
    admin = await asyncpg.connect(DSN)
    await admin.execute(f"DROP DATABASE IF EXISTS {nombre} WITH (FORCE)")
    await admin.close()


async def _factura(pool, tipo: int, encf: str, vendedor_id=None):
    return await facturas_repo.crear_factura(
        pool, tipo_ecf=tipo, ambiente="test", rnc_emisor="131897542", encf=encf, datos={"Encabezado": {}},
        vendedor_id=vendedor_id,
    )


async def test_empresa_upsert(pool):
    assert await repo.obtener_empresa(pool) is None
    campos = dict(nombre_comercial="Caribe Verde", rnc="1-31-89754-2", direccion="Av. 27 de Febrero 415",
                  telefono="809-555-0110", correo="facturacion@caribeverde.do", logo=None,
                  actividad_economica="Venta al por mayor", provincia="Distrito Nacional",
                  municipio="Santo Domingo de Guzmán")
    await repo.guardar_empresa(pool, razon_social="Distribuidora Caribe Verde, SRL", **campos)
    fila = await repo.guardar_empresa(pool, razon_social="Caribe Verde, SRL", **campos)
    assert fila["razon_social"] == "Caribe Verde, SRL"
    assert fila["rnc"] == "131897542"
    assert fila["provincia"] == "Distrito Nacional"
    async with pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM configuracion.empresa") == 1


async def test_secuencias_con_ecf_usados(pool):
    await _factura(pool, 31, "E310000000001")
    await _factura(pool, 31, "E310000000130")
    await _factura(pool, 32, "E320000000007")
    await _factura(pool, 31, None)  # borrador sin e-NCF: no cuenta

    lista = {s["tipo_ecf"]: s for s in await repo.listar_secuencias(pool, HOY, "test")}
    assert lista[31]["estado"] == "sin_configurar"
    assert lista[31]["siguiente_encf"] == "E310000000131"

    s = await repo.guardar_secuencia(pool, 31, desde=1, hasta=5000, proximo=None,
                                     fecha_vencimiento=date(2027, 12, 31), activa=True, hoy=HOY, ambiente="test")
    assert s["siguiente_encf"] == "E310000000131"
    assert s["usados"] == 2  # e-NCF del rango con factura
    assert s["disponibles"] == 4870
    assert s["estado"] == "vigente"

    s = await repo.guardar_secuencia(pool, 32, desde=1, hasta=10, proximo=None,
                                     fecha_vencimiento=None, activa=True, hoy=HOY, ambiente="test")
    assert s["estado"] == "por_agotarse"
    assert s["siguiente_encf"] == "E320000000008"

    with pytest.raises(DatosInvalidosError):
        await repo.guardar_secuencia(pool, 33, desde=10, hasta=5, proximo=None, fecha_vencimiento=None,
                                     activa=True, hoy=HOY, ambiente="test")
    await repo.eliminar_secuencia(pool, 32, "test")
    with pytest.raises(repo.SecuenciaNoEncontradaError):
        await repo.eliminar_secuencia(pool, 32, "test")


async def test_vendedores_y_columna_vendedor_id(pool):
    v = await repo.crear_vendedor(pool, nombre="María Rosario", correo="maria@caribe.do",
                                  comision_pct=Decimal("2.5"), activo=True)
    libre = await repo.crear_vendedor(pool, nombre="Ana", correo=None, comision_pct=Decimal("0"), activo=True)
    factura = await _factura(pool, 31, "E310000000001", vendedor_id=v["id"])
    assert str(factura["vendedor_id"]) == str(v["id"])

    actualizada = await facturas_repo.actualizar_datos(
        pool, uuid.UUID(factura["id"]), encf="E310000000001", datos={}, vendedor_id=libre["id"]
    )
    assert str(actualizada["vendedor_id"]) == str(libre["id"])

    with pytest.raises(repo.VendedorEnUsoError):
        await repo.eliminar_vendedor(pool, libre["id"])
    await repo.eliminar_vendedor(pool, v["id"])
    with pytest.raises(repo.VendedorNoEncontradoError):
        await repo.eliminar_vendedor(pool, v["id"])

    await repo.actualizar_vendedor(pool, libre["id"], nombre="Ana P.", correo=None,
                                   comision_pct=Decimal("1"), activo=False)
    assert [x["nombre"] for x in await repo.listar_vendedores(pool, activo=False)] == ["Ana P."]
    assert await repo.listar_vendedores(pool, activo=True) == []


async def test_preferencias_y_envios(pool, monkeypatch):
    monkeypatch.setenv("CERTIFICADOS_CLAVE", "clave-larga-de-prueba-con-mas-de-32-caracteres")
    assert (await envio.leer_preferencia(pool, "inventario", envio.INVENTARIO_POR_DEFECTO))["metodo"] == "promedio"
    await envio.guardar_preferencia(pool, "inventario", {"metodo": "peps"})
    assert (await envio.leer_preferencia(pool, "inventario", envio.INVENTARIO_POR_DEFECTO))["metodo"] == "peps"

    guardado = await envio.guardar_correo(pool, {"host": "smtp.x.do", "remitente": "f@x.do", "contrasena": "secreta"})
    assert "secreta" not in str(guardado)
    # Sin contraseña nueva se conserva la anterior.
    guardado2 = await envio.guardar_correo(pool, {"host": "smtp.x.do", "remitente": "f@x.do", "contrasena": None})
    assert guardado2["contrasena_cifrada"] == guardado["contrasena_cifrada"]
    async with pool.acquire() as conn:
        crudo = await conn.fetchval("SELECT valor::text FROM configuracion.preferencias WHERE clave = 'correo'")
    assert "secreta" not in crudo

    fid = uuid.UUID((await _factura(pool, 31, "E310000000001"))["id"])
    await envio.registrar_envio(pool, "correo", fid, "cliente@x.do")
    await envio.registrar_envio(pool, "correo", fid, "cliente@x.do")  # una sola vez por factura
    await envio.registrar_envio(pool, "correo", None, "prueba@x.do")
    assert (await envio.enviados_del_mes(pool)) == {"correo": 1, "whatsapp": 0}


async def test_facturas_por_enviar(pool):
    datos = {"Encabezado": {"Comprador": {"CorreoComprador": "cliente@x.do", "RazonSocialComprador": "Cliente"},
                            "Totales": {"MontoTotal": "1180.00"}}}
    f = await facturas_repo.crear_factura(pool, tipo_ecf=31, ambiente="test", rnc_emisor="131897542",
                                          encf="E310000000001", datos=datos)
    await facturas_repo.crear_factura(pool, tipo_ecf=31, ambiente="test", rnc_emisor="131897542",
                                      encf="E310000000002", datos={"Encabezado": {}})
    async with pool.acquire() as conn:
        await conn.execute("UPDATE facturacion.facturas SET estado = 'aceptado'")
    pendientes = await envio.facturas_por_enviar(pool)
    assert [p["encf"] for p in pendientes] == ["E310000000001"]
    assert pendientes[0]["correo"] == "cliente@x.do"
    await envio.registrar_envio(pool, "correo", uuid.UUID(f["id"]), "cliente@x.do")
    assert await envio.facturas_por_enviar(pool) == []
