import httpx
import pytest
import respx

from libs.ecf_common.environments import DGIIEnvironment
from libs.ecf_common.http_client import DGIIServiceError
from services.consulta_timbre.app.dgii_client import ConsultaTimbreDGIIClient


def test_construir_url_ecf():
    cliente = ConsultaTimbreDGIIClient(DGIIEnvironment.TESTECF)

    url = cliente.construir_url_ecf(
        "130000001", "130000002", "e310000000001", "10-10-2020", "02.11", "10-10-2020 09:00:00", "dcp79q"
    )

    assert url.startswith("https://ecf.dgii.gov.do/testecf/consultatimbre?")
    assert "rncemisor=130000001" in url
    assert "codigoseguridad=dcp79q" in url


def test_construir_url_fc():
    cliente = ConsultaTimbreDGIIClient(DGIIEnvironment.TESTECF)

    url = cliente.construir_url_fc("131880738", "e320000000064", "6225.09", "uabnyh")

    assert url.startswith("https://fc.dgii.gov.do/testecf/consultatimbrefc?")
    assert "encf=e320000000064" in url


@pytest.mark.asyncio
@respx.mock
async def test_consultar_ok():
    url = "https://ecf.dgii.gov.do/testecf/consultatimbre?rncemisor=130000001"
    respx.get(url).mock(return_value=httpx.Response(200, text="<html>OK</html>", headers={"content-type": "text/html"}))
    cliente = ConsultaTimbreDGIIClient(DGIIEnvironment.TESTECF)

    respuesta = await cliente.consultar(url)

    assert "OK" in respuesta.contenido
    assert respuesta.content_type == "text/html"


@pytest.mark.asyncio
@respx.mock
async def test_consultar_error_http():
    url = "https://ecf.dgii.gov.do/testecf/consultatimbre?rncemisor=130000001"
    respx.get(url).mock(return_value=httpx.Response(404, text="no encontrado"))
    cliente = ConsultaTimbreDGIIClient(DGIIEnvironment.TESTECF)

    with pytest.raises(DGIIServiceError):
        await cliente.consultar(url)
