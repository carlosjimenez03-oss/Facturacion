"""Rutas de Bancos y Contabilidad (app/bancos.py, app/contabilidad.py) con
el repositorio mockeado. Las consultas se verifican contra un Postgres real
en test_bancos_contabilidad_postgres.py."""
import json
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from services.erp_api.app import bancos_repository, contabilidad, contabilidad_repository, main, precalculos
from services.erp_api.app.bancos_repository import BancoNoEncontradoError, ConflictoBancosError
from services.erp_api.app.contabilidad_repository import ConflictoContabilidadError
from services.erp_api.app.repository import DatosInvalidosError

AHORA = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def client(monkeypatch):
    async def fake_crear_pool(database_url):
        return object()

    async def fake_nada(pool):
        return None

    monkeypatch.setattr(main, "crear_pool", fake_crear_pool)
    monkeypatch.setattr(main, "cerrar_pool", fake_nada)
    monkeypatch.setattr(main, "aplicar_migraciones", fake_nada)
    with TestClient(main.app) as c:
        yield c


def _cuenta(**cambios):
    datos = {
        "id": uuid.uuid4(), "nombre": "Banco Popular", "banco": "Banco Popular Dominicano", "tipo": "corriente",
        "numero_enmascarado": "****4471", "moneda": "DOP", "saldo_inicial": Decimal("500000"),
        "fecha_saldo_inicial": date(2026, 10, 1), "cuenta_contable_id": None, "cuenta_contable_codigo": None,
        "cuenta_contable_nombre": None, "activa": True, "notas": None, "saldo_actual": Decimal("308080.5"),
        "movimientos_sin_conciliar": 3, "lineas_sin_conciliar": 0, "ultimo_movimiento": date(2026, 10, 21),
        "conciliada_hasta": date(2026, 9, 30), "creado_en": AHORA, "actualizado_en": AHORA,
    }
    datos.update(cambios)
    return datos


def test_crear_cuenta_bancaria(client, monkeypatch):
    recibido = {}

    async def fake(pool, **campos):
        recibido.update(campos)
        return _cuenta()

    monkeypatch.setattr(bancos_repository, "crear_cuenta", fake)
    r = client.post("/bancos/cuentas", json={"banco": "Banco Popular Dominicano", "tipo": "corriente",
                                             "numero": "001-234471-1", "saldo_inicial": "500000"})
    assert r.status_code == 201
    assert r.json()["saldo_actual"] == "308080.50" and r.json()["numero_enmascarado"] == "****4471"
    assert "numero" not in r.json()
    assert recibido["moneda"] == "DOP" and recibido["saldo_inicial"] == Decimal("500000")


def test_errores_de_bancos(client, monkeypatch):
    async def conflicto(pool, *args, **kwargs):
        raise ConflictoBancosError("La cuenta Banco Popular ****4471 está conciliada hasta el 30/09/2026")

    async def no_existe(pool, *args, **kwargs):
        raise BancoNoEncontradoError("x")

    monkeypatch.setattr(bancos_repository, "registrar_movimiento", conflicto)
    r = client.post("/bancos/movimientos", json={"cuenta_id": str(uuid.uuid4()), "tipo": "comision", "monto": "10"})
    assert r.status_code == 409 and "conciliada" in r.json()["detail"]
    assert client.post("/bancos/movimientos", json={"cuenta_id": str(uuid.uuid4()), "tipo": "cobro",
                                                    "monto": "10"}).status_code == 422
    monkeypatch.setattr(bancos_repository, "obtener_cuenta", no_existe)
    r = client.get(f"/bancos/cuentas/{uuid.uuid4()}")
    assert r.status_code == 404 and r.json()["detail"] == "Cuenta bancaria no encontrada"
    assert client.get("/bancos/cuentas/no-es-uuid").status_code == 422


def test_importar_extracto_multipart(client, monkeypatch):
    recibido = {}

    async def fake(pool, cuenta_id, contenido, **kwargs):
        recibido.update(kwargs, contenido=contenido)
        return {"separador": ";", "fila_encabezado": 1, "columnas": ["Fecha", "Monto"], "mapeo": {"fecha": "Fecha"},
                "extracto": None, "importadas": 0, "duplicadas": 2, "errores": [{"fila": 3, "motivo": "Monto cero"}]}

    monkeypatch.setattr(bancos_repository, "importar_extracto", fake)
    r = client.post(
        f"/bancos/cuentas/{uuid.uuid4()}/extractos",
        files={"archivo": ("popular.csv", b"Fecha;Monto\n", "text/csv")},
        data={"mapeo": json.dumps({"fecha": "Fecha", "formato_fecha": "DD/MM/AAAA"}), "saldo_final": "249,080.00"},
    )
    assert r.status_code == 201, r.text
    assert recibido["mapeo"] == {"fecha": "Fecha", "formato_fecha": "DD/MM/AAAA"}
    assert recibido["saldo_final"] == Decimal("249080.00") and recibido["nombre_archivo"] == "popular.csv"
    r = client.post(f"/bancos/cuentas/{uuid.uuid4()}/extractos", files={"archivo": ("a.csv", b"x")},
                    data={"mapeo": '{"formato_fecha": "AAAA"}'})
    assert r.status_code == 422 and "mapeo" in r.json()["detail"]


