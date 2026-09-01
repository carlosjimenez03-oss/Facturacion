"""Cliente del servicio DGII de Anulación de e-NCF (ANECF).

Endpoint confirmado en docs/dgii/SERVICIOS_DGII.md (sección "9. Anulación de
e-NCF (ANECF)"): POST {base}/api/operaciones/anularrango (multipart, campo
"xml" con etiqueta madre ANECF, bearer). Disponible solo en TesteCF y eCF.
"""
from libs.ecf_common.environments import DGIIEnvironment, DGIIService, dgii_service_base_url
from libs.ecf_common.http_client import DGIIAsyncClient, raise_for_dgii_status

from .schemas import AnulacionRangoResponse


class AnulacionDGIIClient:
    def __init__(self, ambiente: DGIIEnvironment):
        self._base_url = dgii_service_base_url(DGIIService.ANULACION_RANGOS, ambiente)

    async def anular_rango(
        self, xml_firmado: bytes, nombre_archivo: str, token: str
    ) -> AnulacionRangoResponse:
        url = f"{self._base_url}/api/operaciones/anularrango"
        files = {"xml": (nombre_archivo, xml_firmado, "text/xml")}
        headers = {"accept": "application/json", "Authorization": f"Bearer {token}"}
        async with DGIIAsyncClient() as client:
            response = await client.post(url, files=files, headers=headers)
        raise_for_dgii_status(response, "anular el rango de e-NCF")
        return AnulacionRangoResponse.model_validate(response.json())
