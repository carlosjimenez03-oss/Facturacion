"""Miembros de cada empresa (multiempresa, docs/MULTIEMPRESA.md):
`configuracion.empresa_usuarios`. Lo usan Configuración → Usuarios
(configuracion.py) y Mis empresas (empresas.py).

Reglas de seguridad entre empresas:

- Las filas no se borran: quitar a alguien lo deja 'retirado'. Un usuario
  sin NINGUNA fila solo puede ser uno de antes de la multiempresa (miembro
  implícito de la empresa 1, ver libs/ecf_common/empresa.py); todo usuario
  que crea o agrega un administrador recibe su fila ANTES de tener acceso a
  Kontara, así que nunca cae en esa regla.
- Un usuario que ya es de Kontara (de otra empresa) no se agrega en
  silencio: queda 'invitado' y entra cuando acepta en Mis empresas. Hasta
  entonces la empresa que invita no puede activarlo ni desactivarlo.
- Desactivar es por empresa (estado 'inactivo'). Solo si el usuario no es de
  ninguna otra empresa se desactiva también en Supabase Auth (cierra sus
  sesiones); activar levanta ese bloqueo solo en ese mismo caso.
"""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from libs.ecf_common.empresa import EMPRESA_PREDETERMINADA, empresa_actual, invalidar_cache

IMPLICITO = "implicito"
VIGENTES = ("activo", "inactivo", "invitado")


async def membresias_de_la_empresa(pool, usuario_ids: list[str]) -> dict[str, dict[str, str] | None]:
    """{usuario: {"rol", "estado"} | None} en la empresa en curso. Un
    usuario de antes sin filas, en la empresa 1: {"rol": "implicito",
    "estado": "activo"}. Los retirados cuentan como no miembros."""
    if not usuario_ids:
        return {}
    empresa_id = empresa_actual()
    async with pool.acquire() as conn:
        filas = await conn.fetch(
            """
            SELECT usuario_id, rol, estado FROM configuracion.empresa_usuarios
            WHERE empresa_id = $1 AND usuario_id = ANY($2::text[]) AND estado <> 'retirado'
            """,
            empresa_id,
            usuario_ids,
        )
        con_filas: set[str] = set()
        if empresa_id == EMPRESA_PREDETERMINADA:
            con_filas = {
                f["usuario_id"]
                for f in await conn.fetch(
                    "SELECT DISTINCT usuario_id FROM configuracion.empresa_usuarios WHERE usuario_id = ANY($1::text[])",
                    usuario_ids,
                )
            }
    resultado: dict[str, dict[str, str] | None] = dict.fromkeys(usuario_ids)
    if empresa_id == EMPRESA_PREDETERMINADA:
        resultado.update({u: {"rol": IMPLICITO, "estado": "activo"} for u in usuario_ids if u not in con_filas})
    resultado.update({f["usuario_id"]: {"rol": f["rol"], "estado": f["estado"]} for f in filas})
    return resultado


async def _registrar_previo(conn, usuario: dict[str, Any]) -> None:
    """La fila en la empresa 1 de un usuario de antes (que YA tenía acceso a
    Kontara y ninguna fila): conserva su empresa al recibir otra membresía."""
    await conn.execute(
        """
        INSERT INTO configuracion.empresa_usuarios (empresa_id, usuario_id, correo, rol, agregado_por)
        SELECT 1, $1, $2,
               CASE WHEN $3 OR COALESCE((SELECT previos_admin FROM configuracion.multiempresa_legado WHERE id = 1), false)
                    THEN 'admin' ELSE 'usuario' END,
               'usuario previo'
        WHERE NOT EXISTS (SELECT 1 FROM configuracion.empresa_usuarios WHERE usuario_id = $1)
        ON CONFLICT DO NOTHING
        """,
        usuario["id"],
        usuario.get("correo"),
        bool(usuario.get("admin")),
    )


async def agregar(
    pool,
    usuario: dict[str, Any],
    rol: str,
    *,
    estado: str = "activo",
    agregado_por: str | None,
    tenia_acceso: bool,
) -> None:
    """Fila del usuario en la empresa en curso con `rol` y `estado` (o los
    cambia). `tenia_acceso`: el usuario ya era de Kontara ANTES de esta
    acción (solo entonces puede ser un usuario de antes, miembro implícito de
    la empresa 1, cuya fila se escribe primero)."""
    empresa_id = empresa_actual()
    async with pool.acquire() as conn:
        async with conn.transaction():
            if tenia_acceso and empresa_id != EMPRESA_PREDETERMINADA:
                await _registrar_previo(conn, usuario)
            await conn.execute(
                """
                INSERT INTO configuracion.empresa_usuarios (empresa_id, usuario_id, correo, rol, estado, agregado_por)
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (empresa_id, usuario_id) DO UPDATE
                    SET rol = EXCLUDED.rol, correo = EXCLUDED.correo, estado = EXCLUDED.estado, actualizado_en = now()
                """,
                empresa_id,
                usuario["id"],
                usuario.get("correo"),
                rol,
                estado,
                agregado_por,
            )
    invalidar_cache()


