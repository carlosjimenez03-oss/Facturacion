"""Cliente del servicio DGII de Consulta de resultado e-CF.

Endpoint confirmado en docs/dgii/SERVICIOS_DGII.md (sección "5. Consulta de
resultado e-CF"): GET {base}/api/consultas/estado?trackid= (bearer). Solo
aplica a e-CF enviados por el servicio de Recepción de e-CF (services/recepcion),
identificados por su trackId.
"""
from libs.ecf_common.environments import DGIIEnvironment, DGIIService, dgii_service_base_url
from libs.ecf_common.http_client import DGIIAsyncClient, raise_for_dgii_status

from .schemas import ConsultaResultadoResponse


class ConsultaResultadoDGIIClient:
    def __init__(self, ambiente: DGIIEnvironment):
        self._base_url = dgii_service_base_url(DGIIService.CONSULTA_RESULTADO, ambiente)

    async def consultar(self, track_id: str, token: str) -> ConsultaResultadoResponse:
        url = f"{self._base_url}/api/consultas/estado"
        headers = {"accept": "application/json", "Authorization": f"Bearer {token}"}
        async with DGIIAsyncClient() as client:
            response = await client.get(url, params={"trackid": track_id}, headers=headers)
        raise_for_dgii_status(response, "consultar el resultado del e-CF")
        return ConsultaResultadoResponse.model_validate(response.json())
