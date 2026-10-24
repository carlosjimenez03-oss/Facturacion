"""Modo contingencia (app.contingencia, app.envio y las rutas de app.main):
clasificación de los fallos de envío, transiciones de la factura, sonda,
envío por lotes y leyenda de la representación impresa. Las consultas SQL se
verifican contra un Postgres real en test_contingencia_postgres.py."""
import base64
import re
import uuid
import zlib
from datetime import datetime, timedelta, timezone

import pytest
import respx
from fastapi.testclient import TestClient
from httpx import ConnectError, ReadTimeout, Response

from libs.ecf_common.auth.signing import firmar_xml
from libs.ecf_common.environments import DGIIEnvironment
from services.facturas_api.app import (
    contingencia,
    contingencia_repository,
    envio,
    estado_dgii,
    main,
    repository,
)
from services.facturas_api.app import representacion_impresa as ri
from services.facturas_api.app.config import FacturasApiSettings
from services.facturas_api.app.xml_builder import construir_xml_ecf

RECEPCION = main.settings.recepcion_service_url
AHORA = datetime(2026, 10, 24, 10, 0, tzinfo=timezone.utc)


def texto_pdf(pdf: bytes) -> str:
    """Texto de los streams del PDF de reportlab (ASCII85 + Flate)."""
    partes = []
    for stream in re.findall(rb"stream\r?\n(.*?)endstream", pdf, re.S):
        datos = re.sub(rb"\s", b"", stream)
        try:
            if datos.endswith(b"~>"):
                datos = base64.a85decode(datos[:-2])
            partes.append(zlib.decompress(datos).decode("latin-1"))
        except (ValueError, zlib.error):
            continue
    return "".join(partes)


def _datos() -> dict:
    return {
        "Encabezado": {
            "Version": "1.0",
            "IdDoc": {"TipoeCF": "31", "eNCF": "E310000000001", "FechaVencimientoSecuencia": "31-12-2026",
                      "TipoIngresos": "01", "TipoPago": "1"},
            "Emisor": {"RNCEmisor": "131234567", "RazonSocialEmisor": "Empresa Demo SRL",
                       "DireccionEmisor": "Calle Falsa 123", "FechaEmision": "24-10-2026"},
            "Comprador": {"RNCComprador": "101672919", "RazonSocialComprador": "Cliente Demo SRL"},
            "Totales": {"MontoGravadoTotal": "1000.00", "TotalITBIS": "180.00", "MontoTotal": "1180.00"},
        },
        "DetallesItems": {"Item": [{
            "NumeroLinea": "1", "IndicadorFacturacion": "1", "NombreItem": "Producto X",
            "IndicadorBienoServicio": "1", "CantidadItem": "1.00", "UnidadMedida": "43",
            "PrecioUnitarioItem": "1000.0000", "MontoItem": "1000.00",
        }]},
    }


def _factura(**cambios) -> dict:
    base = {
        "id": str(uuid.uuid4()), "tipo_ecf": 31, "ambiente": "test", "rnc_emisor": "131234567",
        "encf": "E310000000001", "estado": "generado", "datos": _datos(),
        "xml_generado": construir_xml_ecf(31, _datos()).decode("utf-8"), "xml_firmado": None, "firmado_en": None,
        "track_id": None, "dgii_estado": None, "dgii_codigo": None, "dgii_respuesta": None, "error": None,
        "items_inventario": None, "emitida_en_contingencia": False, "contingencia_en": None, "lote_id": None,
        "creado_en": AHORA, "actualizado_en": AHORA,
    }
    base.update(cambios)
    return base


# --- envio.enviar_a_recepcion: qué es "sin conexión" ---------------------------------------------------------

@pytest.fixture
def settings() -> FacturasApiSettings:
    return main.settings.model_copy(update={"timeout_envio_segundos": 5.0, "timeout_sonda_segundos": 1.0})


def _firmada() -> dict:
    return _factura(xml_firmado="<ECF><Signature/></ECF>", firmado_en=AHORA)


