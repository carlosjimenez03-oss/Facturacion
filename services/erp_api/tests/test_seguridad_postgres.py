"""Correcciones de seguridad de la fase 6 contra un Postgres REAL:

- e-NCF asignado por facturas_api con la secuencia bloqueada (sin
  duplicados aunque dos facturas se creen a la vez; 409 si la secuencia
  está agotada, vencida o desactivada) y el índice único (rnc_emisor, encf)
  que solo se crea si no hay duplicados previos;
- el envío automático por correo reclama cada factura antes de mandarla y
  reintenta los errores con espera creciente hasta un máximo;
- el CHECK de `configuracion.envios.estado` de bases anteriores se actualiza;
- eliminar un vendedor sin la columna facturacion.facturas.vendedor_id;
- el enlace del portal queda fijado al RNC del cliente al crearlo;
- `RevocacionSesiones` contra un esquema `auth` como el de Supabase.

Se salta si no hay `COMPRAS_TEST_DATABASE_URL` (igual que
test_configuracion_postgres.py): cada prueba crea una base nueva, carga
`db/init/*.sql` y aplica las migraciones de erp-api y facturas_api.
"""
import asyncio
import json
import os
import uuid
from datetime import date, timedelta
from pathlib import Path

import asyncpg
import pytest

from libs.ecf_common.auth.sesion_supabase import RevocacionSesiones, UsuarioSesion
from services.erp_api.app import configuracion_envio as envio
from services.erp_api.app import configuracion_repository as repo
from services.erp_api.app import soluciones_repository as soluciones
from services.erp_api.app.migraciones import aplicar_migraciones
from services.erp_api.app.migraciones_configuracion import DDL_CONFIGURACION
from services.facturas_api.app import repository as facturas_repo
from services.facturas_api.app import secuencias_encf
from services.facturas_api.app.migraciones import aplicar_migraciones as migrar_facturas

