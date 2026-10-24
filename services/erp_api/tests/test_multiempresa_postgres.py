"""Multiempresa (docs/MULTIEMPRESA.md) contra un Postgres REAL: dos
empresas en la misma base, aisladas por RLS.

La empresa 1 recibe la semilla de Operación (ventas, cobros, compras,
bandeja, inventario, kardex, 606/607/608, papel, auditoría DGII) más bancos,
contabilidad, nómina, POS, portal, pagos en línea y contingencia; la 2 se
crea con `empresas.crear_empresa`. Luego:

- cada GET de erp-api, facturas-api, inventario-api y formato-606 (las rutas
  de la aplicación, recorridas solas) pedido como la empresa 2 no devuelve
  NINGÚN dato de la 1 (ni sus ids, e-NCF, nombres o RNC de clientes);
- lo que crea la 2 con las mismas claves (RNC de cliente, SKU, secuencia,
  e-NCF, período de nómina) no choca con la 1 y la 1 no lo ve;
- la membresía decide el acceso (403) en /auth/verificar y en una API con
  REQUIRE_AUTH, y la conexión no se entrega para otra empresa sin RLS.

Se salta sin `OPERACION_TEST_DATABASE_URL` (como test_operacion_postgres).
"""
import json
import os
import time
import uuid
from pathlib import Path

import asyncpg
import httpx
import jwt
import pytest
from fastapi import FastAPI, Request
from fastapi.routing import APIRoute

from libs.ecf_common import empresa as emp
from libs.ecf_common.auth.sesion_supabase import instalar_verificacion_sesion
from libs.ecf_common.db import crear_pool
from services.erp_api.app import configuracion_repository, empresas
from services.erp_api.app import repository as ventas_repo
from services.erp_api.app.migraciones import aplicar_migraciones as migrar_erp
from services.erp_api.app.soluciones_calculos import hash_token
from services.erp_api.tests import operacion_semilla as s
from services.facturas_api.app import repository as facturas_repo
from services.facturas_api.app import secuencias_encf
from services.facturas_api.app.migraciones import aplicar_migraciones as migrar_facturas
from services.inventario_api.app import repository as inventario_repo
from services.inventario_api.app.migraciones import aplicar_migraciones as migrar_inventario

