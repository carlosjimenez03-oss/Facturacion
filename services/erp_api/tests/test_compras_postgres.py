"""Compras (fase 3) contra un Postgres REAL: concurrencia de la aprobación
comercial con receptor_recepcion, candados de declaraciones presentadas,
notas de crédito, rechazo, vínculo con el 606 y totales del e-CF.

Se salta si no hay `COMPRAS_TEST_DATABASE_URL` (DSN de una base con permiso
para crear bases, p. ej. `postgresql://postgres@localhost:5550/postgres`):
cada prueba crea una base nueva, carga `db/init/*.sql` y aplica las
migraciones de erp-api.
"""
import asyncio
import os
import time
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path

import asyncpg
import pytest

from libs.ecf_common.auth.signing import firmar_xml
from libs.ecf_common.ecf_recibidos import registrar_ecf_recibido
from services.erp_api.app import compras_repository as cr
from services.erp_api.app.aprobacion_client import AprobacionComercialError
from services.erp_api.app.impuestos_repository import ConflictoImpuestosError
from services.erp_api.app.migraciones import aplicar_migraciones
from services.erp_api.app.repository import DatosInvalidosError
from services.receptor_recepcion.app import main as receptor

DSN = os.environ.get("COMPRAS_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="sin COMPRAS_TEST_DATABASE_URL (Postgres real)")

REPO = Path(__file__).resolve().parents[3]
D = Decimal
HOY = date(2026, 10, 23)
NUESTRO_RNC = "131897542"
ACEPTADA = {"mensaje": ["Aprobación comercial recibida"], "estado": "Aprobada", "codigo": "1"}


@pytest.fixture
async def pool(monkeypatch):
    monkeypatch.setattr(cr, "hoy", lambda: HOY)
    nombre = f"compras_{uuid.uuid4().hex[:10]}"
    admin = await asyncpg.connect(DSN)
    await admin.execute(f"CREATE DATABASE {nombre} TEMPLATE template0 ENCODING 'UTF8'")
    await admin.close()
    dsn = DSN.rsplit("/", 1)[0] + f"/{nombre}"
    conn = await asyncpg.connect(dsn)
    for archivo in sorted((REPO / "db" / "init").glob("*.sql")):
        await conn.execute(archivo.read_text(encoding="utf-8"))
    await conn.close()
    # Igual que crear_pool (max 5): las pruebas de concurrencia lo agotarían si se retuviera la conexión.
    p = await asyncpg.create_pool(dsn, min_size=1, max_size=5)
    await aplicar_migraciones(p)
    yield p
    await p.close()
    admin = await asyncpg.connect(DSN)
    await admin.execute(f"DROP DATABASE IF EXISTS {nombre} WITH (FORCE)")
    await admin.close()


def ecf(encf, *, rnc="131234567", nombre="Proveedor, SRL", servicios="1000.00", itbis="180.00", total=None,
        id_doc="", totales_extra="", fecha="05-10-2026", ncf_modificado=None, item=None):
    total = total or f"{D(servicios) + D(itbis):.2f}"
    item = item or (
        "<Item><NumeroLinea>1</NumeroLinea><IndicadorFacturacion>1</IndicadorFacturacion><NombreItem>Servicio</NombreItem>"
        f"<IndicadorBienoServicio>2</IndicadorBienoServicio><MontoItem>{servicios}</MontoItem></Item>"
    )
    referencia = (
        f"<InformacionReferencia><NCFModificado>{ncf_modificado}</NCFModificado></InformacionReferencia>"
        if ncf_modificado else ""
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?><ECF><Encabezado><Version>1.0</Version>'
        f"<IdDoc><TipoeCF>{encf[1:3]}</TipoeCF><eNCF>{encf}</eNCF>{id_doc}</IdDoc>"
        f"<Emisor><RNCEmisor>{rnc}</RNCEmisor><RazonSocialEmisor>{nombre}</RazonSocialEmisor>"
        f"<FechaEmision>{fecha}</FechaEmision></Emisor>"
        f"<Comprador><RNCComprador>{NUESTRO_RNC}</RNCComprador></Comprador>"
        f"<Totales><TotalITBIS>{itbis}</TotalITBIS>{totales_extra}<MontoTotal>{total}</MontoTotal></Totales>"
        f"</Encabezado><DetallesItems>{item}</DetallesItems>{referencia}</ECF>"
    ).encode()


async def recibir(pool, xml):
    """El mismo flujo que receptor_recepcion.recibir_ecf después de armar el ARECF."""
    root = receptor._parse_ecf(xml)
    rnc, _, encf = receptor._extraer_campos_ecf(root)
    await receptor._registrar_compra_606_desde_ecf(pool, root, rnc, encf)
    await registrar_ecf_recibido(pool, xml, root)


async def ecf_id(pool, encf):
    return await pool.fetchval("SELECT id FROM recepcion.ecf_recibidos WHERE encf = $1", encf)


async def registro_606(pool, encf):
    return await pool.fetchval("SELECT id FROM compras606.registros WHERE ncf = $1", encf)


async def presentar(pool, formulario, periodo):
    await pool.execute(
        "INSERT INTO impuestos.declaraciones_presentadas (formulario, periodo, presentada_en) VALUES ($1, $2, $3)",
        formulario,
        periodo,
        HOY,
    )


async def pagar(pool, registro_id, monto, fecha=date(2026, 10, 20), metodo="transferencia", **ret):
    return await cr.registrar_pago(
        pool, registro606_id=registro_id, fecha=fecha, monto=D(monto), metodo=metodo, cuenta_origen=None,
        referencia=None, notas=None, tipo_retencion_isr=ret.get("tipo"), retencion_isr=D(ret.get("isr", "0")),
        retencion_itbis=D(ret.get("itbis", "0")),
    )


def factura(**cambios):
    datos = dict(
        proveedor_rnc="101672919", ncf="B0100000010", fecha_comprobante=date(2026, 10, 5), tipo_bienes_servicios=2,
        monto_servicios=D("1000"), monto_bienes=D("0"), itbis_facturado=D("180"), impuesto_selectivo_consumo=D("0"),
        otros_impuestos=D("0"), monto_propina_legal=D("0"),
    )
    datos.update(cambios)
    return datos


async def decidir(pool, encf, *, aprobado=True, enviar=None, entregar=None):
    async def aceptar(encf, xml):
        return {**ACEPTADA, "xml_firmado": "<ACECF>firmado</ACECF>"}

    return await cr.decidir_aprobacion(
        pool, await ecf_id(pool, encf), aprobado=aprobado, motivo=None if aprobado else "No se recibió",
        rnc_comprador=NUESTRO_RNC, enviar=enviar or aceptar, entregar=entregar,
    )


# --- 1 y 10: la aprobación no bloquea al receptor ni agota el pool ---------------------------------------------------------

async def test_reenvio_no_espera_a_una_aprobacion_en_curso_ni_se_agota_el_pool(pool):
    encfs = [f"E31000000010{i}" for i in range(6)]
    for encf in encfs:
        await recibir(pool, ecf(encf))
    soltar = asyncio.Event()

    async def lenta(encf, xml):
        await soltar.wait()  # la DGII tarda
        return ACEPTADA

    # 6 aprobaciones lentas a la vez con un pool de 5: ninguna retiene conexión durante el envío.
    tareas = [asyncio.create_task(decidir(pool, encf, enviar=lenta)) for encf in encfs]
    await asyncio.sleep(0.5)
    inicio = time.monotonic()
    await asyncio.wait_for(recibir(pool, ecf(encfs[0])), timeout=3)  # reenvío del proveedor
    lista = await asyncio.wait_for(cr.listar_ecf_recibidos(pool, estado="pendiente"), timeout=3)
    assert time.monotonic() - inicio < 1.5
    assert {e["encf"] for e in lista if e["envio_en_curso"]} == set(encfs)
    # Un segundo clic mientras se envía: 409, sin mandar otro ACECF.
    with pytest.raises(cr.ConflictoComprasError, match="se está enviando"):
        await decidir(pool, encfs[0])
    soltar.set()
    resultados = await asyncio.gather(*tareas)
    assert {r["estado_aprobacion"] for r in resultados} == {"aprobado"}


async def test_reenvio_con_la_fila_bloqueada_responde_enseguida(pool):
    await recibir(pool, ecf("E310000000201"))
    otra = await asyncpg.connect(pool._connect_args[0])
    transaccion = otra.transaction()
    await transaccion.start()
    await otra.execute("SELECT 1 FROM recepcion.ecf_recibidos WHERE encf = 'E310000000201' FOR UPDATE")
    inicio = time.monotonic()
    await recibir(pool, ecf("E310000000201"))
    assert time.monotonic() - inicio < 1
    await transaccion.rollback()
    await otra.close()


async def test_envio_vencido_se_puede_reintentar(pool):
    await recibir(pool, ecf("E310000000202"))
    await pool.execute(
        "UPDATE recepcion.ecf_recibidos SET estado_aprobacion = 'enviando', decision_en_envio = 'aprobado',"
        " envio_iniciado_en = now() - interval '10 minutes' WHERE encf = 'E310000000202'"
    )
    assert (await decidir(pool, "E310000000202"))["estado_aprobacion"] == "aprobado"


# --- 2: solo el código 1 de la DGII es éxito ---------------------------------------------------------

async def test_respuesta_no_aceptada_de_la_dgii_sigue_pendiente(pool):
    await recibir(pool, ecf("E310000000301"))

    async def rechazada(encf, xml):
        return {"mensaje": ["e-CF no encontrado"], "estado": "Rechazada", "codigo": "2"}

    with pytest.raises(AprobacionComercialError, match="código 2.*e-CF no encontrado"):
        await decidir(pool, "E310000000301", enviar=rechazada)
    detalle = await cr.obtener_ecf_recibido(pool, await ecf_id(pool, "E310000000301"))
    assert detalle["estado_aprobacion"] == "pendiente" and "e-CF no encontrado" in detalle["error_aprobacion"]

    async def no_requerida(encf, xml):
        return {"mensaje": ["Este tipo de e-CF no requiere aprobación comercial"], "codigo": "2"}

    with pytest.raises(cr.ConflictoComprasError, match="no requiere aprobación comercial"):
        await decidir(pool, "E310000000301", enviar=no_requerida)


async def test_acecf_sin_fecha_emision_es_422(pool):
    await recibir(pool, ecf("E310000000302"))
    await pool.execute("UPDATE recepcion.ecf_recibidos SET fecha_emision = NULL WHERE encf = 'E310000000302'")
    with pytest.raises(DatosInvalidosError, match="FechaEmision"):
        await decidir(pool, "E310000000302")


# --- 3: candado del 606 presentado (y mensajes con nombre y mes) ---------------------------------------------------------

async def test_606_presentado_bloquea_pagos_altas_bajas_y_clasificacion(pool):
    creada = await cr.crear_factura_compra(pool, **factura())
    rid = uuid.UUID(creada["registro606_id"])
    await presentar(pool, "606", "202610")
    with pytest.raises(ConflictoImpuestosError, match="El Formato 606 de octubre 2026 ya está marcado como presentado"):
        await pagar(pool, rid, "100")
    with pytest.raises(ConflictoImpuestosError, match="Formato 606 de octubre 2026"):
        await cr.crear_factura_compra(pool, **factura(ncf="B0100000011"))
    with pytest.raises(ConflictoImpuestosError, match="Formato 606"):
        await cr.eliminar_factura_compra(pool, rid)
    with pytest.raises(ConflictoImpuestosError, match="Formato 606"):
        await cr.actualizar_factura_compra(pool, rid, {"tipo_bienes_servicios": 9})
    # Lo que no toca el 606 sí se puede.
    assert (await cr.actualizar_factura_compra(pool, rid, {"concepto": "Honorarios"}))["concepto"] == "Honorarios"


async def test_it1_presentado_bloquea_eliminar(pool):
    creada = await cr.crear_factura_compra(pool, **factura())
    await presentar(pool, "it1", "202610")
    with pytest.raises(ConflictoImpuestosError, match="El IT-1 de octubre 2026"):
        await cr.eliminar_factura_compra(pool, uuid.UUID(creada["registro606_id"]))


async def test_clasificacion_no_vuelve_a_null(pool):
    creada = await cr.crear_factura_compra(pool, **factura())
    with pytest.raises(DatosInvalidosError, match="ya está clasificado"):
        await cr.actualizar_factura_compra(pool, uuid.UUID(creada["registro606_id"]), {"tipo_bienes_servicios": None})


# --- 4: notas de crédito del proveedor ---------------------------------------------------------

async def test_nota_de_credito_se_registra_y_reduce_el_pendiente(pool):
    creada = await cr.crear_factura_compra(pool, **factura())
    nota = await cr.crear_factura_compra(
        pool, **factura(ncf="B0400000001", ncf_modificado="B0100000010", monto_servicios=D("100"), itbis_facturado=D("18"))
    )
    assert nota["es_nota_credito"] is True and nota["afecta_registro606_id"] == creada["registro606_id"]
    assert nota["pendiente"] == 0 and nota["forma_pago"] == 6
    factura_actual = await cr.obtener_factura_compra(pool, uuid.UUID(creada["registro606_id"]))
    assert factura_actual["notas_credito"] == D("118.00") and factura_actual["pendiente"] == D("1062.00")
    cuentas = await cr.listar_cuentas_por_pagar(pool, estado="todas")
    assert [c["ncf"] for c in cuentas] == ["B0100000010"]  # la nota no es una cuenta por pagar
    with pytest.raises(DatosInvalidosError, match="ncf_modificado|NCF de la factura que modifica"):
        await cr.crear_factura_compra(pool, **factura(ncf="B0400000002"))


# --- 5: rechazo ---------------------------------------------------------

async def test_rechazo_con_pagos_es_409(pool):
    await recibir(pool, ecf("E310000000501"))
    await pagar(pool, await registro_606(pool, "E310000000501"), "850", tipo=2, isr="150", itbis="180")
    with pytest.raises(cr.ConflictoComprasError, match="tiene pagos registrados"):
        await decidir(pool, "E310000000501", aprobado=False)


async def test_rechazo_quita_el_606_y_el_reenvio_no_lo_vuelve_a_crear(pool):
    await recibir(pool, ecf("E310000000502"))
    assert await registro_606(pool, "E310000000502") is not None
    resultado = await decidir(pool, "E310000000502", aprobado=False)
    assert resultado["estado_aprobacion"] == "rechazado" and resultado["compra606_id"] is None
    assert await registro_606(pool, "E310000000502") is None  # sin ITBIS adelantado en el IT-1
    await recibir(pool, ecf("E310000000502"))
    assert await registro_606(pool, "E310000000502") is None


async def test_rechazo_con_el_606_presentado_es_409_antes_de_enviar(pool):
    await recibir(pool, ecf("E310000000503"))
    await presentar(pool, "it1", "202610")
    enviados = []

    async def enviar(encf, xml):
        enviados.append(encf)
        return ACEPTADA

    with pytest.raises(ConflictoImpuestosError, match="IT-1 de octubre 2026"):
        await decidir(pool, "E310000000503", aprobado=False, enviar=enviar)
    assert enviados == []


# --- 6: firma ---------------------------------------------------------

async def test_copia_falsa_sin_firma_se_reemplaza_por_la_firmada(pool, credencial_con_rnc):
    await recibir(pool, ecf("E310000000601", nombre="FALSO", servicios="1.00", itbis="0.18"))
    real = firmar_xml(ecf("E310000000601", nombre="Real", servicios="50000.00", itbis="9000.00"),
                      credencial_con_rnc("131234567"))
    await recibir(pool, real)
    fila = await pool.fetchrow(
        "SELECT razon_social_emisor, monto_total, firma_valida FROM recepcion.ecf_recibidos WHERE encf = 'E310000000601'"
    )
    assert (fila["razon_social_emisor"], fila["monto_total"], fila["firma_valida"]) == ("Real", D("59000.00"), True)
    registro = await pool.fetchrow("SELECT monto_servicios, itbis_facturado FROM compras606.registros WHERE ncf = 'E310000000601'")
    assert (registro["monto_servicios"], registro["itbis_facturado"]) == (D("50000.00"), D("9000.00"))
    # Y una copia falsa posterior ya no pisa a la firmada.
    await recibir(pool, ecf("E310000000601", nombre="FALSO 2", servicios="1.00", itbis="0.18"))
    assert await pool.fetchval("SELECT razon_social_emisor FROM recepcion.ecf_recibidos WHERE encf = 'E310000000601'") == "Real"


# --- 7: vínculo con el 606 ---------------------------------------------------------

async def test_vincular_exige_el_mismo_ncf_y_el_receptor_no_choca(pool):
    await recibir(pool, ecf("E310000000701"))
    otro = await pool.fetchval(
        "INSERT INTO compras606.registros (periodo, origen, rnc_cedula, tipo_id, ncf, fecha_comprobante,"
        " monto_servicios, monto_bienes, itbis_facturado)"
        " VALUES ('202610', 'manual', '131234567', 1, 'E310000000702', '20261005', 10, 0, 1.8) RETURNING id"
    )
    with pytest.raises(DatosInvalidosError, match="tiene el NCF E310000000702"):
        await cr.vincular_ecf_recibido(pool, await ecf_id(pool, "E310000000701"), {"registro606_id": otro})
    # Aunque alguien lo hubiera enlazado a mano, el e-CF que sí le corresponde se guarda.
    await pool.execute("UPDATE recepcion.ecf_recibidos SET compra606_id = $1 WHERE encf = 'E310000000701'", otro)
    await recibir(pool, ecf("E310000000702", servicios="10.00", itbis="1.80"))
    fila = await pool.fetchrow("SELECT compra606_id FROM recepcion.ecf_recibidos WHERE encf = 'E310000000702'")
    assert fila is not None and fila["compra606_id"] is None


# --- 8: total por pagar = MontoTotal del e-CF ---------------------------------------------------------

async def test_total_por_pagar_incluye_propina_y_no_duplica_itbis(pool):
    propina = (
        "<MontoImpuestoAdicional>100.00</MontoImpuestoAdicional><ImpuestosAdicionales><ImpuestoAdicional>"
        "<TipoImpuesto>001</TipoImpuesto><TasaImpuestoAdicional>10</TasaImpuestoAdicional>"
        "<OtrosImpuestosAdicionales>100.00</OtrosImpuestosAdicionales></ImpuestoAdicional></ImpuestosAdicionales>"
    )
    await recibir(pool, ecf("E310000000801", total="1280.00", totales_extra=propina))
    cuenta = await cr.obtener_factura_compra(pool, await registro_606(pool, "E310000000801"))
    assert cuenta["total"] == D("1280.00") and cuenta["pendiente"] == D("1280.00")
    assert cuenta["monto_facturado"] == D("1000.00")
    # ITBIS incluido en las líneas (IndicadorMontoGravado=1): 1180 con ITBIS -> 1000 + 180.
    con_itbis = (
        "<Item><IndicadorFacturacion>1</IndicadorFacturacion><IndicadorBienoServicio>1</IndicadorBienoServicio>"
        "<MontoItem>1180.00</MontoItem></Item>"
    )
    await recibir(pool, ecf("E310000000802", id_doc="<IndicadorMontoGravado>1</IndicadorMontoGravado>", item=con_itbis))
    cuenta = await cr.obtener_factura_compra(pool, await registro_606(pool, "E310000000802"))
    assert (cuenta["monto_facturado"], cuenta["itbis_facturado"], cuenta["total"]) == (
        D("1000.00"), D("180.00"), D("1180.00"),
    )
    pagado = await pagar(pool, await registro_606(pool, "E310000000801"), "1280.00")
    assert pagado["monto"] == D("1280.00")


# --- 9: entrega del ACECF al proveedor ---------------------------------------------------------

async def test_entrega_al_emisor_best_effort(pool):
    await recibir(pool, ecf("E310000000901"))
    await recibir(pool, ecf("E310000000902"))
    entregados = []

    async def entregar(rnc, encf, xml):
        entregados.append((rnc, encf, xml))

    async def falla(rnc, encf, xml):
        raise RuntimeError("El proveedor respondió 401: token requerido")

    ok = await decidir(pool, "E310000000901", entregar=entregar)
    assert ok["enviado_al_emisor"] is True and entregados == [("131234567", "E310000000901", b"<ACECF>firmado</ACECF>")]
    mal = await decidir(pool, "E310000000902", entregar=falla)
    assert mal["estado_aprobacion"] == "aprobado" and mal["enviado_al_emisor"] is False
    assert "401" in mal["error_envio_emisor"]


# --- Pagos: fecha futura y forma de pago a crédito ---------------------------------------------------------

async def test_pago_futuro_y_pago_parcial(pool):
    creada = await cr.crear_factura_compra(pool, **factura())
    rid = uuid.UUID(creada["registro606_id"])
    with pytest.raises(DatosInvalidosError, match="futura"):
        await pagar(pool, rid, "100", fecha=date(2026, 10, 24))
    await pagar(pool, rid, "500", metodo="efectivo")
    assert await pool.fetchval("SELECT forma_pago FROM compras606.registros WHERE id = $1", rid) == 4
    await pagar(pool, rid, "680", metodo="efectivo")
    assert await pool.fetchval("SELECT forma_pago FROM compras606.registros WHERE id = $1", rid) == 1
