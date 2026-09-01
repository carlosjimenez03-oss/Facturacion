import httpx
import pytest
import respx

from libs.ecf_common.environments import DGIIEnvironment
from libs.ecf_common.http_client import DGIIServiceError
from services.recepcion.app.dgii_client import RecepcionDGIIClient

BASE_URL = "https://ecf.dgii.gov.do/testecf/recepcion"


@pytest.mark.asyncio
@respx.mock
async def test_enviar_ecf_retorna_trackid():
    respx.post(f"{BASE_URL}/api/facturaselectronicas").mock(
        return_value=httpx.Response(200, json={"trackId": "abc-123", "error": None, "mensaje": None})
    )
    cliente = RecepcionDGIIClient(DGIIEnvironment.TESTECF)

    respuesta = await cliente.enviar_ecf(b"<ECF>firmado</ECF>", "101672919E3100000001.xml", "tok")

    assert respuesta.trackId == "abc-123"

    request = respx.calls.last.request
    assert request.headers["authorization"] == "Bearer tok"


@pytest.mark.asyncio
@respx.mock
async def test_enviar_ecf_error_http():
    respx.post(f"{BASE_URL}/api/facturaselectronicas").mock(
        return_value=httpx.Response(400, text="XML inválido")
    )
    cliente = RecepcionDGIIClient(DGIIEnvironment.TESTECF)

    with pytest.raises(DGIIServiceError):
        await cliente.enviar_ecf(b"<ECF>firmado</ECF>", "101672919E3100000001.xml", "tok")