DSN = os.environ.get("OPERACION_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="sin OPERACION_TEST_DATABASE_URL (Postgres real)")

REPO = Path(__file__).resolve().parents[3]
SECRETO = "secreto-jwt-de-prueba-de-al-menos-32-caracteres"
ANA = "aaaaaaaa-0000-0000-0000-000000000001"  # solo empresa 1 (implícita, sin membresías)
BETO = "bbbbbbbb-0000-0000-0000-000000000002"  # solo empresa 2
CARLA = "cccccccc-0000-0000-0000-000000000003"  # las dos

# Lo que la empresa 1 tiene y la 2 nunca debe ver.
BANCO = "Banco Aislado de la Uno"
EMPLEADO = "Empleada Exclusiva Uno"
ACTIVO = "Camión de la empresa uno"
VENDEDOR = "Vendedora de la Uno"
TOKEN_PORTAL = "t" * 43
LOTE = "A-9001"


def _marcadores() -> list[str]:
    ids = [s.F1, s.F2, s.F3, s.F4, s.F5, s.F6, s.F7, s.F8, s.F9, s.F10, s.P1, s.P2, s.P3, s.P4,
           s.R1, s.R2, s.R3, s.R4, s.L1, s.ECF_PEND, s.ECF_FALLIDA, s.ECF_ATASCADA, s.ECF_ENTREGA,
           s.COT_POR_VENCER, s.COT_LEJOS]
    # (Sin los primeros de cada serie: la empresa 2 muestra su propio
    # "siguiente", que también es E310000000001.)
    encfs = [f[2] for f in s.FACTURAS if not f[2].endswith("0000000001")]
    return [str(i) for i in ids] + encfs + [
        "Supermercado La Cosecha", "Hotel Brisas", "Constructora Hispaniola", "Cemento gris", "Varilla corrugada",
        "Sucursal Santiago", "Caribe Verde", BANCO, EMPLEADO, ACTIVO, VENDEDOR, LOTE, s.COSECHA[0], s.HOTEL[0],
    ]


async def _sembrar_extra(conn) -> None:
    """Lo que la semilla de Operación no trae: bancos, contabilidad,
    nómina, POS, portal, pagos en línea, contingencia y configuración."""
    cuenta = await conn.fetchval(
        "INSERT INTO bancos.cuentas (nombre, banco, tipo, fecha_saldo_inicial, saldo_inicial) "
        "VALUES ($1, 'Popular', 'corriente', '2026-10-01', 1000) RETURNING id",
        BANCO,
    )
    await conn.execute(
        "INSERT INTO bancos.movimientos (cuenta_id, fecha, tipo, monto, descripcion) "
        "VALUES ($1, '2026-10-02', 'deposito', 500, $2)",
        cuenta,
        BANCO,
    )
    await conn.execute(
        "INSERT INTO nomina.empleados (cedula, nombre, fecha_ingreso, salario_mensual) "
        "VALUES ('00100000001', $1, '2026-01-01', 50000)",
        EMPLEADO,
    )
    await conn.execute(
        "INSERT INTO contabilidad.activos_fijos (descripcion, categoria, fecha_adquisicion, costo, depreciar_desde) "
        "VALUES ($1, 1, '2026-01-01', 900000, '202601')",
        ACTIVO,
    )
    await conn.execute("INSERT INTO configuracion.vendedores (nombre) VALUES ($1)", VENDEDOR)
    await conn.execute(
        "INSERT INTO configuracion.secuencias (ambiente, tipo_ecf, prefijo, desde, hasta, proximo) "
        "VALUES ('test', 31, 'E31', 1, 1000, 1)"
    )
    await conn.execute("INSERT INTO soluciones.pos_sesiones (caja, cajero, fondo_inicial) VALUES ('Caja 1', $1, 1000)",
                       VENDEDOR)
    cliente = await conn.fetchval("SELECT id FROM ventas.clientes WHERE rnc = $1", s.COSECHA[0])
    enlace = await conn.fetchval(
        "INSERT INTO soluciones.portal_enlaces (cliente_id, token_hash, expira_en) "
        "VALUES ($1, $2, now() + interval '30 days') RETURNING id",
        cliente,
        hash_token(TOKEN_PORTAL),
    )
    await conn.execute(
        "INSERT INTO soluciones.pagos_en_linea (referencia, orden, proveedor, ambiente, enlace_id, factura_id, encf, "
        "cliente_nombre, monto, expira_en) VALUES ('ref-uno-' || $1, 'ORD-UNO', 'simulado', 'pruebas', $2, $3, "
        "'E310000000001', $4, 100, now() + interval '1 hour')",
        "x" * 30,
        enlace,
        s.F1,
        s.COSECHA[1],
    )
    await conn.execute(
        "INSERT INTO facturacion.lotes_contingencia (id, documentos, estado) VALUES ($1, 1, 'completado')", LOTE
    )


@pytest.fixture
async def base(monkeypatch):
    for modulo in (ventas_repo, configuracion_repository):
        if hasattr(modulo, "hoy"):
            monkeypatch.setattr(modulo, "hoy", lambda: s.HOY)
    emp.invalidar_cache()
    nombre = f"multiempresa_{uuid.uuid4().hex[:10]}"
    admin = await asyncpg.connect(DSN)
    await admin.execute(f"CREATE DATABASE {nombre} TEMPLATE template0 ENCODING 'UTF8'")
    await admin.close()
    dsn = DSN.rsplit("/", 1)[0] + f"/{nombre}"
    conn = await asyncpg.connect(dsn)
    for archivo in sorted((REPO / "db" / "init").glob("*.sql")):
        await conn.execute(archivo.read_text(encoding="utf-8"))
    pool = await crear_pool(dsn)
    # Los arranques de los servicios (idempotentes sobre db/init).
    await migrar_facturas(pool)
    await migrar_inventario(pool)
    await migrar_erp(pool, ambiente_defecto="test")
    await s.sembrar(conn)
    await _sembrar_extra(conn)
    await conn.close()
    nueva = await empresas.crear_empresa(
        pool, empresas.NuevaEmpresa(rnc="1-01-00000-2", razon_social="Segunda Empresa, SRL"), None
    )
    assert nueva["id"] == 2
    yield pool, dsn
    await pool.close()
    emp.invalidar_cache()
    admin = await asyncpg.connect(DSN)
    await admin.execute(f"DROP DATABASE IF EXISTS {nombre} WITH (FORCE)")
    await admin.close()


# --- recorrido de todos los GET -------------------------------------------------------------

_VALORES = {
    "periodo": "202610", "desde": "2026-09-01", "hasta": "2026-10-31", "anio": "2026", "mes": "10",
    "rnc": s.COSECHA[0], "fecha_salida": "2026-10-24", "cantidad": "1", "producto_id": str(s.P1),
    "cuenta_id": str(uuid.UUID(int=0)), "persona": "juridica", "tipo_bienes_servicios": "02", "monto": "1000",
}
_PATH = {
    "cliente_id": None, "cotizacion_id": str(s.COT_POR_VENCER), "factura_id": str(s.F1), "producto_id": str(s.P1),
    "registro_id": str(s.R1), "ecf_id": str(s.ECF_PEND), "tipo_ecf": "31", "rnc": s.COSECHA[0],
    "lote_id": str(s.L1), "token": TOKEN_PORTAL,
}
# Sin datos de empresas (o con llamadas a otros servicios que aquí no hay).
_OMITIR = {"/health", "/estado-servicios", "/empresas/consulta-rnc/{rnc}", "/auth/verificar",
           "/facturas/{factura_id}/representacion-impresa"}


# Rutas con un parámetro de varios valores: una visita por valor.
_VARIOS = {"formulario": ["it1", "ir17", "ir2"]}

# Las que el recorrido tiene que visitar sí o sí (ventas, cobros, compras,
# bandeja, inventario, kardex, bancos, contabilidad, impuestos, nómina, POS,
# portal, pagos, contingencia, sincronización, reportes, tareas, 606).
OBLIGATORIAS = {
    "/clientes", "/cotizaciones", "/cobros", "/cuentas-por-cobrar", "/documentos-venta", "/compras/comprobantes",
    "/compras/cuentas-por-pagar", "/compras/bandeja", "/compras/bandeja/{ecf_id}", "/productos", "/kardex",
    "/movimientos", "/valuacion", "/almacenes", "/lotes", "/ajustes", "/transferencias", "/bancos/cuentas",
    "/bancos/cuentas/{cuenta_id}/movimientos", "/contabilidad/asientos", "/contabilidad/balanza",
    "/contabilidad/activos-fijos", "/impuestos/607", "/impuestos/608", "/impuestos/precalculo/it1",
    "/impuestos/precalculo/ir2", "/impuestos/declaraciones", "/nomina/empleados", "/nomina/periodos",
    "/pos/sesiones", "/portal/enlaces", "/pagos-en-linea", "/contingencia", "/contingencia/lotes",
    "/sincronizacion/historial", "/sincronizacion/pendientes", "/sincronizacion/facturas-papel", "/reportes/ventas",
    "/reportes/compras", "/reportes/antiguedad-cxc", "/reportes/inventario-valorizado", "/tareas", "/tareas/resumen",
    "/facturas", "/facturas/{factura_id}", "/registros", "/periodos", "/configuracion/empresa",
    "/configuracion/secuencias", "/configuracion/vendedores", "/ventas-auto/envios",
}


def _rutas(app, ids: dict[str, str]):
    for ruta in app.routes:
        if not isinstance(ruta, APIRoute) or "GET" not in ruta.methods or ruta.path in _OMITIR:
            continue
        if ruta.path.startswith("/portal/publico"):
            continue  # se prueba aparte: entra a la empresa del enlace
        params = {p.name: _VALORES[p.name] for p in ruta.dependant.query_params if p.required and p.name in _VALORES}
        if any(p.required and p.name not in _VALORES for p in ruta.dependant.query_params):
            continue
        caminos = [(ruta.path, ruta.path)]
        for p in ruta.dependant.path_params:
            valores = _VARIOS.get(p.name) or [ids.get(p.name) or _PATH.get(p.name)]
            if valores == [None]:
                caminos = []
                break
            marca = "{" + p.name + "}"
            caminos = [
                (plantilla.replace(marca, v) if p.name in _VARIOS else plantilla, camino.replace(marca, v))
                for plantilla, camino in caminos
                for v in valores
            ]
        for plantilla, camino in caminos:
            yield plantilla, camino, params


async def _recorrer(app, pool, empresa_id: int, ids: dict[str, str]) -> dict[str, tuple[int, str, str]]:
    anterior = app.state.db_pool
    app.state.db_pool = pool
    respuestas = {}
    try:
        transporte = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transporte, base_url="http://kontara") as cliente:
            for plantilla, path, params in _rutas(app, ids):
                r = await cliente.get(path, params=params, headers={"X-Kontara-Empresa": str(empresa_id)})
                respuestas[plantilla] = (r.status_code, r.text, str(r.request.url))
    finally:
        app.state.db_pool = anterior
    return respuestas


