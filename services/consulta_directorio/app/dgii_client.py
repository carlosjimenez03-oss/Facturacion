"""Cliente del servicio DGII de Consulta Directorio de Servicios.

Endpoints confirmados en docs/dgii/SERVICIOS_DGII.md (sección "10. Consulta
Directorio de Servicios"):
- GET {base}/api/consultas/listado (bearer) -> listado completo
- GET {base}/api/consultas/obtenerdirectorioporrnc?RNC= (bearer) -> filtrado por RNC

Disponible solo en TesteCF y eCF.
"""
from libs.ecf_common.environments import DGIIEnvironment, DGIIService, dgii_service_base_url
from libs.ecf_common.http_client import DGIIAsyncClient, raise_for_dgii_status

from .schemas import ContribuyenteDirectorio


class ConsultaDirectorioDGIIClient:
    def __init__(self, ambiente: DGIIEnvironment):
        self._base_url = dgii_service_base_url(DGIIService.CONSULTA_DIRECTORIO, ambiente)

    async def listado(self, token: str) -> list[ContribuyenteDirectorio]:
        url = f"{self._base_url}/api/consultas/listado"
        headers = {"accept": "application/json", "Authorization": f"Bearer {token}"}
        async with DGIIAsyncClient() as client:
            response = await client.get(url, headers=headers)
        raise_for_dgii_status(response, "obtener el directorio de servicios")
        return [ContribuyenteDirectorio.model_validate(item) for item in response.json()]

    async def obtener_por_rnc(self, rnc: str, token: str) -> list[ContribuyenteDirectorio]:
        url = f"{self._base_url}/api/consultas/obtenerdirectorioporrnc"
        headers = {"accept": "application/json", "Authorization": f"Bearer {token}"}
        async with DGIIAsyncClient() as client:
            response = await client.get(url, params={"RNC": rnc}, headers=headers)
        raise_for_dgii_status(response, "obtener el directorio de servicios por RNC")
        return [ContribuyenteDirectorio.model_validate(item) for item in response.json()]