@pytest.mark.parametrize(
    ("mock", "sin_conexion", "track"),
    [
        ({"side_effect": ConnectError("connection refused")}, True, None),
        ({"side_effect": ReadTimeout("timeout")}, True, None),
        ({"return_value": Response(503, json={"detail": "La DGII no responde"})}, True, None),
        ({"return_value": Response(504, text="gateway timeout")}, True, None),
        ({"return_value": Response(502, json={"detail": "La DGII rechazó (HTTP 400)"})}, False, None),
        ({"return_value": Response(500, json={"detail": "certificado"})}, False, None),
        ({"return_value": Response(422, json={"detail": "sin firma"})}, False, None),
        ({"return_value": Response(200, json={"trackId": "trk-1"})}, False, "trk-1"),
    ],
)
@respx.mock
async def test_enviar_a_recepcion_distingue_conectividad_de_rechazo(settings, mock, sin_conexion, track):
    ruta = respx.post(f"{RECEPCION}/ecf").mock(**mock)
    resultado = await envio.enviar_a_recepcion(settings, _firmada(), firmado=True)
    assert resultado.sin_conexion is sin_conexion
    assert resultado.track_id == track
    assert resultado.exitoso is bool(track)
    if not track:
        assert resultado.error
    # Con firmado=true se manda el XML firmado guardado, tal cual.
    cuerpo = ruta.calls.last.request.content
    assert b"<ECF><Signature/></ECF>" in cuerpo and b'name="firmado"' in cuerpo


@respx.mock
async def test_enviar_a_recepcion_sin_firma_local_manda_el_xml_sin_firmar(settings):
    ruta = respx.post(f"{RECEPCION}/ecf").mock(return_value=Response(200, json={"trackId": "t"}))
    factura = _factura()
    await envio.enviar_a_recepcion(settings, factura, firmado=False)
    cuerpo = ruta.calls.last.request.content
    assert factura["xml_generado"].encode() in cuerpo and b'name="firmado"' not in cuerpo


# --- Sonda ---------------------------------------------------------

@pytest.mark.parametrize(
    ("mock", "esperado"),
    [
        ({"return_value": Response(200, json={"disponible": True, "detalle": None})}, (True, None)),
        ({"return_value": Response(200, json={"disponible": False, "detalle": "DGII caída"})}, (False, "DGII caída")),
        ({"return_value": Response(404, text="no")}, (True, None)),  # recepcion sin la sonda
        ({"return_value": Response(503, text="no")}, (False, "recepcion respondió HTTP 503")),
    ],
)
@respx.mock
async def test_sondear(settings, mock, esperado):
    respx.get(f"{RECEPCION}/disponibilidad").mock(**mock)
    assert await contingencia.sondear(settings) == esperado


@respx.mock
async def test_sondear_sin_conexion(settings):
    respx.get(f"{RECEPCION}/disponibilidad").mock(side_effect=ConnectError("refused"))
    disponible, detalle = await contingencia.sondear(settings)
    assert not disponible and "No se pudo contactar recepcion" in detalle


# --- asegurar_firma ---------------------------------------------------------

async def test_asegurar_firma_reutiliza_la_firma_vigente(monkeypatch):
    factura = _firmada()

    async def no_debe_guardar(*a, **k):
        raise AssertionError("no debe volver a firmar")

    monkeypatch.setattr(repository, "guardar_firma_envio", no_debe_guardar)
    assert await envio.asegurar_firma(object(), factura, main.settings) is factura


async def test_asegurar_firma_firma_y_guarda_antes_de_enviar(monkeypatch, credencial_prueba):
    factura = _factura(xml_firmado="<viejo/>", firmado_en=None)  # firma de antes de `firmado_en`: se rehace
    guardado = {}

    async def fake_guardar(pool, fid, xml_firmado):
        guardado["xml"] = xml_firmado
        return {**factura, "xml_firmado": xml_firmado, "firmado_en": AHORA}

    monkeypatch.setattr(envio, "cargar_certificado_p12", lambda path, password: credencial_prueba)
    monkeypatch.setattr(repository, "guardar_firma_envio", fake_guardar)
    actualizada = await envio.asegurar_firma(object(), factura, main.settings)
    assert actualizada["xml_firmado"] == guardado["xml"]
    assert "SignatureValue" in guardado["xml"]
    # Determinista: firmar el mismo XML con el mismo certificado da los mismos bytes.
    assert firmar_xml(factura["xml_generado"].encode(), credencial_prueba).decode() == guardado["xml"]


async def test_asegurar_firma_sin_certificado(monkeypatch):
    def sin_certificado(path, password):
        raise FileNotFoundError(path)

    monkeypatch.setattr(envio, "cargar_certificado_p12", sin_certificado)
    with pytest.raises(envio.FirmaNoDisponibleError):
        await envio.asegurar_firma(object(), _factura(), main.settings)


# --- POST /facturas/{id}/enviar: transiciones ---------------------------------------------------------

class _Estado:
    """Estado compartido de contingencia en memoria (para las rutas)."""

    def __init__(self, activa=False, pendientes=0):
        self.activa = activa
        self.pendientes = pendientes
        self.fallas: list[str] = []
        self.exitos = 0
        self.lanzados = 0


