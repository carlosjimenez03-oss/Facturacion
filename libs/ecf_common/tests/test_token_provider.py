import httpx
import pytest
import respx

from libs.ecf_common.auth.token_provider import TokenProvider
from libs.ecf_common.environments import DGIIEnvironment

BASE_URL = "https://ecf.dgii.gov.do/testecf/autenticacion"


def _mock_dgii_ok():
    respx.get(f"{BASE_URL}/api/autenticacion/semilla").mock(
        return_value=httpx.Response(
            200,
            content=b"<SemillaModel><valor>abc</valor><fecha>2026-01-01T00:00:00Z</fecha></SemillaModel>",
        )
    )
    respx.post(f"{BASE_URL}/api/autenticacion/validarsemilla").mock(
        return_value=httpx.Response(
            200,
            json={
                "token": "token-de-prueba",
                "expira": "2099-01-01T00:00:00Z",
                "expedido": "2026-01-01T00:00:00Z",
            },
        )
    )


@pytest.mark.asyncio
@respx.mock
async def test_obtener_token_firma_y_cachea(p12_prueba):
    p12_path, password = p12_prueba
    _mock_dgii_ok()
    provider = TokenProvider(DGIIEnvironment.TESTECF, str(p12_path), password)

    primero = await provider.obtener_token()
    segundo = await provider.obtener_token()

    assert primero.token == "token-de-prueba"
    assert segundo.token == "token-de-prueba"
    semilla_route = respx.get(f"{BASE_URL}/api/autenticacion/semilla")
    assert semilla_route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_forzar_renovacion_ignora_cache(p12_prueba):
    p12_path, password = p12_prueba
    _mock_dgii_ok()
    provider = TokenProvider(DGIIEnvironment.TESTECF, str(p12_path), password)

    await provider.obtener_token()
    await provider.obtener_token(forzar_renovacion=True)

    semilla_route = respx.get(f"{BASE_URL}/api/autenticacion/semilla")
    assert semilla_route.call_count == 2
