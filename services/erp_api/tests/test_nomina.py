"""Rutas de Nómina (services/erp_api/app/nomina.py) con el repositorio
mockeado: forma del JSON (montos con 2 decimales), validación de entrada y
traducción de errores a 404/409/422. Las consultas se verifican contra un
Postgres real en test_nomina_postgres.py."""
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from services.erp_api.app import main, nomina_repository
from services.erp_api.app.nomina_repository import ConflictoNominaError, NominaNoEncontradaError
from services.erp_api.app.repository import DatosInvalidosError

AHORA = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)
D = Decimal


@pytest.fixture
def client(monkeypatch):
    async def fake_crear_pool(database_url):
        return object()

    async def nada(pool):
        return None

    monkeypatch.setattr(main, "crear_pool", fake_crear_pool)
    monkeypatch.setattr(main, "cerrar_pool", nada)
    monkeypatch.setattr(main, "aplicar_migraciones", nada)
    with TestClient(main.app) as c:
        yield c


def _fake(monkeypatch, nombre, resultado=None, error=None):
    llamadas = []

    async def fake(*args, **kwargs):
        llamadas.append((args[1:], kwargs))
        if error:
            raise error
        return resultado

    monkeypatch.setattr(nomina_repository, nombre, fake)
    return llamadas


def _empleado(**cambios):
    datos = {
        "id": uuid.uuid4(), "cedula": "40223456781", "nombre": "Ramón Peralta Núñez", "cargo": "Vendedor",
        "departamento": "Ventas", "fecha_ingreso": date(2024, 3, 1), "salario_mensual": D("50000"),
        "tipo_pago": "mensual", "banco": "Banco Popular", "cuenta_bancaria": "784-123456-7", "tipo_cuenta": "ahorro",
        "afp": None, "ars": None, "correo": None, "estado": "activo", "fecha_salida": None, "dias_vacaciones": 14,
        "creado_en": AHORA, "actualizado_en": AHORA,
    }
    datos.update(cambios)
    return datos


_TOTALES = {k: D("0") for k in (
    "salario", "horas_extra", "otros_ingresos", "bonificacion", "ingresos_exentos", "regalia", "total_ingresos", "afp",
    "sfs", "isr", "infotep_empleado", "otros_descuentos", "total_descuentos", "neto", "afp_empleador",
    "sfs_empleador", "srl", "infotep", "total_aportes_empleador", "costo_total",
)}


def _periodo(**cambios):
    datos = {
        "id": uuid.uuid4(), "tipo": "mensual", "anio": 2026, "mes": 10, "quincena": None, "nombre": "octubre 2026",
        "fecha_inicio": date(2026, 10, 1), "fecha_fin": date(2026, 10, 31), "fecha_pago": date(2026, 10, 30),
        "estado": "calculada", "tasa_srl": D("0.011"), "notas": None, "empleados": 1, "pagados": 0,
        "totales": {**_TOTALES, "neto": D("45191"), "costo_total": D("58145")}, "calculado_en": AHORA,
        "aprobado_en": None, "pagado_en": None, "cerrado_en": None, "creado_en": AHORA,
        "parametros": {"vigente_desde": "2026-02-01"},
        "lineas": [{
            "empleado_id": uuid.uuid4(), "cedula": "40223456781", "nombre": "Ramón", "cargo": None,
            "departamento": None, "salario_mensual": D("50000"), "banco": None, "cuenta_bancaria": None,
            "tipo_cuenta": None, "horas_extra_35": D("0"), "horas_extra_100": D("0"), "otros_ingresos": D("0"),
            "bonificacion": D("0"), "ingresos_exentos": D("0"), "regalia": D("0"), "otros_descuentos": D("0"),
            "salario": D("50000"), "horas_extra": D("0"), "total_ingresos": D("50000"),
            "salario_cotizable": D("50000"), "base_isr": D("47045"), "afp": D("1435"), "sfs": D("1520"),
            "isr": D("1854"), "infotep_empleado": D("0"), "total_descuentos": D("4809"), "neto": D("45191"),
            "afp_empleador": D("3550"), "sfs_empleador": D("3545"), "srl": D("550"), "infotep": D("500"),
            "total_aportes_empleador": D("8145"), "costo_total": D("58145"), "pagado": False, "pagado_en": None,
            "referencia_pago": None,
        }],
    }
    datos.update(cambios)
    return datos