@pytest.fixture
def estado(monkeypatch):
    e = _Estado()

    async def leer(pool):
        return {"activa": e.activa, "pendientes": e.pendientes, "desde": None, "errores_consecutivos": len(e.fallas),
                "ultimo_intento": None, "ultimo_error": e.fallas[-1] if e.fallas else None, "ultimo_exito": None,
                "monto_pendiente": 0, "mas_antigua": None}

    async def falla(pool, error):
        e.fallas.append(error)
        e.activa = True

    async def exito(pool):
        e.exitos += 1
        e.activa = False

    def lanzar(pool, settings, sonda_hecha=False):
        e.lanzados += 1
        return True

    monkeypatch.setattr(contingencia_repository, "leer_estado", leer)
    monkeypatch.setattr(contingencia_repository, "registrar_falla", falla)
    monkeypatch.setattr(contingencia_repository, "registrar_exito", exito)
    monkeypatch.setattr(contingencia, "lanzar_envio_pendientes", lanzar)
    return e


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main.settings, "sincronizar_estado_cada_segundos", 0)
    monkeypatch.setattr(main, "_ESPERA_CONSULTA_TRAS_ENVIO_SEGUNDOS", 0)

    async def fake_crear_pool(database_url):
        return object()

    async def fake_cerrar_pool(pool):
        return None

    async def sin_migraciones(pool):
        return None

    monkeypatch.setattr(main, "crear_pool", fake_crear_pool)
    monkeypatch.setattr(main, "cerrar_pool", fake_cerrar_pool)
    monkeypatch.setattr(main, "aplicar_migraciones", sin_migraciones)
    with TestClient(main.app) as c:
        yield c


@pytest.fixture
def factura_bd(monkeypatch, credencial_prueba):
    """Una factura 'generado' en una "base" en memoria, con firma local."""
    fila = _factura()
    registros = []

    async def obtener(pool, fid):
        return dict(fila)

    async def guardar_firma(pool, fid, xml_firmado):
        fila.update(xml_firmado=xml_firmado, firmado_en=AHORA)
        return dict(fila)

    async def marcar(pool, fid, *, error):
        assert fila["xml_firmado"] and not fila["track_id"]
        fila.update(estado="contingencia", emitida_en_contingencia=True, contingencia_en=AHORA, error=error)
        return dict(fila)

    async def resultado_envio(pool, fid, **kwargs):
        registros.append(kwargs)
        fila.update({k: v for k, v in kwargs.items() if k in ("estado", "track_id", "error")})
        return dict(fila)

    async def consultar(pool, factura, settings, auditar_sin_resultado=True):
        return factura

    monkeypatch.setattr(envio, "cargar_certificado_p12", lambda path, password: credencial_prueba)
    monkeypatch.setattr(repository, "obtener_factura", obtener)
    monkeypatch.setattr(repository, "guardar_firma_envio", guardar_firma)
    monkeypatch.setattr(repository, "marcar_contingencia", marcar)
    monkeypatch.setattr(repository, "registrar_resultado_envio", resultado_envio)
    monkeypatch.setattr(estado_dgii, "actualizar_estado_factura", consultar)
    fila["registros"] = registros
    return fila


@respx.mock
def test_enviar_sin_conexion_queda_en_contingencia(client, estado, factura_bd):
    ruta = respx.post(f"{RECEPCION}/ecf").mock(side_effect=ConnectError("connection refused"))
    r = client.post(f"/facturas/{factura_bd['id']}/enviar")
    assert r.status_code == 200
    cuerpo = r.json()
    assert cuerpo["estado"] == "contingencia" and cuerpo["contingencia"] is True
    assert cuerpo["mensaje"].startswith("Emitida en contingencia")
    assert cuerpo["track_id"] is None and cuerpo["firmado_en"]
    assert estado.activa and "No se pudo contactar recepcion" in estado.fallas[0]
    # Se envió el XML firmado que quedó guardado (el de la RI).
    assert factura_bd["xml_firmado"].encode() in ruta.calls.last.request.content
    assert factura_bd["emitida_en_contingencia"]


@respx.mock
def test_enviar_con_dgii_5xx_queda_en_contingencia(client, estado, factura_bd):
    respx.post(f"{RECEPCION}/ecf").mock(return_value=Response(503, json={"detail": "La DGII no responde"}))
    r = client.post(f"/facturas/{factura_bd['id']}/enviar")
    assert r.status_code == 200 and r.json()["estado"] == "contingencia"
    assert estado.fallas == ["La DGII no responde"]


