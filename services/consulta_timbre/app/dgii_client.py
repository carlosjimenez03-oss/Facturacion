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
from libs.ecf_common.environments import DGIIEnvironment
from libs.ecf_common.http_client import DGIIAsyncClient, raise_for_dgii_status
from libs.ecf_common.timbre_qr import construir_url_timbre_ecf, construir_url_timbre_fc

from .schemas import TimbreContenidoResponse


class ConsultaTimbreDGIIClient:
    def __init__(self, ambiente: DGIIEnvironment):
        self._ambiente = ambiente

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
        return construir_url_timbre_ecf(
            ambiente=self._ambiente,
            rnc_emisor=rnc_emisor,
            rnc_comprador=rnc_comprador,
            encf=encf,
            fecha_emision=fecha_emision,
            monto_total=monto_total,
            fecha_firma=fecha_firma,
            codigo_seguridad=codigo_seguridad,
        )

    def construir_url_fc(
        self, rnc_emisor: str, encf: str, monto_total: str, codigo_seguridad: str
    ) -> str:
        return construir_url_timbre_fc(
            ambiente=self._ambiente,
            rnc_emisor=rnc_emisor,
            encf=encf,
            monto_total=monto_total,
            codigo_seguridad=codigo_seguridad,
        )

    async def consultar(self, url: str) -> TimbreContenidoResponse:
        """Obtiene el contenido crudo de una URL de verificación ya construida."""
        async with DGIIAsyncClient() as client:
            response = await client.get(url, headers={"accept": "*/*"})
        raise_for_dgii_status(response, "consultar el timbre")
        return TimbreContenidoResponse(
            content_type=response.headers.get("content-type", ""), contenido=response.text
        )