async def test_ningun_get_devuelve_datos_de_otra_empresa(base):
    pool, _ = base
    from services.erp_api.app import main as erp
    from services.facturas_api.app import main as facturas
    from services.formato_606.app import main as f606
    from services.inventario_api.app import main as inventario

    async with pool.acquire() as conn:  # empresa 1 (predeterminada)
        ids = {
            "cliente_id": str(await conn.fetchval("SELECT id FROM ventas.clientes LIMIT 1")),
            "cuenta_id": str(await conn.fetchval("SELECT id FROM bancos.cuentas LIMIT 1")),
            "empleado_id": str(await conn.fetchval("SELECT id FROM nomina.empleados LIMIT 1")),
            "activo_id": str(await conn.fetchval("SELECT id FROM contabilidad.activos_fijos LIMIT 1")),
            "sesion_id": str(await conn.fetchval("SELECT id FROM soluciones.pos_sesiones LIMIT 1")),
            "almacen_id": str(await conn.fetchval("SELECT id FROM inventario.almacenes WHERE NOT es_principal LIMIT 1")),
            "pago_id": str(await conn.fetchval("SELECT id FROM soluciones.pagos_en_linea LIMIT 1")),
            "proveedor_id": str(uuid.UUID(int=0)),
        }
    marcadores = _marcadores()
    vistos_en_1: set[str] = set()
    visitadas: set[str] = set()
    revisadas = 0
    for app in (erp.app, facturas.app, inventario.app, f606.app):
        en_1 = await _recorrer(app, pool, 1, ids)
        en_2 = await _recorrer(app, pool, 2, ids)
        visitadas.update(en_2)
        for ruta, (status, cuerpo, url) in en_2.items():
            revisadas += 1
            assert status < 500, (ruta, status, cuerpo[:300])
            # (Lo que se pidió por la URL puede volver en un 404/422: no es una fuga.)
            filtrados = [m for m in marcadores if m in cuerpo and m not in url]
            assert not filtrados, f"{ruta} devuelve a la empresa 2 datos de la 1: {filtrados}"
        for _status, cuerpo, _url in en_1.values():
            vistos_en_1.update(m for m in marcadores if m in cuerpo)
    assert revisadas > 120
    faltan = OBLIGATORIAS - visitadas
    assert not faltan, f"el recorrido no visitó {sorted(faltan)}"
    # La empresa 1 sí ve lo suyo por esas mismas rutas (el recorrido no es vacío).
    assert {BANCO, EMPLEADO, ACTIVO, VENDEDOR, LOTE, "Cemento gris", str(s.F1), "E310000000002"} <= vistos_en_1


