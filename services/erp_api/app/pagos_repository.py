"""Acceso a datos de los pagos con tarjeta en línea (fase 7) -- tablas
`soluciones.pagos_en_linea` y `soluciones.pagos_en_linea_eventos` (ver
`migraciones.DDL_PAGOS`).

Reglas:

* Un intento solo se crea desde un enlace del portal VIGENTE que ofrezca
  «tarjeta», para una factura de ESE cliente (RNC fijado en el enlace),
  aceptada por la DGII, a crédito y con saldo. El monto por defecto es todo
  el saldo; un monto menor solo si la empresa permite pagos parciales. El
  ITBIS que se informa a la pasarela es la parte proporcional del ITBIS de
  la factura. Máximo `INTENTOS_POR_HORA` intentos por enlace y hora.
* Un retorno se aplica con la fila del intento bloqueada (`FOR UPDATE`): el
  número de orden y el monto deben ser los del intento (si no, se rechaza y
  queda en la bitácora). Un aprobado registra el cobro con
  `repository.registrar_cobro_en` (mismas validaciones y candado que un
  cobro manual, método «tarjeta») en la MISMA transacción, así que una
  respuesta repetida o dos retornos a la vez nunca registran dos cobros.
* Un aprobado gana siempre: aunque el intento hubiera vencido, se hubiera
  cancelado o reemplazado, el banco ya cobró. Si el cobro no se puede
  registrar (la factura ya se pagó por otro lado), el intento queda
  aprobado con `incidencia` para que la empresa lo revise (reembolso).
"""
from __future__ import annotations

import json
import logging
import secrets
import uuid
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import asyncpg

from . import repository as ventas
from . import soluciones_repository as portal
from .pagos_proveedores import RetornoPago
from .repository import _CTE_CXC, _RNC_COMPRADOR, DatosInvalidosError, _monto_dgii, hoy

logger = logging.getLogger(__name__)

_CENTAVO = Decimal("0.01")
INTENTOS_POR_HORA = 20

# Parámetros de la respuesta que no se guardan en la bitácora (tarjeta
# enmascarada y bóveda de datos de Azul).
_NO_REGISTRAR = {"cardnumber", "datavaulttoken", "datavaultexpiration", "datavaultbrand"}


class PagoNoEncontradoError(Exception):
    pass


class DemasiadosIntentosError(Exception):
    pass


def nueva_referencia() -> str:
    return secrets.token_urlsafe(32)


def referencia_con_forma_valida(referencia: str) -> bool:
    return portal.calc.token_con_forma_valida(referencia)


def _nueva_orden() -> str:
    # 12 dígitos al azar: no se repite entre bases (pruebas/producción
    # comparten el comercio en Azul) y no revela cuántos pagos hay.
    return f"{secrets.randbelow(10**12):012d}"


def parametros_para_bitacora(params: dict[str, str]) -> dict[str, str]:
    return {k: str(v)[:300] for k, v in params.items() if k.lower() not in _NO_REGISTRAR}


async def registrar_evento(
    conn: asyncpg.Connection, pago_id: uuid.UUID | None, tipo: str, detalle: dict[str, Any] | None = None,
    ip: str | None = None,
) -> None:
    await conn.execute(
        "INSERT INTO soluciones.pagos_en_linea_eventos (pago_id, tipo, detalle, ip) VALUES ($1, $2, $3::jsonb, $4)",
        pago_id, tipo, json.dumps(detalle or {}, default=str), ip,
    )


async def expirar_vencidos(conn: asyncpg.Connection) -> int:
    resultado = await conn.execute(
        """
        UPDATE soluciones.pagos_en_linea SET estado = 'expirado', actualizado_en = now()
        WHERE estado = 'pendiente' AND expira_en <= now()
        """
    )
    return int(resultado.split()[-1])


_SELECT_PAGOS = """
    SELECT p.*, co.anulado AS cobro_anulado
    FROM soluciones.pagos_en_linea p
    LEFT JOIN ventas.cobros co ON co.id = p.cobro_id
"""


def _fila(fila: asyncpg.Record) -> dict[str, Any]:
    datos = dict(fila)
    for campo in ("id", "factura_id", "enlace_id", "cobro_id"):
        if datos.get(campo) is not None:
            datos[campo] = str(datos[campo])
    return datos


