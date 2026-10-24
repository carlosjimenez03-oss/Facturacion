"""Modo contingencia contra un Postgres REAL: migración sobre una base ya
existente, estado compartido, marcado de facturas, armado y reanudación de
lotes, contadores y el envío por lotes completo (recepcion y
consulta_resultado simulados con respx).

Se salta si no hay `CONTINGENCIA_TEST_DATABASE_URL` (DSN de una base con
permiso para crear bases, p. ej. `postgresql://postgres@127.0.0.1:5690/postgres`).
"""
import asyncio
import json
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import asyncpg
import pytest
import respx
from httpx import ConnectError, Response

from services.facturas_api.app import contingencia, contingencia_repository as cr, main, repository
from services.facturas_api.app.migraciones import aplicar_migraciones

DSN = os.environ.get("CONTINGENCIA_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="sin CONTINGENCIA_TEST_DATABASE_URL (Postgres real)")

REPO = Path(__file__).resolve().parents[3]
RECEPCION = main.settings.recepcion_service_url
CONSULTA = main.settings.consulta_resultado_url
BASE = datetime(2026, 10, 24, 10, 0, tzinfo=timezone.utc)


async def _base(*, hasta: str | None = None):
    nombre = f"contingencia_{uuid.uuid4().hex[:10]}"
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
    return nombre, await asyncpg.create_pool(dsn, min_size=1, max_size=5)


async def _borrar(nombre, pool):
    await pool.close()
    admin = await asyncpg.connect(DSN)
    await admin.execute(f"DROP DATABASE IF EXISTS {nombre} WITH (FORCE)")
    await admin.close()


@pytest.fixture
async def pool():
    nombre, p = await _base()
    await aplicar_migraciones(p)  # sobre db/init completo: no cambia nada
    yield p
    await _borrar(nombre, p)


_DATOS = json.dumps({"Encabezado": {"Totales": {"MontoTotal": "1180.00"}}})


async def _factura(pool, i: int, *, estado="contingencia", firmado_en=None, **cols) -> uuid.UUID:
    fid = uuid.uuid4()
    await pool.execute(
        """
        INSERT INTO facturacion.facturas
            (id, tipo_ecf, ambiente, rnc_emisor, encf, estado, datos, xml_generado, xml_firmado, firmado_en,
             emitida_en_contingencia, contingencia_en, track_id)
        VALUES ($1, 31, 'test', '131897542', $2, $3, $4, '<ECF/>', $5, $6, $7, $6, $8)
        """,
        fid, f"E31{i:010d}", estado, _DATOS, f"<ECF n='{i}'><Signature/></ECF>",
        firmado_en or BASE + timedelta(minutes=i), estado == "contingencia", cols.get("track_id"),
    )
    return fid


async def test_migracion_sobre_una_base_anterior_es_idempotente():
    """Base creada solo con db/init 001-016 (como un despliegue anterior) y
    facturas existentes: la migración agrega columnas y tablas sin tocar
    los datos, y correrla de nuevo no cambia nada."""
    nombre, p = await _base(hasta="016_soluciones.sql")
    try:
        await p.execute(
            "INSERT INTO facturacion.facturas (tipo_ecf, ambiente, rnc_emisor, encf, estado, datos)"
            " VALUES (31, 'test', '131897542', 'E310000000001', 'aceptado', '{}')"
        )
        assert await p.fetchval("SELECT to_regclass('facturacion.contingencia')") is None
        await aplicar_migraciones(p)
        await aplicar_migraciones(p)
        fila = await p.fetchrow("SELECT * FROM facturacion.facturas")
        assert fila["estado"] == "aceptado" and fila["emitida_en_contingencia"] is False
        assert fila["intentos_envio"] == 0 and fila["lote_id"] is None and fila["firmado_en"] is None
        assert await p.fetchval("SELECT count(*) FROM facturacion.contingencia") == 1
        assert not await p.fetchval("SELECT activa FROM facturacion.contingencia")
        assert await p.fetchval("SELECT to_regclass('facturacion.facturas_contingencia_idx')") is not None
        # Sin CHECK en facturas.estado: 'contingencia' entra sin cambiar restricciones.
        await p.execute("UPDATE facturacion.facturas SET estado = 'contingencia'")
    finally:
        await _borrar(nombre, p)


async def test_estado_compartido(pool):
    estado = await cr.leer_estado(pool)
    assert not estado["activa"] and estado["pendientes"] == 0 and estado["mas_antigua"] is None
    await cr.registrar_falla(pool, "refused")
    primera = await cr.leer_estado(pool)
    await cr.registrar_falla(pool, "timeout")
    estado = await cr.leer_estado(pool)
    assert estado["activa"] and estado["errores_consecutivos"] == 2 and estado["ultimo_error"] == "timeout"
    assert estado["desde"] == primera["desde"]  # `desde` es la primera falla
    await _factura(pool, 1)
    await _factura(pool, 2)
    estado = await cr.leer_estado(pool)
    assert (estado["pendientes"], estado["monto_pendiente"]) == (2, Decimal("2360.00"))
    assert estado["mas_antigua"] == BASE + timedelta(minutes=1)
    await cr.registrar_exito(pool)
    estado = await cr.leer_estado(pool)
    assert not estado["activa"] and estado["desde"] is None and estado["errores_consecutivos"] == 0
    assert estado["ultimo_exito"] is not None


async def test_marcar_contingencia_y_firma(pool):
    fid = await _factura(pool, 1, estado="generado")
    await pool.execute("UPDATE facturacion.facturas SET xml_firmado = NULL, firmado_en = NULL WHERE id = $1", fid)
    with pytest.raises(repository.FacturaNoEncontradaError):  # sin XML firmado no hay contingencia
        await repository.marcar_contingencia(pool, fid, error="refused")
    firmada = await repository.guardar_firma_envio(pool, fid, "<ECF><Signature/></ECF>")
    assert firmada["firmado_en"] is not None and firmada["estado"] == "generado"
    f = await repository.marcar_contingencia(pool, fid, error="refused")
    assert (f["estado"], f["emitida_en_contingencia"], f["intentos_envio"], f["error"]) == (
        "contingencia", True, 1, "refused",
    )
    desde = f["contingencia_en"]
    f = await repository.marcar_contingencia(pool, fid, error="otra vez")
    assert f["intentos_envio"] == 2 and f["contingencia_en"] == desde
    # Regenerar el XML (solo posible antes de enviar) borra la firma vieja.
    g = await repository.guardar_xml_generado(pool, fid, "<ECF nuevo='1'/>")
    assert g["xml_firmado"] is None and g["firmado_en"] is None
    # Con trackId (ya llegó a la DGII) nunca vuelve a contingencia.
    enviada = await _factura(pool, 2, estado="error", track_id="trk")
    with pytest.raises(repository.FacturaNoEncontradaError):
        await repository.marcar_contingencia(pool, enviada, error="refused")
    resumen = (await repository.listar_facturas(pool, estado="contingencia"))
    assert resumen == []  # la 1 volvió a 'generado' al regenerar


async def test_lotes_mas_antiguos_primero_reanudacion_y_contadores(pool):
    ids = [await _factura(pool, i, firmado_en=BASE + timedelta(minutes=25 - i)) for i in range(25)]
    lote1 = await cr.tomar_lote(pool, tamano=20)
    assert (lote1["id"], lote1["documentos"], lote1["estado"], lote1["pendientes"]) == ("A-0001", 20, "en_cola", 20)
    del_lote = await cr.facturas_pendientes_del_lote(pool, "A-0001")
    firmas = [f["firmado_en"] for f in del_lote]
    assert firmas == sorted(firmas)
    # Las 20 más antiguas: ids[24] .. ids[5] (firmado_en decreciente con i).
    assert {f["id"] for f in del_lote} == {str(i) for i in ids[5:]}
    # Un lote abierto con pendientes se retoma antes de armar otro.
    assert (await cr.tomar_lote(pool, tamano=20))["id"] == "A-0001"

    await cr.marcar_lote(pool, "A-0001", estado="enviando")
    for n, f in enumerate(del_lote[:12]):
        await repository.registrar_resultado_envio(pool, uuid.UUID(f["id"]), estado="enviado", track_id=f"t{n}")
    await cr.marcar_lote(pool, "A-0001", estado="interrumpido", error="refused")
    await cr.recalcular_lotes(pool, ["A-0001"])
    lote = (await cr.listar_lotes(pool))[0]
    assert (lote["estado"], lote["enviados"], lote["pendientes"], lote["ultimo_error"]) == ("interrumpido", 12, 8, "refused")
    assert lote["iniciado_en"] is not None
    assert (await cr.tomar_lote(pool, tamano=20))["id"] == "A-0001"

    for f in del_lote[12:]:
        await repository.registrar_resultado_envio(pool, uuid.UUID(f["id"]), estado="enviado", track_id="t")
    await pool.execute(
        "UPDATE facturacion.facturas SET estado = 'aceptado' WHERE lote_id = 'A-0001' AND track_id <> 't0'"
    )
    await pool.execute("UPDATE facturacion.facturas SET estado = 'rechazado' WHERE track_id = 't0'")
    # Sin pendientes, A-0001 se cierra solo y se arma A-0002 con las 5 restantes.
    lote2 = await cr.tomar_lote(pool, tamano=20)
    assert (lote2["id"], lote2["documentos"]) == ("A-0002", 5)
    await cr.recalcular_lotes(pool)
    lotes = {lote["id"]: lote for lote in await cr.listar_lotes(pool)}
    a1 = lotes["A-0001"]
    assert (a1["estado"], a1["enviados"], a1["aceptados"], a1["rechazados"]) == ("completado", 20, 19, 1)
    assert a1["completado_en"] is not None
    await pool.execute("UPDATE facturacion.facturas SET estado = 'aceptado' WHERE lote_id = 'A-0002'")
    assert await cr.tomar_lote(pool, tamano=20) is None
    assert {lote["estado"] for lote in await cr.listar_lotes(pool)} == {"completado"}


async def test_candado_de_envio_entre_procesos(pool):
    async with cr.candado_envio(pool) as primero:
        async with cr.candado_envio(pool) as segundo:
            assert primero and not segundo
    async with cr.candado_envio(pool) as otra_vez:
        assert otra_vez


@respx.mock
async def test_envio_por_lotes_completo(pool):
    """25 facturas en contingencia: con la DGII caída a la 7.ª, A-0001 queda
    interrumpido; al volver, A-0001 y A-0002 se completan, todas aceptadas,
    con el XML firmado guardado enviado tal cual y la contingencia cerrada."""
    settings = main.settings.model_copy(update={"timeout_sonda_segundos": 1.0})
    ids = [await _factura(pool, i) for i in range(25)]
    await cr.registrar_falla(pool, "refused")
    recibidos: dict[str, bytes] = {}
    caer_tras = {"n": 7}

    def recepcion(request):
        if caer_tras["n"] is not None and len(recibidos) >= caer_tras["n"]:
            raise ConnectError("connection refused")
        encf = re.search(rb'name="encf"\r\n\r\n([^\r]+)', request.content).group(1).decode()
        recibidos[encf] = request.content
        return Response(200, json={"trackId": f"trk-{encf}"})

    respx.get(f"{RECEPCION}/disponibilidad").mock(return_value=Response(200, json={"disponible": True}))
    respx.post(f"{RECEPCION}/ecf").mock(side_effect=recepcion)
    respx.get(url__regex=rf"{re.escape(CONSULTA)}/resultado/.*").mock(
        return_value=Response(200, json={"codigo": 1, "estado": "Aceptado", "mensajes": []})
    )

    r = await contingencia.enviar_pendientes(pool, settings)
    assert r.interrumpido and r.enviados == 7
    lote = (await cr.listar_lotes(pool))[0]
    assert (lote["id"], lote["estado"], lote["enviados"]) == ("A-0001", "interrumpido", 7)
    assert (await cr.leer_estado(pool))["activa"]

    caer_tras["n"] = None
    r = await contingencia.enviar_pendientes(pool, settings)
    assert r.lotes == ["A-0001", "A-0002"] and r.enviados == 18 and not r.interrumpido
    estado = await cr.leer_estado(pool)
    assert not estado["activa"] and estado["pendientes"] == 0
    lotes = {lote["id"]: lote for lote in await cr.listar_lotes(pool)}
    assert {k: (v["documentos"], v["aceptados"], v["estado"]) for k, v in lotes.items()} == {
        "A-0001": (20, 20, "completado"), "A-0002": (5, 5, "completado"),
    }
    filas = await pool.fetch("SELECT * FROM facturacion.facturas WHERE id = ANY($1::uuid[])", ids)
    assert {f["estado"] for f in filas} == {"aceptado"}
    for f in filas:
        assert f["xml_firmado"].encode() in recibidos[f["encf"]]  # byte a byte
        assert f["emitida_en_contingencia"] and f["track_id"] == f"trk-{f['encf']}"
    assert len(recibidos) == 25
    # Las consultas de resultado quedaron auditadas.
    assert await pool.fetchval(
        "SELECT count(*) FROM facturacion.envios_dgii WHERE modulo = 'consulta_resultado'"
    ) == 25


async def test_dos_envios_por_lotes_a_la_vez_no_se_pisan(pool, monkeypatch):
    for i in range(3):
        await _factura(pool, i)
    settings = main.settings
    entradas = []

    async def lento(pool_, s, *, sonda_hecha):
        entradas.append(1)
        await asyncio.sleep(0.2)
        return contingencia.ResultadoLotes(disponible=True)

    monkeypatch.setattr(contingencia, "_enviar_pendientes", lento)
    a, b = await asyncio.gather(
        contingencia.enviar_pendientes(pool, settings), contingencia.enviar_pendientes(pool, settings)
    )
    assert len(entradas) == 1 and (a.ocupado or b.ocupado)