async def test_las_claves_naturales_son_por_empresa(base):
    pool, _ = base
    with emp.en_empresa(2):
        # Mismo RNC de cliente, mismo SKU, misma secuencia y mismo e-NCF que en la 1.
        cliente = await ventas_repo.crear_cliente(
            pool, tipo="cliente", tipo_identificacion="rnc", rnc=s.COSECHA[0], nombre="Cosecha en la Dos",
            correo=None, telefono=None, direccion=None, condicion_pago_dias=0, limite_credito=None, notas=None,
            activo=True,
        )
        producto = await inventario_repo.crear_producto(
            pool, sku="CEM-425", nombre="Cemento de la Dos", descripcion=None, unidad_medida="UND",
            precio_venta=100, stock_inicial=5, costo_unitario_inicial=10,
        )
        await configuracion_repository.guardar_secuencia(
            pool, 31, desde=1, hasta=100, proximo=None, fecha_vencimiento=None, activa=True, hoy=s.HOY, ambiente="test"
        )
        factura = await secuencias_encf.crear_factura(
            pool, tipo_ecf=31, ambiente="test", rnc_emisor="101000002", encf=None, asignar_encf=True,
            datos={"Encabezado": {"IdDoc": {"TipoeCF": "31"}}}, dia=s.HOY,
        )
        assert factura["encf"] == "E310000000001"  # su propia numeración
        assert [c["nombre"] for c in await ventas_repo.listar_clientes(pool)] == ["Cosecha en la Dos"]
        assert [p["sku"] for p in await inventario_repo.listar_productos(pool)] == ["CEM-425"]
        # El almacén principal y el catálogo de cuentas de la empresa nueva.
        async with pool.acquire() as conn:
            assert await conn.fetchval("SELECT count(*) FROM inventario.almacenes WHERE es_principal") == 1
            assert await conn.fetchval("SELECT count(*) FROM contabilidad.cuentas") > 50
            assert await conn.fetchval("SELECT count(*) FROM facturacion.facturas") == 1
    # La 1 no ve nada de eso y su siguiente e-NCF sigue su propia secuencia.
    async with pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM ventas.clientes WHERE nombre = 'Cosecha en la Dos'") == 0
        assert await conn.fetchval("SELECT nombre FROM inventario.productos WHERE sku = 'CEM-425'") == "Cemento gris 42.5 kg"
        assert await conn.fetchval("SELECT count(*) FROM facturacion.facturas WHERE id = $1", uuid.UUID(factura["id"])) == 0
    with pytest.raises(facturas_repo.FacturaNoEncontradaError):
        await facturas_repo.obtener_factura(pool, uuid.UUID(factura["id"]))
    with emp.todas_las_empresas():
        async with pool.acquire() as conn:
            assert await conn.fetchval(
                "SELECT count(DISTINCT empresa_id) FROM ventas.clientes WHERE rnc = $1", s.COSECHA[0]
            ) == 2
            # Todo lo que existía quedó en la 1.
            assert await conn.fetchval(
                "SELECT count(*) FROM facturacion.facturas WHERE empresa_id = 1"
            ) == len(s.FACTURAS)
    assert cliente["rnc"] == s.COSECHA[0] and producto["sku"] == "CEM-425"


