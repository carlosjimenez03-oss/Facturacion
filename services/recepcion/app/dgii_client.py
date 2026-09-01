"""Cliente del servicio DGII de Recepción de e-CF.

Endpoint confirmado en docs/dgii/SERVICIOS_DGII.md (sección "2. Recepción de
e-CF"): POST {base}/api/facturaselectronicas (multipart, campo "xml", bearer).

No recibe Facturas de Consumo Electrónicas (tipo 32) con monto inferior a
RD$250,000.00 — esas van al servicio de Recepción RFCE (services/recepcion_rfce).
"""
from libs.ecf_common.environments import DGIIEnvironment, DGIIService, dgii_service_base_url
from libs.ecf_common.http_client import DGIIAsyncClient, raise_for_dgii_status

from .schemas import RecepcionResponse


class RecepcionDGIIClient:
    def __init__(self, ambiente: DGIIEnvironment):
        self._base_url = dgii_service_base_url(DGIIService.RECEPCION_ECF, ambiente)

    async def enviar_ecf(self, xml_firmado: bytes, nombre_archivo: str, token: str) -> RecepcionResponse:
        """POST /api/facturaselectronicas (multipart, xml firmado, bearer) -> trackId."""
        url = f"{self._base_url}/api/facturaselectronicas"
        files = {"xml": (nombre_archivo, xml_firmado, "text/xml")}
        headers = {"accept": "application/json", "Authorization": f"Bearer {token}"}
        async with DGIIAsyncClient() as client:
            response = await client.post(url, files=files, headers=headers)
        raise_for_dgii_status(response, "enviar el e-CF")
        return RecepcionResponse.model_validate(response.json())