def _parametros():
    return {
        "vigente_desde": date(2026, 2, 1), "salario_minimo_cotizable": D("23223"), "afp_empleado": D("0.0287"),
        "afp_empleador": D("0.071"), "sfs_empleado": D("0.0304"), "sfs_empleador": D("0.0709"),
        "srl_empleador": D("0.011"), "infotep_empleador": D("0.01"), "infotep_bonificacion_empleado": D("0.005"),
        "tope_afp_salarios": D("20"), "tope_sfs_salarios": D("10"), "tope_srl_salarios": D("4"),
        "escala_isr": [{"desde": "0.00", "tasa": "0", "fijo": "0.00"}, {"desde": "416220.00", "tasa": "0.15", "fijo": "0"}],
        "regalia_tope_exento": None, "divisor_salario_diario": D("23.83"), "horas_jornada": D("8"),
        "recargo_horas_extra": D("0.35"), "recargo_horas_extra_exceso": D("1"), "fuente": "TSS",
        "tope_afp": D("464460"), "tope_sfs": D("232230"), "tope_srl": D("92892"), "exento_isr_anual": D("416220"),
        "actualizado_en": AHORA,
    }


# --- Empleados ---------------------------------------------------------

def test_listar_empleados_con_filtros(client, monkeypatch):
    llamadas = _fake(monkeypatch, "listar_empleados", [_empleado()])
    r = client.get("/nomina/empleados", params={"q": "ramon", "estado": "activo"})
    assert r.status_code == 200
    assert r.json()[0]["salario_mensual"] == "50000.00"
    assert llamadas[0][1] == {"q": "ramon", "estado": "activo", "tipo_pago": None}
    assert client.get("/nomina/empleados", params={"estado": "otro"}).status_code == 422


def test_crear_empleado(client, monkeypatch):
    llamadas = _fake(monkeypatch, "crear_empleado", _empleado())
    r = client.post("/nomina/empleados", json={
        "cedula": "402-2345678-1", "nombre": "  Ramón  ", "fecha_ingreso": "2024-03-01", "salario_mensual": "50000",
    })
    assert r.status_code == 201
    assert llamadas[0][1]["nombre"] == "Ramón" and llamadas[0][1]["tipo_pago"] == "mensual"
    assert client.post("/nomina/empleados", json={"cedula": "402", "nombre": "x", "fecha_ingreso": "2024-03-01",
                                                  "salario_mensual": "0"}).status_code == 422


@pytest.mark.parametrize(
    ("error", "codigo"),
    [(ConflictoNominaError("Ya existe"), 409), (DatosInvalidosError("La cédula"), 422)],
)
def test_crear_empleado_errores(client, monkeypatch, error, codigo):
    _fake(monkeypatch, "crear_empleado", error=error)
    r = client.post("/nomina/empleados", json={
        "cedula": "40223456781", "nombre": "R", "fecha_ingreso": "2024-03-01", "salario_mensual": "50000",
    })
    assert r.status_code == codigo and r.json()["detail"] == str(error)


def test_empleado_no_encontrado_e_id_invalido(client, monkeypatch):
    _fake(monkeypatch, "obtener_empleado", error=NominaNoEncontradaError())
    assert client.get(f"/nomina/empleados/{uuid.uuid4()}").json() == {"detail": "Empleado no encontrado"}
    assert client.get("/nomina/empleados/xyz").status_code == 422


def test_eliminar_empleado_en_nomina_409(client, monkeypatch):
    _fake(monkeypatch, "eliminar_empleado", error=ConflictoNominaError("dele de baja"))
    assert client.delete(f"/nomina/empleados/{uuid.uuid4()}").status_code == 409
    _fake(monkeypatch, "eliminar_empleado")
    assert client.delete(f"/nomina/empleados/{uuid.uuid4()}").status_code == 204


