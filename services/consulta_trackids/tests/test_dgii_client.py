import httpx
import pytest
import respx

from libs.ecf_common.environments import DGIIEnvironment
from libs.ecf_common.http_client import DGIIServiceError
from services.consulta_trackids.app.dgii_client import ConsultaTrackIdsDGIIClient

BASE_URL = "https://ecf.dgii.gov.do/testecf/consultatrackids"


@pytest.mark.asyncio
@respx.mock
async def test_consultar_retorna_lista():
    respx.get(f"{BASE_URL}/api/trackids/consulta").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"trackId": "t1", "estado": "Aceptado", "fechaRecepcion": "2026-01-01T00:00:00Z"},
                {"trackId": "t2", "estado": "Rechazado", "fechaRecepcion": "2026-01-02T00:00:00Z"},
            ],
        )
    )
    cliente = ConsultaTrackIdsDGIIClient(DGIIEnvironment.TESTECF)

    resultado = await cliente.consultar("101672919", "e3100000000", "tok")

    assert len(resultado) == 2
    assert resultado[0].trackId == "t1"


@pytest.mark.asyncio
@respx.mock
async def test_consultar_error_http():
    respx.get(f"{BASE_URL}/api/trackids/consulta").mock(return_value=httpx.Response(400, text="inválido"))
    cliente = ConsultaTrackIdsDGIIClient(DGIIEnvironment.TESTECF)

    with pytest.raises(DGIIServiceError):
        await cliente.consultar("101672919", "e3100000000", "tok")
