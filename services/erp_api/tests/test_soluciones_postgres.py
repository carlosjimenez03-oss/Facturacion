"""Soluciones (fase 6) contra un Postgres REAL: prueba gratis y consumo,
enlaces del portal (solo las facturas del cliente; revocado/vencido = no
válido), generación idempotente de envíos de Kontara Ventas y caja del POS.

Se salta si no hay `SOLUCIONES_TEST_DATABASE_URL` (DSN de una base con
permiso para crear bases, p. ej. `postgresql://postgres@localhost:5670/postgres`):
cada prueba crea una base nueva, carga `db/init/*.sql` y aplica las
migraciones de erp-api.
"""
import json
import os
import uuid
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import asyncpg
import pytest

from services.erp_api.app import soluciones_repository as repo
from services.erp_api.app.migraciones import aplicar_migraciones
from services.erp_api.app.repository import DatosInvalidosError
from services.erp_api.app.soluciones_repository import ConflictoSolucionesError, NoEncontradoError

DSN = os.environ.get("SOLUCIONES_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="sin SOLUCIONES_TEST_DATABASE_URL (Postgres real)")

REPO = Path(__file__).resolve().parents[3]
D = Decimal


@pytest.fixture
async def pool():
    nombre = f"soluciones_{uuid.uuid4().hex[:10]}"
    admin = await asyncpg.connect(DSN)
    await admin.execute(f"CREATE DATABASE {nombre} TEMPLATE template0 ENCODING 'UTF8'")
    await admin.close()
    dsn = DSN.rsplit("/", 1)[0] + f"/{nombre}"
    conn = await asyncpg.connect(dsn)
    for archivo in sorted((REPO / "db" / "init").glob("*.sql")):
        await conn.execute(archivo.read_text(encoding="utf-8"))
    await conn.close()
    p = await asyncpg.create_pool(dsn, min_size=1, max_size=5)
    await aplicar_migraciones(p)
    yield p
    await p.close()
    admin = await asyncpg.connect(DSN)
    await admin.execute(f"DROP DATABASE IF EXISTS {nombre} WITH (FORCE)")
    await admin.close()


def dgii(fecha):
    return fecha.strftime("%d-%m-%Y")


async def factura(pool, encf, *, rnc="131234567", nombre="Cliente Uno, SRL", total="1180.00", estado="aceptado",
                  credito=False, fecha=None, vence=None, track=True, tipo=None, xml_firmado=None):
    hoy = repo.hoy()
    fecha = fecha or hoy
    id_doc = {"TipoeCF": encf[1:3], "eNCF": encf, "TipoPago": "2" if credito else "1"}
    if credito:
        id_doc["FechaLimitePago"] = dgii(vence or fecha + timedelta(days=30))
    datos = {
        "Encabezado": {
            "Version": "1.0",
            "IdDoc": id_doc,
            "Emisor": {"RNCEmisor": "131897542", "RazonSocialEmisor": "Distribuidora Demo, SRL", "FechaEmision": dgii(fecha)},
            **({"Comprador": {"RNCComprador": rnc, "RazonSocialComprador": nombre}} if rnc else {}),
            "Totales": {"MontoTotal": total},
        },
        "DetallesItems": {"Item": []},
    }
    async with pool.acquire() as conn:
        return await conn.fetchval(
            """
            INSERT INTO facturacion.facturas (tipo_ecf, ambiente, rnc_emisor, encf, estado, datos, track_id,
                                              xml_generado, xml_firmado)
            VALUES ($1, 'test', '131897542', $2, $3, $4::json, $5, '<ECF>x</ECF>', $6)
            RETURNING id
            """,
            tipo or int(encf[1:3]),
            encf,
            estado,
            json.dumps(datos),
            "track-" + encf if track else None,
            xml_firmado,
        )


async def cliente(pool, nombre, rnc, *, telefono="809-555-0101", correo=None):
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "INSERT INTO ventas.clientes (nombre, rnc, telefono, correo) VALUES ($1, $2, $3, $4) RETURNING id",
            nombre,
            rnc,
            telefono,
            correo,
        )


# --- Planes y suscripción ---------------------------------------------------------