def test_importar_csv(client, monkeypatch):
    llamadas = _fake(monkeypatch, "importar_empleados", {"creados": 2, "actualizados": 0,
                                                          "errores": [{"fila": 3, "mensaje": "Cédula"}]})
    r = client.post("/nomina/empleados/importar", json={"csv": "cedula,nombre\n"})
    assert r.json()["errores"] == [{"fila": 3, "mensaje": "Cédula"}]
    assert llamadas[0][0] == ("cedula,nombre\n",)


def test_prestaciones(client, monkeypatch):
    eid = uuid.uuid4()
    llamadas = _fake(monkeypatch, "prestaciones_empleado", {
        "empleado_id": eid, "fecha_salida": date(2026, 10, 1), "desahucio": False, "meses_servicio": 24,
        "salario_diario": D("2000"), "dias_preaviso": 0, "preaviso": D("0"), "dias_cesantia": D("0"),
        "cesantia": D("0"), "dias_vacaciones": 14, "vacaciones": D("28000"), "regalia": D("35745"),
        "total": D("63745"),
    })
    r = client.get(f"/nomina/empleados/{eid}/prestaciones", params={"fecha_salida": "2026-10-01", "desahucio": "false"})
    assert r.json()["total"] == "63745.00"
    assert llamadas[0][0][1:] == (date(2026, 10, 1), False)


# --- Empresa y parámetros ---------------------------------------------------------

def test_empresa(client, monkeypatch):
    empresa = {"rnc": "131897542", "razon_social": None, "categoria": "Comercio", "municipio": "Santiago",
               "contacto_nombre": None, "tasa_srl": D("0.011"), "frecuencia_pago": "mensual", "dia_pago": 30,
               "banco": None, "cuenta_origen": None, "completa": True, "actualizado_en": AHORA}
    llamadas = _fake(monkeypatch, "guardar_empresa", empresa)
    r = client.put("/nomina/empresa", json={"rnc": "131897542", "tasa_srl": "0.012", "dia_pago": 28})
    assert r.status_code == 200 and r.json()["completa"] is True
    assert llamadas[0][1]["tasa_srl"] == D("0.012")
    assert client.put("/nomina/empresa", json={"tasa_srl": "1.1"}).status_code == 422
    assert client.put("/nomina/empresa", json={"dia_pago": 40}).status_code == 422


def test_parametros(client, monkeypatch):
    _fake(monkeypatch, "listar_parametros", [_parametros()])
    r = client.get("/nomina/parametros")
    assert r.json()[0]["tope_srl"] == "92892.00" and r.json()[0]["escala_isr"][1]["desde"] == "416220.00"
    llamadas = _fake(monkeypatch, "guardar_parametros", _parametros())
    cuerpo = {k: str(v) for k, v in _parametros().items() if k not in (
        "vigente_desde", "escala_isr", "regalia_tope_exento", "tope_afp", "tope_sfs", "tope_srl", "exento_isr_anual",
        "actualizado_en")}
    cuerpo["escala_isr"] = [{"desde": "0", "tasa": "0"}, {"desde": "416220", "tasa": "0.15"}]
    assert client.put("/nomina/parametros/2027-01-01", json=cuerpo).status_code == 200
    assert llamadas[0][0][0] == date(2027, 1, 1)
    assert llamadas[0][1]["escala_isr"][1] == {"desde": D("416220"), "tasa": D("0.15"), "fijo": D("0")}
    assert client.put("/nomina/parametros/2027-01-01", json={**cuerpo, "afp_empleado": "2.87"}).status_code == 422
    assert client.put("/nomina/parametros/2027-01-01", json={**cuerpo, "escala_isr": []}).status_code == 422
    _fake(monkeypatch, "eliminar_parametros", error=ConflictoNominaError("Debe quedar al menos una"))
    assert client.delete("/nomina/parametros/2026-02-01").status_code == 409