@respx.mock
def test_enviar_rechazado_no_es_contingencia(client, estado, factura_bd):
    respx.post(f"{RECEPCION}/ecf").mock(return_value=Response(502, json={"detail": "La DGII rechazó (HTTP 400)"}))
    estado.activa = True
    respx.get(f"{RECEPCION}/disponibilidad").mock(return_value=Response(200, json={"disponible": True}))
    r = client.post(f"/facturas/{factura_bd['id']}/enviar")
    assert r.status_code == 502
    assert factura_bd["estado"] == "error"
    # La DGII respondió (aunque fuera para rechazar): fin de la contingencia.
    assert not estado.activa and estado.exitos == 1 and not estado.fallas


@respx.mock
def test_enviar_en_contingencia_activa_va_directo_sin_esperar_el_timeout(client, estado, factura_bd):
    estado.activa = True
    respx.get(f"{RECEPCION}/disponibilidad").mock(return_value=Response(200, json={"disponible": False, "detalle": "DGII caída"}))
    ruta_envio = respx.post(f"{RECEPCION}/ecf")
    r = client.post(f"/facturas/{factura_bd['id']}/enviar")
    assert r.status_code == 200 and r.json()["estado"] == "contingencia"
    assert not ruta_envio.called
    assert estado.fallas == ["DGII caída"]


@respx.mock
def test_enviar_tras_la_contingencia_sale_de_ella_y_lanza_los_lotes(client, estado, factura_bd):
    estado.activa, estado.pendientes = True, 3
    respx.get(f"{RECEPCION}/disponibilidad").mock(return_value=Response(200, json={"disponible": True}))
    respx.post(f"{RECEPCION}/ecf").mock(return_value=Response(200, json={"trackId": "trk-9"}))
    r = client.post(f"/facturas/{factura_bd['id']}/enviar")
    assert r.status_code == 200
    assert (r.json()["estado"], r.json()["track_id"], r.json()["contingencia"]) == ("enviado", "trk-9", False)
    assert not estado.activa and estado.exitos == 1 and estado.lanzados == 1


@respx.mock
def test_enviar_una_factura_en_contingencia_reutiliza_su_firma(client, estado, factura_bd):
    factura_bd.update(estado="contingencia", xml_firmado="<ECF><ds/></ECF>", firmado_en=AHORA, emitida_en_contingencia=True)
    ruta = respx.post(f"{RECEPCION}/ecf").mock(return_value=Response(200, json={"trackId": "trk-2"}))
    r = client.post(f"/facturas/{factura_bd['id']}/enviar")
    assert r.status_code == 200 and r.json()["estado"] == "enviado"
    assert b"<ECF><ds/></ECF>" in ruta.calls.last.request.content


@respx.mock
def test_enviar_sin_certificado_local_no_puede_quedar_en_contingencia(client, estado, factura_bd, monkeypatch):
    def sin_certificado(path, password):
        raise FileNotFoundError(path)

    monkeypatch.setattr(envio, "cargar_certificado_p12", sin_certificado)
    respx.post(f"{RECEPCION}/ecf").mock(side_effect=ConnectError("refused"))
    r = client.post(f"/facturas/{factura_bd['id']}/enviar")
    assert r.status_code == 502 and factura_bd["estado"] == "error"


def test_generar_xml_y_actualizar_estado_rechazan_una_factura_en_contingencia(client, factura_bd):
    factura_bd.update(estado="contingencia", xml_firmado="<x/>", firmado_en=AHORA)
    r = client.post(f"/facturas/{factura_bd['id']}/generar-xml")
    assert r.status_code == 409 and "contingencia" in r.json()["detail"]
    r = client.post(f"/facturas/{factura_bd['id']}/actualizar-estado")
    assert r.status_code == 409 and "automáticamente" in r.json()["detail"]


def test_firmar_no_cambia_la_firma_de_una_factura_en_contingencia(client, factura_bd, monkeypatch):
    factura_bd.update(estado="contingencia", xml_firmado="<firmado-entregado/>", firmado_en=AHORA)

    async def no_debe_guardar(*a, **k):
        raise AssertionError("no debe volver a firmar")

    monkeypatch.setattr(repository, "guardar_xml_firmado", no_debe_guardar)
    r = client.post(f"/facturas/{factura_bd['id']}/firmar")
    assert r.status_code == 200 and r.json()["xml_firmado"] == "<firmado-entregado/>"


# --- Rutas /contingencia ---------------------------------------------------------

def _lote(id_, **cambios):
    base = {"id": id_, "documentos": 20, "enviados": 0, "aceptados": 0, "rechazados": 0, "pendientes": 20,
            "estado": "en_cola", "ultimo_error": None, "creado_en": AHORA, "iniciado_en": None,
            "completado_en": None, "actualizado_en": AHORA}
    base.update(cambios)
    return base


