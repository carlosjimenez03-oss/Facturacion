import httpx
import pytest
import respx

from libs.ecf_common.environments import DGIIEnvironment
from libs.ecf_common.http_client import DGIIServiceError
from services.consulta_rfce.app.dgii_client import ConsultaRFCEDGIIClient

BASE_URL = "https://fc.dgii.gov.do/testecf/consultarfce"


@pytest.mark.asyncio
@respx.mock
async def test_consultar_ok():
    respx.get(f"{BASE_URL}/api/Consultas/Consulta").mock(
        return_value=httpx.Response(
            200,
            json={
                "rnc": "131880738",
                "encf": "E320000000064",
                "secuenciaUtilizada": True,
                "codigo": "1",
                "estado": "Aceptado",
                "mensajes": [],
            },
        )
    )
    cliente = ConsultaRFCEDGIIClient(DGIIEnvironment.TESTECF)

    respuesta = await cliente.consultar("131880738", "E320000000064", "uabnyh", "tok")

    assert respuesta.estado == "Aceptado"
    request = respx.calls.last.request
    assert "RNC_Emisor=131880738" in str(request.url)


@pytest.mark.asyncio
@respx.mock
async def test_consultar_error_http():
    respx.get(f"{BASE_URL}/api/Consultas/Consulta").mock(return_value=httpx.Response(401, text="sin token"))
    cliente = ConsultaRFCEDGIIClient(DGIIEnvironment.TESTECF)

    with pytest.raises(DGIIServiceError):
        await cliente.consultar("131880738", "E320000000064", "uabnyh", "tok")
