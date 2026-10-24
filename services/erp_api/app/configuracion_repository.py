"""Acceso a Postgres del esquema `configuracion` (empresa, secuencias e-NCF
y vendedores) y el cálculo del estado de cada secuencia.

El siguiente e-NCF de cada tipo NO se reserva aquí: lo asigna facturas_api
al crear la factura, con la fila de la secuencia bloqueada
(services/facturas_api/app/secuencias_encf.py, misma regla). Lo que hace
este módulo es mostrarlo -- el mayor entre `desde`, `proximo` y el último
e-NCF ya guardado en `facturacion.facturas` (que solo se LEE) + 1 -- y
avisar cuando la secuencia está agotada, vencida o por agotarse/vencer.
"""
from __future__ import annotations

import re
import uuid
from datetime import date
from typing import Any

import asyncpg

from libs.ecf_common.empresa import EMPRESA_PREDETERMINADA, empresa_actual, invalidar_cache

from .repository import DatosInvalidosError

NOMBRES_TIPO = {
    31: "Crédito fiscal",
    32: "Consumo",
    33: "Nota de débito",
    34: "Nota de crédito",
    41: "Compras",
    43: "Gastos menores",
    44: "Regímenes especiales",
    45: "Gubernamental",
    46: "Exportaciones",
    47: "Pagos al exterior",
}

# Avisos: quedan pocas (<= 10 o >= 90 % usado) o vence en <= 30 días.
_POCAS_DISPONIBLES = 10
_PORCENTAJE_POR_AGOTARSE = 0.9
_DIAS_POR_VENCER = 30

# Logo: data URL de imagen, hasta ~256 KB de archivo (base64 crece 4/3).
_LOGO_RE = re.compile(r"^data:image/(png|jpeg|webp|svg\+xml);base64,[A-Za-z0-9+/=]+$")
_LOGO_MAX_CARACTERES = 350_000
_CORREO_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class SecuenciaNoEncontradaError(Exception):
    pass


class VendedorNoEncontradoError(Exception):
    pass


class VendedorEnUsoError(Exception):
    pass


def prefijo_de(tipo_ecf: int) -> str:
    return f"E{tipo_ecf}"


def encf_de(tipo_ecf: int, numero: int) -> str:
    return f"{prefijo_de(tipo_ecf)}{numero:010d}"


def _validar_tipo(tipo_ecf: int) -> None:
    if tipo_ecf not in NOMBRES_TIPO:
        raise DatosInvalidosError(f"tipo_ecf debe ser uno de {sorted(NOMBRES_TIPO)}")


def _texto(valor: str | None) -> str | None:
    valor = (valor or "").strip()
    return valor or None


def _validar_correo(correo: str | None) -> str | None:
    correo = _texto(correo)
    if correo and not _CORREO_RE.match(correo):
        raise DatosInvalidosError("El correo no tiene un formato válido")
    return correo


# --- Empresa ---------------------------------------------------------

# Desde la multiempresa los datos de cada empresa viven en el registro
# `configuracion.empresas` (ver libs/ecf_common/empresa.py); la fila única
# de `configuracion.empresa` se sigue escribiendo para la empresa 1, para
# poder volver a una versión anterior sin perder lo guardado.
_COLUMNAS_EMPRESA = (
    "razon_social, nombre_comercial, rnc, direccion, telefono, correo, logo, actividad_economica, provincia, municipio"
)


class RncEnUsoError(Exception):
    """Otra empresa de la instalación ya tiene ese RNC."""


def validar_datos_empresa(
    *,
    razon_social: str,
    rnc: str | None,
    correo: str | None,
    logo: str | None,
) -> tuple[str, str | None, str | None, str | None]:
    """(razón social, RNC solo dígitos, correo, logo) validados."""
    razon = (razon_social or "").strip()
    if not razon:
        raise DatosInvalidosError("La razón social es obligatoria")
    rnc_limpio = re.sub(r"\D", "", rnc or "") or None
    if rnc_limpio and len(rnc_limpio) not in (9, 11):
        raise DatosInvalidosError("El RNC debe tener 9 dígitos (o 11 si es una cédula)")
    logo = _texto(logo)
    if logo and (len(logo) > _LOGO_MAX_CARACTERES or not _LOGO_RE.match(logo)):
        raise DatosInvalidosError("El logo debe ser una imagen PNG, JPEG, WebP o SVG de 256 KB como máximo")
    return razon, rnc_limpio, _validar_correo(correo), logo


async def obtener_empresa(pool: asyncpg.Pool) -> dict[str, Any] | None:
    """Los datos guardados de la empresa en curso, o None si todavía no se
    guardaron (la empresa 1 recién creada desde EMISOR_RNC)."""
    async with pool.acquire() as conn:
        fila = await conn.fetchrow(
            "SELECT * FROM configuracion.empresas WHERE id = $1 AND configurada", empresa_actual()
        )
    return dict(fila) if fila else None


