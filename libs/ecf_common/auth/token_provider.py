"""Orquestador de alto nivel para obtener un token de sesión de la DGII.

Combina credencial (certificado .p12), cliente de Autenticación y cache en
memoria. Cada módulo/servicio (recepción, consultas, aprobación comercial,
etc.) instancia su propio `TokenProvider` y lo usa para autenticar sus
llamadas a la DGII — sin depender de llamar por HTTP al servicio
`services/autenticacion` (que existe como wrapper API para consumidores
externos no-Python, p. ej. un ERP).
"""
import asyncio

from libs.ecf_common.environments import DGIIEnvironment

from .dgii_client import AutenticacionDGIIClient
from .schemas import TokenResponse
from .certificado_activo import huella_activo
from .signing import CredencialFirma, cargar_certificado_p12, firmar_xml
from .token_cache import TokenCache


class TokenProvider:
    def __init__(
        self,
        ambiente: DGIIEnvironment,
        certificado_path: str,
        certificado_password: str,
        margen_segundos: int = 60,
    ):
        self._certificado_path = certificado_path
        self._certificado_password = certificado_password
        self._cliente = AutenticacionDGIIClient(ambiente)
        self._cache = TokenCache(margen_segundos=margen_segundos)
        self._lock = asyncio.Lock()
        self._credencial: CredencialFirma | None = None
        self._huella_certificado: float | None = None

    def _credencial_firma(self) -> CredencialFirma:
        # Si en Configuración se cargó otro certificado, se recarga (y el
        # token de la DGII que se pida después ya sale firmado con el nuevo).
        huella = huella_activo()
        if self._credencial is None or huella != self._huella_certificado:
            self._credencial = cargar_certificado_p12(
                self._certificado_path, self._certificado_password
            )
            self._huella_certificado = huella
        return self._credencial

    async def obtener_token(self, forzar_renovacion: bool = False) -> TokenResponse:
        if not forzar_renovacion:
            token = self._cache.vigente()
            if token is not None:
                return token

        async with self._lock:
            if not forzar_renovacion:
                token = self._cache.vigente()
                if token is not None:
                    return token

            credencial = self._credencial_firma()
            semilla = await self._cliente.obtener_semilla()
            semilla_firmada = firmar_xml(semilla, credencial)
            token = await self._cliente.validar_semilla(semilla_firmada)

            self._cache.guardar(token)
            return token
