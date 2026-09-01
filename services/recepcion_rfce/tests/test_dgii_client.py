import httpx
import pytest
import respx

from libs.ecf_common.environments import DGIIEnvironment
from libs.ecf_common.http_client import DGIIServiceError
from services.recepcion_rfce.app.dgii_client import RecepcionRFCEDGIIClient

BASE_URL = "https://fc.dgii.gov.do/testecf/recepcionfc"


@pytest.mark.asyncio
@respx.mock
async def test_enviar_rfce_ok():
    respx.post(f"{BASE_URL}/api/recepcion/ecf").mock(
        return_value=httpx.Response(
            200,
            json={
                "codigo": 1,
                "estado": "Aceptado",
                "mensajes": [],
                "encf": "E320000000001",
                "secuenciaUtilizada": False,
            },
        )
    )
    cliente = RecepcionRFCEDGIIClient(DGIIEnvironment.TESTECF)

    respuesta = await cliente.enviar_rfce(b"<RFCE/>", "101672919E320000000001.xml", "tok")

    assert respuesta.estado == "Aceptado"
    assert respuesta.secuenciaUtilizada is False


@pytest.mark.asyncio
@respx.mock
async def test_enviar_rfce_error_http():
    respx.post(f"{BASE_URL}/api/recepcion/ecf").mock(return_value=httpx.Response(400, text="rechazado"))
    cliente = RecepcionRFCEDGIIClient(DGIIEnvironment.TESTECF)

    with pytest.raises(DGIIServiceError):
        await cliente.enviar_rfce(b"<RFCE/>", "101672919E320000000001.xml", "tok")