async def test_insertar_en_otra_empresa_no_se_puede(base):
    """La política también vale al escribir: una fila con empresa_id ajeno
    (WITH CHECK) ni un UPDATE de filas ajenas (no las ve)."""
    pool, _ = base
    with emp.en_empresa(2):
        async with pool.acquire() as conn:
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.execute("INSERT INTO ventas.clientes (nombre, empresa_id) VALUES ('Intruso', 1)")
            assert await conn.execute("UPDATE ventas.clientes SET nombre = 'x'") == "UPDATE 0"
            assert await conn.execute("DELETE FROM inventario.productos") == "DELETE 0"


async def test_portal_publico_entra_a_la_empresa_del_enlace(base):
    pool, _ = base
    from services.erp_api.app import main as erp

    erp.app.state.db_pool = pool
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=erp.app), base_url="http://k") as cliente:
            # Aunque el navegador pida la empresa 2, el enlace es de la 1.
            r = await cliente.get(f"/portal/publico/{TOKEN_PORTAL}", headers={"X-Kontara-Empresa": "2"})
            assert r.status_code == 200 and r.json()["cliente_nombre"] == s.COSECHA[1]
            assert len(r.json()["facturas"]) > 0
            r = await cliente.get("/portal/publico/pagos/ref-uno-" + "x" * 30)
            assert r.status_code == 200 and r.json()["encf"] == "E310000000001"
    finally:
        erp.app.state.db_pool = None


def _token(sub: str, admin: bool = False) -> str:
    claims = {"sub": sub, "email": f"{sub[:4]}@kontara.do", "aud": "authenticated", "role": "authenticated",
              "exp": int(time.time()) + 3600, "app_metadata": {"kontara": True, "kontara_admin": admin}}
    return jwt.encode(claims, SECRETO, algorithm="HS256")