async def test_la_prueba_empieza_en_el_primer_uso_y_no_se_reinicia(pool):
    primera = await repo.obtener_suscripcion(pool)
    assert primera["estado"] == "prueba"
    assert primera["dias_restantes"] == 15
    assert primera["prueba_fin"] - primera["prueba_inicio"] == timedelta(days=14)
    async with pool.acquire() as conn:
        await conn.execute("UPDATE soluciones.suscripcion SET prueba_inicio = prueba_inicio - 20, prueba_fin = prueba_fin - 20")
    segunda = await repo.obtener_suscripcion(pool)
    assert segunda["estado"] == "prueba_vencida"
    assert segunda["dias_restantes"] == 0


async def test_consumo_cuenta_ecf_enviados_del_mes_y_bytes_de_xml(pool):
    hoy = repo.hoy()
    await factura(pool, "E310000000001")
    await factura(pool, "E320000000002", rnc=None)
    await factura(pool, "E310000000003", track=False, estado="borrador")
    await factura(pool, "E310000000004", fecha=hoy.replace(day=1) - timedelta(days=1))
    c = await repo.consumo(pool)
    assert c["ecf_emitidos"] == 2
    assert c["ecf_limite"] == 2000  # en prueba: límites del plan Pyme
    assert c["usuarios"] is None  # sin SUPABASE_SERVICE_ROLE_KEY no se cuentan
    c = await repo.consumo(pool, usuarios=3)
    assert (c["usuarios"], c["usuarios_limite"], c["usuarios_porcentaje"]) == (3, 5, 60)
    assert c["almacenamiento_bytes"] == 4 * len("<ECF>x</ECF>")


async def test_solicitud_de_plan_calcula_montos_en_el_servidor(pool):
    s = await repo.crear_solicitud_plan(pool, plan="pyme", periodicidad="anual", forma_pago="transferencia")
    assert (s["subtotal"], s["itbis"], s["total"]) == (D("44500.00"), D("8010.00"), D("52510.00"))
    assert (await repo.obtener_suscripcion(pool))["solicitud_pendiente"]["plan"] == "pyme"
    with pytest.raises(DatosInvalidosError):
        await repo.crear_solicitud_plan(pool, plan="oro", periodicidad="mensual", forma_pago="tarjeta")


# --- Portal de clientes ---------------------------------------------------------

async def test_portal_solo_muestra_las_facturas_aceptadas_del_cliente(pool):
    hoy = repo.hoy()
    uno = await cliente(pool, "Cliente Uno, SRL", "131234567")
    await cliente(pool, "Otro Cliente, SRL", "130000001")
    contado = await factura(pool, "E310000000010")
    credito = await factura(pool, "E310000000011", total="5000.00", credito=True, vence=hoy - timedelta(days=2))
    ajena = await factura(pool, "E310000000012", rnc="130000001", nombre="Otro Cliente, SRL")
    await factura(pool, "E310000000013", estado="rechazado")
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO ventas.cobros (factura_id, fecha, monto, metodo) VALUES ($1, $2, 1500, 'efectivo')", credito, hoy
        )
    await repo.guardar_config_portal(pool, banco="Banco Popular", numero_cuenta="123-456789-0", titular="Distribuidora Demo")

    enlace = await repo.crear_enlace(pool, cliente_id=uno, metodos_pago=["transferencia", "tarjeta"], dias_validez=30)
    assert len(enlace["token"]) >= 43 and enlace["estado"] == "activo"
    async with pool.acquire() as conn:
        guardado = await conn.fetchval("SELECT token_hash FROM soluciones.portal_enlaces")
    assert guardado != enlace["token"]  # solo el hash

    await repo.portal_publico(pool, enlace["token"], registrar_acceso=False)  # vista previa del usuario
    vista = await repo.portal_publico(pool, enlace["token"])
    assert {f["encf"] for f in vista["facturas"]} == {"E310000000010", "E310000000011"}
    por_encf = {f["encf"]: f for f in vista["facturas"]}
    assert por_encf["E310000000010"]["estado_pago"] == "contado"
    assert por_encf["E310000000011"]["estado_pago"] == "vencida"
    assert vista["saldo_pendiente"] == D("3500.00")
    assert vista["transferencia"]["numero_cuenta"] == "123-456789-0"
    assert vista["empresa"] == "Distribuidora Demo, SRL"

    assert (await repo.factura_del_portal(pool, enlace["token"], contado))["encf"] == "E310000000010"
    with pytest.raises(NoEncontradoError):
        await repo.factura_del_portal(pool, enlace["token"], ajena)

    enlaces = await repo.listar_enlaces(pool, cliente_id=uno)
    assert enlaces[0]["accesos"] == 1 and "token" not in enlaces[0]

    revocado = await repo.revocar_enlace(pool, uuid.UUID(enlace["id"]))
    assert revocado["estado"] == "revocado"
    with pytest.raises(NoEncontradoError):
        await repo.portal_publico(pool, enlace["token"])
    with pytest.raises(NoEncontradoError):
        await repo.factura_del_portal(pool, enlace["token"], contado)