def test_previsualizar_extracto_real(client):
    contenido = "Fecha;Descripción;Débito;Crédito\n05/10/2026;Depósito;;1.500,00\n".encode("cp1252")
    r = client.post("/bancos/extractos/previsualizar", files={"archivo": ("x.csv", contenido)},
                    data={"mapeo": json.dumps({"separador_decimal": ","})})
    assert r.status_code == 200
    assert r.json()["lineas"][0]["monto"] == "1500.00" and r.json()["mapeo"]["credito"] == "Crédito"
    r = client.post("/bancos/extractos/previsualizar", files={"archivo": ("x.csv", b"a,b\n1,2\n")})
    assert r.status_code == 422 and "encabezado" in r.json()["detail"]


def test_generar_asientos_y_pendientes(client, monkeypatch):
    llamadas = []

    async def fake(pool, desde, hasta):
        llamadas.append((desde, hasta))
        return {"desde": desde, "hasta": hasta, "creados": 3, "actualizados": 0, "anulados": 0, "sin_cambios": 1,
                "pendientes": [], "no_contabilizados": [{
                    "origen": "compra", "origen_id": "r1", "fecha": date(2026, 10, 7), "referencia": "B0100000099",
                    "descripcion": "Compra B0100000099", "monto": Decimal("1180"),
                    "motivo": "Sin 'Tipo de bienes y servicios' en el 606: clasifíquelo"}]}

    monkeypatch.setattr(contabilidad_repository, "generar_asientos", fake)
    monkeypatch.setattr(contabilidad_repository, "pendientes_de_contabilizar", fake)
    monkeypatch.setattr(contabilidad, "hoy", lambda: date(2026, 10, 23))
    r = client.post("/contabilidad/asientos/generar", json={"desde": "2026-10-01", "hasta": "2026-10-31"})
    assert r.status_code == 200 and r.json()["no_contabilizados"][0]["monto"] == "1180.00"
    r = client.get("/contabilidad/asientos/pendientes")
    assert r.status_code == 200
    assert llamadas[-1] == (date(2026, 10, 1), date(2026, 10, 23))  # sin fechas: el mes en curso


def test_errores_de_contabilidad(client, monkeypatch):
    async def invalido(pool, **campos):
        raise DatosInvalidosError("El asiento no cuadra: débitos RD$ 10.00, créditos RD$ 9.00 (diferencia RD$ 1.00)")

    async def conflicto(pool, *args, **kwargs):
        raise ConflictoContabilidadError("La depreciación de octubre 2026 ya está registrada en el asiento AS-0420")

    monkeypatch.setattr(contabilidad_repository, "crear_asiento", invalido)
    monkeypatch.setattr(contabilidad_repository, "registrar_depreciacion", conflicto)
    cuenta = str(uuid.uuid4())
    r = client.post("/contabilidad/asientos", json={"concepto": "x", "contabilizar": True, "lineas": [
        {"cuenta_id": cuenta, "debito": "10"}, {"cuenta_id": cuenta, "credito": "9"}]})
    assert r.status_code == 422 and "no cuadra" in r.json()["detail"]
    r = client.post("/contabilidad/depreciacion", json={"periodo": "202610"})
    assert r.status_code == 409 and "AS-0420" in r.json()["detail"]
    assert client.post("/contabilidad/activos-fijos", json={
        "descripcion": "x", "categoria": 4, "fecha_adquisicion": "2026-01-01", "costo": "1"}).status_code == 422


def test_ir2_usa_la_depreciacion_sugerida_salvo_valor_manual():
    base = dict(anio="2026", ingresos=Decimal("1000000"), compras_por_tipo={10: Decimal("500000")},
                valuacion_inventario=Decimal("0"), hoy=date(2026, 10, 23))
    sin_activos = precalculos.calcular_ir2(**base, ajustes={})
    ajuste = next(a for a in sin_activos["ajustes"] if a["clave"] == "depreciacion")
    assert ajuste["sugerido"] is None and ajuste["valor"] == 0
    assert any("como ajuste" in a for a in sin_activos["advertencias"])
    con_activos = precalculos.calcular_ir2(**base, ajustes={}, depreciacion_sugerida=Decimal("80000"))
    ajuste = next(a for a in con_activos["ajustes"] if a["clave"] == "depreciacion")
    assert (ajuste["valor"], ajuste["sugerido"], ajuste["manual"]) == (Decimal("80000.00"), Decimal("80000.00"), False)
    assert any("activos fijos de Contabilidad" in a for a in con_activos["advertencias"])
    manual = precalculos.calcular_ir2(**base, ajustes={"depreciacion": Decimal("100")},
                                      depreciacion_sugerida=Decimal("80000"))
    ajuste = next(a for a in manual["ajustes"] if a["clave"] == "depreciacion")
    assert (ajuste["valor"], ajuste["manual"]) == (Decimal("100.00"), True)
    assert manual["resultado"] != con_activos["resultado"]