DSN = os.environ.get("COMPRAS_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="sin COMPRAS_TEST_DATABASE_URL (Postgres real)")

REPO = Path(__file__).resolve().parents[3]
HOY = date(2026, 10, 24)
RNC = "131897542"


@pytest.fixture
async def pool():
    nombre = f"seguridad_{uuid.uuid4().hex[:10]}"
    admin = await asyncpg.connect(DSN)
    await admin.execute(f"CREATE DATABASE {nombre} TEMPLATE template0 ENCODING 'UTF8'")
    await admin.close()
    dsn = DSN.rsplit("/", 1)[0] + f"/{nombre}"
    conn = await asyncpg.connect(dsn)
    for archivo in sorted((REPO / "db" / "init").glob("*.sql")):
        await conn.execute(archivo.read_text(encoding="utf-8"))
    await conn.close()
    p = await asyncpg.create_pool(dsn, min_size=1, max_size=6)
    for _ in range(2):  # idempotentes
        await aplicar_migraciones(p)
        await migrar_facturas(p)
    yield p
    await p.close()
    admin = await asyncpg.connect(DSN)
    await admin.execute(f"DROP DATABASE IF EXISTS {nombre} WITH (FORCE)")
    await admin.close()


def _datos(tipo: int = 31, encf: str = "") -> dict:
    return {"Encabezado": {"Version": "1.0", "IdDoc": {"TipoeCF": str(tipo), "eNCF": encf, "TipoPago": "1"},
                           "Emisor": {"RNCEmisor": RNC}}}


async def _crear(pool, tipo=31, encf=None, asignar=False, dia=HOY, ambiente="test"):
    return await secuencias_encf.crear_factura(
        pool, tipo_ecf=tipo, ambiente=ambiente, rnc_emisor=RNC, encf=encf, asignar_encf=asignar,
        datos=_datos(tipo, encf or ""), dia=dia,
    )


async def _secuencia(pool, tipo=31, desde=1, hasta=5000, proximo=None, vence=date(2027, 12, 31), activa=True,
                     ambiente="test"):
    await repo.guardar_secuencia(pool, tipo, desde=desde, hasta=hasta, proximo=proximo,
                                 fecha_vencimiento=vence, activa=activa, hoy=HOY, ambiente=ambiente)


# --- e-NCF asignado por el servidor ---------------------------------------------------------

async def test_asigna_el_siguiente_de_la_secuencia_y_lo_pone_en_los_datos(pool):
    await _secuencia(pool, desde=1, hasta=5000, proximo=120)
    f = await _crear(pool, asignar=True)
    assert f["encf"] == "E310000000120"
    assert f["datos"]["Encabezado"]["IdDoc"]["eNCF"] == "E310000000120"
    assert list(f["datos"]["Encabezado"]["IdDoc"])[:2] == ["TipoeCF", "eNCF"]  # orden del XSD
    # encf vacío también asigna; y coincide con lo que muestra Configuración.
    assert (await repo.obtener_secuencia(pool, 31, HOY, "test"))["siguiente_encf"] == "E310000000121"
    assert (await _crear(pool, encf="  "))["encf"] == "E310000000121"


async def test_facturas_simultaneas_nunca_repiten_e_ncf(pool):
    await _secuencia(pool, desde=1, hasta=5000)
    creadas = await asyncio.gather(*[_crear(pool, asignar=True) for _ in range(12)])
    encfs = [f["encf"] for f in creadas]
    assert len(set(encfs)) == 12
    assert sorted(encfs) == [f"E31{n:010d}" for n in range(1, 13)]


@pytest.mark.parametrize(
    ("secuencia", "mensaje"),
    [
        ({"hasta": 2}, "agotada"),
        ({"vence": date(2026, 10, 23)}, "venció el 23/10/2026"),
        ({"activa": False}, "desactivada"),
    ],
    ids=["agotada", "vencida", "desactivada"],
)
async def test_secuencia_que_no_deja_emitir_responde_con_el_motivo(pool, secuencia, mensaje):
    await _secuencia(pool, **{"desde": 1, "hasta": 5000, **secuencia})
    if "hasta" in secuencia:
        await _crear(pool, asignar=True)
        await _crear(pool, asignar=True)
    with pytest.raises(secuencias_encf.EncfNoDisponibleError, match=mensaje):
        await _crear(pool, asignar=True)


async def test_sin_secuencia_registrada(pool):
    with pytest.raises(secuencias_encf.EncfNoDisponibleError, match="No hay una secuencia E32"):
        await _crear(pool, tipo=32, asignar=True)
    # Sin pedir asignación: borrador sin e-NCF, como antes.
    assert (await _crear(pool, tipo=32))["encf"] is None
    # Y el que manda el cliente se acepta.
    assert (await _crear(pool, tipo=32, encf="e320000000005"))["encf"] == "E320000000005"


async def test_e_ncf_del_cliente_duplicado_se_rechaza(pool):
    f = await _crear(pool, encf="E310000000007")
    with pytest.raises(secuencias_encf.EncfNoDisponibleError, match="ya está usado"):
        await _crear(pool, encf="E310000000007")
    # PUT de otro borrador con ese e-NCF: también; el propio sí puede conservarlo.
    otro = await _crear(pool, encf="E310000000008")
    with pytest.raises(secuencias_encf.EncfNoDisponibleError):
        await secuencias_encf.validar_encf_para_actualizar(pool, uuid.UUID(otro["id"]), RNC, "E310000000007", "test")
    await secuencias_encf.validar_encf_para_actualizar(pool, uuid.UUID(f["id"]), RNC, "E310000000007", "test")
    # Con la secuencia: salta los que ya mandó el cliente.
    await _secuencia(pool, desde=1, hasta=5000)
    assert (await _crear(pool, asignar=True))["encf"] == "E310000000009"


async def test_indice_unico_solo_sin_duplicados_previos(pool, caplog):
    for _ in range(2):  # duplicado de antes de esta versión
        await facturas_repo.crear_factura(pool, tipo_ecf=31, ambiente="test", rnc_emisor=RNC,
                                          encf="E310000000001", datos={})
    assert await secuencias_encf.asegurar_indice_encf_unico(pool) is False
    assert "E310000000001 ×2" in caplog.text
    async with pool.acquire() as conn:
        assert await conn.fetchval("SELECT to_regclass('facturacion.facturas_rnc_ambiente_encf_unico_idx')") is None
        await conn.execute(
            "DELETE FROM facturacion.facturas WHERE id = (SELECT id FROM facturacion.facturas LIMIT 1)"
        )
    assert await secuencias_encf.asegurar_indice_encf_unico(pool) is True
    assert await secuencias_encf.asegurar_indice_encf_unico(pool) is True  # idempotente
    with pytest.raises(asyncpg.UniqueViolationError):
        await facturas_repo.crear_factura(pool, tipo_ecf=31, ambiente="test", rnc_emisor=RNC,
                                          encf="E310000000001", datos={})
    # Otro emisor y los borradores sin e-NCF no chocan.
    await facturas_repo.crear_factura(pool, tipo_ecf=31, ambiente="test", rnc_emisor="101000000",
                                      encf="E310000000001", datos={})
    for _ in range(2):
        await facturas_repo.crear_factura(pool, tipo_ecf=31, ambiente="test", rnc_emisor=RNC, encf=None, datos={})


async def test_secuencias_y_e_ncf_por_ambiente(pool):
    """Fase 7: cada ambiente DGII tiene su secuencia y su numeración; el
    mismo e-NCF puede existir en pruebas y en producción."""
    await _secuencia(pool, desde=1, hasta=5000, proximo=40)
    for _ in range(3):
        await _crear(pool, asignar=True)
    # Producción sin secuencia: no hay de dónde asignar (aunque pruebas tenga).
    with pytest.raises(secuencias_encf.EncfNoDisponibleError, match="ambiente Producción"):
        await _crear(pool, asignar=True, ambiente="production")
    await _secuencia(pool, desde=1, hasta=100, ambiente="production")
    prod = await _crear(pool, asignar=True, ambiente="production")
    assert prod["encf"] == "E310000000001" and prod["ambiente"] == "production"
    assert (await _crear(pool, asignar=True))["encf"] == "E310000000043"
    # El e-NCF E310000000040 ya está en pruebas, pero en producción está libre.
    assert (await _crear(pool, encf="E310000000040", ambiente="production"))["encf"] == "E310000000040"
    with pytest.raises(secuencias_encf.EncfNoDisponibleError, match="ya está usado"):
        await _crear(pool, encf="E310000000040")
    # Configuración muestra lo de cada ambiente.
    assert (await repo.obtener_secuencia(pool, 31, HOY, "production"))["siguiente_encf"] == "E310000000041"
    assert (await repo.obtener_secuencia(pool, 31, HOY, "test"))["siguiente_encf"] == "E310000000044"
    assert (await repo.obtener_secuencia(pool, 31, HOY, "qa"))["configurada"] is False
    # Con el índice único nuevo (por ambiente) siguen conviviendo.
    assert await secuencias_encf.asegurar_indice_encf_unico(pool) is True
    async with pool.acquire() as conn:
        assert await conn.fetchval("SELECT to_regclass('facturacion.facturas_rnc_ambiente_encf_unico_idx')")
        assert await conn.fetchval("SELECT to_regclass('facturacion.facturas_rnc_encf_unico_idx')") is None
    with pytest.raises(asyncpg.UniqueViolationError):
        await facturas_repo.crear_factura(pool, tipo_ecf=31, ambiente="production", rnc_emisor=RNC,
                                          encf="E310000000040", datos={})


async def test_migracion_de_secuencias_de_una_base_anterior():
    """Una base de antes de la fase 7 (db/init hasta 017) con secuencias y el
    índice único sin ambiente: las secuencias quedan en el ambiente de la
    instalación (APP_ENV) y la clave pasa a (ambiente, tipo_ecf)."""
    nombre = f"seguridad_{uuid.uuid4().hex[:10]}"
    admin = await asyncpg.connect(DSN)
    await admin.execute(f"CREATE DATABASE {nombre} TEMPLATE template0 ENCODING 'UTF8'")
    await admin.close()
    dsn = DSN.rsplit("/", 1)[0] + f"/{nombre}"
    conn = await asyncpg.connect(dsn)
    try:
        for archivo in sorted((REPO / "db" / "init").glob("*.sql")):
            if archivo.name <= "017_contingencia.sql":
                await conn.execute(archivo.read_text(encoding="utf-8"))
        await conn.execute(
            "INSERT INTO configuracion.secuencias (tipo_ecf, prefijo, desde, hasta, proximo) VALUES (31, 'E31', 1, 9, 1)"
        )
        await conn.execute(
            "CREATE UNIQUE INDEX facturas_rnc_encf_unico_idx ON facturacion.facturas (rnc_emisor, encf) "
            "WHERE encf IS NOT NULL"
        )
        p = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
        try:
            for _ in range(2):
                await aplicar_migraciones(p, ambiente_defecto="production")
                await migrar_facturas(p)
            assert await secuencias_encf.asegurar_indice_encf_unico(p) is True
            filas = await conn.fetch("SELECT ambiente, tipo_ecf FROM configuracion.secuencias")
            assert [(f["ambiente"], f["tipo_ecf"]) for f in filas] == [("production", 31)]
            clave = await conn.fetchval(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'secuencias_pkey'"
            )
            assert clave == "PRIMARY KEY (empresa_id, ambiente, tipo_ecf)"
            assert await conn.fetchval("SELECT to_regclass('facturacion.facturas_rnc_encf_unico_idx')") is None
            with pytest.raises(asyncpg.CheckViolationError):
                await conn.execute(
                    "INSERT INTO configuracion.secuencias (ambiente, tipo_ecf, prefijo, desde, hasta, proximo) "
                    "VALUES ('staging', 31, 'E31', 1, 9, 1)"
                )
        finally:
            await p.close()
    finally:
        await conn.close()
        admin = await asyncpg.connect(DSN)
        await admin.execute(f"DROP DATABASE IF EXISTS {nombre} WITH (FORCE)")
        await admin.close()


async def test_indice_unico_nunca_impide_arrancar():
    class PoolRoto:
        def acquire(self):
            raise OSError("sin base")

    assert await secuencias_encf.asegurar_indice_encf_unico(PoolRoto()) is False


# --- envío automático: reclamo y reintentos ---------------------------------------------------------

async def _aceptada(pool, encf):
    datos = {"Encabezado": {"Comprador": {"CorreoComprador": "cliente@x.do", "RazonSocialComprador": "Cliente"},
                            "Totales": {"MontoTotal": "1180.00"}}}
    f = await facturas_repo.crear_factura(pool, tipo_ecf=31, ambiente="test", rnc_emisor=RNC, encf=encf, datos=datos)
    async with pool.acquire() as conn:
        await conn.execute("UPDATE facturacion.facturas SET estado = 'aceptado' WHERE id = $1", uuid.UUID(f["id"]))
    return uuid.UUID(f["id"])


async def test_reclamo_y_reintentos_del_envio_automatico(pool):
    fid = await _aceptada(pool, "E310000000001")
    assert [f["id"] for f in await envio.facturas_por_enviar(pool)] == [fid]

    assert await envio.reclamar_envio(pool, "correo", fid, "cliente@x.do") == 1
    # Otra pasada (u otra réplica) a la vez: no la obtiene, y ya no está por enviar.
    assert await envio.reclamar_envio(pool, "correo", fid, "cliente@x.do") is None
    assert await envio.facturas_por_enviar(pool) == []

    await envio.terminar_envio(pool, "correo", fid, 1, "SMTP caído")
    async with pool.acquire() as conn:
        fila = await conn.fetchrow("SELECT * FROM configuracion.envios WHERE factura_id = $1", fid)
    assert (fila["estado"], fila["intentos"], fila["error"]) == ("error", 1, "SMTP caído")
    assert fila["proximo_intento"] is not None
    # Todavía no toca reintentar.
    assert await envio.facturas_por_enviar(pool) == []
    assert await envio.reclamar_envio(pool, "correo", fid, "cliente@x.do") is None

    async def vence_espera():
        async with pool.acquire() as conn:
            await conn.execute("UPDATE configuracion.envios SET proximo_intento = now() - interval '1 second'")

    for intento in range(2, envio.ENVIO_MAX_INTENTOS + 1):
        await vence_espera()
        assert [f["id"] for f in await envio.facturas_por_enviar(pool)] == [fid]
        assert await envio.reclamar_envio(pool, "correo", fid, "cliente@x.do") == intento
        await envio.terminar_envio(pool, "correo", fid, intento, "SMTP caído")
    async with pool.acquire() as conn:
        fila = await conn.fetchrow("SELECT * FROM configuracion.envios WHERE factura_id = $1", fid)
    # Agotó los intentos: no se reintenta más (sin tormenta de reintentos).
    assert (fila["intentos"], fila["proximo_intento"]) == (envio.ENVIO_MAX_INTENTOS, None)
    await vence_espera()
    assert await envio.facturas_por_enviar(pool) == []
    assert await envio.reclamar_envio(pool, "correo", fid, "cliente@x.do") is None

    otra = await _aceptada(pool, "E310000000002")
    assert await envio.reclamar_envio(pool, "correo", otra, "cliente@x.do") == 1
    await envio.terminar_envio(pool, "correo", otra, 1)
    assert await envio.reclamar_envio(pool, "correo", otra, "cliente@x.do") is None
    assert await envio.enviados_del_mes(pool) == {"correo": 1, "whatsapp": 0}


async def test_check_de_estado_de_bases_anteriores_se_actualiza(pool):
    async with pool.acquire() as conn:
        await conn.execute("ALTER TABLE configuracion.envios DROP CONSTRAINT envios_estado_check")
        await conn.execute(
            "ALTER TABLE configuracion.envios ADD CONSTRAINT envios_estado_check CHECK (estado IN ('enviado', 'error'))"
        )
        await conn.execute(DDL_CONFIGURACION)
        definicion = await conn.fetchval(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'envios_estado_check'"
        )
    assert "pendiente" in definicion
    fid = await _aceptada(pool, "E310000000003")
    assert await envio.reclamar_envio(pool, "correo", fid, "c@x.do") == 1


async def test_pasada_con_lock_tomado_no_hace_nada(pool, monkeypatch):
    await envio.guardar_preferencia(pool, "correo", {"host": "smtp.x.do", "remitente": "f@x.do", "envio_automatico": True})
    await _aceptada(pool, "E310000000004")
    async with pool.acquire() as otra:
        assert await otra.fetchval("SELECT pg_try_advisory_lock($1)", envio._LOCK_ENVIO_AUTOMATICO)
        from services.erp_api.app.config import get_settings

        assert await envio.enviar_facturas_aceptadas(pool, get_settings(), "Caribe") == 0
        await otra.execute("SELECT pg_advisory_unlock($1)", envio._LOCK_ENVIO_AUTOMATICO)
    async with pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM configuracion.envios") == 0


# --- vendedores ---------------------------------------------------------

async def test_eliminar_vendedor_sin_columna_vendedor_id(pool):
    v = await repo.crear_vendedor(pool, nombre="Ana", correo=None, comision_pct=0, activo=True)
    async with pool.acquire() as conn:
        await conn.execute("ALTER TABLE facturacion.facturas DROP COLUMN vendedor_id")
    await repo.eliminar_vendedor(pool, v["id"])  # sin 500
    assert await repo.listar_vendedores(pool) == []
    with pytest.raises(repo.VendedorNoEncontradoError):
        await repo.eliminar_vendedor(pool, v["id"])


async def test_eliminar_vendedor_sin_tabla_de_facturas(pool):
    v = await repo.crear_vendedor(pool, nombre="Ana", correo=None, comision_pct=0, activo=True)
    async with pool.acquire() as conn:
        await conn.execute("DROP SCHEMA facturacion CASCADE")
    await repo.eliminar_vendedor(pool, v["id"])
    assert await repo.listar_vendedores(pool) == []


# --- portal: enlace fijado al RNC ---------------------------------------------------------

async def test_enlace_del_portal_queda_fijado_al_rnc_de_cuando_se_creo(pool):
    async with pool.acquire() as conn:
        cliente = await conn.fetchval(
            "INSERT INTO ventas.clientes (nombre, rnc) VALUES ('Cliente Uno, SRL', '131234567') RETURNING id"
        )
        for encf, rnc in (("E310000000020", "131234567"), ("E310000000021", "130000001")):
            datos = {"Encabezado": {"IdDoc": {"TipoeCF": "31", "eNCF": encf, "TipoPago": "1"},
                                    "Emisor": {"FechaEmision": HOY.strftime("%d-%m-%Y")},
                                    "Comprador": {"RNCComprador": rnc}, "Totales": {"MontoTotal": "100.00"}}}
            await conn.execute(
                "INSERT INTO facturacion.facturas (tipo_ecf, ambiente, rnc_emisor, encf, estado, datos) "
                "VALUES (31, 'test', $1, $2, 'aceptado', $3::json)",
                RNC, encf, json.dumps(datos),
            )
    enlace = await soluciones.crear_enlace(pool, cliente_id=cliente, metodos_pago=[], dias_validez=30)
    async with pool.acquire() as conn:
        # Alguien edita el RNC del contacto después de mandar el enlace...
        await conn.execute("UPDATE ventas.clientes SET rnc = '130000001' WHERE id = $1", cliente)
    vista = await soluciones.portal_publico(pool, enlace["token"], registrar_acceso=False)
    # ...el enlace sigue mostrando SOLO las facturas del RNC de entonces.
    assert [f["encf"] for f in vista["facturas"]] == ["E310000000020"]
    assert vista["cliente_rnc"] == "131234567"
    assert (await soluciones.listar_enlaces(pool))[0]["cliente_rnc"] == "131234567"


# --- RevocacionSesiones con un esquema auth como el de Supabase ---------------------------------------------------------

async def test_revocacion_contra_auth_sessions(pool):
    r = RevocacionSesiones(ttl=0)
    usuario_id, sesion = uuid.uuid4(), uuid.uuid4()
    yo = UsuarioSesion(id=str(usuario_id), correo=None, rol="authenticated", expira=0, sesion_id=str(sesion))
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE SCHEMA auth;
            CREATE TABLE auth.users (id uuid PRIMARY KEY, banned_until timestamptz, deleted_at timestamptz);
            CREATE TABLE auth.sessions (id uuid PRIMARY KEY, user_id uuid REFERENCES auth.users (id),
                                        not_after timestamptz);
            """
        )
        await conn.execute("INSERT INTO auth.users (id) VALUES ($1)", usuario_id)
        await conn.execute("INSERT INTO auth.sessions (id, user_id) VALUES ($1, $2)", sesion, usuario_id)
    assert await r.vigente(pool, yo) is True
    async with pool.acquire() as conn:
        await conn.execute("UPDATE auth.users SET banned_until = now() + interval '100 years'")
    assert await r.vigente(pool, yo) is False  # desactivado en Configuración → Usuarios
    async with pool.acquire() as conn:
        await conn.execute("UPDATE auth.users SET banned_until = NULL")
        await conn.execute("DELETE FROM auth.sessions")
    assert await r.vigente(pool, yo) is False  # cerró sesión
    assert r.desactivada is False


async def test_revocacion_con_auth_viejo_sin_columnas(pool):
    r = RevocacionSesiones(ttl=0)
    sesion = uuid.uuid4()
    yo = UsuarioSesion(id="u", correo=None, rol="authenticated", expira=0, sesion_id=str(sesion))
    async with pool.acquire() as conn:
        await conn.execute(
            "CREATE SCHEMA auth; CREATE TABLE auth.users (id uuid PRIMARY KEY);"
            "CREATE TABLE auth.sessions (id uuid PRIMARY KEY, user_id uuid);"
        )
        await conn.execute("INSERT INTO auth.sessions (id) VALUES ($1)", sesion)
    assert await r.vigente(pool, yo) is True
    assert await r.vigente(pool, UsuarioSesion(id="u", correo=None, rol="a", expira=0, sesion_id=str(uuid.uuid4()))) is False


async def test_revocacion_sin_esquema_auth_se_apaga(pool):
    r = RevocacionSesiones(ttl=0)
    yo = UsuarioSesion(id="u", correo=None, rol="authenticated", expira=0, sesion_id=str(uuid.uuid4()))
    assert await r.vigente(pool, yo) is True
    assert r.desactivada is True


async def test_vencimiento_de_la_espera_crece(pool):
    fid = await _aceptada(pool, "E310000000005")
    esperas = []
    for intento in (1, 2, 3):
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO configuracion.envios (canal, factura_id, destino, estado, intentos) "
                "VALUES ('correo', $1, 'x', 'pendiente', $2) "
                "ON CONFLICT (canal, factura_id) WHERE factura_id IS NOT NULL DO UPDATE SET estado = 'pendiente'",
                fid, intento,
            )
        await envio.terminar_envio(pool, "correo", fid, intento, "falla")
        async with pool.acquire() as conn:
            esperas.append(await conn.fetchval(
                "SELECT proximo_intento - enviado_en FROM configuracion.envios WHERE factura_id = $1", fid
            ))
    assert esperas == [timedelta(minutes=10), timedelta(minutes=20), timedelta(minutes=40)]
