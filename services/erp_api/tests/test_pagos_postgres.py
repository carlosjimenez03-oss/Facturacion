"""Pagos con tarjeta en línea (fase 7) contra un Postgres REAL, por HTTP
(la app de erp-api con el pool de la prueba) y con el proveedor simulado,
que firma igual que Azul:

portal → intento → «página del banco» → retorno firmado → cobro (método
tarjeta) + asiento, una sola vez aunque el retorno se repita o llegue dos
veces a la vez; firma alterada, monto que no coincide, declinado,
cancelado, vencido, pagos parciales, factura ajena y límite de intentos.

Se salta si no hay `SOLUCIONES_TEST_DATABASE_URL` (como
test_soluciones_postgres.py): cada prueba crea una base nueva, carga
`db/init/*.sql` y aplica las migraciones de erp-api.
"""
import asyncio
import html
import json
import os
import re
import uuid
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit

import asyncpg
import httpx
import pytest

from services.erp_api.app import configuracion_envio as preferencias
from services.erp_api.app import main, pagos
from services.erp_api.app import pagos_proveedores as pp
from services.erp_api.app import repository as ventas
from services.erp_api.app import soluciones_repository as portal
from services.erp_api.app.migraciones import aplicar_migraciones

DSN = os.environ.get("SOLUCIONES_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="sin SOLUCIONES_TEST_DATABASE_URL (Postgres real)")

REPO = Path(__file__).resolve().parents[3]
D = Decimal
RNC = "131234567"


@pytest.fixture
async def pool():
    nombre = f"pagos_{uuid.uuid4().hex[:10]}"
    admin = await asyncpg.connect(DSN)
    await admin.execute(f"CREATE DATABASE {nombre} TEMPLATE template0 ENCODING 'UTF8'")
    await admin.close()
    dsn = DSN.rsplit("/", 1)[0] + f"/{nombre}"
    conn = await asyncpg.connect(dsn)
    for archivo in sorted((REPO / "db" / "init").glob("*.sql")):
        await conn.execute(archivo.read_text(encoding="utf-8"))
    await conn.close()
    p = await asyncpg.create_pool(dsn, min_size=1, max_size=8)
    await aplicar_migraciones(p)
    await aplicar_migraciones(p)  # idempotente
    yield p
    await p.close()
    admin = await asyncpg.connect(DSN)
    await admin.execute(f"DROP DATABASE IF EXISTS {nombre} WITH (FORCE)")
    await admin.close()


@pytest.fixture
async def api(pool, monkeypatch):
    monkeypatch.setattr(pagos.get_pagos_settings(), "app_env", "test")
    monkeypatch.setattr(pagos.get_pagos_settings(), "pagos_url_publica", "")
    monkeypatch.setattr(pagos.get_pagos_settings(), "azul_auth_key", "")
    monkeypatch.setattr(main.get_settings(), "supabase_jwt_secret", "")
    anterior = main.app.state.db_pool
    main.app.state.db_pool = pool
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://testserver") as c:
        yield c
    main.app.state.db_pool = anterior


def dgii(fecha):
    return fecha.strftime("%d-%m-%Y")


async def factura(pool, encf, *, rnc=RNC, total="1180.00", itbis="180.00", credito=True, estado="aceptado"):
    hoy = ventas.hoy()
    id_doc = {"TipoeCF": encf[1:3], "eNCF": encf, "TipoPago": "2" if credito else "1"}
    if credito:
        id_doc["FechaLimitePago"] = dgii(hoy + timedelta(days=30))
    datos = {
        "Encabezado": {
            "Version": "1.0",
            "IdDoc": id_doc,
            "Emisor": {"RNCEmisor": "131897542", "RazonSocialEmisor": "Distribuidora Demo, SRL", "FechaEmision": dgii(hoy)},
            "Comprador": {"RNCComprador": rnc, "RazonSocialComprador": "Cliente Uno, SRL"},
            "Totales": {"MontoGravadoTotal": str(D(total) - D(itbis)), "TotalITBIS": itbis, "MontoTotal": total},
        },
        "DetallesItems": {"Item": []},
    }
    async with pool.acquire() as conn:
        return await conn.fetchval(
            """
            INSERT INTO facturacion.facturas (tipo_ecf, ambiente, rnc_emisor, encf, estado, datos, track_id, xml_generado)
            VALUES ($1, 'test', '131897542', $2, $3, $4::json, $5, '<ECF>x</ECF>') RETURNING id
            """,
            int(encf[1:3]), encf, estado, json.dumps(datos), "track-" + encf,
        )


async def enlace(pool, rnc=RNC, metodos=("tarjeta", "transferencia")):
    async with pool.acquire() as conn:
        cliente_id = await conn.fetchval(
            "INSERT INTO ventas.clientes (nombre, rnc) VALUES ('Cliente Uno, SRL', $1) RETURNING id", rnc
        )
    return (await portal.crear_enlace(pool, cliente_id=cliente_id, metodos_pago=list(metodos), dias_validez=30))["token"]


async def configurar(api, **cambios):
    datos = {"activo": True, "proveedor": "simulado", "ambiente": "pruebas", "merchant_name": "Distribuidora Demo"} | cambios
    r = await api.put("/configuracion/pagos-en-linea", json=datos)
    assert r.status_code == 200, r.text
    return r.json()


async def iniciar(api, token, factura_id, monto=None):
    return await api.post(f"/portal/publico/{token}/pagos", json={"factura_id": str(factura_id), "monto": monto})


async def pagina_simulada(api, inicio):
    r = await api.post(urlsplit(inicio["accion"]).path, data=inicio["campos"])
    assert r.status_code == 200, r.text
    assert r.headers["content-security-policy"].startswith("default-src 'none'")
    enlaces = {m[0]: html.unescape(m[1]) for m in re.findall(r'id="(\w+)" href="([^"]+)"', r.text)}
    return enlaces


def ruta(url):
    partes = urlsplit(url)
    return partes.path + (f"?{partes.query}" if partes.query else "")


async def cobros(pool, factura_id):
    async with pool.acquire() as conn:
        return await conn.fetch("SELECT * FROM ventas.cobros WHERE factura_id = $1", factura_id)


async def asientos_de(pool, cobro_id):
    async with pool.acquire() as conn:
        return await conn.fetch(
            "SELECT * FROM contabilidad.asientos WHERE origen = 'cobro' AND origen_id = $1 AND estado <> 'anulado'",
            str(cobro_id),
        )


async def pendiente(pool, factura_id):
    async with pool.acquire() as conn:
        return await conn.fetchval(f"WITH {ventas._CTE_CXC} SELECT pendiente FROM cxc WHERE factura_id = $1", factura_id)


async def tipos_de_eventos(pool, referencia):
    async with pool.acquire() as conn:
        return [f["tipo"] for f in await conn.fetch(
            """
            SELECT e.tipo FROM soluciones.pagos_en_linea_eventos e
            JOIN soluciones.pagos_en_linea p ON p.id = e.pago_id WHERE p.referencia = $1 ORDER BY e.id
            """,
            referencia,
        )]


# --- Flujo completo ---------------------------------------------------------

async def test_pago_aprobado_registra_un_cobro_y_su_asiento_una_sola_vez(pool, api):
    token = await enlace(pool)
    fid = await factura(pool, "E310000000001")
    config = await configurar(api)
    assert config["listo"] and config["auth_key_configurada"] is False and config["proveedor"] == "simulado"

    portal_publico = (await api.get(f"/portal/publico/{token}")).json()
    assert portal_publico["pago_en_linea"] == {
        "proveedor": "simulado", "nombre": "Simulado (pruebas)", "pruebas": True, "permitir_parcial": False,
    }

    r = await iniciar(api, token, fid)
    assert r.status_code == 201, r.text
    inicio = r.json()
    assert inicio["monto"] == "1180.00" and inicio["itbis"] == "180.00"
    campos = inicio["campos"]
    assert (campos["Amount"], campos["ITBIS"], campos["CurrencyCode"]) == ("118000", "18000", "$")
    assert campos["ApprovedUrl"] == f"http://testserver/portal/publico/pagos/{inicio['referencia']}/retorno/aprobado"
    assert token not in json.dumps(campos)  # el token del portal no viaja al banco

    enlaces = await pagina_simulada(api, inicio)
    aprobar = ruta(enlaces["aprobar"])
    r = await api.get(aprobar)
    assert r.status_code == 303
    assert r.headers["location"] == f"/p/pago/{inicio['referencia']}"

    estado = (await api.get(f"/portal/publico/pagos/{inicio['referencia']}")).json()
    assert estado["estado"] == "aprobado" and estado["cobro_registrado"] and not estado["requiere_revision"]
    assert estado["codigo_autorizacion"]

    filas = await cobros(pool, fid)
    assert len(filas) == 1
    cobro = filas[0]
    assert (cobro["monto"], cobro["metodo"], cobro["anulado"]) == (D("1180.00"), "tarjeta", False)
    assert "Aut." in cobro["referencia"] and "RRN" in cobro["referencia"]
    assert await pendiente(pool, fid) == D("0.00")
    asientos = await asientos_de(pool, cobro["id"])
    assert len(asientos) == 1 and asientos[0]["total"] == D("1180.00")

    # Repetir el retorno (recarga, doble callback): ni otro cobro ni otro asiento.
    for _ in range(2):
        assert (await api.get(aprobar)).status_code == 303
    assert len(await cobros(pool, fid)) == 1
    assert len(await asientos_de(pool, cobro["id"])) == 1
    eventos = await tipos_de_eventos(pool, inicio["referencia"])
    assert eventos.count("cobro_registrado") == 1 and eventos.count("repetido") == 2

    # «Generar asientos» después lo ve igual (sin duplicar).
    from services.erp_api.app import contabilidad_repository

    await contabilidad_repository.generar_asientos(pool, ventas.hoy(), ventas.hoy())
    assert len(await asientos_de(pool, cobro["id"])) == 1

    # Cobros: el listado interno muestra el pago con su referencia del banco.
    lista = (await api.get("/pagos-en-linea")).json()
    assert lista[0]["estado"] == "aprobado" and lista[0]["rrn"] and lista[0]["cobro_id"] == str(cobro["id"])
    # La factura pagada ya no admite otro intento.
    assert (await iniciar(api, token, fid)).status_code == 422


async def test_dos_retornos_a_la_vez_registran_un_solo_cobro(pool, api):
    token = await enlace(pool)
    fid = await factura(pool, "E310000000002")
    await configurar(api)
    inicio = (await iniciar(api, token, fid)).json()
    aprobar = ruta((await pagina_simulada(api, inicio))["aprobar"])
    respuestas = await asyncio.gather(*[api.get(aprobar) for _ in range(6)])
    assert {r.status_code for r in respuestas} == {303}
    assert len(await cobros(pool, fid)) == 1


async def test_firma_alterada_se_rechaza_y_queda_en_la_bitacora(pool, api):
    token = await enlace(pool)
    fid = await factura(pool, "E310000000003")
    await configurar(api)
    inicio = (await iniciar(api, token, fid)).json()
    aprobar = (await pagina_simulada(api, inicio))["aprobar"]
    params = dict(parse_qsl(urlsplit(aprobar).query, keep_blank_values=True))
    alterado = urlsplit(aprobar).path + "?" + urlencode(params | {"Amount": "100"})
    r = await api.get(alterado)
    assert r.status_code == 303 and r.headers["location"].endswith("?aviso=firma")
    estado = (await api.get(f"/portal/publico/pagos/{inicio['referencia']}")).json()
    assert estado["estado"] == "pendiente"
    assert await cobros(pool, fid) == []
    # Sin firma tampoco.
    sin_firma = urlsplit(aprobar).path + "?" + urlencode({k: v for k, v in params.items() if k != "AuthHash"})
    assert (await api.get(sin_firma)).headers["location"].endswith("?aviso=firma")
    assert (await tipos_de_eventos(pool, inicio["referencia"])).count("firma_invalida") == 2
    # La respuesta buena todavía sirve.
    assert (await api.get(ruta(aprobar))).status_code == 303
    assert len(await cobros(pool, fid)) == 1


async def test_respuesta_firmada_con_otro_monto_u_orden_no_cobra(pool, api):
    token = await enlace(pool)
    fid = await factura(pool, "E310000000004")
    await configurar(api)
    inicio = (await iniciar(api, token, fid)).json()
    campos = inicio["campos"]
    base = urlsplit(campos["ApprovedUrl"]).path
    otro_monto = pp.respuesta_simulada(campos | {"Amount": "100"}, "aprobado", rrn="1" * 12, autorizacion="123456", fecha="x")
    r = await api.get(f"{base}?{urlencode(otro_monto)}")
    assert r.headers["location"].endswith("?aviso=datos")
    otra_orden = pp.respuesta_simulada(campos | {"OrderNumber": "999"}, "aprobado", rrn="1" * 12, autorizacion="1", fecha="x")
    assert (await api.get(f"{base}?{urlencode(otra_orden)}")).headers["location"].endswith("?aviso=datos")
    assert await cobros(pool, fid) == []
    pago = (await api.get("/pagos-en-linea")).json()[0]
    assert pago["estado"] == "pendiente" and "no se registró el cobro" in pago["incidencia"]
    eventos = await tipos_de_eventos(pool, inicio["referencia"])
    assert "monto_no_coincide" in eventos and "orden_no_coincide" in eventos


async def test_declinado_y_cancelado_no_cobran(pool, api):
    token = await enlace(pool)
    fid = await factura(pool, "E310000000005")
    await configurar(api)
    inicio = (await iniciar(api, token, fid)).json()
    enlaces = await pagina_simulada(api, inicio)
    assert (await api.get(ruta(enlaces["declinar"]))).status_code == 303
    estado = (await api.get(f"/portal/publico/pagos/{inicio['referencia']}")).json()
    assert estado["estado"] == "declinado" and not estado["cobro_registrado"] and estado["mensaje"] == "DECLINADA"
    assert await cobros(pool, fid) == []
    assert await pendiente(pool, fid) == D("1180.00")
    # Cancelar después de declinado no cambia nada.
    assert (await api.get(ruta(enlaces["cancelar"]))).status_code == 303
    assert (await api.get(f"/portal/publico/pagos/{inicio['referencia']}")).json()["estado"] == "declinado"

    otro = (await iniciar(api, token, fid)).json()
    cancelar = ruta((await pagina_simulada(api, otro))["cancelar"])
    assert (await api.get(cancelar)).status_code == 303
    assert (await api.get(f"/portal/publico/pagos/{otro['referencia']}")).json()["estado"] == "cancelado"


async def test_intento_vencido_expira_pero_un_aprobado_tardio_igual_se_cobra(pool, api):
    token = await enlace(pool)
    fid = await factura(pool, "E310000000006")
    await configurar(api)
    inicio = (await iniciar(api, token, fid)).json()
    aprobar = ruta((await pagina_simulada(api, inicio))["aprobar"])
    async with pool.acquire() as conn:
        await conn.execute("UPDATE soluciones.pagos_en_linea SET expira_en = now() - interval '1 minute'")
    assert (await api.get(f"/portal/publico/pagos/{inicio['referencia']}")).json()["estado"] == "expirado"
    # El banco ya cobró: el aprobado firmado gana.
    assert (await api.get(aprobar)).status_code == 303
    assert (await api.get(f"/portal/publico/pagos/{inicio['referencia']}")).json()["estado"] == "aprobado"
    assert len(await cobros(pool, fid)) == 1


async def test_intento_nuevo_reemplaza_al_pendiente(pool, api):
    token = await enlace(pool)
    fid = await factura(pool, "E310000000007")
    await configurar(api)
    primero = (await iniciar(api, token, fid)).json()
    aprobar_primero = ruta((await pagina_simulada(api, primero))["aprobar"])
    segundo = (await iniciar(api, token, fid)).json()
    assert primero["campos"]["OrderNumber"] != segundo["campos"]["OrderNumber"]
    assert (await api.get(f"/portal/publico/pagos/{primero['referencia']}")).json()["estado"] == "cancelado"
    # Si el reemplazado igual se aprobó en el banco, se cobra (una vez) y el segundo ya no puede.
    await api.get(aprobar_primero)
    assert len(await cobros(pool, fid)) == 1
    aprobar_segundo = ruta((await pagina_simulada(api, segundo))["aprobar"])
    await api.get(aprobar_segundo)
    assert len(await cobros(pool, fid)) == 1
    pago = next(p for p in (await api.get("/pagos-en-linea")).json() if p["referencia"] == segundo["referencia"])
    assert pago["estado"] == "aprobado" and pago["cobro_id"] is None and "reembolso" in pago["incidencia"]
    estado = (await api.get(f"/portal/publico/pagos/{segundo['referencia']}")).json()
    assert estado["requiere_revision"] and not estado["cobro_registrado"]


async def test_pagos_parciales_e_itbis_proporcional(pool, api):
    token = await enlace(pool)
    fid = await factura(pool, "E310000000008", total="1180.00", itbis="180.00")
    await configurar(api)
    r = await iniciar(api, token, fid, "500.00")
    assert r.status_code == 422 and "saldo completo" in r.json()["detail"]
    await configurar(api, permitir_parcial=True)
    inicio = (await iniciar(api, token, fid, "500.00")).json()
    assert (inicio["monto"], inicio["itbis"]) == ("500.00", "76.27")
    await api.get(ruta((await pagina_simulada(api, inicio))["aprobar"]))
    assert await pendiente(pool, fid) == D("680.00")
    assert (await iniciar(api, token, fid, "680.01")).status_code == 422
    assert (await iniciar(api, token, fid, "0")).status_code == 422
    assert (await iniciar(api, token, fid)).json()["monto"] == "680.00"


async def test_solo_facturas_del_cliente_del_enlace_con_tarjeta(pool, api):
    token = await enlace(pool)
    ajena = await factura(pool, "E310000000009", rnc="101010101")
    contado = await factura(pool, "E310000000010", credito=False)
    sin_aceptar = await factura(pool, "E310000000011", estado="enviado")
    await configurar(api)
    for fid in (ajena, contado, sin_aceptar, uuid.uuid4()):
        r = await iniciar(api, token, fid)
        assert r.status_code == 404 and r.json()["detail"] == "Enlace o factura no válidos"
    propia = await factura(pool, "E310000000012")
    assert (await iniciar(api, "B" * 43, propia)).status_code == 404
    sin_tarjeta = await enlace(pool, rnc="131234568", metodos=("transferencia",))
    assert (await api.get(f"/portal/publico/{sin_tarjeta}")).json()["pago_en_linea"] is None
    otra = await factura(pool, "E310000000013", rnc="131234568")
    assert (await iniciar(api, sin_tarjeta, otra)).status_code == 404


async def test_apagado_o_incompleto_no_ofrece_ni_acepta_pagos(pool, api):
    token = await enlace(pool)
    fid = await factura(pool, "E310000000014")
    assert (await api.get(f"/portal/publico/{token}")).json()["pago_en_linea"] is None
    assert (await iniciar(api, token, fid)).status_code == 409
    # Azul sin AuthKey no se puede activar.
    r = await api.put("/configuracion/pagos-en-linea", json={
        "activo": True, "proveedor": "azul", "merchant_id": "39000000001", "merchant_name": "Demo", "merchant_type": "ECommerce",
    })
    assert r.status_code == 422 and "AuthKey" in r.json()["detail"]


async def test_limite_de_intentos_por_enlace(pool, api):
    token = await enlace(pool)
    fid = await factura(pool, "E310000000015")
    await configurar(api)
    for _ in range(20):
        assert (await iniciar(api, token, fid)).status_code == 201
    r = await iniciar(api, token, fid)
    assert r.status_code == 429


async def test_azul_con_auth_key_cifrada_y_cuenta_bancaria(pool, api, monkeypatch, tmp_path):
    monkeypatch.setenv("CERTIFICADOS_CLAVE", "clave-de-cifrado-de-prueba-larga-32+")
    from services.erp_api.app import bancos_repository

    cuenta = await bancos_repository.crear_cuenta(
        pool, banco="Banco Popular", tipo="corriente", moneda="DOP", saldo_inicial=D("0"), activa=True,
    )
    token = await enlace(pool)
    fid = await factura(pool, "E310000000016")
    config = await configurar(
        api, proveedor="azul", merchant_id="39000000001", merchant_name="Distribuidora Demo",
        merchant_type="ECommerce", auth_key="llave-azul-de-prueba", cuenta_bancaria_id=str(cuenta["id"]),
    )
    assert config["auth_key_configurada"] and config["auth_key_origen"] == "guardada"
    assert "llave-azul-de-prueba" not in json.dumps(config)
    guardado = await preferencias.leer_preferencia(pool, "pagos_en_linea", {})
    assert guardado["auth_key_cifrada"].startswith("v2:") and "llave-azul" not in json.dumps(guardado)

    inicio = (await iniciar(api, token, fid)).json()
    assert inicio["accion"] == "https://pruebas.azul.com.do/PaymentPage/"
    campos = inicio["campos"]
    assert campos["AuthHash"] == pp.azul_hash_solicitud(campos, "llave-azul-de-prueba")
    # Lo que Azul devolvería a ApprovedUrl, firmado con la misma llave.
    respuesta = {
        "OrderNumber": campos["OrderNumber"], "Amount": campos["Amount"], "AuthorizationCode": "OK5521",
        "DateTime": "20261024101500", "ResponseCode": "ISO8583", "IsoCode": "00", "ResponseMessage": "APROBADA",
        "ErrorDescription": "", "RRN": "202610240001", "AzulOrderId": "44550012", "CardNumber": "411111******1111",
    }
    respuesta["AuthHash"] = pp.azul_hash_respuesta(respuesta, "llave-azul-de-prueba")
    # Firmada como el simulado: no vale para un pago de Azul.
    falsa = pp.respuesta_simulada(campos, "aprobado", rrn="1" * 12, autorizacion="1", fecha="x")
    base = urlsplit(campos["ApprovedUrl"]).path
    assert (await api.get(f"{base}?{urlencode(falsa)}")).headers["location"].endswith("?aviso=firma")
    assert (await api.get(f"{base}?{urlencode(respuesta)}")).status_code == 303
    [cobro] = await cobros(pool, fid)
    assert str(cobro["cuenta_bancaria_id"]) == str(cuenta["id"])
    assert cobro["referencia"] == "Azul · Aut. OK5521 · RRN 202610240001"
    pago = (await api.get("/pagos-en-linea")).json()[0]
    assert (pago["codigo_autorizacion"], pago["rrn"], pago["orden_proveedor"]) == ("OK5521", "202610240001", "44550012")
    # La tarjeta enmascarada no queda en la bitácora.
    eventos = (await api.get(f"/pagos-en-linea/{pago['id']}/eventos")).json()
    assert "CardNumber" not in json.dumps(eventos) and "411111" not in json.dumps(eventos)
    # La variable de entorno manda sobre la guardada.
    monkeypatch.setattr(pagos.get_pagos_settings(), "azul_auth_key", "otra-llave")
    assert (await api.get("/configuracion/pagos-en-linea")).json()["auth_key_origen"] == "variable"
    # La pasarela simulada no atiende pagos de Azul.
    assert (await api.post(f"/portal/publico/pagos/{inicio['referencia']}/simulado", data=campos)).status_code == 404


async def test_en_produccion_el_simulado_no_se_acepta(pool, api, monkeypatch):
    token = await enlace(pool)
    fid = await factura(pool, "E310000000017")
    await configurar(api)
    inicio = (await iniciar(api, token, fid)).json()
    aprobar = ruta((await pagina_simulada(api, inicio))["aprobar"])
    monkeypatch.setattr(pagos.get_pagos_settings(), "app_env", "production")
    assert (await api.get(aprobar)).headers["location"].endswith("?aviso=firma")
    assert await cobros(pool, fid) == []
    assert (await api.get(f"/portal/publico/{token}")).json()["pago_en_linea"] is None
    r = await api.put("/configuracion/pagos-en-linea", json={"activo": False, "proveedor": "simulado"})
    assert r.status_code == 422
    config = (await api.get("/configuracion/pagos-en-linea")).json()
    assert [p["clave"] for p in config["proveedores"]] == ["azul"]
