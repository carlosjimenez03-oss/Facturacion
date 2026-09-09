"""Store en memoria de semillas emitidas por `receptor_autenticacion`.

Anti-replay: cada semilla emitida por `GET /fe/autenticacion/api/semilla`
debe poder canjearse una sola vez por `POST
/fe/autenticacion/api/validacioncertificado`, y expira a los pocos minutos
si nadie la firma y la devuelve. En memoria (no Postgres) porque es un
desafío de un solo uso y vida muy corta -- perderlo en un reinicio del
proceso solo obliga al llamante a pedir una semilla nueva, y este servicio
corre como una única réplica (igual que el resto de módulos del repo).
"""
from __future__ import annotations

import secrets
import threading
from datetime import datetime, timedelta, timezone


class SemillaStore:
    def __init__(self, ttl_segundos: int = 300):
        self._ttl = timedelta(seconds=ttl_segundos)
        self._valores: dict[str, datetime] = {}
        self._lock = threading.Lock()

    def emitir(self) -> tuple[str, datetime]:
        """Genera un valor de semilla nuevo y lo registra con su expiración."""
        valor = secrets.token_urlsafe(96)
        ahora = datetime.now(timezone.utc)
        with self._lock:
            self._purgar(ahora)
            self._valores[valor] = ahora + self._ttl
        return valor, ahora

    def consumir(self, valor: str) -> bool:
        """Canjea `valor` una sola vez: True si era una semilla vigente
        emitida por este store y aún no consumida/expirada."""
        ahora = datetime.now(timezone.utc)
        with self._lock:
            self._purgar(ahora)
            expira = self._valores.pop(valor, None)
        return expira is not None and expira >= ahora

    def _purgar(self, ahora: datetime) -> None:
        vencidas = [valor for valor, expira in self._valores.items() if expira < ahora]
        for valor in vencidas:
            del self._valores[valor]