async def crear_intento(
    pool: asyncpg.Pool,
    *,
    token: str,
    factura_id: uuid.UUID,
    monto: Decimal | None,
    permitir_parcial: bool,
    proveedor: str,
    ambiente: str,
    vigencia_minutos: int,
    ip: str | None = None,
) -> dict[str, Any]:
    async with pool.acquire() as conn:
        async with conn.transaction():
            enlace = await portal._enlace_vigente(conn, token)
            if "tarjeta" not in list(enlace["metodos_pago"]):
                raise portal.NoEncontradoError("Este enlace no ofrece pago con tarjeta")
            recientes = await conn.fetchval(
                """
                SELECT count(*) FROM soluciones.pagos_en_linea
                WHERE enlace_id = $1 AND creado_en > now() - interval '1 hour'
                """,
                enlace["id"],
            )
            if recientes >= INTENTOS_POR_HORA:
                raise DemasiadosIntentosError(
                    "Demasiados intentos de pago desde este enlace: espere unos minutos e intente de nuevo"
                )
            # Mismo candado por factura que un cobro: el saldo no cambia mientras se calcula.
            await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1, 0))", str(factura_id))
            factura = await conn.fetchrow(
                f"""
                WITH {_CTE_CXC}
                SELECT f.id, f.encf, cxc.pendiente, cxc.total,
                       {_monto_dgii("{Encabezado,Totales,TotalITBIS}")} AS itbis_total,
                       COALESCE(d #>> '{{Encabezado,Comprador,RazonSocialComprador}}', '') AS cliente_nombre
                FROM facturacion.facturas f
                CROSS JOIN LATERAL (SELECT f.datos::jsonb AS d) x
                JOIN cxc ON cxc.factura_id = f.id
                WHERE f.id = $1 AND f.estado = 'aceptado' AND {_RNC_COMPRADOR} = $2
                """,
                factura_id,
                enlace["rnc"],
            )
            if factura is None:
                raise portal.NoEncontradoError("Factura no encontrada")
            pendiente: Decimal = factura["pendiente"]
            if pendiente <= 0:
                raise DatosInvalidosError("Esta factura ya está pagada")
            monto = pendiente if monto is None else Decimal(monto).quantize(_CENTAVO, rounding=ROUND_HALF_UP)
            if monto <= 0:
                raise DatosInvalidosError("El monto a pagar debe ser mayor que cero")
            if monto > pendiente:
                raise DatosInvalidosError(f"El monto ({monto}) supera el saldo pendiente de la factura ({pendiente})")
            if monto != pendiente and not permitir_parcial:
                raise DatosInvalidosError("Esta empresa solo acepta el pago del saldo completo de la factura")
            itbis = Decimal("0")
            if factura["total"] > 0 and factura["itbis_total"] > 0:
                itbis = (factura["itbis_total"] * monto / factura["total"]).quantize(_CENTAVO, rounding=ROUND_HALF_UP)
                itbis = min(itbis, monto)
            # Un intento nuevo reemplaza a los pendientes de la misma factura
            # (p. ej. el cliente volvió atrás desde la página del banco).
            reemplazados = await conn.fetch(
                """
                UPDATE soluciones.pagos_en_linea SET estado = 'cancelado', actualizado_en = now()
                WHERE factura_id = $1 AND estado = 'pendiente' RETURNING id
                """,
                factura_id,
            )
            for fila in reemplazados:
                await registrar_evento(conn, fila["id"], "reemplazado", {}, ip)
            for _ in range(5):
                orden = _nueva_orden()
                existe = await conn.fetchval("SELECT 1 FROM soluciones.pagos_en_linea WHERE orden = $1", orden)
                if not existe:
                    break
            nuevo = await conn.fetchrow(
                """
                INSERT INTO soluciones.pagos_en_linea
                    (referencia, orden, proveedor, ambiente, enlace_id, factura_id, encf, cliente_rnc, cliente_nombre,
                     monto, itbis, expira_en)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, now() + make_interval(mins => $12))
                RETURNING *
                """,
                nueva_referencia(),
                orden,
                proveedor,
                ambiente,
                enlace["id"],
                factura_id,
                factura["encf"],
                enlace["rnc"],
                factura["cliente_nombre"] or enlace["nombre"],
                monto,
                itbis,
                vigencia_minutos,
            )
            await registrar_evento(conn, nuevo["id"], "creado", {"monto": monto, "itbis": itbis}, ip)
    return _fila(nuevo) | {"cobro_anulado": None}


async def obtener_por_referencia(pool: asyncpg.Pool, referencia: str) -> dict[str, Any]:
    if not referencia_con_forma_valida(referencia):
        raise PagoNoEncontradoError(referencia)
    async with pool.acquire() as conn:
        await expirar_vencidos(conn)
        fila = await conn.fetchrow(f"{_SELECT_PAGOS} WHERE p.referencia = $1", referencia)
    if fila is None:
        raise PagoNoEncontradoError(referencia)
    return _fila(fila)


