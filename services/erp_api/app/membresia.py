"""Miembros de cada empresa (multiempresa, docs/MULTIEMPRESA.md):
`configuracion.empresa_usuarios`. Lo usa Configuración → Usuarios
(configuracion.py) para listar, invitar, cambiar el rol y quitar usuarios de
la empresa en curso.

Un usuario sin ninguna fila es miembro implícito de la empresa 1 (los de
antes de la multiempresa, con el rol de su marca global `kontara_admin`):
antes de darle su primera membresía en otra empresa se escribe la implícita,
para que no deje de entrar a la 1.
"""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from libs.ecf_common.empresa import EMPRESA_PREDETERMINADA, empresa_actual, invalidar_cache

IMPLICITO = "implicito"


async def roles_de_la_empresa(pool, usuario_ids: list[str]) -> dict[str, str | None]:
    """{usuario: 'admin' | 'usuario' | 'implicito' | None} en la empresa en
    curso ('implicito' = sin membresías, miembro de la 1 por compatibilidad)."""
    if not usuario_ids:
        return {}
    empresa_id = empresa_actual()
    async with pool.acquire() as conn:
        filas = await conn.fetch(
            """
            SELECT usuario_id, rol FROM configuracion.empresa_usuarios
            WHERE empresa_id = $1 AND usuario_id = ANY($2::text[])
            """,
            empresa_id,
            usuario_ids,
        )
        con_filas = set()
        if empresa_id == EMPRESA_PREDETERMINADA:
            con_filas = {
                f["usuario_id"]
                for f in await conn.fetch(
                    "SELECT DISTINCT usuario_id FROM configuracion.empresa_usuarios WHERE usuario_id = ANY($1::text[])",
                    usuario_ids,
                )
            }
    roles: dict[str, str | None] = dict.fromkeys(usuario_ids)
    if empresa_id == EMPRESA_PREDETERMINADA:
        roles.update({u: IMPLICITO for u in usuario_ids if u not in con_filas})
    roles.update({f["usuario_id"]: f["rol"] for f in filas})
    return roles


async def _materializar_implicita(conn, usuario: dict[str, Any]) -> None:
    if await conn.fetchval(
        "SELECT 1 FROM configuracion.empresa_usuarios WHERE usuario_id = $1 LIMIT 1", usuario["id"]
    ):
        return
    await conn.execute(
        """
        INSERT INTO configuracion.empresa_usuarios (empresa_id, usuario_id, correo, rol)
        VALUES (1, $1, $2, $3) ON CONFLICT DO NOTHING
        """,
        usuario["id"],
        usuario.get("correo"),
        "admin" if usuario.get("admin") else "usuario",
    )


async def agregar(pool, usuario: dict[str, Any], rol: str, *, agregado_por: str | None, nuevo: bool = False) -> None:
    """Miembro de la empresa en curso con `rol` (o cambia su rol). `nuevo`:
    se acaba de crear en Supabase Auth (no era miembro implícito de nada)."""
    empresa_id = empresa_actual()
    async with pool.acquire() as conn:
        async with conn.transaction():
            if not nuevo and empresa_id != EMPRESA_PREDETERMINADA:
                await _materializar_implicita(conn, usuario)
            await conn.execute(
                """
                INSERT INTO configuracion.empresa_usuarios (empresa_id, usuario_id, correo, rol, agregado_por)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (empresa_id, usuario_id) DO UPDATE SET rol = EXCLUDED.rol, correo = EXCLUDED.correo
                """,
                empresa_id,
                usuario["id"],
                usuario.get("correo"),
                rol,
                agregado_por,
            )
    invalidar_cache()


async def quitar(pool, usuario: dict[str, Any]) -> None:
    """Lo saca de la empresa en curso. Si es la única empresa a la que entra,
    no: eso es desactivarlo (409)."""
    empresa_id = empresa_actual()
    async with pool.acquire() as conn:
        async with conn.transaction():
            otras = await conn.fetchval(
                """
                SELECT count(*) FROM configuracion.empresa_usuarios
                WHERE usuario_id = $1 AND empresa_id <> $2
                """,
                usuario["id"],
                empresa_id,
            )
            if not otras:
                raise HTTPException(
                    status_code=409,
                    detail="Es la única empresa de este usuario: para quitarle el acceso, desactívelo",
                )
            await conn.execute(
                "DELETE FROM configuracion.empresa_usuarios WHERE empresa_id = $1 AND usuario_id = $2",
                empresa_id,
                usuario["id"],
            )
    invalidar_cache()