async def guardar_empresa(
    pool: asyncpg.Pool,
    *,
    razon_social: str,
    nombre_comercial: str | None,
    rnc: str | None,
    direccion: str | None,
    telefono: str | None,
    correo: str | None,
    logo: str | None,
    actividad_economica: str | None = None,
    provincia: str | None = None,
    municipio: str | None = None,
) -> dict[str, Any]:
    razon, rnc_limpio, correo, logo = validar_datos_empresa(
        razon_social=razon_social, rnc=rnc, correo=correo, logo=logo
    )
    valores = (
        razon,
        _texto(nombre_comercial),
        rnc_limpio,
        _texto(direccion),
        _texto(telefono),
        correo,
        logo,
        _texto(actividad_economica),
        _texto(provincia),
        _texto(municipio),
    )
    empresa_id = empresa_actual()
    async with pool.acquire() as conn:
        async with conn.transaction():
            try:
                fila = await conn.fetchrow(
                    f"""
                    UPDATE configuracion.empresas SET
                        ({_COLUMNAS_EMPRESA}, configurada, actualizado_en)
                        = ($2, $3, $4, $5, $6, $7, $8, $9, $10, $11, true, now())
                    WHERE id = $1
                    RETURNING *
                    """,
                    empresa_id,
                    *valores,
                )
            except asyncpg.UniqueViolationError as exc:
                raise RncEnUsoError("Otra empresa de esta instalación ya tiene ese RNC") from exc
            if fila is None:
                raise DatosInvalidosError("La empresa no existe")
            if empresa_id == EMPRESA_PREDETERMINADA and await conn.fetchval(
                "SELECT to_regclass('configuracion.empresa')"
            ):
                await conn.execute(
                    f"""
                    INSERT INTO configuracion.empresa (id, {_COLUMNAS_EMPRESA})
                    VALUES (1, $1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                    ON CONFLICT (id) DO UPDATE SET ({_COLUMNAS_EMPRESA}, actualizado_en)
                        = (EXCLUDED.razon_social, EXCLUDED.nombre_comercial, EXCLUDED.rnc, EXCLUDED.direccion,
                           EXCLUDED.telefono, EXCLUDED.correo, EXCLUDED.logo, EXCLUDED.actividad_economica,
                           EXCLUDED.provincia, EXCLUDED.municipio, now())
                    """,
                    *valores,
                )
    invalidar_cache()
    return dict(fila)


# --- Secuencias e-NCF ---------------------------------------------------------

def calcular_secuencia(
    tipo_ecf: int,
    fila: dict[str, Any] | None,
    ultimo_usado: int | None,
    hoy: date,
    cantidad_usados: int | None = None,
) -> dict[str, Any]:
    """Une la secuencia configurada (o None) con el mayor e-NCF ya usado de
    ese tipo y devuelve el siguiente número, cuántos quedan y su estado.

    `usados` es cuántos e-NCF del rango ya tienen factura (`cantidad_usados`,
    contados en facturacion.facturas); `disponibles` es lo que queda desde el
    siguiente hasta el final del rango (los números saltados no vuelven)."""
    base: dict[str, Any] = {
        "tipo_ecf": tipo_ecf,
        "nombre": NOMBRES_TIPO.get(tipo_ecf, f"Tipo {tipo_ecf}"),
        "prefijo": prefijo_de(tipo_ecf),
        "ultimo_usado": ultimo_usado,
    }
    if fila is None:
        siguiente = (ultimo_usado or 0) + 1
        return {
            **base,
            "configurada": False,
            "activa": False,
            "siguiente": siguiente,
            "siguiente_encf": encf_de(tipo_ecf, siguiente),
            "estado": "sin_configurar",
            "avisos": ["Registre en Configuración el rango autorizado por la DGII para este tipo."],
        }

    desde, hasta, proximo = int(fila["desde"]), int(fila["hasta"]), int(fila["proximo"])
    vence: date | None = fila.get("fecha_vencimiento")
    siguiente = max(desde, proximo, (ultimo_usado or 0) + 1)
    total = hasta - desde + 1
    disponibles = max(0, hasta - siguiente + 1)
    consumidos = min(total, siguiente - desde)
    usados = consumidos if cantidad_usados is None else cantidad_usados
    avisos: list[str] = []
    estado = "vigente"

    vencida = vence is not None and vence < hoy
    agotada = disponibles == 0
    dias = (vence - hoy).days if vence is not None else None
    por_vencer = dias is not None and 0 <= dias <= _DIAS_POR_VENCER
    por_agotarse = not agotada and (disponibles <= _POCAS_DISPONIBLES or consumidos >= total * _PORCENTAJE_POR_AGOTARSE)

    if vencida:
        avisos.append(f"La secuencia venció el {vence:%d/%m/%Y}: solicite una nueva en la Oficina Virtual de la DGII.")
    if agotada:
        avisos.append(f"La secuencia está agotada (autorizada hasta {encf_de(tipo_ecf, hasta)}).")
    elif por_agotarse:
        avisos.append(f"Quedan {disponibles:,} e-NCF disponibles de {total:,}.")
    if por_vencer and not vencida:
        avisos.append(f"La secuencia vence en {dias} día{'s' if dias != 1 else ''} ({vence:%d/%m/%Y}).")

    if not fila["activa"]:
        estado = "inactiva"
        avisos.insert(0, "La secuencia está desactivada.")
    elif vencida:
        estado = "vencida"
    elif agotada:
        estado = "agotada"
    elif por_vencer:
        estado = "por_vencer"
    elif por_agotarse:
        estado = "por_agotarse"

    return {
        **base,
        "configurada": True,
        "desde": desde,
        "hasta": hasta,
        "proximo": proximo,
        "fecha_vencimiento": vence,
        "activa": fila["activa"],
        "siguiente": siguiente,
        "siguiente_encf": encf_de(tipo_ecf, siguiente) if not agotada else None,
        "usados": usados,
        "disponibles": disponibles,
        "consumo_pct": round(consumidos * 100 / total, 1),
        "estado": estado,
        "avisos": avisos,
        "actualizado_en": fila.get("actualizado_en"),
    }


