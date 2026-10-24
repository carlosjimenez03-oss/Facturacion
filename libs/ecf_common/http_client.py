"""Cliente HTTP saliente hacia los servicios web de la DGII.

Requisito del proyecto: toda comunicación REST debe usar TLS 1.2 o 1.3. Se
construye un `ssl.SSLContext` con versión mínima TLS 1.2 (Python/OpenSSL
negocian automáticamente TLS 1.3 con el servidor cuando ambos lo soportan) y
se usa exclusivamente ese contexto para las llamadas salientes a la DGII.
"""
import ssl
from contextlib import contextmanager
from typing import Iterator

import httpx


def build_tls_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context


@contextmanager
def dgii_http_client(timeout: float = 30.0) -> Iterator[httpx.Client]:
    """Cliente síncrono con TLS 1.2/1.3 forzado para llamar a la DGII."""
    with httpx.Client(verify=build_tls_context(), timeout=timeout) as client:
        yield client


class DGIIAsyncClient:
    """Wrapper de httpx.AsyncClient con TLS 1.2/1.3 forzado.

    Se usa como context manager async: `async with DGIIAsyncClient() as client:`.
    """

    def __init__(self, timeout: float = 30.0):
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> httpx.AsyncClient:
        self._client = httpx.AsyncClient(verify=build_tls_context(), timeout=self._timeout)
        return self._client

    async def __aexit__(self, *exc_info: object) -> None:
        assert self._client is not None
        await self._client.aclose()


class DGIIServiceError(RuntimeError):
    """Error al comunicarse con un servicio web de la DGII (HTTP no exitoso).

    `status_code` es el HTTP que respondió la DGII (None si no se conoce):
    un 5xx es una indisponibilidad del servicio (modo contingencia), un 4xx
    un rechazo del comprobante o de la solicitud."""

    def __init__(self, mensaje: str, status_code: int | None = None):
        super().__init__(mensaje)
        self.status_code = status_code

    @property
    def es_indisponibilidad(self) -> bool:
        return self.status_code is not None and self.status_code >= 500


def raise_for_dgii_status(response: httpx.Response, accion: str) -> None:
    """Lanza DGIIServiceError con contexto si la respuesta no fue exitosa (2xx)."""
    if response.is_success:
        return
    raise DGIIServiceError(
        f"La DGII rechazó la solicitud para {accion} "
        f"(HTTP {response.status_code}): {response.text}",
        status_code=response.status_code,
    )