async def registrar_evento_suelto(
    pool: asyncpg.Pool, pago_id: str | None, tipo: str, detalle: dict[str, Any], ip: str | None
) -> None:
    async with pool.acquire() as conn:
        await registrar_evento(conn, uuid.UUID(pago_id) if pago_id else None, tipo, detalle, ip)


async def cancelar(pool: asyncpg.Pool, referencia: str, ip: str | None = None) -> dict[str, Any]:
    """CancelUrl de la pasarela: no trae parámetros (ni firma), así que solo
    cancela un intento que sigue pendiente; nunca toca uno aprobado."""
    if not referencia_con_forma_valida(referencia):
        raise PagoNoEncontradoError(referencia)
    async with pool.acquire() as conn:
        async with conn.transaction():
            fila = await conn.fetchrow(
                "SELECT id, estado FROM soluciones.pagos_en_linea WHERE referencia = $1 FOR UPDATE", referencia
            )
            if fila is None:
                raise PagoNoEncontradoError(referencia)
            if fila["estado"] == "pendiente":
                await conn.execute(
                    """
                    UPDATE soluciones.pagos_en_linea
                    SET estado = 'cancelado', respondido_en = now(), actualizado_en = now() WHERE id = $1
                    """,
                    fila["id"],
                )
            await registrar_evento(conn, fila["id"], "cancelado", {"estado_anterior": fila["estado"]}, ip)
    return await obtener_por_referencia(pool, referencia)


async def aplicar_retorno(
    pool: asyncpg.Pool,
    referencia: str,
    retorno: RetornoPago,
    *,
    cuenta_bancaria_id: uuid.UUID | None,
    nombre_proveedor: str,
    params: dict[str, str],
    ip: str | None = None,
) -> dict[str, Any]:
    """Aplica un retorno YA VALIDADO (firma correcta). Devuelve
    `{"pago": ..., "rechazo": motivo | None, "cobro_nuevo": bool}`."""
    if not retorno.valido:  # pragma: no cover (lo filtra la ruta)
        raise ValueError("retorno sin validar")
    bitacora = parametros_para_bitacora(params)
    rechazo: str | None = None
    cobro_nuevo = False
    async with pool.acquire() as conn:
        async with conn.transaction():
            intento = await conn.fetchrow(
                "SELECT * FROM soluciones.pagos_en_linea WHERE referencia = $1 FOR UPDATE", referencia
            )
            if intento is None:
                raise PagoNoEncontradoError(referencia)
            pago_id = intento["id"]
            if retorno.orden != intento["orden"]:
                rechazo = "El número de orden de la respuesta no es el de este pago"
                await registrar_evento(conn, pago_id, "orden_no_coincide", bitacora, ip)
            elif retorno.monto != intento["monto"]:
                rechazo = "El monto de la respuesta no coincide con el del pago"
                await registrar_evento(conn, pago_id, "monto_no_coincide", bitacora, ip)
                if retorno.aprobado:
                    await conn.execute(
                        """
                        UPDATE soluciones.pagos_en_linea SET incidencia = $2, actualizado_en = now() WHERE id = $1
                        """,
                        pago_id,
                        f"La pasarela informó un pago aprobado por {retorno.monto} en vez de {intento['monto']}: "
                        "no se registró el cobro; verifíquelo en el portal del banco",
                    )
            elif intento["estado"] == "aprobado":
                # Respuesta repetida (recarga, doble retorno): no cobra otra vez.
                tipo = "repetido" if retorno.aprobado else "declinado_tras_aprobado"
                await registrar_evento(conn, pago_id, tipo, bitacora, ip)
            else:
                campos = (
                    retorno.codigo_autorizacion or None, retorno.rrn or None, retorno.orden_proveedor or None,
                    retorno.iso_code or None, retorno.mensaje or None, retorno.error_descripcion or None,
                    retorno.fecha or None,
                )
                estado = "aprobado" if retorno.aprobado else "declinado"
                await conn.execute(
                    """
                    UPDATE soluciones.pagos_en_linea
                    SET estado = $2, codigo_autorizacion = $3, rrn = $4, orden_proveedor = $5, iso_code = $6,
                        respuesta = $7, error_descripcion = $8, fecha_proveedor = $9,
                        respondido_en = now(), actualizado_en = now()
                    WHERE id = $1
                    """,
                    pago_id,
                    estado,
                    *campos,
                )
                await registrar_evento(
                    conn, pago_id, estado, bitacora | {"estado_anterior": intento["estado"]}, ip
                )
                if retorno.aprobado:
                    cobro_nuevo = await _registrar_cobro(
                        conn, intento, retorno, cuenta_bancaria_id, nombre_proveedor, ip
                    )
    pago = await obtener_por_referencia(pool, referencia)
    return {"pago": pago, "rechazo": rechazo, "cobro_nuevo": cobro_nuevo}


