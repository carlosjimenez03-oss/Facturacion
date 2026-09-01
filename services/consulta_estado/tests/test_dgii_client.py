import httpx
import pytest
import respx

from libs.ecf_common.environments import DGIIEnvironment
from libs.ecf_common.http_client import DGIIServiceError
from services.consulta_estado.app.dgii_client import ConsultaEstadoDGIIClient

BASE_URL = "https://ecf.dgii.gov.do/testecf/consultaestado"


@pytest.mark.asyncio
@respx.mock
async def test_consultar_ok():
    respx.get(f"{BASE_URL}/api/consultas/estado").mock(
        return_value=httpx.Response(200, json={"codigo": 1, "estado": "Aceptado", "rncEmisor": "131880738"})
    )
    cliente = ConsultaEstadoDGIIClient(DGIIEnvironment.TESTECF)

    respuesta = await cliente.consultar("131880738", "e3100000000", "tok")

    assert respuesta.estado == "Aceptado"


@pytest.mark.asyncio
@respx.mock
async def test_consultar_con_opcionales():
    route = respx.get(f"{BASE_URL}/api/consultas/estado").mock(
        return_value=httpx.Response(200, json={"codigo": 1, "estado": "Aceptado"})
    )
    cliente = ConsultaEstadoDGIIClient(DGIIEnvironment.TESTECF)

    await cliente.consultar(
        "131880738", "e3100000000", "tok", rnc_comprador="131880738", codigo_seguridad="884848448"
    )

    request = route.calls.last.request
    assert "rnccomprador=131880738" in str(request.url)
    assert "codigoseguridad=884848448" in str(request.url)


@pytest.mark.asyncio
@respx.mock
async def test_consultar_error_http():
    respx.get(f"{BASE_URL}/api/consultas/estado").mock(return_value=httpx.Response(403, text="no delegado"))
    cliente = ConsultaEstadoDGIIClient(DGIIEnvironment.TESTECF)

    with pytest.raises(DGIIServiceError):
        await cliente.consultar("131880738", "e3100000000", "tok")
