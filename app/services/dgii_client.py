"""Cliente HTTP hacia los servicios web de e-CF de la DGII.

Encapsula las llamadas al ambiente configurado (TesteCF, CerteCF o eCF) segun
APP_ENV. Los metodos son esqueletos: implementan la forma de las llamadas
descritas en el Reporte Tecnico e-CF, pendientes de completar con la firma
digital del comprobante y el manejo de tokens de autenticacion.
"""
import httpx

from app.config import Settings
from app.core.environments import DGII_API_PATHS


class DGIIClient:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._base_url = settings.dgii_base_url

    async def obtener_semilla(self) -> httpx.Response:
        url = self._base_url + DGII_API_PATHS["semilla"]
        async with httpx.AsyncClient() as client:
            return await client.get(url)

    async def enviar_ecf(self, xml_firmado: bytes) -> httpx.Response:
        url = self._base_url + DGII_API_PATHS["recepcion"]
        async with httpx.AsyncClient() as client:
            return await client.post(
                url,
                content=xml_firmado,
                headers={"Content-Type": "application/xml"},
            )

    async def consultar_estado(self, rnc_emisor: str, encf: str) -> httpx.Response:
        url = f"{self._base_url}{DGII_API_PATHS['consulta_estado']}/{rnc_emisor}/{encf}"
        async with httpx.AsyncClient() as client:
            return await client.get(url)
