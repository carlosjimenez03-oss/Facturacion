"""Ambiente DGII activo (TesteCF / CerteCF / eCF) leído en tiempo de ejecución.

Hasta la fase 7 el ambiente solo salía de `APP_ENV` (variable de entorno de
cada servicio en Dokploy, ver docs/AMBIENTES.md): cambiarlo exigía editar
el Compose y volver a desplegar. Ahora se elige en Configuración → Ambiente
DGII (erp-api, solo administradores) y queda en la fila única de
`configuracion.ambiente_dgii`, que leen todos los servicios que hablan con la
DGII:

- **La fila manda sobre la variable.** Sin fila (nunca se cambió desde
  Configuración), sin tabla (erp-api todavía no migró), sin `DATABASE_URL` o
  con la base caída, cada servicio usa su `APP_ENV` como siempre.
- **Caché corta** (`TTL_SEGUNDOS`, 15 s): un cambio llega a todos los
  servicios en ese plazo sin consultar la base en cada request. erp-api, que
  es quien lo cambia, invalida su propia caché al guardar.
- Los valores son los de `APP_ENV` (`test`, `qa`, `production`), que es lo
  que ya guardan `facturacion.facturas.ambiente` y `envios_dgii.ambiente`.

Una factura queda con el ambiente activo al crearse (su e-NCF sale de la
secuencia de ese ambiente) y se envía y consulta siempre en ese ambiente:
facturas-api se lo pasa explícito a `recepcion` y `consulta_resultado`
(parámetro `ambiente`), que si no lo reciben usan el activo.

`TokenProviderPorAmbiente` guarda un `TokenProvider` (token de sesión de la
DGII) por ambiente: el token de TesteCF no sirve en eCF ni al revés.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from libs.ecf_common.environments import APP_ENV_TO_DGII_ENV, DGIIEnvironment, dgii_environment_for

logger = logging.getLogger(__name__)

AMBIENTES = tuple(APP_ENV_TO_DGII_ENV)  # ('test', 'qa', 'production')
TTL_SEGUNDOS = 15.0

# Para mostrarlo (Configuración, avisos de los 409).
NOMBRES = {"test": "Pruebas (TesteCF)", "qa": "Certificación (CerteCF)", "production": "Producción (eCF)"}

_CONSULTA = "SELECT ambiente FROM configuracion.ambiente_dgii WHERE id = 1"


class AmbienteInvalidoError(ValueError):
    pass


def validar_ambiente(valor: str | None) -> str:
    """'test' | 'qa' | 'production'. Acepta también el nombre DGII
    (testecf, certecf, ecf), sin distinguir mayúsculas."""
    texto = (valor or "").strip().lower()
    if texto in AMBIENTES:
        return texto
    for app_env, dgii in APP_ENV_TO_DGII_ENV.items():
        if texto == dgii.value:
            return app_env
    raise AmbienteInvalidoError(f"Ambiente {valor!r} inválido: use test, qa o production (TesteCF, CerteCF, eCF)")


def nombre(ambiente: str | None) -> str:
    return NOMBRES.get(ambiente or "", ambiente or "")


async def leer_ambiente_guardado(conn_o_pool: Any) -> str | None:
    """El ambiente de la fila de Configuración, o None si no hay fila o
    tabla. Lanza los errores de conexión (quien llama decide)."""
    if await conn_o_pool.fetchval("SELECT to_regclass('configuracion.ambiente_dgii')") is None:
        return None
    valor = await conn_o_pool.fetchval(_CONSULTA)
    return validar_ambiente(valor) if valor else None


class AmbienteDGII:
    """Resuelve el ambiente activo de un servicio: el de Configuración (con
    caché) o, si no hay, `defecto` (su APP_ENV)."""

    def __init__(self, defecto: str, *, ttl_segundos: float = TTL_SEGUNDOS, reloj=time.monotonic):
        self.defecto = validar_ambiente(defecto)
        self.ttl = ttl_segundos
        self._reloj = reloj
        self._valor: str | None = None
        self._origen = "variables"
        self._vence = 0.0

    def invalidar(self) -> None:
        self._vence = 0.0

    def fijar(self, ambiente: str) -> None:
        """Lo que se acaba de guardar (erp-api), sin esperar a la caché."""
        self._valor = validar_ambiente(ambiente)
        self._origen = "configuracion"
        self._vence = self._reloj() + self.ttl

    async def actual(self, pool: Any) -> str:
        if self._reloj() < self._vence:
            return self._valor or self.defecto
        valor: str | None = None
        if pool is not None:
            try:
                valor = await leer_ambiente_guardado(pool)
            except Exception as exc:  # base caída, sin permiso...: se usa APP_ENV
                logger.warning("No se pudo leer el ambiente DGII de Configuración (%s): se usa APP_ENV", exc)
        self._valor = valor
        self._origen = "configuracion" if valor else "variables"
        self._vence = self._reloj() + self.ttl
        return valor or self.defecto

    async def origen(self, pool: Any) -> str:
        """'configuracion' (fila guardada) o 'variables' (APP_ENV)."""
        await self.actual(pool)
        return self._origen

    async def resolver(self, pool: Any, pedido: str | None = None) -> str:
        """El ambiente pedido explícitamente (p. ej. el de la factura) o, si
        no hay, el activo. Lanza AmbienteInvalidoError si el pedido no existe."""
        if pedido:
            return validar_ambiente(pedido)
        return await self.actual(pool)

    async def dgii(self, pool: Any, pedido: str | None = None) -> DGIIEnvironment:
        return dgii_environment_for(await self.resolver(pool, pedido))


class TokenProviderPorAmbiente:
    """Un `TokenProvider` por ambiente DGII, creado al primer uso. Mismo
    certificado para todos (es el del emisor); lo que cambia es el servicio
    de Autenticación de la DGII y, con él, el token."""

    def __init__(self, certificado_path: str, certificado_password: str, margen_segundos: int = 60):
        self._certificado_path = certificado_path
        self._certificado_password = certificado_password
        self._margen_segundos = margen_segundos
        self._proveedores: dict[DGIIEnvironment, Any] = {}

    def para(self, ambiente: DGIIEnvironment):
        from libs.ecf_common.auth.token_provider import TokenProvider

        if ambiente not in self._proveedores:
            self._proveedores[ambiente] = TokenProvider(
                ambiente=ambiente,
                certificado_path=self._certificado_path,
                certificado_password=self._certificado_password,
                margen_segundos=self._margen_segundos,
            )
        return self._proveedores[ambiente]

    async def obtener_token(self, forzar_renovacion: bool = False, *, ambiente: DGIIEnvironment):
        return await self.para(ambiente).obtener_token(forzar_renovacion)
