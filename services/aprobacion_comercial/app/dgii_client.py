"""Cliente del servicio DGII de Recepción de aprobación comercial.

Endpoint confirmado en docs/dgii/SERVICIOS_DGII.md (sección "8. Recepción de
aprobación comercial"): POST {base}/api/aprobacioncomercial (multipart,
campo "xml" con etiqueta madre ACECF, bearer).
"""
from libs.ecf_common.environments import DGIIEnvironment, DGIIService, dgii_service_base_url
from libs.ecf_common.http_client import DGIIAsyncClient, raise_for_dgii_status

from .schemas import AprobacionComercialResponse


class AprobacionComercialDGIIClient:
    def __init__(self, ambiente: DGIIEnvironment):
        self._base_url = dgii_service_base_url(DGIIService.APROBACION_COMERCIAL, ambiente)

    async def enviar_aprobacion(
        self, xml_firmado: bytes, nombre_archivo: str, token: str
    ) -> AprobacionComercialResponse:
        url = f"{self._base_url}/api/aprobacioncomercial"
        files = {"xml": (nombre_archivo, xml_firmado, "text/xml")}
        headers = {"accept": "application/json", "Authorization": f"Bearer {token}"}
        async with DGIIAsyncClient() as client:
            response = await client.post(url, files=files, headers=headers)
        raise_for_dgii_status(response, "enviar la aprobación comercial")
        return AprobacionComercialResponse.model_validate(response.json())
