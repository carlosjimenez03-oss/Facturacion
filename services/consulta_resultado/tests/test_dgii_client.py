import httpx
import pytest
import respx

from libs.ecf_common.environments import DGIIEnvironment
from libs.ecf_common.http_client import DGIIServiceError
from services.consulta_resultado.app.dgii_client import ConsultaResultadoDGIIClient

BASE_URL = "https://ecf.dgii.gov.do/testecf/consultaresultado"


@pytest.mark.asyncio
@respx.mock
async def test_consultar_ok():
    respx.get(f"{BASE_URL}/api/consultas/estado").mock(
        return_value=httpx.Response(
            200,
            json={
                "trackId": "abc-123",
                "codigo": 1,
                "estado": "Aceptado",
                "rnc": "101672919",
                "eNCF": "E310000000001",
                "secuenciaUtilizada": False,
                "fechaRecepcion": "2026-01-01T00:00:00Z",
                "mensajes": [],
            },
        )
    )
    cliente = ConsultaResultadoDGIIClient(DGIIEnvironment.TESTECF)

    respuesta = await cliente.consultar("abc-123", "tok")

    assert respuesta.estado == "Aceptado"
    request = respx.calls.last.request
    assert "trackid=abc-123" in str(request.url)


@pytest.mark.asyncio
@respx.mock
async def test_consultar_error_http():
    respx.get(f"{BASE_URL}/api/consultas/estado").mock(return_value=httpx.Response(404, text="no existe"))
    cliente = ConsultaResultadoDGIIClient(DGIIEnvironment.TESTECF)

    with pytest.raises(DGIIServiceError):
        await cliente.consultar("abc-123", "tok")
