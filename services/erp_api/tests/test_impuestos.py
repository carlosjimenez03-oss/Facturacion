"""Rutas de impuestos (services/erp_api/app/impuestos.py) con el
repositorio mockeado, más las validaciones del repositorio que no necesitan
base de datos (claves de ajustes, NCF del 608, períodos)."""
import uuid
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from services.erp_api.app import impuestos_repository, main
from services.erp_api.app.config import get_settings
from services.erp_api.app.repository import DatosInvalidosError


@pytest.fixture
def client(monkeypatch):
    async def fake_crear_pool(database_url):
        return object()

    async def fake_cerrar_pool(pool):
        return None

    async def fake_aplicar_migraciones(pool):
        return None

    monkeypatch.setattr(main, "crear_pool", fake_crear_pool)
    monkeypatch.setattr(main, "cerrar_pool", fake_cerrar_pool)
    monkeypatch.setattr(main, "aplicar_migraciones", fake_aplicar_migraciones)
    monkeypatch.setattr(get_settings(), "emisor_rnc", "1-31-89754-2")
    with TestClient(main.app) as c:
        yield c


def _fila_607(**cambios):
    fila = {
        "factura_id": str(uuid.uuid4()), "rnc_cedula": "101672919", "tipo_id": 1, "ncf": "E310000000001",
        "ncf_modificado": None, "tipo_ingreso": "01", "fecha_comprobante": date(2026, 10, 5),
        "fecha_retencion": None, "cliente_nombre": "Ferretería Ozama SRL", "tipo_ecf": 31,
    }
    for campo in ("monto_facturado", "itbis_facturado", "itbis_retenido_terceros", "itbis_percibido",
                  "retencion_renta_terceros", "isr_percibido", "impuesto_selectivo_consumo", "otros_impuestos",
                  "monto_propina_legal", "efectivo", "cheque_transferencia", "tarjeta", "venta_credito",
                  "bonos_certificados", "permuta", "otras_formas"):
        fila[campo] = Decimal("0")
    fila.update(monto_facturado=Decimal("1000"), itbis_facturado=Decimal("180"), efectivo=Decimal("1180"))
    fila.update(cambios)
    return fila


def test_formato_607_pasa_rnc_normalizado_y_serializa_montos(client, monkeypatch):
    recibido = {}

    async def fake(pool, periodo, rnc):
        recibido.update(periodo=periodo, rnc=rnc)
        return {"periodo": periodo, "rnc_emisor": rnc, "filas": [_fila_607()], "advertencias": ["aviso"],
                "totales": {"cantidad": 1, "monto_facturado": Decimal("1000"), "itbis_facturado": Decimal("180"),
                            "itbis_retenido_terceros": Decimal("0"), "retencion_renta_terceros": Decimal("0")}}

    monkeypatch.setattr(impuestos_repository, "formato_607", fake)

    response = client.get("/impuestos/607?periodo=202610")

    assert response.status_code == 200
    assert recibido == {"periodo": "202610", "rnc": "131897542"}
    cuerpo = response.json()
    assert cuerpo["filas"][0]["monto_facturado"] == "1000.00"
    assert cuerpo["filas"][0]["fecha_comprobante"] == "2026-10-05"
    assert cuerpo["totales"]["itbis_facturado"] == "180.00"


def test_txt_607_devuelve_archivo_con_nombre(client, monkeypatch):
    async def fake(pool, periodo, rnc):
        return f"DGII_F_607_{rnc}_{periodo}.TXT", b"607|131897542|202610|0"

    monkeypatch.setattr(impuestos_repository, "archivo_607", fake)

    response = client.get("/impuestos/607/txt?periodo=202610", headers={"Origin": "http://localhost:5173"})

    assert response.status_code == 200
    assert response.headers["content-type"] == "text/plain; charset=utf-8"
    assert response.headers["content-disposition"] == 'attachment; filename="DGII_F_607_131897542_202610.TXT"'
    assert "content-disposition" in response.headers["access-control-expose-headers"].lower()
    assert response.content == b"607|131897542|202610|0"


def test_txt_607_sin_comprobantes_reportables_es_422(client, monkeypatch):
    async def fake(pool, periodo, rnc):
        raise DatosInvalidosError("No hay comprobantes que remitir en el 607")

    monkeypatch.setattr(impuestos_repository, "archivo_607", fake)

    response = client.get("/impuestos/607/txt?periodo=202610")

    assert response.status_code == 422
    assert "No hay comprobantes" in response.json()["detail"]