async def cambiar_rol(pool, usuario: dict[str, Any], rol: str) -> None:
    empresa_id = empresa_actual()
    async with pool.acquire() as conn:
        async with conn.transaction():
            if empresa_id == EMPRESA_PREDETERMINADA:
                await _registrar_previo(conn, usuario)
            await conn.execute(
                "UPDATE configuracion.empresa_usuarios SET rol = $3, actualizado_en = now() "
                "WHERE empresa_id = $1 AND usuario_id = $2",
                empresa_id,
                usuario["id"],
                rol,
            )
    invalidar_cache()


async def cambiar_estado(pool, usuario: dict[str, Any], estado: str) -> bool:
    """Activa / desactiva al usuario EN LA EMPRESA EN CURSO. Devuelve si el
    usuario no es de ninguna otra empresa (entonces también se activa /
    desactiva en Supabase Auth)."""
    empresa_id = empresa_actual()
    async with pool.acquire() as conn:
        async with conn.transaction():
            if empresa_id == EMPRESA_PREDETERMINADA:
                await _registrar_previo(conn, usuario)
            await conn.execute(
                "UPDATE configuracion.empresa_usuarios SET estado = $3, actualizado_en = now() "
                "WHERE empresa_id = $1 AND usuario_id = $2",
                empresa_id,
                usuario["id"],
                estado,
            )
            otras = await conn.fetchval(
                "SELECT count(*) FROM configuracion.empresa_usuarios "
                "WHERE usuario_id = $1 AND empresa_id <> $2 AND estado = ANY($3::text[])",
                usuario["id"],
                empresa_id,
                list(VIGENTES),
            )
    invalidar_cache()
    return not otras


async def quitar(pool, usuario: dict[str, Any]) -> None:
    """Lo saca de la empresa en curso (la fila queda 'retirado'). Si es la
    única empresa a la que entra, no: eso es desactivarlo (409). Una
    invitación pendiente sí se puede retirar."""
    empresa_id = empresa_actual()
    async with pool.acquire() as conn:
        async with conn.transaction():
            if empresa_id == EMPRESA_PREDETERMINADA:
                await _registrar_previo(conn, usuario)
            propia = await conn.fetchval(
                "SELECT estado FROM configuracion.empresa_usuarios WHERE empresa_id = $1 AND usuario_id = $2",
                empresa_id,
                usuario["id"],
            )
            otras = await conn.fetchval(
                "SELECT count(*) FROM configuracion.empresa_usuarios "
                "WHERE usuario_id = $1 AND empresa_id <> $2 AND estado = ANY($3::text[])",
                usuario["id"],
                empresa_id,
                list(VIGENTES),
            )
            if propia != "invitado" and not otras:
                raise HTTPException(
                    status_code=409,
                    detail="Es la única empresa de este usuario: para quitarle el acceso, desactívelo",
                )
            await conn.execute(
                "UPDATE configuracion.empresa_usuarios SET estado = 'retirado', actualizado_en = now() "
                "WHERE empresa_id = $1 AND usuario_id = $2",
                empresa_id,
                usuario["id"],
            )
    invalidar_cache()


async def responder_invitacion(pool, usuario_id: str, empresa_id: int, aceptar: bool) -> bool:
    """El invitado acepta (pasa a 'activo') o rechaza ('retirado')."""
    async with pool.acquire() as conn:
        resultado = await conn.execute(
            "UPDATE configuracion.empresa_usuarios SET estado = $3, actualizado_en = now() "
            "WHERE empresa_id = $1 AND usuario_id = $2 AND estado = 'invitado'",
            empresa_id,
            usuario_id,
            "activo" if aceptar else "retirado",
        )
    invalidar_cache()
    return resultado.endswith(" 1")


async def invitaciones(pool, usuario_id: str) -> list[dict[str, Any]]:
    async with pool.acquire() as conn:
        filas = await conn.fetch(
            """
            SELECT e.id, e.rnc, e.razon_social, e.nombre_comercial, eu.rol, eu.agregado_por
            FROM configuracion.empresa_usuarios eu JOIN configuracion.empresas e ON e.id = eu.empresa_id
            WHERE eu.usuario_id = $1 AND eu.estado = 'invitado' AND e.estado = 'activa'
            ORDER BY e.id
            """,
            usuario_id,
        )
    return [dict(f) for f in filas]
