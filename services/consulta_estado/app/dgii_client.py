"""Cliente del servicio DGII de Consulta de estado e-CF.

Endpoint confirmado en docs/dgii/SERVICIOS_DGII.md (sección "6. Consulta de
estado e-CF"): GET {base}/api/consultas/estado?rncemisor=&ncfelectronico=&
rnccomprador=&codigoseguridad= (bearer). Requiere que el usuario autenticado
esté delegado como emisor o receptor del comprobante consultado. Disponible
solo en TesteCF y eCF (no documentado como público en CerteCF).
"""
from libs.ecf_common.environments import DGIIEnvironment, DGIIService, dgii_service_base_url
from libs.ecf_common.http_client import DGIIAsyncClient, raise_for_dgii_status

from .schemas import ConsultaEstadoResponse


class ConsultaEstadoDGIIClient:
    def __init__(self, ambiente: DGIIEnvironment):
        self._base_url = dgii_service_base_url(DGIIService.CONSULTA_ESTADO, ambiente)

    async def consultar(
        self,
        rnc_emisor: str,
        ncf_electronico: str,
        token: str,
        rnc_comprador: str | None = None,
        codigo_seguridad: str | None = None,
    ) -> ConsultaEstadoResponse:
        url = f"{self._base_url}/api/consultas/estado"
        params = {"rncemisor": rnc_emisor, "ncfelectronico": ncf_electronico}
        if rnc_comprador is not None:
            params["rnccomprador"] = rnc_comprador
        if codigo_seguridad is not None:
            params["codigoseguridad"] = codigo_seguridad
        headers = {"accept": "application/json", "Authorization": f"Bearer {token}"}
        async with DGIIAsyncClient() as client:
            response = await client.get(url, params=params, headers=headers)
        raise_for_dgii_status(response, "consultar el estado del e-CF")
        return ConsultaEstadoResponse.model_validate(response.json())
