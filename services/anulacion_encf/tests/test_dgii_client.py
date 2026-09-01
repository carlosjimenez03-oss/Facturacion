import httpx
import pytest
import respx

from libs.ecf_common.environments import DGIIEnvironment
from libs.ecf_common.http_client import DGIIServiceError
from services.anulacion_encf.app.dgii_client import AnulacionDGIIClient

BASE_URL = "https://ecf.dgii.gov.do/testecf/anulacionrangos"


@pytest.mark.asyncio
@respx.mock
async def test_anular_rango_ok():
    respx.post(f"{BASE_URL}/api/operaciones/anularrango").mock(
        return_value=httpx.Response(200, json={"rnc": "101672919", "codigo": "1", "nombre": "OK", "mensajes": []})
    )
    cliente = AnulacionDGIIClient(DGIIEnvironment.TESTECF)

    respuesta = await cliente.anular_rango(b"<ANECF/>", "101672919-anulacion.xml", "tok")

    assert respuesta.rnc == "101672919"


@pytest.mark.asyncio
@respx.mock
async def test_anular_rango_error_http():
    respx.post(f"{BASE_URL}/api/operaciones/anularrango").mock(return_value=httpx.Response(400, text="inválido"))
    cliente = AnulacionDGIIClient(DGIIEnvironment.TESTECF)

    with pytest.raises(DGIIServiceError):
        await cliente.anular_rango(b"<ANECF/>", "101672919-anulacion.xml", "tok")