async def ultimos_encf_usados(conn: asyncpg.Connection, ambiente: str) -> dict[int, int]:
    """Mayor número de e-NCF guardado en facturacion.facturas del ambiente, por tipo."""
    try:
        filas = await conn.fetch(
            """
            SELECT substr(encf, 2, 2)::int AS tipo, max(substr(encf, 4)::bigint) AS mayor
            FROM facturacion.facturas
            WHERE encf ~ '^E[0-9]{12}$' AND ambiente = $1
            GROUP BY 1
            """,
            ambiente,
        )
    except asyncpg.UndefinedTableError:
        return {}
    return {int(f["tipo"]): int(f["mayor"]) for f in filas}


async def cantidad_usados_en_rango(conn: asyncpg.Connection, ambiente: str) -> dict[int, int]:
    """Cuántos e-NCF de cada secuencia configurada del ambiente ya tienen factura."""
    try:
        filas = await conn.fetch(
            """
            SELECT s.tipo_ecf, count(DISTINCT f.encf) AS cantidad
            FROM configuracion.secuencias s
            JOIN facturacion.facturas f
              ON f.encf ~ '^E[0-9]{12}$'
             AND f.ambiente = s.ambiente
             AND substr(f.encf, 2, 2)::int = s.tipo_ecf
             AND substr(f.encf, 4)::bigint BETWEEN s.desde AND s.hasta
            WHERE s.ambiente = $1
            GROUP BY s.tipo_ecf
            """,
            ambiente,
        )
    except asyncpg.UndefinedTableError:
        return {}
    return {int(f["tipo_ecf"]): int(f["cantidad"]) for f in filas}


async def listar_secuencias(pool: asyncpg.Pool, hoy: date, ambiente: str) -> list[dict[str, Any]]:
    """Las secuencias del `ambiente` DGII (son distintas en TesteCF, CerteCF y eCF)."""
    async with pool.acquire() as conn:
        filas = {
            int(f["tipo_ecf"]): dict(f)
            for f in await conn.fetch("SELECT * FROM configuracion.secuencias WHERE ambiente = $1", ambiente)
        }
        usados = await ultimos_encf_usados(conn, ambiente)
        cantidades = await cantidad_usados_en_rango(conn, ambiente) if filas else {}
    return [
        calcular_secuencia(tipo, filas.get(tipo), usados.get(tipo), hoy, cantidades.get(tipo, 0) if tipo in filas else None)
        for tipo in NOMBRES_TIPO
    ]


async def obtener_secuencia(pool: asyncpg.Pool, tipo_ecf: int, hoy: date, ambiente: str) -> dict[str, Any]:
    _validar_tipo(tipo_ecf)
    async with pool.acquire() as conn:
        fila = await conn.fetchrow(
            "SELECT * FROM configuracion.secuencias WHERE tipo_ecf = $1 AND ambiente = $2", tipo_ecf, ambiente
        )
        usados = await ultimos_encf_usados(conn, ambiente)
        cantidades = await cantidad_usados_en_rango(conn, ambiente) if fila else {}
    return calcular_secuencia(
        tipo_ecf, dict(fila) if fila else None, usados.get(tipo_ecf), hoy, cantidades.get(tipo_ecf, 0) if fila else None
    )


