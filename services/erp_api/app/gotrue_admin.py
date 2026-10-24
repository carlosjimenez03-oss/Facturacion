"""Cliente de la API admin de GoTrue (Supabase Auth) para la pantalla
Configuración → Usuarios: listar, crear, invitar y desactivar usuarios.

Llama al gateway de Supabase por la red interna (`SUPABASE_URL`, por
defecto http://api-gw:8000) con la `SUPABASE_SERVICE_ROLE_KEY` como
`apikey` y `Authorization`. Esa llave salta todas las reglas de Supabase:
solo vive en erp-api, nunca se manda al navegador.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from .config import ErpApiSettings

# "Desactivar" en GoTrue es banear: ~100 años; "none" lo levanta.
_BAN_INDEFINIDO = "876000h"


class GoTrueNoConfiguradoError(Exception):
    pass


class GoTrueError(Exception):
    def __init__(self, status: int, mensaje: str):
        super().__init__(mensaje)
        self.status = status


def configurado(settings: ErpApiSettings) -> bool:
    return bool(settings.supabase_url and settings.supabase_service_role_key)


def _cliente(settings: ErpApiSettings) -> httpx.AsyncClient:
    if not configurado(settings):
        raise GoTrueNoConfiguradoError("SUPABASE_SERVICE_ROLE_KEY no está configurado en erp-api")
    llave = settings.supabase_service_role_key
    return httpx.AsyncClient(
        base_url=f"{settings.supabase_url.rstrip('/')}/auth/v1",
        headers={"apikey": llave, "Authorization": f"Bearer {llave}"},
        timeout=settings.supabase_timeout_segundos,
    )


def _mensaje(respuesta: httpx.Response) -> str:
    try:
        cuerpo = respuesta.json()
    except ValueError:
        return respuesta.text or respuesta.reason_phrase
    if isinstance(cuerpo, dict):
        return str(cuerpo.get("msg") or cuerpo.get("error_description") or cuerpo.get("message") or cuerpo.get("error") or cuerpo)
    return str(cuerpo)


async def _llamar(settings: ErpApiSettings, metodo: str, ruta: str, **kwargs) -> Any:
    async with _cliente(settings) as cliente:
        try:
            respuesta = await cliente.request(metodo, ruta, **kwargs)
        except httpx.HTTPError as exc:
            raise GoTrueError(502, f"Supabase Auth no responde: {exc}") from exc
    if respuesta.status_code >= 400:
        raise GoTrueError(respuesta.status_code, _mensaje(respuesta))
    return respuesta.json() if respuesta.content else None


def _fecha(valor: str | None) -> datetime | None:
    if not valor:
        return None
    try:
        return datetime.fromisoformat(valor.replace("Z", "+00:00"))
    except ValueError:
        return None


def usuario_desde_gotrue(u: dict[str, Any], ahora: datetime | None = None) -> dict[str, Any]:
    ahora = ahora or datetime.now(timezone.utc)
    baneado_hasta = _fecha(u.get("banned_until"))
    return {
        "id": u["id"],
        "correo": u.get("email"),
        "creado_en": _fecha(u.get("created_at")),
        "ultimo_acceso": _fecha(u.get("last_sign_in_at")),
        "confirmado": bool(u.get("email_confirmed_at") or u.get("confirmed_at")),
        "activo": not (baneado_hasta and baneado_hasta > ahora),
        "invitado": bool(u.get("invited_at")) and not u.get("last_sign_in_at"),
    }


async def listar_usuarios(settings: ErpApiSettings) -> list[dict[str, Any]]:
    cuerpo = await _llamar(settings, "GET", "/admin/users", params={"page": 1, "per_page": 200})
    usuarios = cuerpo.get("users", []) if isinstance(cuerpo, dict) else cuerpo or []
    return sorted((usuario_desde_gotrue(u) for u in usuarios), key=lambda u: (u["correo"] or "").lower())


async def crear_usuario(settings: ErpApiSettings, correo: str, contrasena: str) -> dict[str, Any]:
    u = await _llamar(
        settings, "POST", "/admin/users", json={"email": correo, "password": contrasena, "email_confirm": True}
    )
    return usuario_desde_gotrue(u)


async def invitar_usuario(settings: ErpApiSettings, correo: str) -> dict[str, Any]:
    u = await _llamar(settings, "POST", "/invite", json={"email": correo})
    return usuario_desde_gotrue(u)


async def cambiar_activo(settings: ErpApiSettings, usuario_id: str, activo: bool) -> dict[str, Any]:
    u = await _llamar(
        settings,
        "PUT",
        f"/admin/users/{usuario_id}",
        json={"ban_duration": "none" if activo else _BAN_INDEFINIDO},
    )
    return usuario_desde_gotrue(u)


async def salud(settings: ErpApiSettings) -> dict[str, Any]:
    """GET /auth/v1/health (para "Estado de las soluciones")."""
    return await _llamar(settings, "GET", "/health")