def test_tipos_anulacion(client):
    tipos = client.get("/impuestos/608/tipos-anulacion").json()

    assert len(tipos) == 10
    assert tipos[0] == {"codigo": "01", "descripcion": "Deterioro de factura preimpresa"}
    assert tipos[9]["codigo"] == "10"


def test_formato_608_y_alta_manual(client, monkeypatch):
    async def fake_formato(pool, periodo, rnc):
        return {"periodo": periodo, "rnc_emisor": rnc, "advertencias": [], "filas": [
            {"id": None, "origen": "anulacion_encf", "ncf": "E310000000010", "fecha_comprobante": date(2026, 10, 14),
             "tipo_anulacion": "08", "tipo_anulacion_label": "Errores en secuencia de NCF"}]}

    recibido = {}

    async def fake_agregar(pool, **kwargs):
        recibido.update(kwargs)
        return {"id": str(uuid.uuid4()), "origen": "manual", "ncf": "B0100000005",
                "fecha_comprobante": kwargs["fecha_comprobante"], "tipo_anulacion": "04",
                "tipo_anulacion_label": "Corrección de la información"}

    monkeypatch.setattr(impuestos_repository, "formato_608", fake_formato)
    monkeypatch.setattr(impuestos_repository, "agregar_anulacion_608", fake_agregar)

    assert client.get("/impuestos/608?periodo=202610").json()["filas"][0]["id"] is None
    response = client.post(
        "/impuestos/608", json={"ncf": " b0100000005 ", "fecha_comprobante": "2026-10-03", "tipo_anulacion": "04"}
    )

    assert response.status_code == 201
    assert recibido == {"ncf": "b0100000005", "fecha_comprobante": date(2026, 10, 3), "tipo_anulacion": "04"}
    assert response.json()["origen"] == "manual"


def test_eliminar_anulacion_608_errores(client, monkeypatch):
    async def no_existe(pool, anulacion_id):
        raise impuestos_repository.AnulacionNoEncontradaError(str(anulacion_id))

    async def presentada(pool, anulacion_id):
        raise impuestos_repository.ConflictoImpuestosError("El 608 de 202610 ya está marcado como presentado")

    assert client.delete("/impuestos/608/no-es-uuid").status_code == 422
    monkeypatch.setattr(impuestos_repository, "eliminar_anulacion_608", no_existe)
    assert client.delete(f"/impuestos/608/{uuid.uuid4()}").status_code == 404
    monkeypatch.setattr(impuestos_repository, "eliminar_anulacion_608", presentada)
    response = client.delete(f"/impuestos/608/{uuid.uuid4()}")
    assert response.status_code == 409
    assert "presentado" in response.json()["detail"]


def _precalculo(formulario="it1", periodo="202610"):
    return {
        "formulario": formulario, "periodo": periodo, "titulo": "Pre-cálculo IT-1 · octubre 2026",
        "subtitulo": "…", "nota": "…", "vence": date(2026, 11, 20),
        "secciones": [{"numero": "I", "titulo": "Ingresos por operaciones", "casillas": [
            {"codigo": "1", "etiqueta": "Operaciones gravadas al 18%", "valor": Decimal("-5"), "tipo": "normal",
             "ayuda": None}]}],
        "resultado": {"etiqueta": "ITBIS a pagar", "valor": Decimal("10")},
        "resumen": [{"etiqueta": "ITBIS facturado", "valor": "18000.00"}],
        "ajustes": [{"clave": "saldo_favor_anterior", "etiqueta": "Saldo", "valor": Decimal("0"), "ayuda": None}],
        "advertencias": [],
    }


