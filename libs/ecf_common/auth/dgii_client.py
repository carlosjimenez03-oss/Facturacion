"""Cliente del servicio DGII de Autenticación (semilla + validarsemilla).

Endpoints confirmados en docs/dgii/SERVICIOS_DGII.md (sección "1. Autenticación"):
- GET  {base}/api/autenticacion/semilla
- POST {base}/api/autenticacion/validarsemilla  (multipart, campo "xml")
"""
from libs.ecf_common.environments import DGIIEnvironment, DGIIService, dgii_service_base_url
from libs.ecf_common.http_client import DGIIAsyncClient, DGIIServiceError, raise_for_dgii_status

from .schemas import TokenResponse

# Alias retrocompatible: código existente puede referenciar este nombre.
DGIIAutenticacionError = DGIIServiceError


class AutenticacionDGIIClient:
    def __init__(self, ambiente: DGIIEnvironment):
        self._base_url = dgii_service_base_url(DGIIService.AUTENTICACION, ambiente)

    async def obtener_semilla(self) -> bytes:
        """GET /api/autenticacion/semilla -> XML SemillaModel (sin firmar)."""
        url = f"{self._base_url}/api/autenticacion/semilla"
        async with DGIIAsyncClient() as client:
            response = await client.get(url, headers={"accept": "*/*"})
        raise_for_dgii_status(response, "obtener la semilla")
        return response.content

    async def validar_semilla(self, semilla_firmada: bytes) -> TokenResponse:
        """POST /api/autenticacion/validarsemilla (multipart, xml firmado) -> token."""
        url = f"{self._base_url}/api/autenticacion/validarsemilla"
        files = {"xml": ("semilla.xml", semilla_firmada, "text/xml")}
        async with DGIIAsyncClient() as client:
            response = await client.post(url, files=files, headers={"accept": "application/json"})
        raise_for_dgii_status(response, "validar la semilla firmada")
        return TokenResponse.model_validate(response.json())