async def _membresias(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO configuracion.empresa_usuarios (empresa_id, usuario_id, rol) VALUES "
            "(2, $1, 'usuario'), (1, $2, 'usuario'), (2, $2, 'admin')",
            BETO,
            CARLA,
        )
    emp.invalidar_cache()


async def test_auth_verificar_exige_membresia(base, monkeypatch):
    pool, _ = base
    await _membresias(pool)
    from services.erp_api.app import main as erp
    from services.erp_api.app.config import get_settings

    monkeypatch.setattr(get_settings(), "supabase_jwt_secret", SECRETO)
    erp.app.state.db_pool = pool
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=erp.app), base_url="http://k") as cliente:
            async def verificar(sub, empresa=None):
                cabeceras = {"Authorization": f"Bearer {_token(sub)}"}
                if empresa:
                    cabeceras["X-Kontara-Empresa"] = str(empresa)
                return await cliente.get("/auth/verificar", headers=cabeceras)

            # Ana (de antes, sin membresías): solo la 1.
            r = await verificar(ANA)
            assert r.status_code == 204 and r.headers["x-kontara-empresa"] == "1"
            r = await verificar(ANA, 2)
            assert r.status_code == 403 and r.headers["x-kontara-motivo"] == "sin_empresa"
            # Beto: solo la 2 (y es la que se le da si no pide ninguna).
            assert (await verificar(BETO)).headers["x-kontara-empresa"] == "2"
            assert (await verificar(BETO, 1)).status_code == 403
            # Carla: las dos.
            assert (await verificar(CARLA, 1)).status_code == 204
            assert (await verificar(CARLA, 2)).headers["x-kontara-empresa"] == "2"
            # Una empresa que no existe, o un valor que no es un id.
            assert (await verificar(CARLA, 99)).status_code == 403
            assert (await verificar(CARLA, "1;DROP")).status_code == 422
    finally:
        erp.app.state.db_pool = None


async def test_api_con_require_auth_no_confia_en_la_cabecera(base):
    """facturas-api / inventario-api con REQUIRE_AUTH: la empresa que pide
    el cliente se valida contra la membresía del usuario del token; las
    llamadas internas (X-Kontara-Interno) la pasan explícita."""
    pool, _ = base
    await _membresias(pool)

    class _Settings:
        require_auth = True
        supabase_jwt_secret = SECRETO
        kontara_admin_emails = ""
        kontara_token_interno = "token-interno-de-prueba-de-32-caracteres-o-mas"

    app = FastAPI()
    app.state.db_pool = pool
    emp.instalar_empresa(app, _Settings)
    instalar_verificacion_sesion(app, _Settings)

    @app.get("/quien")
    async def quien(request: Request):
        async with request.app.state.db_pool.acquire() as conn:
            clientes = await conn.fetchval("SELECT count(*) FROM ventas.clientes")
        return {"empresa": emp.empresa_actual(), "admin": request.state.usuario.admin if request.state.usuario else None,
                "clientes": clientes}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://k") as cliente:
        def pedir(sub=None, empresa=None, interno=False):
            cabeceras = {}
            if sub:
                cabeceras["Authorization"] = f"Bearer {_token(sub)}"
            if empresa:
                cabeceras["X-Kontara-Empresa"] = str(empresa)
            if interno:
                cabeceras["X-Kontara-Interno"] = _Settings.kontara_token_interno
            return cliente.get("/quien", headers=cabeceras)

        r = await pedir(ANA, 2)
        assert r.status_code == 403 and r.json()["codigo"] == "sin_empresa"
        r = await pedir(BETO)
        assert r.json() == {"empresa": 2, "admin": False, "clientes": 0}
        r = await pedir(CARLA, 2)
        assert r.json()["empresa"] == 2 and r.json()["admin"] is True  # admin de la 2...
        r = await pedir(CARLA, 1)
        assert r.json()["admin"] is False and r.json()["clientes"] > 0  # ...pero no de la 1
        assert (await pedir(interno=True, empresa=2)).json()["empresa"] == 2
        assert (await pedir(empresa=2)).status_code == 401  # sin sesión ni token interno


