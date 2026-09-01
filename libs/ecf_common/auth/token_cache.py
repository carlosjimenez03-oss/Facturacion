"""Cache en memoria del token de sesión de la DGII.

Cache simple de un único proceso: válida para una instancia del servicio.
Si el servicio se despliega con múltiples réplicas, cada una mantiene su
propio token (la DGII no limita tokens concurrentes por RNC), o puede
sustituirse esta clase por una implementación respaldada en Redis sin
cambiar el resto del módulo.
"""
import asyncio
import re
from datetime import datetime, timezone

from .schemas import TokenResponse

_FRACTION_RE = re.compile(r"\.(\d+)")


def parse_dgii_datetime(value: str) -> datetime | None:
    """Parsea fechas de la DGII (offset variable, hasta 7 dígitos de fracción)."""
    normalized = _FRACTION_RE.sub(lambda m: "." + m.group(1)[:6], value)
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class TokenCache:
    def __init__(self, margen_segundos: int = 60):
        self._margen_segundos = margen_segundos
        self._token: TokenResponse | None = None
        self._expira: datetime | None = None
        self._lock = asyncio.Lock()

    @property
    def lock(self) -> asyncio.Lock:
        return self._lock

    def vigente(self) -> TokenResponse | None:
        if self._token is None or self._expira is None:
            return None
        margen = self._margen_segundos
        if datetime.now(timezone.utc).timestamp() >= self._expira.timestamp() - margen:
            return None
        return self._token

    def guardar(self, token: TokenResponse) -> None:
        self._token = token
        self._expira = parse_dgii_datetime(token.expira)
