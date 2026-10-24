"""Nómina contra un Postgres REAL: siembra de parámetros, empleados e
importación CSV, ciclo de vida del período (calcular, novedades, aprobar,
pagar, cerrar), quincenas, ingresos a mitad de mes, archivos y reportes.

Se salta si no hay `NOMINA_TEST_DATABASE_URL` (DSN de una base con permiso
para crear bases, p. ej. `postgresql://postgres@localhost:5660/postgres`):
cada prueba crea una base nueva, carga `db/init/*.sql` y aplica las
migraciones de erp-api.
"""
import asyncio
import os
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path

import asyncpg
import pytest

from services.erp_api.app import nomina_repository as nr
from services.erp_api.app.migraciones import aplicar_migraciones
from services.erp_api.app.repository import DatosInvalidosError

DSN = os.environ.get("NOMINA_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="sin NOMINA_TEST_DATABASE_URL (Postgres real)")

REPO = Path(__file__).resolve().parents[3]
D = Decimal
HOY = date(2026, 10, 24)


@pytest.fixture
async def pool(monkeypatch):
    monkeypatch.setattr(nr, "hoy", lambda: HOY)
    nombre = f"nomina_{uuid.uuid4().hex[:10]}"
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
    await aplicar_migraciones(p)  # idempotente
    yield p
    await p.close()
    admin = await asyncpg.connect(DSN)
    await admin.execute(f"DROP DATABASE IF EXISTS {nombre} WITH (FORCE)")
    await admin.close()


def _empleado(**cambios):
    datos = dict(
        cedula="402-2345678-1", nombre="Ramón Peralta Núñez", cargo="Vendedor", departamento="Ventas",
        fecha_ingreso=date(2024, 3, 1), salario_mensual=D("50000"), tipo_pago="mensual", banco="Banco Popular",
        cuenta_bancaria="784-123456-7", tipo_cuenta="ahorro", afp="AFP Popular", ars="ARS Humano",
    )
    datos.update(cambios)
    return datos


async def test_parametros_sembrados_y_vigentes(pool):
    lista = await nr.listar_parametros(pool)
    assert [p["vigente_desde"] for p in lista] == [date(2026, 2, 1), date(2025, 4, 1)]
    ind = await nr.indicadores(pool, date(2026, 10, 1))
    assert ind["salario_minimo_cotizable"] == D("23223.00")
    assert (ind["tope_afp"], ind["tope_sfs"], ind["tope_srl"]) == (D("464460.00"), D("232230.00"), D("92892.00"))
    assert ind["exento_isr_mensual"] == D("34685.00")
    enero = await nr.indicadores(pool, date(2026, 1, 15))
    assert enero["salario_minimo_cotizable"] == D("21674.80")
    with pytest.raises(DatosInvalidosError):
        await nr.indicadores(pool, date(2024, 1, 1))


async def test_editar_y_eliminar_parametros(pool):
    base = (await nr.listar_parametros(pool))[0]
    campos = {k: base[k] for k in nr._CAMPOS_PARAMETROS}
    campos["salario_minimo_cotizable"] = D("25000")
    nuevo = await nr.guardar_parametros(pool, date(2027, 1, 1), **campos)
    assert nuevo["tope_afp"] == D("500000.00")
    assert (await nr.indicadores(pool, date(2027, 3, 1)))["tope_srl"] == D("100000.00")
    with pytest.raises(DatosInvalidosError):
        await nr.guardar_parametros(pool, date(2027, 1, 1), **{**campos, "escala_isr": []})
    await nr.eliminar_parametros(pool, date(2027, 1, 1))
    await nr.eliminar_parametros(pool, date(2025, 4, 1))
    with pytest.raises(nr.ConflictoNominaError):
        await nr.eliminar_parametros(pool, date(2026, 2, 1))
    with pytest.raises(nr.NominaNoEncontradaError):
        await nr.eliminar_parametros(pool, date(2030, 1, 1))


async def test_empresa(pool):
    vacia = await nr.obtener_empresa(pool)
    assert vacia["completa"] is False and vacia["tasa_srl"] == D("0.0110")
    e = await nr.guardar_empresa(
        pool, rnc="1-31-89754-2", razon_social="Distribuidora Caribe Verde, SRL", categoria="Comercio",
        municipio="Santo Domingo de Guzmán", contacto_nombre="María Rosario", tasa_srl=D("0.012"),
        frecuencia_pago="mensual", dia_pago=28, banco="Banco Popular", cuenta_origen="700-000123-4",
    )
    assert e["rnc"] == "131897542" and e["completa"] is True and e["tasa_srl"] == D("0.0120")
    with pytest.raises(DatosInvalidosError):
        await nr.guardar_empresa(pool, **{**{k: e[k] for k in nr._CAMPOS_EMPRESA}, "rnc": "123"})


async def test_empleados_crud(pool):
    e = await nr.crear_empleado(pool, **_empleado())
    assert e["cedula"] == "40223456781" and e["estado"] == "activo"
    assert e["dias_vacaciones"] == 14
    with pytest.raises(nr.ConflictoNominaError):
        await nr.crear_empleado(pool, **_empleado(nombre="Otro"))
    with pytest.raises(DatosInvalidosError):
        await nr.crear_empleado(pool, **_empleado(cedula="123"))
    assert [x["nombre"] for x in await nr.listar_empleados(pool, q="peralta")] == ["Ramón Peralta Núñez"]
    assert await nr.listar_empleados(pool, q="40223") != []
    baja = await nr.actualizar_empleado(pool, e["id"], **_empleado(estado="inactivo", fecha_salida=date(2026, 10, 15)))
    assert baja["estado"] == "inactivo" and baja["fecha_salida"] == date(2026, 10, 15)
    assert await nr.listar_empleados(pool, estado="activo") == []
    await nr.eliminar_empleado(pool, e["id"])
    with pytest.raises(nr.NominaNoEncontradaError):
        await nr.obtener_empleado(pool, e["id"])


async def test_importar_csv(pool):
    await nr.crear_empleado(pool, **_empleado())
    texto = (
        "﻿Cédula;Nombre completo;Cargo;Salario;Fecha de ingreso;Tipo de pago;Cuenta\n"
        "402-2345678-1;Ramón Peralta;Supervisor;55,000.00;01/03/2024;mensual;784-123456-7\n"
        "001-1391820-5;Yokasta Marte Reyes;Cajera;RD$32,000;2025-06-16;Quincenal;\n"
        "123;Sin cédula;;1000;2025-01-01;mensual;\n"
        "001-1391820-5;Repetida;;1000;2025-01-01;mensual;\n"
        "00200000001;Fecha mala;;1000;31/31/2025;mensual;\n"
    )
    r = await nr.importar_empleados(pool, texto)
    assert (r["creados"], r["actualizados"]) == (1, 1)
    assert [e["fila"] for e in r["errores"]] == [4, 5, 6]
    ramon = (await nr.listar_empleados(pool, q="40223456781"))[0]
    assert ramon["cargo"] == "Supervisor" and ramon["salario_mensual"] == D("55000.00")
    assert ramon["nombre"] == "Ramón Peralta"
    yokasta = (await nr.listar_empleados(pool, q="yokasta"))[0]
    assert yokasta["tipo_pago"] == "quincenal"
    with pytest.raises(DatosInvalidosError, match="Faltan columnas"):
        await nr.importar_empleados(pool, "nombre,cargo\nA,B\n")


async def test_ciclo_de_vida_de_la_nomina_mensual(pool):
    await nr.guardar_empresa(
        pool, rnc="131897542", razon_social="Caribe Verde", categoria="Comercio", municipio="Santiago",
        contacto_nombre=None, tasa_srl=D("0.011"), frecuencia_pago="mensual", dia_pago=30, banco=None,
        cuenta_origen="700-1",
    )
    a = await nr.crear_empleado(pool, **_empleado())
    b = await nr.crear_empleado(pool, **_empleado(cedula="00113918205", nombre="Carmen Julia Batista",
                                                 salario_mensual=D("500000"), cuenta_bancaria=None))
    await nr.crear_empleado(pool, **_empleado(cedula="00200000002", nombre="Quincenal", tipo_pago="quincenal"))
    await nr.crear_empleado(pool, **_empleado(cedula="00200000003", nombre="Futuro", fecha_ingreso=date(2026, 11, 2)))

    p = await nr.crear_periodo(pool, tipo="mensual", anio=2026, mes=10)
    assert p["estado"] == "borrador" and p["fecha_pago"] == date(2026, 10, 30)
    assert [linea["nombre"] for linea in p["lineas"]] == ["Carmen Julia Batista", "Ramón Peralta Núñez"]
    assert p["lineas"][0]["neto"] is None
    with pytest.raises(nr.ConflictoNominaError):
        await nr.crear_periodo(pool, tipo="mensual", anio=2026, mes=10)
    with pytest.raises(nr.ConflictoNominaError, match="calcúlela"):
        await nr.aprobar_periodo(pool, p["id"])

    p = await nr.calcular_periodo(pool, p["id"])
    assert p["estado"] == "calculada" and p["parametros"]["salario_minimo_cotizable"] == "23223.00"
    lineas = {linea["nombre"]: linea for linea in p["lineas"]}
    assert lineas["Ramón Peralta Núñez"]["isr"] == D("1854.00")
    assert lineas["Ramón Peralta Núñez"]["neto"] == D("45191.00")
    assert lineas["Carmen Julia Batista"]["afp"] == D("13330.00")
    assert lineas["Carmen Julia Batista"]["srl"] == D("1021.81")
    assert p["totales"]["neto"] == D("45191.00") + D("371124.72")
    assert p["totales"]["costo_total"] == D("58145.00") + D("555463.58")

    # Novedades: horas extra y regalía; recalcula todo el período.
    p = await nr.guardar_novedades(pool, p["id"], a["id"], horas_extra_35=D("10"), regalia=D("41666.67"))
    ramon = next(linea for linea in p["lineas"] if linea["empleado_id"] == a["id"])
    assert ramon["horas_extra"] == D("3540.70")  # 50,000 / 23.83 / 8 × 1.35 × 10
    assert ramon["regalia"] == D("41666.67") and ramon["total_ingresos"] == D("95207.37")
    with pytest.raises(nr.NominaNoEncontradaError):
        await nr.guardar_novedades(pool, p["id"], uuid.uuid4())
    with pytest.raises(DatosInvalidosError, match="negativo"):
        await nr.guardar_novedades(pool, p["id"], a["id"], otros_descuentos=D("999999"))
    sugerida = await nr.regalia_sugerida(pool, p["id"], a["id"])
    assert sugerida["regalia"] == D("50000.00")

    p = await nr.aprobar_periodo(pool, p["id"])
    assert p["estado"] == "aprobada"
    with pytest.raises(nr.ConflictoNominaError):
        await nr.guardar_novedades(pool, p["id"], a["id"])
    with pytest.raises(nr.ConflictoNominaError):
        await nr.eliminar_periodo(pool, p["id"])
    nombre, contenido = await nr.archivo_banco(pool, p["id"])
    assert nombre == "nomina_202610_banco.csv"
    texto = contenido.decode("utf-8")
    assert texto.startswith("﻿RNC empresa,Cuenta origen")
    assert "40223456781,Ramón Peralta Núñez,Banco Popular,ahorro,784-123456-7," in texto

    p = await nr.reabrir_periodo(pool, p["id"])
    assert p["estado"] == "calculada"
    p = await nr.aprobar_periodo(pool, p["id"])
    p = await nr.pagar_periodo(pool, p["id"], empleado_ids=[a["id"]], referencia="TRX-1")
    assert p["estado"] == "aprobada" and p["pagados"] == 1
    with pytest.raises(nr.ConflictoNominaError):
        await nr.reabrir_periodo(pool, p["id"])
    with pytest.raises(DatosInvalidosError):
        await nr.pagar_periodo(pool, p["id"], empleado_ids=[uuid.uuid4()])
    p = await nr.pagar_periodo(pool, p["id"])
    assert p["estado"] == "pagada" and p["pagados"] == 2
    p = await nr.cerrar_periodo(pool, p["id"])
    assert p["estado"] == "cerrada"
    with pytest.raises(nr.ConflictoNominaError):
        await nr.calcular_periodo(pool, p["id"])

    # Un empleado en nómina no se elimina (se da de baja).
    with pytest.raises(nr.ConflictoNominaError):
        await nr.eliminar_empleado(pool, b["id"])

    # Reportes del mes
    tss = await nr.reporte_tss(pool, 2026, 10)
    assert tss["totales"]["afp"] == D("1536.62") + D("13330.00")  # 53,540.70 × 2.87 %
    assert tss["vence"] == date(2026, 11, 4)  # 3.er día hábil de noviembre 2026
    carmen = next(e for e in tss["empleados"] if e["nombre"] == "Carmen Julia Batista")
    assert carmen["salario_cotizable"] == D("500000.00")
    nombre, contenido = await nr.archivo_tss(pool, 2026, 10)
    assert nombre == "tss_autodeterminacion_202610.csv"
    assert "C,00113918205,Carmen Julia Batista,500000.00" in contenido.decode("utf-8")
    ir3 = await nr.reporte_ir3(pool, 2026, 10)
    assert ir3["vence"] == date(2026, 11, 10) and ir3["asalariados"] == 2 and ir3["retenidos"] == 2
    ir13 = await nr.reporte_ir13(pool, 2026)
    assert ir13["totales"]["isr"] == ir3["totales"]["isr"]
    meses = await nr.costo_vs_pago(pool, 2026)
    assert len(meses) == 12 and meses[9]["costo"] == p["totales"]["costo_total"] and meses[0]["costo"] == 0


async def test_quincenas_ingreso_y_salida_a_mitad(pool):
    e = await nr.crear_empleado(pool, **_empleado(tipo_pago="quincenal", salario_mensual=D("60000"),
                                                 fecha_ingreso=date(2026, 10, 9)))
    q1 = await nr.crear_periodo(pool, tipo="quincenal", anio=2026, mes=10, quincena=1)
    assert q1["fecha_pago"] == date(2026, 10, 15)
    q1 = await nr.calcular_periodo(pool, q1["id"])
    # Del 9 al 15: 7 de 15 días de la quincena -> 30,000 × 7/15 = 14,000
    assert q1["lineas"][0]["salario"] == D("14000.00")
    q2 = await nr.calcular_periodo(pool, (await nr.crear_periodo(pool, tipo="quincenal", anio=2026, mes=10, quincena=2))["id"])
    assert q2["lineas"][0]["salario"] == D("30000.00")
    assert q2["lineas"][0]["isr"] == D("1743.33")
    # Baja el 20/11: la 1.ª quincena de noviembre completa; la de diciembre ya no lo incluye.
    await nr.actualizar_empleado(pool, e["id"], **_empleado(tipo_pago="quincenal", salario_mensual=D("60000"),
                                                            fecha_ingreso=date(2026, 10, 9), estado="inactivo",
                                                            fecha_salida=date(2026, 11, 20)))
    n2 = await nr.calcular_periodo(pool, (await nr.crear_periodo(pool, tipo="quincenal", anio=2026, mes=11, quincena=2))["id"])
    assert n2["lineas"][0]["salario"] == D("10000.00")  # 16 al 20: 5 de 15 días
    d1 = await nr.crear_periodo(pool, tipo="quincenal", anio=2026, mes=12, quincena=1)
    assert d1["lineas"] == []
    with pytest.raises(nr.ConflictoNominaError, match="no tiene empleados"):
        await nr.aprobar_periodo(pool, (await nr.calcular_periodo(pool, d1["id"]))["id"])
    with pytest.raises(DatosInvalidosError):
        await nr.crear_periodo(pool, tipo="quincenal", anio=2026, mes=12)
    await nr.eliminar_periodo(pool, d1["id"])


async def test_parametros_de_enero_usan_el_salario_minimo_anterior(pool):
    await nr.crear_empleado(pool, **_empleado(salario_mensual=D("500000")))
    p = await nr.calcular_periodo(pool, (await nr.crear_periodo(pool, tipo="mensual", anio=2026, mes=1))["id"])
    assert p["lineas"][0]["afp"] == D("12441.34")  # 433,496 × 2.87 % = 12,441.3352
    assert p["parametros"]["vigente_desde"] == "2025-04-01"


async def test_resumen_y_avisos(pool):
    r = await nr.resumen(pool)
    assert r["pasos_completados"] == 1 and r["periodo"] is None
    assert r["periodo_sugerido"]["nombre"] == "octubre 2026"
    assert r["indicadores"]["salario_minimo_cotizable"] == D("23223.00")
    assert [a["id"] for a in await nr.avisos(pool)] == ["empresa"]

    await nr.crear_empleado(pool, **_empleado())
    p = await nr.calcular_periodo(pool, (await nr.crear_periodo(pool, tipo="mensual", anio=2026, mes=10))["id"])
    r = await nr.resumen(pool)
    assert r["pasos_completados"] == 3 and r["periodo"]["totales"]["neto"] == D("45191.00")
    avisos = await nr.avisos(pool)
    assert f"aprobar-{p['id']}" in [a["id"] for a in avisos]
    await nr.aprobar_periodo(pool, p["id"])
    ids = [a["id"] for a in await nr.avisos(pool)]
    assert f"pagar-{p['id']}" in ids and "tss-202610" in ids and "ir3-202610" in ids
    await nr.pagar_periodo(pool, p["id"])
    await nr.cerrar_periodo(pool, p["id"])
    r = await nr.resumen(pool)
    assert r["pasos_completados"] == 3 and r["periodo"]["estado"] == "cerrada"  # sin abiertos: el último


async def test_calculos_concurrentes_no_se_pisan(pool):
    await nr.crear_empleado(pool, **_empleado())
    p = await nr.crear_periodo(pool, tipo="mensual", anio=2026, mes=10)
    resultados = await asyncio.gather(
        nr.calcular_periodo(pool, p["id"]), nr.aprobar_periodo(pool, p["id"]), return_exceptions=True,
    )
    # O se calculó y luego se aprobó, o la aprobación llegó antes (409): nunca queda a medias.
    final = await nr.obtener_periodo(pool, p["id"])
    assert final["estado"] in ("calculada", "aprobada")
    assert all(not isinstance(x, Exception) or isinstance(x, nr.ConflictoNominaError) for x in resultados)