async def test_sin_aislamiento_no_se_entrega_otra_empresa(base):
    """Si la conexión salta RLS (superusuario sin poder cambiar a
    kontara_app), la empresa 2 falla cerrada; la 1 sigue funcionando."""
    pool, dsn = base
    conn = await asyncpg.connect(dsn)
    preparar = emp.crear_preparador(dsn)
    try:
        with emp.en_empresa(2), emp.modo_sistema():
            await preparar(conn)  # modo sistema (migraciones): se permite
        # Simula una base sin el rol: la consulta de preparación dice "no aislado".
        original = emp._SQL_PREPARAR
        emp._SQL_PREPARAR = original.replace("rolname = 'kontara_app'", "rolname = 'no_existe_este_rol'")
        assert emp._SQL_PREPARAR != original
        try:
            with emp.en_empresa(2), pytest.raises(emp.AislamientoNoDisponibleError):
                await preparar(conn)
            with emp.en_empresa(1):
                await preparar(conn)
        finally:
            emp._SQL_PREPARAR = original
    finally:
        await conn.close()
    assert await emp.aislamiento_disponible(pool) is True
    # El pooler de Supabase en modo transacción (6543) tampoco conserva la empresa.
    with emp.en_empresa(2), pytest.raises(emp.AislamientoNoDisponibleError):
        await emp.crear_preparador("postgresql://u@h:6543/db")(None)


async def test_mis_empresas_y_seleccion(base):
    pool, _ = base
    await _membresias(pool)
    usuario = emp_usuario(CARLA)
    mias = await empresas.mis_empresas(pool, usuario)
    assert [(e["id"], e["rol"]) for e in mias] == [(1, "usuario"), (2, "admin")]
    assert [e["id"] for e in await empresas.mis_empresas(pool, emp_usuario(ANA))] == [1]
    assert await emp.empresa_preferida(pool, CARLA) == 1
    await empresas.recordar_seleccion(pool, CARLA, 2)
    assert await emp.empresa_preferida(pool, CARLA) == 2
    # Agregar una empresa: el creador queda como admin y conserva la 1 (implícita).
    nueva = await empresas.crear_empresa(
        pool, empresas.NuevaEmpresa(rnc="131000003", razon_social="Tercera, SRL"), emp_usuario(ANA, admin=True)
    )
    assert [(e["id"], e["rol"]) for e in await empresas.mis_empresas(pool, emp_usuario(ANA, admin=True))] == [
        (1, "admin"), (nueva["id"], "admin")
    ]
    with pytest.raises(configuracion_repository.RncEnUsoError):
        await empresas.crear_empresa(pool, empresas.NuevaEmpresa(rnc="131000003", razon_social="Otra"), None)
    # Planes: el total de la instalación y el desglose de las empresas del usuario.
    from services.erp_api.app import soluciones_repository

    consumo = await soluciones_repository.consumo(pool, empresas={1: "Uno", 2: "Dos"})
    assert consumo["ecf_emitidos"] == sum(e["ecf_emitidos"] for e in consumo["ecf_por_empresa"])
    assert json.dumps(consumo, default=str)


def emp_usuario(sub: str, admin: bool = False):
    from libs.ecf_common.auth.sesion_supabase import UsuarioSesion

    return UsuarioSesion(id=sub, correo=f"{sub[:4]}@kontara.do", rol="authenticated", expira=0, admin=admin,
                         admin_global=admin)


async def test_tareas_de_fondo_dan_una_vuelta_por_empresa(base, monkeypatch):
    pool, _ = base
    from services.facturas_api.app import estado_dgii

    vistas = []

    async def una_vuelta(p, settings):
        vistas.append(emp.empresa_actual())
        raise RuntimeError("una empresa que falla no frena a las demás")

    monkeypatch.setattr(estado_dgii, "una_vuelta", una_vuelta)

    class _S:
        sincronizar_estado_cada_segundos = 0

    import asyncio

    tarea = asyncio.create_task(estado_dgii.bucle_sincronizacion(pool, _S))
    for _ in range(50):
        await asyncio.sleep(0.02)
        if len(vistas) >= 2:
            break
    tarea.cancel()
    assert vistas[:2] == [1, 2]