async def test_enlace_vencido_no_es_valido_y_cliente_sin_rnc_no_tiene_portal(pool):
    uno = await cliente(pool, "Cliente Uno, SRL", "131234567")
    sin_rnc = await cliente(pool, "Consumidor", None)
    enlace = await repo.crear_enlace(pool, cliente_id=uno, metodos_pago=[], dias_validez=1)
    async with pool.acquire() as conn:
        await conn.execute("UPDATE soluciones.portal_enlaces SET expira_en = now() - interval '1 minute'")
    with pytest.raises(NoEncontradoError):
        await repo.portal_publico(pool, enlace["token"])
    assert (await repo.listar_enlaces(pool))[0]["estado"] == "vencido"
    with pytest.raises(DatosInvalidosError):
        await repo.crear_enlace(pool, cliente_id=sin_rnc, metodos_pago=[], dias_validez=30)
    with pytest.raises(NoEncontradoError):
        await repo.portal_publico(pool, "x" * 43)


# --- Kontara Ventas ---------------------------------------------------------

async def test_generar_envios_de_las_cuatro_automatizaciones_es_idempotente(pool):
    hoy = repo.hoy()
    await cliente(pool, "Cobro Pronto, SRL", "131000001", correo="pagos@cobro.do")
    await cliente(pool, "Inactivo Viejo, SRL", "131000002")
    await cliente(pool, "Hace Un Mes, SRL", "131000003")
    await cliente(pool, "Sin Telefono, SRL", "131000004", telefono=None)
    await factura(pool, "E310000000020", rnc="131000001", nombre="Cobro Pronto, SRL", credito=True,
                  vence=hoy + timedelta(days=2))
    await factura(pool, "E310000000021", rnc="131000002", fecha=hoy - timedelta(days=120))
    await factura(pool, "E310000000022", rnc="131000003", fecha=hoy - timedelta(days=45))
    await factura(pool, "E310000000023", rnc="131000004", fecha=hoy - timedelta(days=45))

    # Módulo inactivo: no genera nada.
    assert (await repo.generar_envios(pool))["creados"] == 0

    estado = await repo.activar_ventas(pool, True)
    assert estado["prueba_hasta"] == hoy + timedelta(days=30) and estado["habilitado"]
    for clave in ("motivar", "cobranza", "reactivacion"):
        await repo.actualizar_automatizacion(pool, clave, activa=True)
    await repo.actualizar_automatizacion(pool, "cobranza", canal="ambos")
    fid = await repo.actualizar_automatizacion(pool, "fidelizacion", activa=True)
    assert fid["activada_en"] is not None
    # Aceptada después de activar fidelización -> se agradece.
    await factura(pool, "E310000000024", rnc="131000001", nombre="Cobro Pronto, SRL")

    r = await repo.generar_envios(pool)
    assert r["por_automatizacion"] == {"reactivacion": 1, "motivar": 1, "cobranza": 2, "fidelizacion": 1}
    assert r["omitidos_sin_contacto"] == 1
    envios = await repo.listar_envios(pool)
    assert {e["estado"] for e in envios} == {"pendiente_canal"}
    cobranza = [e for e in envios if e["automatizacion"] == "cobranza"]
    assert {e["canal"] for e in cobranza} == {"whatsapp", "correo"}
    assert "E310000000020" in cobranza[0]["mensaje"] and "1,180.00" in cobranza[0]["mensaje"]
    whatsapp = next(e for e in cobranza if e["canal"] == "whatsapp")
    assert whatsapp["destino"] == "18095550101"

    # Segunda corrida el mismo día: nada nuevo.
    assert (await repo.generar_envios(pool))["creados"] == 0
    # Un plan Pyme mantiene el módulo habilitado aunque termine la prueba.
    await repo.obtener_suscripcion(pool)
    async with pool.acquire() as conn:
        await conn.execute("UPDATE soluciones.ventas_modulo SET prueba_hasta = $1", hoy - timedelta(days=1))
        await conn.execute("UPDATE soluciones.suscripcion SET plan = 'pyme'")
    assert (await repo.estado_ventas(pool))["habilitado"]

    marcado = await repo.marcar_envio(pool, uuid.UUID(whatsapp["id"]), "enviado_manual")
    assert marcado["estado"] == "enviado_manual" and marcado["enviado_en"] is not None
    correo = next(e for e in cobranza if e["canal"] == "correo")
    await repo.registrar_entrega(pool, correo["id"], error=None)
    [entregado] = await repo.listar_envios(pool, estado="enviado")
    with pytest.raises(ConflictoSolucionesError):
        await repo.marcar_envio(pool, uuid.UUID(entregado["id"]), "descartado")