async def guardar_secuencia(
    pool: asyncpg.Pool,
    tipo_ecf: int,
    *,
    desde: int,
    hasta: int,
    proximo: int | None,
    fecha_vencimiento: date | None,
    activa: bool,
    hoy: date,
    ambiente: str,
) -> dict[str, Any]:
    _validar_tipo(tipo_ecf)
    if hasta < desde:
        raise DatosInvalidosError("El final del rango (hasta) no puede ser menor que el inicio (desde)")
    proximo = desde if proximo is None else proximo
    if not desde <= proximo <= hasta + 1:
        raise DatosInvalidosError("El próximo número debe estar dentro del rango autorizado")
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO configuracion.secuencias
                (tipo_ecf, prefijo, desde, hasta, proximo, fecha_vencimiento, activa, ambiente)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            ON CONFLICT (empresa_id, ambiente, tipo_ecf) DO UPDATE SET
                desde = EXCLUDED.desde,
                hasta = EXCLUDED.hasta,
                proximo = EXCLUDED.proximo,
                fecha_vencimiento = EXCLUDED.fecha_vencimiento,
                activa = EXCLUDED.activa,
                actualizado_en = now()
            """,
            tipo_ecf,
            prefijo_de(tipo_ecf),
            desde,
            hasta,
            proximo,
            fecha_vencimiento,
            activa,
            ambiente,
        )
    return await obtener_secuencia(pool, tipo_ecf, hoy, ambiente)


async def eliminar_secuencia(pool: asyncpg.Pool, tipo_ecf: int, ambiente: str) -> None:
    _validar_tipo(tipo_ecf)
    async with pool.acquire() as conn:
        resultado = await conn.execute(
            "DELETE FROM configuracion.secuencias WHERE tipo_ecf = $1 AND ambiente = $2", tipo_ecf, ambiente
        )
    if resultado == "DELETE 0":
        raise SecuenciaNoEncontradaError(str(tipo_ecf))


# --- Vendedores ---------------------------------------------------------

async def listar_vendedores(pool: asyncpg.Pool, *, activo: bool | None = None) -> list[dict[str, Any]]:
    async with pool.acquire() as conn:
        if activo is None:
            filas = await conn.fetch("SELECT * FROM configuracion.vendedores ORDER BY nombre")
        else:
            filas = await conn.fetch(
                "SELECT * FROM configuracion.vendedores WHERE activo = $1 ORDER BY nombre", activo
            )
    return [dict(f) for f in filas]


async def crear_vendedor(pool: asyncpg.Pool, *, nombre: str, correo: str | None, comision_pct, activo: bool) -> dict:
    async with pool.acquire() as conn:
        fila = await conn.fetchrow(
            """
            INSERT INTO configuracion.vendedores (nombre, correo, comision_pct, activo)
            VALUES ($1, $2, $3, $4)
            RETURNING *
            """,
            nombre.strip(),
            _validar_correo(correo),
            comision_pct,
            activo,
        )
    return dict(fila)


async def actualizar_vendedor(
    pool: asyncpg.Pool, vendedor_id: uuid.UUID, *, nombre: str, correo: str | None, comision_pct, activo: bool
) -> dict:
    async with pool.acquire() as conn:
        fila = await conn.fetchrow(
            """
            UPDATE configuracion.vendedores
            SET nombre = $2, correo = $3, comision_pct = $4, activo = $5, actualizado_en = now()
            WHERE id = $1
            RETURNING *
            """,
            vendedor_id,
            nombre.strip(),
            _validar_correo(correo),
            comision_pct,
            activo,
        )
    if fila is None:
        raise VendedorNoEncontradoError(str(vendedor_id))
    return dict(fila)


async def eliminar_vendedor(pool: asyncpg.Pool, vendedor_id: uuid.UUID) -> None:
    """Solo si ninguna factura lo usa; si no, hay que desactivarlo."""
    async with pool.acquire() as conn:
        try:
            en_uso = await conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM facturacion.facturas WHERE vendedor_id = $1)", vendedor_id
            )
        except (asyncpg.UndefinedTableError, asyncpg.UndefinedColumnError):
            # facturas_api todavía no agregó la columna: nadie lo usa.
            en_uso = False
        if en_uso:
            raise VendedorEnUsoError(str(vendedor_id))
        resultado = await conn.execute("DELETE FROM configuracion.vendedores WHERE id = $1", vendedor_id)
    if resultado == "DELETE 0":
        raise VendedorNoEncontradoError(str(vendedor_id))