async def _registrar_cobro(
    conn: asyncpg.Connection,
    intento: asyncpg.Record,
    retorno: RetornoPago,
    cuenta_bancaria_id: uuid.UUID | None,
    nombre_proveedor: str,
    ip: str | None,
) -> bool:
    referencia = " · ".join(
        x for x in (
            nombre_proveedor.split(" ")[0],
            f"Aut. {retorno.codigo_autorizacion}" if retorno.codigo_autorizacion else "",
            f"RRN {retorno.rrn}" if retorno.rrn else "",
        ) if x
    )
    datos = {
        "factura_id": intento["factura_id"],
        "fecha": hoy(),
        "monto": intento["monto"],
        "metodo": "tarjeta",
        "cuenta_destino": f"Pago en línea · {nombre_proveedor}",
        "referencia": referencia,
        "notas": f"Pago con tarjeta en línea desde el portal de clientes · orden {intento['orden']}",
    }
    ultimo_error = ""
    for cuenta in dict.fromkeys([cuenta_bancaria_id, None]):
        try:
            async with conn.transaction():  # savepoint: un fallo no deshace el estado 'aprobado'
                cobro = await ventas.registrar_cobro_en(conn, **datos, cuenta_bancaria_id=cuenta)
        except DatosInvalidosError as exc:
            ultimo_error = str(exc)
            continue
        incidencia = None
        if cuenta_bancaria_id is not None and cuenta is None:
            incidencia = (
                f"El cobro se registró sin la cuenta bancaria de Pagos en línea ({ultimo_error}): "
                "corríjala en Configuración"
            )
        await conn.execute(
            """
            UPDATE soluciones.pagos_en_linea SET cobro_id = $2, incidencia = $3, actualizado_en = now() WHERE id = $1
            """,
            intento["id"],
            uuid.UUID(cobro["id"]),
            incidencia,
        )
        await registrar_evento(conn, intento["id"], "cobro_registrado", {"cobro_id": cobro["id"]}, ip)
        return True
    logger.warning("Pago en línea %s aprobado sin cobro registrado: %s", intento["orden"], ultimo_error)
    await conn.execute(
        "UPDATE soluciones.pagos_en_linea SET incidencia = $2, actualizado_en = now() WHERE id = $1",
        intento["id"],
        f"Pago aprobado por el banco, pero no se pudo registrar el cobro: {ultimo_error}. "
        "Revíselo (puede requerir un reembolso o aplicarlo a otra factura).",
    )
    await registrar_evento(conn, intento["id"], "cobro_fallido", {"motivo": ultimo_error}, ip)
    return False


async def listar(
    pool: asyncpg.Pool, *, factura_id: uuid.UUID | None = None, estado: str | None = None, limite: int = 200
) -> list[dict[str, Any]]:
    condiciones, valores = [], []
    if factura_id is not None:
        valores.append(factura_id)
        condiciones.append(f"p.factura_id = ${len(valores)}")
    if estado is not None:
        valores.append(estado)
        condiciones.append(f"p.estado = ${len(valores)}")
    where = f"WHERE {' AND '.join(condiciones)}" if condiciones else ""
    valores.append(limite)
    async with pool.acquire() as conn:
        await expirar_vencidos(conn)
        filas = await conn.fetch(f"{_SELECT_PAGOS} {where} ORDER BY p.creado_en DESC LIMIT ${len(valores)}", *valores)
    return [_fila(f) for f in filas]


async def eventos(pool: asyncpg.Pool, pago_id: uuid.UUID) -> list[dict[str, Any]]:
    async with pool.acquire() as conn:
        filas = await conn.fetch(
            "SELECT tipo, detalle, creado_en FROM soluciones.pagos_en_linea_eventos WHERE pago_id = $1 ORDER BY id",
            pago_id,
        )
    return [
        {"tipo": f["tipo"], "detalle": json.loads(f["detalle"]) if isinstance(f["detalle"], str) else f["detalle"],
         "creado_en": f["creado_en"]}
        for f in filas
    ]
