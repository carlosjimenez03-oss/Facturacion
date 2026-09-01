import httpx
import pytest
import respx

from libs.ecf_common.environments import DGIIEnvironment
from libs.ecf_common.http_client import DGIIServiceError
from services.consulta_directorio.app.dgii_client import ConsultaDirectorioDGIIClient

BASE_URL = "https://ecf.dgii.gov.do/testecf/consultadirectorio"

ITEM = {
    "nombre": "Empresa Prueba SRL",
    "rnc": "101672919",
    "urlRecepcion": "https://ejemplo.do/recepcion",
    "urlAceptacion": "https://ejemplo.do/aprobacioncomercial",
    "urlOpcional": None,
}


@pytest.mark.asyncio
@respx.mock
async def test_listado_ok():
    respx.get(f"{BASE_URL}/api/consultas/listado").mock(return_value=httpx.Response(200, json=[ITEM]))
    cliente = ConsultaDirectorioDGIIClient(DGIIEnvironment.TESTECF)

    resultado = await cliente.listado("tok")

    assert resultado[0].rnc == "101672919"


@pytest.mark.asyncio
@respx.mock
async def test_obtener_por_rnc_ok():
    route = respx.get(f"{BASE_URL}/api/consultas/obtenerdirectorioporrnc").mock(
        return_value=httpx.Response(200, json=[ITEM])
    )
    cliente = ConsultaDirectorioDGIIClient(DGIIEnvironment.TESTECF)

    resultado = await cliente.obtener_por_rnc("101672919", "tok")

    assert resultado[0].nombre == "Empresa Prueba SRL"
    assert "RNC=101672919" in str(route.calls.last.request.url)


@pytest.mark.asyncio
@respx.mock
async def test_listado_error_http():
    respx.get(f"{BASE_URL}/api/consultas/listado").mock(return_value=httpx.Response(401, text="sin token"))
    cliente = ConsultaDirectorioDGIIClient(DGIIEnvironment.TESTECF)

    with pytest.raises(DGIIServiceError):
        await cliente.listado("tok")