def test_get_contingencia(client, estado, monkeypatch):
    estado.activa, estado.pendientes = True, 5

    async def lotes(pool, limite):
        return [_lote("A-0002", documentos=5, pendientes=5), _lote("A-0001", estado="completado", enviados=20)]

    async def leer(pool):
        return {"activa": True, "desde": AHORA, "errores_consecutivos": 2, "ultimo_intento": AHORA,
                "ultimo_error": "refused", "ultimo_exito": None, "pendientes": 5, "monto_pendiente": 5900,
                "mas_antigua": AHORA}

    monkeypatch.setattr(contingencia_repository, "listar_lotes", lotes)
    monkeypatch.setattr(contingencia_repository, "leer_estado", leer)
    r = client.get("/contingencia")
    assert r.status_code == 200
    c = r.json()
    assert c["activa"] and c["pendientes"] == 5 and c["horas_limite"] == 72 and c["lote_tamano"] == 20
    assert datetime.fromisoformat(c["vence_en"]) == AHORA + timedelta(hours=72)
    assert [lote["id"] for lote in c["lotes"]] == ["A-0002", "A-0001"]


@respx.mock
def test_reintentar_conexion_ahora(client, estado, monkeypatch):
    async def lotes(pool, limite):
        return []

    monkeypatch.setattr(contingencia_repository, "listar_lotes", lotes)
    sonda = respx.get(f"{RECEPCION}/disponibilidad")

    # Sin conexión: se registra la falla, no se lanza nada.
    sonda.mock(return_value=Response(200, json={"disponible": False, "detalle": "DGII caída"}))
    r = client.post("/contingencia/reintentar")
    assert r.status_code == 200
    assert (r.json()["disponible"], r.json()["envio_iniciado"]) == (False, False)
    assert estado.activa and estado.fallas == ["DGII caída"] and estado.lanzados == 0

    # Con conexión y pendientes: se lanza el envío por lotes en segundo plano.
    estado.pendientes = 25
    sonda.mock(return_value=Response(200, json={"disponible": True}))
    r = client.post("/contingencia/reintentar")
    assert (r.json()["disponible"], r.json()["envio_iniciado"]) == (True, True)
    assert estado.lanzados == 1

    # Con conexión y nada pendiente: sale de contingencia.
    estado.pendientes, estado.activa = 0, True
    r = client.post("/contingencia/reintentar")
    assert r.json()["disponible"] and not estado.activa


# --- Envío por lotes (enviar_pendientes) con una base en memoria ---------------------------------------------------------