async def test_prueba_de_ventas_vencida_sin_plan_no_genera(pool):
    await repo.activar_ventas(pool, True)
    async with pool.acquire() as conn:
        await conn.execute("UPDATE soluciones.ventas_modulo SET prueba_hasta = $1", repo.hoy() - timedelta(days=1))
    estado = await repo.estado_ventas(pool)
    assert estado["activo"] and not estado["habilitado"]
    assert "prueba" in (await repo.generar_envios(pool))["motivo"]


# --- POS ---------------------------------------------------------

async def test_caja_venta_mixta_y_arqueo(pool):
    sesion = await repo.abrir_sesion(pool, caja="Caja principal", fondo_inicial=D("2000"), cajero="Ana", almacen_id=None)
    with pytest.raises(ConflictoSolucionesError):
        await repo.abrir_sesion(pool, caja="Caja principal", fondo_inicial=D("0"), cajero=None, almacen_id=None)
    sid = uuid.UUID(sesion["id"])

    f1 = await factura(pool, "E320000000030", rnc=None, total="1180.00")
    v1 = await repo.registrar_venta(pool, sid, factura_id=f1, pagos=[{"metodo": "efectivo", "monto": D("1500")}])
    assert (v1["recibido"], v1["cambio"]) == (D("1500.00"), D("320.00"))
    f2 = await factura(pool, "E310000000031", total="5900.00")
    v2 = await repo.registrar_venta(
        pool, sid, factura_id=f2,
        pagos=[{"metodo": "tarjeta", "monto": D("4000")}, {"metodo": "efectivo", "monto": D("1900")}],
    )
    assert v2["cliente_rnc"] == "131234567" and v2["cambio"] == D("0.00")

    with pytest.raises(ConflictoSolucionesError):  # misma factura dos veces
        await repo.registrar_venta(pool, sid, factura_id=f1, pagos=[{"metodo": "efectivo", "monto": D("1180")}])
    sin_enviar = await factura(pool, "E320000000032", rnc=None, track=False, estado="generado")
    with pytest.raises(ConflictoSolucionesError):
        await repo.registrar_venta(pool, sid, factura_id=sin_enviar, pagos=[{"metodo": "efectivo", "monto": D("1180")}])
    corta = await factura(pool, "E320000000033", rnc=None)
    with pytest.raises(DatosInvalidosError):
        await repo.registrar_venta(pool, sid, factura_id=corta, pagos=[{"metodo": "tarjeta", "monto": D("100")}])

    abierta = await repo.sesion_abierta(pool, "Caja principal")
    assert abierta["cantidad_ventas"] == 2 and abierta["total_ventas"] == D("7080.00")
    assert abierta["esperado"] == {"efectivo": D("5080.00"), "tarjeta": D("4000.00"), "transferencia": D("0.00")}

    cerrada = await repo.cerrar_sesion(
        pool, sid, contado={"efectivo": D("5000"), "tarjeta": D("4000"), "transferencia": D("0")}, notas="Faltan 80"
    )
    assert cerrada["estado"] == "cerrada"
    assert cerrada["diferencias"]["efectivo"] == D("-80.00")
    with pytest.raises(ConflictoSolucionesError):
        await repo.registrar_venta(pool, sid, factura_id=corta, pagos=[{"metodo": "efectivo", "monto": D("1180")}])
    assert await repo.sesion_abierta(pool, "Caja principal") is None
    historial = await repo.listar_sesiones(pool)
    assert historial[0]["cantidad_ventas"] == 2 and historial[0]["diferencias"]["efectivo"] == D("-80.00")
    # Se puede abrir una nueva sesión después del cierre.
    await repo.abrir_sesion(pool, caja="Caja principal", fondo_inicial=D("0"), cajero=None, almacen_id=None)