def test_precalculo_y_ajustes(client, monkeypatch):
    llamadas = []

    async def fake_precalculo(pool, formulario, periodo):
        llamadas.append((formulario, periodo))
        return _precalculo(formulario, periodo)

    async def fake_guardar(pool, formulario, periodo, valores):
        llamadas.append((formulario, periodo, valores))
        return _precalculo(formulario, periodo)

    monkeypatch.setattr(impuestos_repository, "precalculo", fake_precalculo)
    monkeypatch.setattr(impuestos_repository, "guardar_ajustes", fake_guardar)

    cuerpo = client.get("/impuestos/precalculo/it1?periodo=202610").json()
    response = client.put("/impuestos/precalculo/it1/ajustes?periodo=202610",
                          json={"valores": {"saldo_favor_anterior": "1500.50"}})

    assert cuerpo["secciones"][0]["casillas"][0]["valor"] == "-5.00"
    assert cuerpo["vence"] == "2026-11-20"
    assert response.status_code == 200
    assert llamadas == [("it1", "202610"), ("it1", "202610", {"saldo_favor_anterior": "1500.50"})]
    assert client.get("/impuestos/precalculo/ir99?periodo=202610").status_code == 422


def test_declaraciones(client, monkeypatch):
    async def fake_listar(pool, anio):
        return [{"formulario": "ir17", "periodo": "202601", "vence": date(2026, 2, 10), "estado": "vencida",
                 "presentada_en": None, "monto": None, "referencia": None}]

    recibido = {}

    async def fake_marcar(pool, formulario, periodo, **kwargs):
        recibido.update(formulario=formulario, periodo=periodo, **kwargs)
        return {"formulario": formulario, "periodo": periodo, "vence": date(2026, 11, 20), "estado": "presentada",
                "presentada_en": date(2026, 10, 23), "monto": kwargs["monto"], "referencia": kwargs["referencia"]}

    async def fake_desmarcar(pool, formulario, periodo):
        return {"formulario": formulario, "periodo": periodo, "vence": date(2026, 11, 20), "estado": "pendiente",
                "presentada_en": None, "monto": None, "referencia": None}

    monkeypatch.setattr(impuestos_repository, "listar_declaraciones", fake_listar)
    monkeypatch.setattr(impuestos_repository, "marcar_presentada", fake_marcar)
    monkeypatch.setattr(impuestos_repository, "desmarcar_presentada", fake_desmarcar)

    assert client.get("/impuestos/declaraciones?anio=2026").json()[0]["estado"] == "vencida"
    marcada = client.post("/impuestos/declaraciones/it1/202610/presentada",
                          json={"monto": "10500", "referencia": "OV-123"}).json()
    assert recibido == {"formulario": "it1", "periodo": "202610", "monto": Decimal("10500"),
                        "referencia": "OV-123", "presentada_en": None}
    assert (marcada["estado"], marcada["monto"]) == ("presentada", "10500.00")
    assert client.delete("/impuestos/declaraciones/it1/202610/presentada").json()["estado"] == "pendiente"
    assert client.post("/impuestos/declaraciones/it9/202610/presentada", json={}).status_code == 422


# --- Validaciones del repositorio (sin base de datos) ---------------------------------------------------------

def test_validar_periodos():
    assert impuestos_repository.validar_periodo_formulario("it1", "202610") == "202610"
    assert impuestos_repository.validar_periodo_formulario("ir2", "2026") == "2026"
    for formulario, periodo in (("it1", "2026"), ("it1", "202613"), ("ir17", "201804"), ("ir2", "202610")):
        with pytest.raises(DatosInvalidosError):
            impuestos_repository.validar_periodo_formulario(formulario, periodo)


def test_parsear_ajustes_rechaza_claves_desconocidas_y_valores_invalidos():
    assert impuestos_repository._parsear_ajustes("ir2", {"anticipos": "1,234.567", "inventario_final": ""}) == {
        "anticipos": Decimal("1234.57"), "inventario_final": None}
    with pytest.raises(DatosInvalidosError, match="desconocidas"):
        impuestos_repository._parsear_ajustes("it1", {"anticipos": "1"})
    with pytest.raises(DatosInvalidosError, match="inválido"):
        impuestos_repository._parsear_ajustes("ir17", {"recargos": "abc"})


@pytest.mark.parametrize(
    ("ncf", "tipo", "mensaje"),
    [("E310000000001", "04", "e-NCF"), ("B0500000001", "04", "NCF inválido"), ("B0100000001", "11", "Tipo")],
)
async def test_agregar_anulacion_608_valida_antes_de_tocar_la_base(ncf, tipo, mensaje):
    with pytest.raises(DatosInvalidosError, match=mensaje):
        await impuestos_repository.agregar_anulacion_608(
            None, ncf=ncf, fecha_comprobante=date(2026, 10, 3), tipo_anulacion=tipo
        )