class _Base:
    """Imita las tablas facturas / lotes_contingencia / contingencia con la
    misma semántica que contingencia_repository (verificada contra Postgres)."""

    def __init__(self, n: int):
        self.facturas = {}
        for i in range(n):
            fid = str(uuid.uuid4())
            self.facturas[fid] = _factura(
                id=fid, encf=f"E31{i + 1:010d}", estado="contingencia", xml_firmado=f"<ECF n='{i}'/>",
                firmado_en=AHORA + timedelta(minutes=i), emitida_en_contingencia=True,
            )
        self.lotes: dict[str, dict] = {}
        self.secuencia = 0
        self.estado = {"activa": True, "errores": 3}
        self.orden_envio: list[str] = []

    def instalar(self, monkeypatch):
        async def leer_estado(pool):
            pendientes = [f for f in self.facturas.values() if f["estado"] == "contingencia"]
            return {"activa": self.estado["activa"], "pendientes": len(pendientes)}

        async def registrar_falla(pool, error):
            self.estado.update(activa=True, errores=self.estado["errores"] + 1, error=error)

        async def registrar_exito(pool):
            self.estado.update(activa=False, errores=0)

        async def tomar_lote(pool, *, tamano):
            for lote in sorted(self.lotes.values(), key=lambda lote: lote["id"]):
                if lote["estado"] == "completado":
                    continue
                if any(f["lote_id"] == lote["id"] and f["estado"] == "contingencia" for f in self.facturas.values()):
                    return dict(lote)
                lote["estado"] = "completado"
            libres = sorted(
                (f for f in self.facturas.values() if f["estado"] == "contingencia" and not f["lote_id"]),
                key=lambda f: f["firmado_en"],
            )[:tamano]
            if not libres:
                return None
            self.secuencia += 1
            lote_id = f"A-{self.secuencia:04d}"
            self.lotes[lote_id] = {"id": lote_id, "documentos": len(libres), "estado": "en_cola"}
            for f in libres:
                f["lote_id"] = lote_id
            return dict(self.lotes[lote_id])

        async def pendientes_del_lote(pool, lote_id):
            return sorted(
                (dict(f) for f in self.facturas.values() if f["lote_id"] == lote_id and f["estado"] == "contingencia"),
                key=lambda f: f["firmado_en"],
            )

        async def enviadas_del_lote(pool, lote_id):
            return [dict(f) for f in self.facturas.values() if f["lote_id"] == lote_id and f["estado"] == "enviado"]

        async def marcar_lote(pool, lote_id, *, estado, error=None):
            self.lotes[lote_id]["estado"] = estado
            if error:
                self.lotes[lote_id]["ultimo_error"] = error

        async def nada(*a, **k):
            return None

        async def interrumpir(pool, error):
            for lote in self.lotes.values():
                if lote["estado"] == "enviando":
                    lote["estado"] = "interrumpido"

        async def marcar_contingencia(pool, fid, *, error):
            self.facturas[str(fid)]["error"] = error
            return dict(self.facturas[str(fid)])

        async def resultado_envio(pool, fid, **kwargs):
            f = self.facturas[str(fid)]
            f.update({k: v for k, v in kwargs.items() if k in ("estado", "track_id", "error")})
            return dict(f)

        async def consultar(pool, factura, settings, auditar_sin_resultado=True):
            self.facturas[factura["id"]]["estado"] = "aceptado"
            return factura

        @asynccontextmanager_compat
        async def candado(pool):
            yield True

        monkeypatch.setattr(contingencia_repository, "leer_estado", leer_estado)
        monkeypatch.setattr(contingencia_repository, "registrar_falla", registrar_falla)
        monkeypatch.setattr(contingencia_repository, "registrar_exito", registrar_exito)
        monkeypatch.setattr(contingencia_repository, "tomar_lote", tomar_lote)
        monkeypatch.setattr(contingencia_repository, "facturas_pendientes_del_lote", pendientes_del_lote)
        monkeypatch.setattr(contingencia_repository, "facturas_enviadas_del_lote", enviadas_del_lote)
        monkeypatch.setattr(contingencia_repository, "marcar_lote", marcar_lote)
        monkeypatch.setattr(contingencia_repository, "recalcular_lotes", nada)
        monkeypatch.setattr(contingencia_repository, "interrumpir_lotes_en_curso", interrumpir)
        monkeypatch.setattr(contingencia_repository, "candado_envio", candado)
        monkeypatch.setattr(repository, "marcar_contingencia", marcar_contingencia)
        monkeypatch.setattr(repository, "registrar_resultado_envio", resultado_envio)
        monkeypatch.setattr(estado_dgii, "actualizar_estado_factura", consultar)
        monkeypatch.setattr(envio, "aplicar_flujo_tras_envio", self._flujo)
        self.flujo: list[str] = []

    async def _flujo(self, pool, factura, settings):
        self.flujo.append(factura["encf"])

    def por_estado(self) -> dict[str, int]:
        cuenta: dict[str, int] = {}
        for f in self.facturas.values():
            cuenta[f["estado"]] = cuenta.get(f["estado"], 0) + 1
        return cuenta


def asynccontextmanager_compat(funcion):
    from contextlib import asynccontextmanager

    return asynccontextmanager(funcion)


def _recepcion(base: _Base, *, caer_tras: int | None = None, rechazar: set[str] = frozenset()):
    """Stub de POST /ecf: guarda el XML recibido; tras `caer_tras` envíos
    la conexión se corta; los e-NCF de `rechazar` los rechaza la DGII."""
    recibidos: list[bytes] = []

    def responder(request):
        if caer_tras is not None and len(recibidos) >= caer_tras:
            raise ConnectError("connection refused")
        cuerpo = request.content
        encf = re.search(rb'name="encf"\r\n\r\n([^\r]+)', cuerpo).group(1).decode()
        recibidos.append(cuerpo)
        base.orden_envio.append(encf)
        if encf in rechazar:
            return Response(502, json={"detail": "La DGII rechazó (HTTP 400): RNC inválido"})
        return Response(200, json={"trackId": f"trk-{encf}"})

    return responder, recibidos


