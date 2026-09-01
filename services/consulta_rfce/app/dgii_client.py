"""Cliente del servicio DGII de Consulta de Resumen de Factura de Consumo (RFCE).

Endpoint confirmado en docs/dgii/SERVICIOS_DGII.md (sección "4. Consulta de
Resumen de Factura de Consumo Electrónica (RFCE)"):
GET {base}/api/Consultas/Consulta?RNC_Emisor=&ENCF=&Cod_Seguridad_eCF= (bearer)
"""
from libs.ecf_common.environments import DGIIEnvironment, DGIIService, dgii_service_base_url
from libs.ecf_common.http_client import DGIIAsyncClient, raise_for_dgii_status

from .schemas import ConsultaRFCEResponse


class ConsultaRFCEDGIIClient:
    def __init__(self, ambiente: DGIIEnvironment):
        self._base_url = dgii_service_base_url(DGIIService.CONSULTA_RFCE, ambiente)

    async def consultar(
        self, rnc_emisor: str, encf: str, codigo_seguridad: str, token: str
    ) -> ConsultaRFCEResponse:
        url = f"{self._base_url}/api/Consultas/Consulta"
        params = {"RNC_Emisor": rnc_emisor, "ENCF": encf, "Cod_Seguridad_eCF": codigo_seguridad}
        headers = {"accept": "application/json", "Authorization": f"Bearer {token}"}
        async with DGIIAsyncClient() as client:
            response = await client.get(url, params=params, headers=headers)
        raise_for_dgii_status(response, "consultar el RFCE")
        return ConsultaRFCEResponse.model_validate(response.json())
