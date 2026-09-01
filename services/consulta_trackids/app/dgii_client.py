"""Cliente del servicio DGII de Consulta de trackId e-CF.

Endpoint confirmado en docs/dgii/SERVICIOS_DGII.md (sección "7. Consulta de
trackId e-CF"): GET {base}/api/trackids/consulta?rncemisor=&encf= (bearer).
Puede haber múltiples trackIds para un mismo e-NCF si se reenvió varias
veces. Requiere estar delegado como emisor. Disponible solo en TesteCF y eCF.
"""
from libs.ecf_common.environments import DGIIEnvironment, DGIIService, dgii_service_base_url
from libs.ecf_common.http_client import DGIIAsyncClient, raise_for_dgii_status

from .schemas import TrackingDetalle


class ConsultaTrackIdsDGIIClient:
    def __init__(self, ambiente: DGIIEnvironment):
        self._base_url = dgii_service_base_url(DGIIService.CONSULTA_TRACKIDS, ambiente)

    async def consultar(self, rnc_emisor: str, encf: str, token: str) -> list[TrackingDetalle]:
        url = f"{self._base_url}/api/trackids/consulta"
        params = {"rncemisor": rnc_emisor, "encf": encf}
        headers = {"accept": "application/json", "Authorization": f"Bearer {token}"}
        async with DGIIAsyncClient() as client:
            response = await client.get(url, params=params, headers=headers)
        raise_for_dgii_status(response, "consultar los trackIds del e-NCF")
        return [TrackingDetalle.model_validate(item) for item in response.json()]