@respx.mock
async def test_lotes_de_20_los_mas_antiguos_primero_y_sale_de_contingencia(monkeypatch, settings):
    base = _Base(25)
    base.instalar(monkeypatch)
    respx.get(f"{RECEPCION}/disponibilidad").mock(return_value=Response(200, json={"disponible": True}))
    responder, recibidos = _recepcion(base)
    respx.post(f"{RECEPCION}/ecf").mock(side_effect=responder)

    resultado = await contingencia.enviar_pendientes(object(), settings)

    assert resultado.lotes == ["A-0001", "A-0002"] and resultado.enviados == 25 and not resultado.interrumpido
    assert [base.lotes[i]["documentos"] for i in ("A-0001", "A-0002")] == [20, 5]
    assert {lote["estado"] for lote in base.lotes.values()} == {"completado"}
    orden = sorted(base.facturas.values(), key=lambda f: f["firmado_en"])
    assert base.orden_envio == [f["encf"] for f in orden]  # en orden de firma
    assert base.por_estado() == {"aceptado": 25}  # consulta del resultado tras cada lote
    assert base.flujo == base.orden_envio  # inventario / 606 de cada una
    assert not base.estado["activa"]
    # Se envió el XML firmado guardado de cada factura, tal cual.
    assert all(f["xml_firmado"].encode() in cuerpo for f, cuerpo in zip(orden, recibidos))


@respx.mock
async def test_caida_a_mitad_de_lote_lo_interrumpe_y_se_retoma(monkeypatch, settings):
    base = _Base(25)
    base.instalar(monkeypatch)
    respx.get(f"{RECEPCION}/disponibilidad").mock(return_value=Response(200, json={"disponible": True}))
    responder, _ = _recepcion(base, caer_tras=12)
    respx.post(f"{RECEPCION}/ecf").mock(side_effect=responder)

    resultado = await contingencia.enviar_pendientes(object(), settings)
    assert resultado.interrumpido and resultado.enviados == 12 and resultado.lotes == ["A-0001"]
    assert base.lotes["A-0001"]["estado"] == "interrumpido"
    assert "connection refused" in base.lotes["A-0001"]["ultimo_error"]
    # Las 12 enviadas esperan su resultado (la tarea de fondo lo consulta).
    assert base.por_estado() == {"enviado": 12, "contingencia": 13}
    assert base.estado["activa"]

    # Vuelve la conexión: se retoma A-0001 (8 que faltan) y luego A-0002 (5).
    responder, _ = _recepcion(base)
    respx.post(f"{RECEPCION}/ecf").mock(side_effect=responder)
    resultado = await contingencia.enviar_pendientes(object(), settings)
    assert resultado.lotes == ["A-0001", "A-0002"] and resultado.enviados == 13
    assert base.lotes["A-0001"]["documentos"] == 20 and base.lotes["A-0002"]["documentos"] == 5
    assert base.por_estado() == {"aceptado": 25} and not base.estado["activa"]
    assert len(base.orden_envio) == 25 and len(set(base.orden_envio)) == 25  # ninguna se envió dos veces


@respx.mock
async def test_sonda_caida_no_envia_nada(monkeypatch, settings):
    base = _Base(3)
    base.instalar(monkeypatch)
    respx.get(f"{RECEPCION}/disponibilidad").mock(side_effect=ConnectError("refused"))
    ruta = respx.post(f"{RECEPCION}/ecf")
    resultado = await contingencia.enviar_pendientes(object(), settings)
    assert not resultado.disponible and not ruta.called and not base.lotes
    assert base.estado["activa"] and base.estado["errores"] == 4


@respx.mock
async def test_rechazo_al_recibir_no_detiene_el_lote(monkeypatch, settings):
    base = _Base(4)
    base.instalar(monkeypatch)
    rechazada = sorted(base.facturas.values(), key=lambda f: f["firmado_en"])[1]["encf"]
    respx.get(f"{RECEPCION}/disponibilidad").mock(return_value=Response(200, json={"disponible": True}))
    responder, _ = _recepcion(base, rechazar={rechazada})
    respx.post(f"{RECEPCION}/ecf").mock(side_effect=responder)
    resultado = await contingencia.enviar_pendientes(object(), settings)
    assert (resultado.enviados, resultado.rechazados, resultado.interrumpido) == (3, 1, False)
    assert base.por_estado() == {"aceptado": 3, "error": 1}
    assert not base.estado["activa"]


@respx.mock
async def test_tamano_de_lote_configurable(monkeypatch, settings):
    base = _Base(7)
    base.instalar(monkeypatch)
    respx.get(f"{RECEPCION}/disponibilidad").mock(return_value=Response(200, json={"disponible": True}))
    responder, _ = _recepcion(base)
    respx.post(f"{RECEPCION}/ecf").mock(side_effect=responder)
    resultado = await contingencia.enviar_pendientes(object(), settings.model_copy(update={"contingencia_lote_tamano": 3}))
    assert resultado.lotes == ["A-0001", "A-0002", "A-0003"]
    assert [base.lotes[i]["documentos"] for i in resultado.lotes] == [3, 3, 1]


