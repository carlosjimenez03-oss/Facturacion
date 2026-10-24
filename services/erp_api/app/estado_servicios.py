"""Estado de las soluciones: consulta el `/health` de cada servicio por la
red interna de Docker (timeouts cortos, todos a la vez), la base de datos y
Supabase Auth. Lo usa GET /estado-servicios (plataforma.py)."""
from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from . import gotrue_admin
from .config import ErpApiSettings

# id del servicio (nombre en docker-compose) -> (nombre legible, grupo).
SERVICIOS: dict[str, tuple[str, str]] = {
    "facturas-api": ("Facturación electrónica", "Aplicación"),
    "inventario-api": ("Inventario", "Aplicación"),
    "formato-606": ("Formato 606", "Aplicación"),
    "erp-api": ("ERP: ventas, compras, impuestos y configuración", "Aplicación"),
    "autenticacion": ("Autenticación DGII (semilla y token)", "Servicios DGII"),
    "recepcion": ("Recepción de e-CF", "Servicios DGII"),
    "recepcion-rfce": ("Recepción de resúmenes RFCE", "Servicios DGII"),
    "consulta-resultado": ("Consulta de resultado (trackId)", "Servicios DGII"),
    "consulta-estado": ("Consulta de estado de e-CF", "Servicios DGII"),
    "consulta-trackids": ("Consulta de trackIds", "Servicios DGII"),
    "consulta-rfce": ("Consulta de RFCE", "Servicios DGII"),
    "consulta-directorio": ("Directorio de receptores", "Servicios DGII"),
    "consulta-timbre": ("Consulta de timbre", "Servicios DGII"),
    "aprobacion-comercial": ("Aprobación comercial (envío)", "Servicios DGII"),
    "anulacion-encf": ("Anulación de e-NCF", "Servicios DGII"),
    "receptor-autenticacion": ("Receptor: autenticación", "Receptor electrónico"),
    "receptor-recepcion": ("Receptor: recepción de e-CF", "Receptor electrónico"),
    "receptor-aprobacion-comercial": ("Receptor: aprobación comercial", "Receptor electrónico"),
    "base-datos": ("Base de datos (Supabase Postgres)", "Plataforma"),
    "supabase-auth": ("Inicio de sesión (Supabase Auth)", "Plataforma"),
}


def _servicio(id_: str, estado: str, latencia_ms: int | None = None, detalle: str | None = None) -> dict[str, Any]:
    nombre, grupo = SERVICIOS.get(id_, (id_, "Otros"))
    return {"id": id_, "nombre": nombre, "grupo": grupo, "estado": estado, "latencia_ms": latencia_ms, "detalle": detalle}


def _ms(inicio: float) -> int:
    return int((time.perf_counter() - inicio) * 1000)


def _detalle_health(cuerpo: Any) -> str | None:
    if not isinstance(cuerpo, dict):
        return None
    partes = [str(cuerpo[k]) for k in ("app_env", "dgii_environment") if cuerpo.get(k)]
    return " · ".join(partes) or None


async def consultar_servicio(cliente: httpx.AsyncClient, id_: str, url: str, timeout: float) -> dict[str, Any]:
    inicio = time.perf_counter()
    try:
        respuesta = await cliente.get(f"{url.rstrip('/')}/health", timeout=timeout)
    except httpx.TimeoutException:
        return _servicio(id_, "caido", _ms(inicio), f"No respondió en {timeout:g} s")
    except httpx.HTTPError as exc:
        return _servicio(id_, "caido", None, f"No se pudo conectar ({type(exc).__name__})")
    if respuesta.status_code != 200:
        return _servicio(id_, "error", _ms(inicio), f"Respondió HTTP {respuesta.status_code}")
    try:
        detalle = _detalle_health(respuesta.json())
    except ValueError:
        detalle = None
    return _servicio(id_, "ok", _ms(inicio), detalle)


async def consultar_base_datos(pool, timeout: float) -> dict[str, Any]:
    if pool is None:
        return _servicio("base-datos", "caido", None, "erp-api no tiene conexión a la base")
    inicio = time.perf_counter()
    try:
        async with pool.acquire(timeout=timeout) as conn:
            await conn.fetchval("SELECT 1", timeout=timeout)
    except Exception as exc:  # cualquier fallo de conexión cuenta como caída
        return _servicio("base-datos", "caido", None, f"{type(exc).__name__}: {exc}"[:200])
    return _servicio("base-datos", "ok", _ms(inicio))


async def consultar_supabase_auth(settings: ErpApiSettings) -> dict[str, Any]:
    if not gotrue_admin.configurado(settings):
        return _servicio("supabase-auth", "no_configurado", None, "Falta SUPABASE_SERVICE_ROLE_KEY en erp-api")
    inicio = time.perf_counter()
    try:
        cuerpo = await gotrue_admin.salud(settings)
    except gotrue_admin.GoTrueError as exc:
        return _servicio("supabase-auth", "caido" if exc.status == 502 else "error", _ms(inicio), str(exc)[:200])
    version = cuerpo.get("version") if isinstance(cuerpo, dict) else None
    return _servicio("supabase-auth", "ok", _ms(inicio), f"GoTrue {version}" if version else None)


async def estado_de_servicios(settings: ErpApiSettings, pool) -> list[dict[str, Any]]:
    timeout = settings.estado_servicios_timeout_segundos
    async with httpx.AsyncClient() as cliente:
        consultas = [
            consultar_servicio(cliente, id_, url, timeout) for id_, url in settings.estado_servicios.items()
        ]
        resultados = await asyncio.gather(
            *consultas, consultar_base_datos(pool, timeout), consultar_supabase_auth(settings)
        )
    # erp-api responde esta misma consulta: está en línea.
    propio = _servicio("erp-api", "ok", 0, settings.app_env)
    servicios = [propio, *resultados]
    orden = list(SERVICIOS)
    return sorted(servicios, key=lambda s: orden.index(s["id"]) if s["id"] in orden else len(orden))
