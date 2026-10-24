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

**Por empresa (multiempresa, docs/MULTIEMPRESA.md).** La fila de
`configuracion.ambiente_dgii` es de cada empresa (RLS: la consulta solo ve
la de la empresa del request), así que la caché se guarda por empresa, y
el token de la DGII por (empresa, ambiente): cada empresa se autentica con
su propio certificado.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from libs.ecf_common.empresa import empresa_actual
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
    """Resuelve el ambiente activo de un servicio para la empresa en curso:
    el de Configuración (con caché por empresa) o, si no hay, `defecto` (su
    APP_ENV)."""

    def __init__(self, defecto: str, *, ttl_segundos: float = TTL_SEGUNDOS, reloj=time.monotonic):
        self.defecto = validar_ambiente(defecto)
        self.ttl = ttl_segundos
        self._reloj = reloj
        # empresa -> (valor guardado o None, origen, vence)
        self._cache: dict[int, tuple[str | None, str, float]] = {}

    def invalidar(self) -> None:
        self._cache.clear()

    def fijar(self, ambiente: str) -> None:
        """Lo que se acaba de guardar (erp-api), sin esperar a la caché."""
        self._cache[empresa_actual()] = (validar_ambiente(ambiente), "configuracion", self._reloj() + self.ttl)

    async def actual(self, pool: Any) -> str:
        empresa = empresa_actual()
        en_cache = self._cache.get(empresa)
        if en_cache is not None and self._reloj() < en_cache[2]:
            return en_cache[0] or self.defecto
        valor: str | None = None
        if pool is not None:
            try:
                valor = await leer_ambiente_guardado(pool)
            except Exception as exc:  # base caída, sin permiso...: se usa APP_ENV
                logger.warning("No se pudo leer el ambiente DGII de Configuración (%s): se usa APP_ENV", exc)
        self._cache[empresa] = (valor, "configuracion" if valor else "variables", self._reloj() + self.ttl)
        return valor or self.defecto

    async def origen(self, pool: Any) -> str:
        """'configuracion' (fila guardada) o 'variables' (APP_ENV)."""
        await self.actual(pool)
        return self._cache[empresa_actual()][1]

    async def resolver(self, pool: Any, pedido: str | None = None) -> str:
        """El ambiente pedido explícitamente (p. ej. el de la factura) o, si
        no hay, el activo. Lanza AmbienteInvalidoError si el pedido no existe."""
        if pedido:
            return validar_ambiente(pedido)
        return await self.actual(pool)

    async def dgii(self, pool: Any, pedido: str | None = None) -> DGIIEnvironment:
        return dgii_environment_for(await self.resolver(pool, pedido))


class TokenProviderPorAmbiente:
    """Un `TokenProvider` por (empresa, ambiente DGII), creado al primer
    uso. Cada empresa firma la semilla con su certificado (el que carga
    `signing.cargar_certificado_p12` para la empresa en curso); por ambiente
    cambia el servicio de Autenticación de la DGII y, con él, el token."""

    def __init__(self, certificado_path: str, certificado_password: str, margen_segundos: int = 60):
        self._certificado_path = certificado_path
        self._certificado_password = certificado_password
        self._margen_segundos = margen_segundos
        self._proveedores: dict[tuple[int, DGIIEnvironment], Any] = {}

    def para(self, ambiente: DGIIEnvironment):
        from libs.ecf_common.auth.token_provider import TokenProvider

        clave = (empresa_actual(), ambiente)
        if clave not in self._proveedores:
            self._proveedores[clave] = TokenProvider(
                ambiente=ambiente,
                certificado_path=self._certificado_path,
                certificado_password=self._certificado_password,
                margen_segundos=self._margen_segundos,
            )
        return self._proveedores[clave]

    async def obtener_token(self, forzar_renovacion: bool = False, *, ambiente: DGIIEnvironment):
        return await self.para(ambiente).obtener_token(forzar_renovacion)