@respx.mock
async def test_sin_pendientes_la_sonda_decide_la_contingencia(monkeypatch, settings):
    base = _Base(0)
    base.instalar(monkeypatch)
    sonda = respx.get(f"{RECEPCION}/disponibilidad")
    sonda.mock(return_value=Response(200, json={"disponible": True}))
    await contingencia.enviar_pendientes(object(), settings)
    assert not base.estado["activa"]
    sonda.mock(return_value=Response(200, json={"disponible": False, "detalle": "caída"}))
    await contingencia.enviar_pendientes(object(), settings)
    assert base.estado["activa"] and base.estado["error"] == "caída"


async def test_un_solo_envio_por_lotes_a_la_vez(monkeypatch, settings):
    base = _Base(1)
    base.instalar(monkeypatch)
    async with contingencia._candado_de(1):
        resultado = await contingencia.enviar_pendientes(object(), settings)
    assert resultado.ocupado and not resultado.lotes


async def test_una_vuelta_corre_contingencia_y_luego_lo_demas(monkeypatch, settings):
    llamadas = []

    async def fake_contingencia(pool, s):
        llamadas.append("contingencia")
        return contingencia.ResultadoLotes(disponible=True)

    async def fake(nombre):
        async def _f(*a, **k):
            llamadas.append(nombre)
            return 0
        return _f

    monkeypatch.setattr(contingencia, "enviar_pendientes", fake_contingencia)
    monkeypatch.setattr(estado_dgii, "sincronizar_pendientes", await fake("resultados"))
    monkeypatch.setattr(contingencia_repository, "recalcular_lotes", await fake("lotes"))
    monkeypatch.setattr(estado_dgii, "sincronizar_inventario_pendiente", await fake("inventario"))
    await estado_dgii.una_vuelta(object(), settings)
    assert llamadas == ["contingencia", "resultados", "lotes", "inventario"]


# --- Representación impresa ---------------------------------------------------------

def test_ri_lleva_la_leyenda_de_contingencia_y_el_codigo_del_xml_que_se_envia(credencial_prueba):
    xml_firmado = firmar_xml(construir_xml_ecf(31, _datos()), credencial_prueba).decode("utf-8")
    factura = _factura(xml_firmado=xml_firmado, emitida_en_contingencia=True)
    texto = texto_pdf(ri.generar_pdf(factura, DGIIEnvironment.TESTECF))
    assert "modalidad de Contingencia" in texto and "setenta y dos \\(72\\) horas" in texto
    assert ri.codigo_seguridad_desde_xml_firmado(xml_firmado) in texto

    sin = texto_pdf(ri.generar_pdf(_factura(xml_firmado=xml_firmado), DGIIEnvironment.TESTECF))
    assert "Contingencia" not in sin


def test_leyenda_es_la_del_instructivo_de_la_dgii():
    assert contingencia.LEYENDA_CONTINGENCIA == (
        "e-CF emitido en modalidad de Contingencia, el cual podrá ser consultado para su validez fiscal, "
        "a partir de las setenta y dos (72) horas."
    )


# --- Fase 7: ambiente de la factura ---------------------------------------------------------

@respx.mock
def test_no_se_envia_una_factura_de_otro_ambiente(client, estado, factura_bd, monkeypatch):
    """Creada en pruebas y el activo ya es producción: su e-NCF es de la
    secuencia de pruebas, no se envía."""
    from services.facturas_api.app import ambiente

    async def produccion(pool):
        return "production"

    monkeypatch.setattr(ambiente.ACTIVO, "actual", produccion)
    ruta = respx.post(f"{RECEPCION}/ecf")
    r = client.post(f"/facturas/{factura_bd['id']}/enviar")
    assert r.status_code == 409 and "Pruebas (TesteCF)" in r.json()["detail"]
    assert not ruta.called
    # El QR de la RI y la consulta usan el ambiente de la factura, no el activo.
    assert ambiente.dgii_de_factura(factura_bd, main.settings) == DGIIEnvironment.TESTECF
    assert ambiente.dgii_de_factura({**factura_bd, "ambiente": "production"}, main.settings) == DGIIEnvironment.ECF


def test_lista_filtra_por_ambiente(client, monkeypatch):
    pedidos = []

    async def listar(pool, **kw):
        pedidos.append(kw["ambiente"])
        return []

    monkeypatch.setattr(repository, "listar_facturas", listar)
    assert client.get("/facturas", params={"ambiente": "eCF"}).status_code == 200
    assert client.get("/facturas").status_code == 200
    assert client.get("/facturas", params={"ambiente": "otro"}).status_code == 422
    assert pedidos == ["production", None]
