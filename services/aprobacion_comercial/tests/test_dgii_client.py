import httpx
import pytest
import respx

from libs.ecf_common.environments import DGIIEnvironment
from libs.ecf_common.http_client import DGIIServiceError
from services.aprobacion_comercial.app.dgii_client import AprobacionComercialDGIIClient

BASE_URL = "https://ecf.dgii.gov.do/testecf/aprobacioncomercial"


@pytest.mark.asyncio
@respx.mock
async def test_enviar_aprobacion_ok():
    respx.post(f"{BASE_URL}/api/aprobacioncomercial").mock(
        return_value=httpx.Response(200, json={"mensaje": [], "estado": "Aprobada", "codigo": "1"})
    )
    cliente = AprobacionComercialDGIIClient(DGIIEnvironment.TESTECF)

    respuesta = await cliente.enviar_aprobacion(b"<ACECF/>", "101672919E3100000001.xml", "tok")

    assert respuesta.estado == "Aprobada"


@pytest.mark.asyncio
@respx.mock
async def test_enviar_aprobacion_error_http():
    respx.post(f"{BASE_URL}/api/aprobacioncomercial").mock(return_value=httpx.Response(400, text="inválido"))
    cliente = AprobacionComercialDGIIClient(DGIIEnvironment.TESTECF)

    with pytest.raises(DGIIServiceError):
        await cliente.enviar_aprobacion(b"<ACECF/>", "101672919E3100000001.xml", "tok")
