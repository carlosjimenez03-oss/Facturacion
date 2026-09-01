"""Cliente de los servicios DGII de Consulta timbre (QR): e-CF y FC.

Endpoints confirmados en docs/dgii/SERVICIOS_DGII.md (secciones "11. Consulta
timbre (QR)" y "12. Consulta timbre FC (QR)"). A diferencia del resto de
servicios, estos son URLs PÚBLICAS (sin token bearer): se construyen por
concatenación de parámetros y son las que se codifican en el QR de la
representación impresa (RI) de un e-CF o RFCE, para que cualquiera pueda
verificarlo abriendo la URL.

Formato de fecha confirmado por el ejemplo oficial: DD-MM-YYYY para
`fechaemision`, y "DD-MM-YYYY HH:mm:ss" (con el espacio codificado como
%2009 en la URL de ejemplo) para `fechafirma`. Este cliente no reformatea
fechas: recibe los valores ya formateados por quien construye la solicitud
(típicamente el propio módulo que generó y firmó el e-CF/RFCE).
"""
from urllib.parse import urlencode

from libs.ecf_common.environments import DGIIEnvironment, DGIIService, dgii_service_base_url
from libs.ecf_common.http_client import DGIIAsyncClient, raise_for_dgii_status

from .schemas import TimbreContenidoResponse


class ConsultaTimbreDGIIClient:
    def __init__(self, ambiente: DGIIEnvironment):
        self._base_url_ecf = dgii_service_base_url(DGIIService.CONSULTA_TIMBRE, ambiente)
        self._base_url_fc = dgii_service_base_url(DGIIService.CONSULTA_TIMBRE_FC, ambiente)

    def construir_url_ecf(
        self,
        rnc_emisor: str,
        rnc_comprador: str,
        encf: str,
        fecha_emision: str,
        monto_total: str,
        fecha_firma: str,
        codigo_seguridad: str,
    ) -> str:
        params = {
            "rncemisor": rnc_emisor,
            "rnccomprador": rnc_comprador,
            "encf": encf,
            "fechaemision": fecha_emision,
            "montototal": monto_total,
            "fechafirma": fecha_firma,
            "codigoseguridad": codigo_seguridad,
        }
        return f"{self._base_url_ecf}?{urlencode(params)}"

    def construir_url_fc(
        self, rnc_emisor: str, encf: str, monto_total: str, codigo_seguridad: str
    ) -> str:
        params = {
            "rncemisor": rnc_emisor,
            "encf": encf,
            "montototal": monto_total,
            "codigoseguridad": codigo_seguridad,
        }
        return f"{self._base_url_fc}?{urlencode(params)}"

    async def consultar(self, url: str) -> TimbreContenidoResponse:
        """Obtiene el contenido crudo de una URL de verificación ya construida."""
        async with DGIIAsyncClient() as client:
            response = await client.get(url, headers={"accept": "*/*"})
        raise_for_dgii_status(response, "consultar el timbre")
        return TimbreContenidoResponse(
            content_type=response.headers.get("content-type", ""), contenido=response.text
        )