def test_indicadores(client, monkeypatch):
    _fake(monkeypatch, "indicadores", {
        "fecha": date(2026, 10, 24), "vigente_desde": date(2026, 2, 1), "salario_minimo_cotizable": D("23223"),
        "tope_afp": D("464460"), "tope_sfs": D("232230"), "tope_srl": D("92892"), "exento_isr_anual": D("416220"),
        "exento_isr_mensual": D("34685"),
    })
    assert client.get("/nomina/indicadores").json()["salario_minimo_cotizable"] == "23223.00"
    _fake(monkeypatch, "indicadores", error=DatosInvalidosError("No hay parámetros"))
    assert client.get("/nomina/indicadores", params={"fecha": "2000-01-01"}).status_code == 422


# --- Períodos ---------------------------------------------------------

def test_crear_periodo(client, monkeypatch):
    llamadas = _fake(monkeypatch, "crear_periodo", _periodo(estado="borrador"))
    r = client.post("/nomina/periodos", json={"tipo": "mensual", "anio": 2026, "mes": 10})
    assert r.status_code == 201 and r.json()["lineas"][0]["neto"] == "45191.00"
    assert llamadas[0][1] == {"tipo": "mensual", "anio": 2026, "mes": 10, "quincena": None, "fecha_pago": None,
                              "notas": None}
    assert client.post("/nomina/periodos", json={"tipo": "semanal", "anio": 2026, "mes": 10}).status_code == 422
    assert client.post("/nomina/periodos", json={"tipo": "quincenal", "anio": 2026, "mes": 13,
                                                 "quincena": 1}).status_code == 422
    _fake(monkeypatch, "crear_periodo", error=ConflictoNominaError("Ya existe"))
    assert client.post("/nomina/periodos", json={"tipo": "mensual", "anio": 2026, "mes": 10}).status_code == 409


def test_listar_periodos(client, monkeypatch):
    p = _periodo()
    p.pop("lineas")
    p.pop("parametros")
    llamadas = _fake(monkeypatch, "listar_periodos", [p])
    r = client.get("/nomina/periodos", params={"anio": 2026})
    assert r.json()[0]["totales"]["costo_total"] == "58145.00" and "lineas" not in r.json()[0]
    assert llamadas[0][1] == {"anio": 2026, "estado": None}


@pytest.mark.parametrize("accion", ["calcular", "aprobar", "reabrir", "cerrar"])
def test_acciones_del_periodo(client, monkeypatch, accion):
    pid = uuid.uuid4()
    nombre = {"calcular": "calcular_periodo", "aprobar": "aprobar_periodo", "reabrir": "reabrir_periodo",
              "cerrar": "cerrar_periodo"}[accion]
    llamadas = _fake(monkeypatch, nombre, _periodo(id=pid))
    assert client.post(f"/nomina/periodos/{pid}/{accion}").status_code == 200
    assert llamadas[0][0] == (pid,)
    _fake(monkeypatch, nombre, error=ConflictoNominaError("está aprobada"))
    assert client.post(f"/nomina/periodos/{pid}/{accion}").json() == {"detail": "está aprobada"}
    _fake(monkeypatch, nombre, error=NominaNoEncontradaError())
    assert client.post(f"/nomina/periodos/{pid}/{accion}").json() == {"detail": "Nómina no encontrada"}


def test_novedades(client, monkeypatch):
    pid, eid = uuid.uuid4(), uuid.uuid4()
    llamadas = _fake(monkeypatch, "guardar_novedades", _periodo(id=pid))
    r = client.put(f"/nomina/periodos/{pid}/lineas/{eid}", json={"horas_extra_35": "10", "regalia": "41666.67"})
    assert r.status_code == 200
    assert llamadas[0][0] == (pid, eid)
    assert llamadas[0][1]["horas_extra_35"] == D("10") and llamadas[0][1]["otros_descuentos"] == D("0")
    assert client.put(f"/nomina/periodos/{pid}/lineas/{eid}", json={"bonificacion": "-1"}).status_code == 422
    assert client.put(f"/nomina/periodos/{pid}/lineas/nada", json={}).json() == {"detail": "empleado_id inválido"}
    _fake(monkeypatch, "guardar_novedades", error=NominaNoEncontradaError())
    assert client.put(f"/nomina/periodos/{pid}/lineas/{eid}", json={}).json() == {
        "detail": "El empleado no está en esta nómina"}


