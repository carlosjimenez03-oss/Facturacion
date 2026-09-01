"""Cliente del servicio DGII de Recepción de resumen factura de consumo (RFCE).

Endpoint confirmado en docs/dgii/SERVICIOS_DGII.md (sección "3. Recepción de
resumen factura de consumo electrónica (RFCE)"): POST {base}/api/recepcion/ecf
(multipart, campo "xml", bearer). Solo para Facturas de Consumo (tipo 32) con
monto inferior a RD$250,000.00 — montos iguales o superiores van al servicio
de Recepción de e-CF (services/recepcion).
"""
from libs.ecf_common.environments import DGIIEnvironment, DGIIService, dgii_service_base_url
from libs.ecf_common.http_client import DGIIAsyncClient, raise_for_dgii_status

from .schemas import RecepcionRFCEResponse


class RecepcionRFCEDGIIClient:
    def __init__(self, ambiente: DGIIEnvironment):
        self._base_url = dgii_service_base_url(DGIIService.RECEPCION_RFCE, ambiente)

    async def enviar_rfce(
        self, xml_firmado: bytes, nombre_archivo: str, token: str
    ) -> RecepcionRFCEResponse:
        """POST /api/recepcion/ecf (multipart, xml firmado, bearer)."""
        url = f"{self._base_url}/api/recepcion/ecf"
        files = {"xml": (nombre_archivo, xml_firmado, "text/xml")}
        headers = {"accept": "application/json", "Authorization": f"Bearer {token}"}
        async with DGIIAsyncClient() as client:
            response = await client.post(url, files=files, headers=headers)
        raise_for_dgii_status(response, "enviar el resumen de factura de consumo (RFCE)")
        return RecepcionRFCEResponse.model_validate(response.json())
