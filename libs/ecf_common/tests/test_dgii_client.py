import httpx
import pytest
import respx

from libs.ecf_common.auth.dgii_client import (
    AutenticacionDGIIClient,
    DGIIAutenticacionError,
)
from libs.ecf_common.environments import DGIIEnvironment

BASE_URL = "https://ecf.dgii.gov.do/testecf/autenticacion"


@pytest.mark.asyncio
@respx.mock
async def test_obtener_semilla_retorna_xml():
    respx.get(f"{BASE_URL}/api/autenticacion/semilla").mock(
        return_value=httpx.Response(
            200,
            content=b"<SemillaModel><valor>abc</valor><fecha>2026-01-01T00:00:00Z</fecha></SemillaModel>",
        )
    )
    cliente = AutenticacionDGIIClient(DGIIEnvironment.TESTECF)

    semilla = await cliente.obtener_semilla()

    assert b"<valor>abc</valor>" in semilla


@pytest.mark.asyncio
@respx.mock
async def test_obtener_semilla_error_http():
    respx.get(f"{BASE_URL}/api/autenticacion/semilla").mock(
        return_value=httpx.Response(500, text="error interno")
    )
    cliente = AutenticacionDGIIClient(DGIIEnvironment.TESTECF)

    with pytest.raises(DGIIAutenticacionError):
        await cliente.obtener_semilla()


@pytest.mark.asyncio
@respx.mock
async def test_validar_semilla_retorna_token():
    respx.post(f"{BASE_URL}/api/autenticacion/validarsemilla").mock(
        return_value=httpx.Response(
            200,
            json={
                "token": "token-de-prueba",
                "expira": "2026-01-01T01:00:00Z",
                "expedido": "2026-01-01T00:00:00Z",
            },
        )
    )
    cliente = AutenticacionDGIIClient(DGIIEnvironment.TESTECF)

    token = await cliente.validar_semilla(b"<SemillaModel>firmada</SemillaModel>")

    assert token.token == "token-de-prueba"
    assert token.expira == "2026-01-01T01:00:00Z"


@pytest.mark.asyncio
@respx.mock
async def test_validar_semilla_error_http():
    respx.post(f"{BASE_URL}/api/autenticacion/validarsemilla").mock(
        return_value=httpx.Response(400, text="firma inválida")
    )
    cliente = AutenticacionDGIIClient(DGIIEnvironment.TESTECF)

    with pytest.raises(DGIIAutenticacionError):
        await cliente.validar_semilla(b"<SemillaModel>firmada</SemillaModel>")