def test_pagar(client, monkeypatch):
    pid, eid = uuid.uuid4(), uuid.uuid4()
    llamadas = _fake(monkeypatch, "pagar_periodo", _periodo(id=pid, estado="pagada"))
    r = client.post(f"/nomina/periodos/{pid}/pagar", json={"empleado_ids": [str(eid)], "referencia": "TRX"})
    assert r.json()["estado"] == "pagada"
    assert llamadas[0][1] == {"empleado_ids": [eid], "referencia": "TRX"}
    client.post(f"/nomina/periodos/{pid}/pagar", json={})
    assert llamadas[1][1] == {"empleado_ids": None, "referencia": None}


def test_archivo_banco_y_tss(client, monkeypatch):
    _fake(monkeypatch, "archivo_banco", ("nomina_202610_banco.csv", "﻿RNC empresa\r\n".encode()))
    r = client.get(f"/nomina/periodos/{uuid.uuid4()}/archivo-banco")
    assert r.headers["content-disposition"] == 'attachment; filename="nomina_202610_banco.csv"'
    assert r.headers["content-type"].startswith("text/csv")
    _fake(monkeypatch, "archivo_banco", error=ConflictoNominaError("Apruebe la nómina"))
    assert client.get(f"/nomina/periodos/{uuid.uuid4()}/archivo-banco").status_code == 409
    llamadas = _fake(monkeypatch, "archivo_tss", ("tss_autodeterminacion_202610.csv", b"x"))
    assert client.get("/nomina/reportes/tss/csv", params={"anio": 2026, "mes": 10}).status_code == 200
    assert llamadas[0][0] == (2026, 10)


def test_reportes(client, monkeypatch):
    fila = {"empleado_id": uuid.uuid4(), "cedula": "1", "nombre": "R", "ingresos_gravados": D("50000"),
            "ingresos_exentos": D("0"), "tss": D("2955"), "base_isr": D("47045"), "isr": D("1854")}
    _fake(monkeypatch, "reporte_ir3", {
        "anio": 2026, "mes": 10, "nombre": "octubre 2026", "nominas_pendientes": 0, "asalariados": 1, "retenidos": 1,
        "vence": date(2026, 11, 10), "empleados": [fila],
        "totales": {k: fila[k] for k in ("ingresos_gravados", "ingresos_exentos", "tss", "base_isr", "isr")},
    })
    r = client.get("/nomina/reportes/ir3", params={"anio": 2026, "mes": 10})
    assert r.json()["totales"]["isr"] == "1854.00" and r.json()["vence"] == "2026-11-10"
    _fake(monkeypatch, "costo_vs_pago", [{"mes": 1, "nombre": "enero", "costo": D("0"), "neto": D("0"),
                                          "ingresos": D("0"), "empleados": 0}])
    assert client.get("/nomina/reportes/costo-pago", params={"anio": 2026}).json()[0]["costo"] == "0.00"
    _fake(monkeypatch, "reporte_tss", error=DatosInvalidosError("Año o mes inválido"))
    assert client.get("/nomina/reportes/tss", params={"anio": 2026, "mes": 13}).status_code == 422


def test_resumen_y_avisos(client, monkeypatch):
    _fake(monkeypatch, "resumen", {
        "empresa_completa": False, "contacto_nombre": None, "empleados_activos": 0, "nomina_calculada": False,
        "pasos_completados": 1, "periodo": None,
        "periodo_sugerido": {"tipo": "mensual", "anio": 2026, "mes": 10, "nombre": "octubre 2026"}, "indicadores": None,
    })
    assert client.get("/nomina/resumen").json()["pasos_completados"] == 1
    _fake(monkeypatch, "avisos", [{"id": "ir3-202610", "tipo": "dgii", "titulo": "DGII · Recordatorio IR-3",
                                   "detalle": "vencen", "fecha": date(2026, 11, 10), "enlace": "/nomina/reportes"}])
    assert client.get("/nomina/avisos").json()[0]["fecha"] == "2026-11-10"
