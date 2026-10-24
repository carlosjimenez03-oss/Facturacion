"""Acceso a Postgres de Bancos (fase 5): cuentas bancarias, movimientos,
transferencias entre cuentas, extractos importados y conciliación.

Modelo (ver docs/ERP_API.md, "Bancos"):

- Los **movimientos** de una cuenta son la unión de `bancos.movimientos`
  (depósitos, retiros, cargos, intereses, comisiones y las patas de las
  transferencias) con los cobros (`ventas.cobros`) y pagos a proveedores
  (`compras.pagos`) que tienen `cuenta_bancaria_id`. Cada uno se identifica
  por (`origen`, `id`): 'manual' | 'cobro' | 'pago'. Los cobros y pagos NO se
  copian: anular uno lo quita de la cuenta sin tocar Bancos.
- Monto con signo: + entra a la cuenta, − sale. Saldo actual = saldo inicial
  + movimientos desde `fecha_saldo_inicial`.
- **Conciliación:** una línea del extracto (`bancos.extracto_lineas`) se
  concilia con un movimiento del mismo monto. Una línea que apunta a un
  movimiento anulado cuenta como no conciliada. Cerrar la conciliación al
  corte `hasta` exige que no queden líneas del banco sin conciliar y que el
  saldo según el banco cuadre con libros menos las partidas en tránsito.
  Cerrada, lo que Bancos registra con fecha <= `hasta` (movimientos,
  transferencias, conciliar/desconciliar) queda bloqueado (409) hasta
  reabrirla. Registrar cobros y pagos no se bloquea: si se registra uno con
  fecha ya conciliada, aparece como partida en tránsito de la siguiente.
  Anular uno conciliado con una línea de la conciliación cerrada sí da 409
  (`repository.liberar_conciliacion`); en el período abierto, su línea se
  desconcilia.

Todas las operaciones que cambian una cuenta toman su candado
(`_bloquear_cuenta`), igual que el cierre de su conciliación.
"""
from __future__ import annotations

import json
import uuid
from datetime import date
from decimal import Decimal
from typing import Any

import asyncpg

from . import bancos_calculos
from .bancos_calculos import ExtractoInvalidoError
from .contabilidad_calculos import NOMBRE_MOVIMIENTO, TIPOS_MOVIMIENTO, TIPOS_MOVIMIENTO_ENTRADA
from .repository import DatosInvalidosError, _redondear, bloquear_cuenta_bancaria, hoy

_CERO = Decimal("0")


class BancoNoEncontradoError(Exception):
    """Cuenta, movimiento, transferencia, extracto, línea o conciliación inexistente (404)."""


class ConflictoBancosError(Exception):
    """La operación no aplica en el estado actual (409)."""


# --- SQL compartido ---------------------------------------------------------

_DESCRIPCION_TIPO = "CASE m.tipo " + " ".join(
    f"WHEN '{tipo}' THEN '{nombre}'" for tipo, nombre in NOMBRE_MOVIMIENTO.items()
) + " ELSE m.tipo END"

def _filtro_movs(columna_cuenta: str, columna_id: str, cuenta: str | None, movimiento: str | None) -> str:
    condiciones = [f"{columna_cuenta} = {cuenta}"] if cuenta else []
    if movimiento:
        condiciones.append(f"{columna_id} = {movimiento}")
    return "".join(f" AND {c}" for c in condiciones)


def _cte_movimientos(cuenta: str | None = None, *, movimiento: str | None = None) -> str:
    """CTE `movs` / `movs_c`: movimientos de las cuentas (ver docstring del
    módulo). `documento_id` = factura (cobro) o renglón del 606 (pago);
    `conciliado_con` = línea del extracto con que está conciliado.
    `cuenta` / `movimiento`: expresión SQL (p. ej. "$1") para leer solo los
    de esa cuenta / ese id desde las tablas (con sus índices, en lugar de
    recorrer los movimientos de todas las cuentas en cada consulta)."""
    filtro_m = _filtro_movs("m.cuenta_id", "m.id", cuenta, movimiento)
    filtro_co = _filtro_movs("co.cuenta_bancaria_id", "co.id", cuenta, movimiento)
    filtro_pg = _filtro_movs("pg.cuenta_bancaria_id", "pg.id", cuenta, movimiento)
    return f"""
    movs AS (
        SELECT 'manual'::text AS origen, m.id, m.cuenta_id, m.fecha, m.tipo,
               (CASE WHEN m.tipo IN ('deposito', 'interes', 'transferencia_entrada') THEN m.monto
                     ELSE -m.monto END)::numeric(18, 2) AS monto,
               COALESCE(NULLIF(m.descripcion, ''), {_DESCRIPCION_TIPO}) AS descripcion,
               m.referencia, m.transferencia_id, NULL::uuid AS documento_id, m.creado_en
        FROM bancos.movimientos m
        WHERE NOT m.anulado{filtro_m}
        UNION ALL
        SELECT 'cobro', co.id, co.cuenta_bancaria_id, co.fecha, 'cobro', co.monto::numeric(18, 2),
               'Cobro ' || COALESCE(f.encf, '')
                   || COALESCE(' · ' || NULLIF(f.datos::jsonb #>> '{{Encabezado,Comprador,RazonSocialComprador}}', ''), ''),
               co.referencia, NULL::uuid, co.factura_id, co.creado_en
        FROM ventas.cobros co
        LEFT JOIN facturacion.facturas f ON f.id = co.factura_id
        WHERE co.cuenta_bancaria_id IS NOT NULL AND NOT co.anulado{filtro_co}
        UNION ALL
        SELECT 'pago', pg.id, pg.cuenta_bancaria_id, pg.fecha, 'pago', (-pg.monto)::numeric(18, 2),
               'Pago ' || COALESCE(r.ncf, '')
                   || COALESCE(' · ' || NULLIF(COALESCE(cf.proveedor_nombre, er.razon_social_emisor, r.rnc_cedula), ''), ''),
               pg.referencia, NULL::uuid, pg.registro606_id, pg.creado_en
        FROM compras.pagos pg
        LEFT JOIN compras606.registros r ON r.id = pg.registro606_id
        LEFT JOIN compras.facturas cf ON cf.registro606_id = pg.registro606_id
        LEFT JOIN recepcion.ecf_recibidos er ON er.compra606_id = pg.registro606_id
        WHERE pg.cuenta_bancaria_id IS NOT NULL AND NOT pg.anulado AND pg.monto > 0{filtro_pg}
    ),
    movs_c AS (
        SELECT movs.*, el.id AS conciliado_con, el.fecha AS fecha_linea
        FROM movs
        LEFT JOIN bancos.extracto_lineas el ON el.mov_origen = movs.origen AND el.mov_id = movs.id
    )
"""


def _cte_lineas(cuenta: str | None = None) -> str:
    """CTE `lineas`: líneas de extracto con su movimiento (NULL si no está
    conciliada o si el movimiento se anuló). Va después de
    `_cte_movimientos` con la misma `cuenta`."""
    return f"""
    lineas AS (
        SELECT el.*, mv.origen AS m_origen, mv.id AS m_id, mv.fecha AS m_fecha, mv.tipo AS m_tipo,
               mv.monto AS m_monto, mv.descripcion AS m_descripcion, mv.referencia AS m_referencia
        FROM bancos.extracto_lineas el
        LEFT JOIN movs mv ON mv.origen = el.mov_origen AND mv.id = el.mov_id
        {f"WHERE el.cuenta_id = {cuenta}" if cuenta else ""}
    )
"""


def _cte_cuenta(cuenta: str) -> str:
    """Movimientos y líneas de una sola cuenta (`cuenta`: expresión SQL)."""
    return f"{_cte_movimientos(cuenta)}, {_cte_lineas(cuenta)}"


def _select_cuentas(cuenta: str | None = None) -> str:
    """Cuentas con su saldo actual y lo pendiente de conciliar. Con `cuenta`
    (expresión SQL) solo lee los movimientos de esa cuenta: agregar
    `WHERE c.id = <cuenta>`."""
    return f"""
    WITH {_cte_movimientos(cuenta)},
    {_cte_lineas(cuenta)},
    resumen_movs AS (
        SELECT m.cuenta_id,
               COALESCE(sum(m.monto) FILTER (WHERE m.fecha >= c.fecha_saldo_inicial), 0) AS neto,
               count(*) FILTER (WHERE m.conciliado_con IS NULL AND m.fecha >= c.fecha_saldo_inicial) AS sin_conciliar,
               max(m.fecha) AS ultimo
        FROM movs_c m JOIN bancos.cuentas c ON c.id = m.cuenta_id
        GROUP BY m.cuenta_id
    ),
    resumen_lineas AS (
        SELECT cuenta_id, count(*) FILTER (WHERE m_id IS NULL) AS sin_conciliar
        FROM lineas GROUP BY cuenta_id
    ),
    resumen_conc AS (
        SELECT cuenta_id, max(hasta) AS hasta FROM bancos.conciliaciones
        {f"WHERE cuenta_id = {cuenta}" if cuenta else ""}
        GROUP BY cuenta_id
    )
    SELECT c.id, c.nombre, c.banco, c.tipo, c.numero, c.moneda, c.saldo_inicial, c.fecha_saldo_inicial,
           c.cuenta_contable_id, cc.codigo AS cuenta_contable_codigo, cc.nombre AS cuenta_contable_nombre,
           c.activa, c.notas, c.creado_en, c.actualizado_en,
           (c.saldo_inicial + COALESCE(rm.neto, 0))::numeric(18, 2) AS saldo_actual,
           COALESCE(rm.sin_conciliar, 0)::int AS movimientos_sin_conciliar,
           COALESCE(rl.sin_conciliar, 0)::int AS lineas_sin_conciliar,
           rm.ultimo AS ultimo_movimiento,
           rc.hasta AS conciliada_hasta
    FROM bancos.cuentas c
    LEFT JOIN contabilidad.cuentas cc ON cc.id = c.cuenta_contable_id
    LEFT JOIN resumen_movs rm ON rm.cuenta_id = c.id
    LEFT JOIN resumen_lineas rl ON rl.cuenta_id = c.id
    LEFT JOIN resumen_conc rc ON rc.cuenta_id = c.id
"""


def _fila_cuenta(fila: asyncpg.Record) -> dict[str, Any]:
    datos = dict(fila)
    datos["numero_enmascarado"] = bancos_calculos.enmascarar_numero(datos.pop("numero"))
    return datos


def _nombre_cuenta(fila: asyncpg.Record | dict[str, Any]) -> str:
    mascara = bancos_calculos.enmascarar_numero(fila.get("numero") if isinstance(fila, dict) else fila["numero"])
    return f"{fila['nombre']}{f' {mascara}' if mascara else ''}"


async def _bloquear_cuenta(conn: asyncpg.Connection, cuenta_id: uuid.UUID) -> None:
    """Candado de transacción por cuenta: serializa movimientos, conciliación
    y cierre de la misma cuenta (también lo toman anular_cobro y anular_pago)."""
    await bloquear_cuenta_bancaria(conn, cuenta_id)


async def _cuenta_fila(conn: asyncpg.Connection, cuenta_id: uuid.UUID) -> asyncpg.Record:
    fila = await conn.fetchrow("SELECT * FROM bancos.cuentas WHERE id = $1", cuenta_id)
    if fila is None:
        raise BancoNoEncontradoError(str(cuenta_id))
    return fila


async def _conciliada_hasta(conn: asyncpg.Connection, cuenta_id: uuid.UUID) -> date | None:
    return await conn.fetchval("SELECT max(hasta) FROM bancos.conciliaciones WHERE cuenta_id = $1", cuenta_id)


async def _exigir_abierta(conn: asyncpg.Connection, cuenta: asyncpg.Record, fecha: date) -> None:
    """409 si la fecha está dentro de una conciliación cerrada de la cuenta."""
    hasta = await _conciliada_hasta(conn, cuenta["id"])
    if hasta is not None and fecha <= hasta:
        raise ConflictoBancosError(
            f"La cuenta {_nombre_cuenta(cuenta)} está conciliada hasta el {hasta:%d/%m/%Y}: reabra esa conciliación "
            f"para cambiar movimientos del {fecha:%d/%m/%Y}"
        )


async def _validar_cuenta_contable(
    conn: asyncpg.Connection, cuenta_contable_id: uuid.UUID | None, *, tipos: tuple[str, ...] | None = None
) -> None:
    if cuenta_contable_id is None:
        return
    fila = await conn.fetchrow(
        "SELECT codigo, nombre, tipo, acepta_movimientos, activa FROM contabilidad.cuentas WHERE id = $1",
        cuenta_contable_id,
    )
    if fila is None:
        raise DatosInvalidosError("La cuenta contable indicada no existe")
    if not fila["acepta_movimientos"] or not fila["activa"]:
        raise DatosInvalidosError(
            f"La cuenta contable {fila['codigo']} {fila['nombre']} no acepta movimientos (es de grupo o está inactiva)"
        )
    if tipos and fila["tipo"] not in tipos:
        raise DatosInvalidosError(
            f"La cuenta contable {fila['codigo']} {fila['nombre']} es de {fila['tipo']}: use una cuenta de "
            f"{' o '.join(tipos)}"
        )


# --- Cuentas bancarias ---------------------------------------------------------

async def listar_cuentas(pool: asyncpg.Pool, *, activa: bool | None = None) -> list[dict[str, Any]]:
    where, valores = "", []
    if activa is not None:
        where, valores = "WHERE c.activa = $1", [activa]
    async with pool.acquire() as conn:
        filas = await conn.fetch(f"{_select_cuentas()} {where} ORDER BY c.activa DESC, c.nombre", *valores)
    return [_fila_cuenta(f) for f in filas]


async def _cuenta(conn: asyncpg.Connection, cuenta_id: uuid.UUID) -> dict[str, Any]:
    fila = await conn.fetchrow(f"{_select_cuentas('$1')} WHERE c.id = $1", cuenta_id)
    if fila is None:
        raise BancoNoEncontradoError(str(cuenta_id))
    return _fila_cuenta(fila)


async def obtener_cuenta(pool: asyncpg.Pool, cuenta_id: uuid.UUID) -> dict[str, Any]:
    async with pool.acquire() as conn:
        return await _cuenta(conn, cuenta_id)


_NOMBRE_TIPO_CUENTA = {"corriente": "Cuenta corriente", "ahorro": "Cuenta de ahorro", "caja_chica": "Caja chica",
                       "tarjeta": "Tarjeta de crédito"}


async def crear_cuenta(pool: asyncpg.Pool, **campos: Any) -> dict[str, Any]:
    if campos["moneda"] != "DOP" and campos["tipo"] == "caja_chica":
        raise DatosInvalidosError("La caja chica es en pesos (DOP)")
    nombre = campos.get("nombre") or f"{campos['banco']} · {_NOMBRE_TIPO_CUENTA[campos['tipo']]}"
    async with pool.acquire() as conn:
        async with conn.transaction():
            await _validar_cuenta_contable(conn, campos.get("cuenta_contable_id"), tipos=("activo", "pasivo"))
            cuenta_id = await conn.fetchval(
                """
                INSERT INTO bancos.cuentas
                    (nombre, banco, tipo, numero, moneda, saldo_inicial, fecha_saldo_inicial, cuenta_contable_id,
                     activa, notas)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                RETURNING id
                """,
                nombre,
                campos["banco"],
                campos["tipo"],
                campos.get("numero"),
                campos["moneda"],
                _redondear(campos["saldo_inicial"]),
                campos.get("fecha_saldo_inicial") or hoy(),
                campos.get("cuenta_contable_id"),
                campos["activa"],
                campos.get("notas"),
            )
            return await _cuenta(conn, cuenta_id)


async def actualizar_cuenta(pool: asyncpg.Pool, cuenta_id: uuid.UUID, campos: dict[str, Any]) -> dict[str, Any]:
    """Cambia solo lo enviado. Saldo inicial / fecha: 409 si la cuenta ya
    tiene una conciliación cerrada o (fecha) movimientos anteriores a la
    nueva. Moneda: 409 si tiene movimientos."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            await _bloquear_cuenta(conn, cuenta_id)
            actual = await _cuenta_fila(conn, cuenta_id)
            nuevos = {k: v for k, v in campos.items() if k in (
                "nombre", "banco", "tipo", "numero", "moneda", "saldo_inicial", "fecha_saldo_inicial",
                "cuenta_contable_id", "activa", "notas",
            )}
            for requerido in ("nombre", "banco", "tipo", "moneda", "saldo_inicial", "fecha_saldo_inicial", "activa"):
                if requerido in nuevos and nuevos[requerido] is None:
                    raise DatosInvalidosError(f"«{requerido}» no puede quedar vacío")
            if "saldo_inicial" in nuevos:
                nuevos["saldo_inicial"] = _redondear(nuevos["saldo_inicial"])
            cambia_saldo = any(
                k in nuevos and nuevos[k] != actual[k] for k in ("saldo_inicial", "fecha_saldo_inicial")
            )
            if cambia_saldo and await _conciliada_hasta(conn, cuenta_id) is not None:
                raise ConflictoBancosError(
                    f"La cuenta {_nombre_cuenta(actual)} ya tiene conciliaciones cerradas: reábralas para cambiar su "
                    "saldo inicial"
                )
            hay_movs = await _cantidad_movimientos(conn, cuenta_id)
            if "moneda" in nuevos and nuevos["moneda"] != actual["moneda"] and hay_movs:
                raise ConflictoBancosError("La cuenta tiene movimientos: no se puede cambiar su moneda")
            if "fecha_saldo_inicial" in nuevos and nuevos["fecha_saldo_inicial"] > actual["fecha_saldo_inicial"]:
                anterior = await conn.fetchval(
                    f"WITH {_cte_movimientos('$1')} SELECT min(fecha) FROM movs", cuenta_id
                )
                if anterior is not None and anterior < nuevos["fecha_saldo_inicial"]:
                    raise ConflictoBancosError(
                        f"La cuenta tiene movimientos desde el {anterior:%d/%m/%Y}: la fecha del saldo inicial no "
                        "puede ser posterior"
                    )
            if "cuenta_contable_id" in nuevos:
                await _validar_cuenta_contable(conn, nuevos["cuenta_contable_id"], tipos=("activo", "pasivo"))
            if (nuevos.get("moneda", actual["moneda"]) != "DOP" and nuevos.get("tipo", actual["tipo"]) == "caja_chica"):
                raise DatosInvalidosError("La caja chica es en pesos (DOP)")
            if nuevos:
                columnas = list(nuevos)
                asignaciones = ", ".join(f"{c} = ${i + 2}" for i, c in enumerate(columnas))
                await conn.execute(
                    f"UPDATE bancos.cuentas SET {asignaciones}, actualizado_en = now() WHERE id = $1",
                    cuenta_id,
                    *[nuevos[c] for c in columnas],
                )
            return await _cuenta(conn, cuenta_id)


async def _cantidad_movimientos(conn: asyncpg.Connection, cuenta_id: uuid.UUID) -> int:
    return await conn.fetchval(
        """
        SELECT (SELECT count(*) FROM bancos.movimientos WHERE cuenta_id = $1)
             + (SELECT count(*) FROM ventas.cobros WHERE cuenta_bancaria_id = $1)
             + (SELECT count(*) FROM compras.pagos WHERE cuenta_bancaria_id = $1)
             + (SELECT count(*) FROM bancos.extractos WHERE cuenta_id = $1)
        """,
        cuenta_id,
    )


async def eliminar_cuenta(pool: asyncpg.Pool, cuenta_id: uuid.UUID) -> None:
    async with pool.acquire() as conn:
        async with conn.transaction():
            await _bloquear_cuenta(conn, cuenta_id)
            cuenta = await _cuenta_fila(conn, cuenta_id)
            if await _cantidad_movimientos(conn, cuenta_id):
                raise ConflictoBancosError(
                    f"La cuenta {_nombre_cuenta(cuenta)} tiene movimientos, cobros, pagos o extractos: desactívela en "
                    "lugar de eliminarla"
                )
            await conn.execute("DELETE FROM bancos.conciliaciones WHERE cuenta_id = $1", cuenta_id)
            await conn.execute("DELETE FROM bancos.cuentas WHERE id = $1", cuenta_id)


# --- Movimientos ---------------------------------------------------------

def _fila_movimiento(fila: asyncpg.Record) -> dict[str, Any]:
    datos = {k: fila[k] for k in (
        "origen", "id", "cuenta_id", "fecha", "tipo", "monto", "descripcion", "referencia", "transferencia_id",
        "documento_id", "creado_en",
    )}
    datos["conciliado"] = fila["conciliado_con"] is not None
    datos["extracto_linea_id"] = fila["conciliado_con"]
    if "saldo" in fila.keys():
        datos["saldo"] = fila["saldo"]
    return datos


async def listar_movimientos(
    pool: asyncpg.Pool,
    cuenta_id: uuid.UUID,
    *,
    desde: date | None = None,
    hasta: date | None = None,
    conciliado: bool | None = None,
    q: str | None = None,
    limite: int = 500,
    offset: int = 0,
) -> dict[str, Any]:
    """Movimientos de la cuenta, más recientes primero, con el saldo después
    de cada uno (orden: fecha, registro). `saldo_inicial` = saldo al empezar
    `desde`; `saldo_final` = al terminar `hasta` (o hoy)."""
    if desde and hasta and desde > hasta:
        raise DatosInvalidosError("«desde» es posterior a «hasta»")
    async with pool.acquire() as conn:
        cuenta = await _cuenta(conn, cuenta_id)
        condiciones = ["TRUE"]
        valores: list[Any] = [cuenta_id]
        if desde:
            valores.append(desde)
            condiciones.append(f"fecha >= ${len(valores)}")
        if hasta:
            valores.append(hasta)
            condiciones.append(f"fecha <= ${len(valores)}")
        if conciliado is not None:
            condiciones.append("conciliado_con IS NOT NULL" if conciliado else "conciliado_con IS NULL")
        if q:
            valores.append(f"%{q}%")
            condiciones.append(f"(descripcion ILIKE ${len(valores)} OR referencia ILIKE ${len(valores)})")
        valores.extend([limite, offset])
        filas = await conn.fetch(
            f"""
            WITH {_cte_movimientos('$1')},
            con_saldo AS (
                SELECT m.*, (c.saldo_inicial + sum(m.monto) OVER (
                           ORDER BY m.fecha, m.creado_en, m.origen, m.id ROWS UNBOUNDED PRECEDING))::numeric(18, 2) AS saldo
                FROM movs_c m JOIN bancos.cuentas c ON c.id = m.cuenta_id
                WHERE m.cuenta_id = $1 AND m.fecha >= c.fecha_saldo_inicial
            )
            SELECT * FROM con_saldo
            WHERE {' AND '.join(condiciones)}
            ORDER BY fecha DESC, creado_en DESC, origen DESC, id DESC
            LIMIT ${len(valores) - 1} OFFSET ${len(valores)}
            """,
            *valores,
        )
        totales = await conn.fetchrow(
            f"""
            WITH {_cte_movimientos('$1')}
            SELECT
                COALESCE(sum(m.monto) FILTER (WHERE $2::date IS NOT NULL AND m.fecha < $2), 0) AS antes,
                COALESCE(sum(m.monto) FILTER (WHERE ($3::date IS NULL OR m.fecha <= $3)), 0) AS hasta_fin,
                COALESCE(sum(m.monto) FILTER (WHERE m.monto > 0 AND ($2::date IS NULL OR m.fecha >= $2)
                                              AND ($3::date IS NULL OR m.fecha <= $3)), 0) AS entradas,
                COALESCE(-sum(m.monto) FILTER (WHERE m.monto < 0 AND ($2::date IS NULL OR m.fecha >= $2)
                                               AND ($3::date IS NULL OR m.fecha <= $3)), 0) AS salidas
            FROM movs m JOIN bancos.cuentas c ON c.id = m.cuenta_id
            WHERE m.cuenta_id = $1 AND m.fecha >= c.fecha_saldo_inicial
            """,
            cuenta_id,
            desde,
            hasta,
        )
    saldo_inicial = cuenta["saldo_inicial"] + totales["antes"]
    return {
        "cuenta": cuenta,
        "desde": desde,
        "hasta": hasta,
        "saldo_inicial": saldo_inicial,
        "entradas": totales["entradas"],
        "salidas": totales["salidas"],
        "saldo_final": cuenta["saldo_inicial"] + totales["hasta_fin"],
        "movimientos": [_fila_movimiento(f) for f in filas],
    }


async def _movimiento(conn: asyncpg.Connection, origen: str, mov_id: uuid.UUID) -> dict[str, Any] | None:
    fila = await conn.fetchrow(
        f"WITH {_cte_movimientos(movimiento='$2')} SELECT * FROM movs_c WHERE origen = $1", origen, mov_id
    )
    return _fila_movimiento(fila) if fila is not None else None


def _validar_tasa(cuenta: asyncpg.Record, tasa_cambio: Decimal | None) -> Decimal | None:
    if cuenta["moneda"] == "DOP":
        if tasa_cambio not in (None, Decimal(1)):
            raise DatosInvalidosError("La tasa de cambio solo aplica a cuentas en dólares")
        return None
    if tasa_cambio is None or tasa_cambio <= 0:
        raise DatosInvalidosError(
            f"La cuenta {_nombre_cuenta(cuenta)} es en {cuenta['moneda']}: indique la tasa de cambio (RD$ por US$) "
            "para contabilizar el movimiento"
        )
    return tasa_cambio


async def _validar_cuenta_operable(conn: asyncpg.Connection, cuenta: asyncpg.Record, fecha: date) -> None:
    if not cuenta["activa"]:
        raise DatosInvalidosError(f"La cuenta {_nombre_cuenta(cuenta)} está inactiva")
    if fecha < cuenta["fecha_saldo_inicial"]:
        raise DatosInvalidosError(
            f"La fecha es anterior al saldo inicial de {_nombre_cuenta(cuenta)} ({cuenta['fecha_saldo_inicial']:%d/%m/%Y})"
        )
    await _exigir_abierta(conn, cuenta, fecha)


async def _insertar_movimiento(
    conn: asyncpg.Connection,
    cuenta: asyncpg.Record,
    *,
    fecha: date,
    tipo: str,
    monto: Decimal,
    descripcion: str | None,
    referencia: str | None,
    cuenta_contable_id: uuid.UUID | None,
    tasa_cambio: Decimal | None,
) -> uuid.UUID:
    if tipo not in TIPOS_MOVIMIENTO:
        raise DatosInvalidosError(f"Tipo de movimiento inválido: {tipo}")
    monto = _redondear(monto)
    if monto <= 0:
        raise DatosInvalidosError("El monto debe ser mayor que cero")
    await _validar_cuenta_operable(conn, cuenta, fecha)
    tasa = _validar_tasa(cuenta, tasa_cambio)
    await _validar_cuenta_contable(conn, cuenta_contable_id)
    return await conn.fetchval(
        """
        INSERT INTO bancos.movimientos
            (cuenta_id, fecha, tipo, monto, descripcion, referencia, cuenta_contable_id, tasa_cambio)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        RETURNING id
        """,
        cuenta["id"],
        fecha,
        tipo,
        monto,
        descripcion,
        referencia,
        cuenta_contable_id,
        tasa,
    )


async def registrar_movimiento(pool: asyncpg.Pool, **campos: Any) -> dict[str, Any]:
    """Depósito, retiro, cargo bancario, interés o comisión (monto positivo;
    el tipo dice si entra o sale). 409 si la fecha está conciliada."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            await _bloquear_cuenta(conn, campos["cuenta_id"])
            cuenta = await _cuenta_fila(conn, campos["cuenta_id"])
            mov_id = await _insertar_movimiento(
                conn, cuenta, fecha=campos.get("fecha") or hoy(), tipo=campos["tipo"], monto=campos["monto"],
                descripcion=campos.get("descripcion"), referencia=campos.get("referencia"),
                cuenta_contable_id=campos.get("cuenta_contable_id"), tasa_cambio=campos.get("tasa_cambio"),
            )
            return await _movimiento(conn, "manual", mov_id)


async def anular_movimiento(pool: asyncpg.Pool, movimiento_id: uuid.UUID) -> dict[str, Any]:
    async with pool.acquire() as conn:
        async with conn.transaction():
            fila = await conn.fetchrow("SELECT * FROM bancos.movimientos WHERE id = $1", movimiento_id)
            if fila is None:
                raise BancoNoEncontradoError(str(movimiento_id))
            await _bloquear_cuenta(conn, fila["cuenta_id"])
            fila = await conn.fetchrow("SELECT * FROM bancos.movimientos WHERE id = $1 FOR UPDATE", movimiento_id)
            if fila["anulado"]:
                raise ConflictoBancosError("El movimiento ya está anulado")
            if fila["transferencia_id"] is not None:
                raise ConflictoBancosError("El movimiento es parte de una transferencia: anule la transferencia")
            cuenta = await _cuenta_fila(conn, fila["cuenta_id"])
            await _exigir_abierta(conn, cuenta, fila["fecha"])
            await _desconciliar_movimiento(conn, "manual", movimiento_id)
            await conn.execute(
                "UPDATE bancos.movimientos SET anulado = true, anulado_en = now() WHERE id = $1", movimiento_id
            )
    return {
        "origen": "manual", "id": fila["id"], "cuenta_id": fila["cuenta_id"], "fecha": fila["fecha"],
        "tipo": fila["tipo"], "monto": fila["monto"] if fila["tipo"] in TIPOS_MOVIMIENTO_ENTRADA else -fila["monto"],
        "descripcion": fila["descripcion"] or NOMBRE_MOVIMIENTO.get(fila["tipo"], fila["tipo"]),
        "referencia": fila["referencia"], "transferencia_id": None, "documento_id": None,
        "creado_en": fila["creado_en"], "conciliado": False, "extracto_linea_id": None, "anulado": True,
    }


async def _desconciliar_movimiento(conn: asyncpg.Connection, origen: str, mov_id: uuid.UUID) -> None:
    await conn.execute(
        "UPDATE bancos.extracto_lineas SET mov_origen = NULL, mov_id = NULL, conciliada_en = NULL"
        " WHERE mov_origen = $1 AND mov_id = $2",
        origen,
        mov_id,
    )


# --- Transferencias ---------------------------------------------------------

_SELECT_TRANSFERENCIAS = """
    SELECT t.*, co.nombre AS cuenta_origen_nombre, co.numero AS cuenta_origen_numero, co.moneda AS moneda_origen,
           cd.nombre AS cuenta_destino_nombre, cd.numero AS cuenta_destino_numero, cd.moneda AS moneda_destino
    FROM bancos.transferencias t
    JOIN bancos.cuentas co ON co.id = t.cuenta_origen_id
    JOIN bancos.cuentas cd ON cd.id = t.cuenta_destino_id
"""


def _fila_transferencia(fila: asyncpg.Record) -> dict[str, Any]:
    datos = dict(fila)
    datos["cuenta_origen_nombre"] = _nombre_cuenta(
        {"nombre": datos["cuenta_origen_nombre"], "numero": datos.pop("cuenta_origen_numero")})
    datos["cuenta_destino_nombre"] = _nombre_cuenta(
        {"nombre": datos["cuenta_destino_nombre"], "numero": datos.pop("cuenta_destino_numero")})
    return datos


async def listar_transferencias(
    pool: asyncpg.Pool, *, cuenta_id: uuid.UUID | None = None, limite: int = 200, offset: int = 0
) -> list[dict[str, Any]]:
    where, valores = "", []
    if cuenta_id is not None:
        where, valores = "WHERE $1 IN (t.cuenta_origen_id, t.cuenta_destino_id)", [cuenta_id]
    valores.extend([limite, offset])
    async with pool.acquire() as conn:
        filas = await conn.fetch(
            f"{_SELECT_TRANSFERENCIAS} {where} ORDER BY t.fecha DESC, t.creado_en DESC "
            f"LIMIT ${len(valores) - 1} OFFSET ${len(valores)}",
            *valores,
        )
    return [_fila_transferencia(f) for f in filas]


async def _transferencia(conn: asyncpg.Connection, transferencia_id: uuid.UUID) -> dict[str, Any]:
    fila = await conn.fetchrow(f"{_SELECT_TRANSFERENCIAS} WHERE t.id = $1", transferencia_id)
    if fila is None:
        raise BancoNoEncontradoError(str(transferencia_id))
    return _fila_transferencia(fila)


async def registrar_transferencia(pool: asyncpg.Pool, **campos: Any) -> dict[str, Any]:
    """Crea la transferencia y sus dos movimientos (salida y entrada) en una
    sola transacción: o quedan los dos o ninguno. Entre monedas distintas
    exige `monto_destino` (y la tasa sale de los dos montos); entre dos
    cuentas en dólares, `tasa_cambio`."""
    origen_id, destino_id = campos["cuenta_origen_id"], campos["cuenta_destino_id"]
    if origen_id == destino_id:
        raise DatosInvalidosError("La cuenta de origen y la de destino deben ser distintas")
    fecha = campos.get("fecha") or hoy()
    monto = _redondear(campos["monto"])
    if monto <= 0:
        raise DatosInvalidosError("El monto de la transferencia debe ser mayor que cero")
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Candados en orden fijo: dos transferencias cruzadas no se bloquean mutuamente.
            for cuenta_id in sorted((origen_id, destino_id), key=str):
                await _bloquear_cuenta(conn, cuenta_id)
            origen = await _cuenta_fila(conn, origen_id)
            destino = await _cuenta_fila(conn, destino_id)
            await _validar_cuenta_operable(conn, origen, fecha)
            await _validar_cuenta_operable(conn, destino, fecha)
            monto_destino = campos.get("monto_destino")
            tasa = campos.get("tasa_cambio")
            if origen["moneda"] == destino["moneda"]:
                if monto_destino is not None and _redondear(monto_destino) != monto:
                    raise DatosInvalidosError("Entre cuentas de la misma moneda el monto que llega es el mismo")
                monto_destino = monto
                if origen["moneda"] == "DOP":
                    tasa = None
                elif tasa is None or tasa <= 0:
                    raise DatosInvalidosError("Indique la tasa de cambio (RD$ por US$) para contabilizar la transferencia")
            else:
                if monto_destino is None or monto_destino <= 0:
                    raise DatosInvalidosError(
                        f"La cuenta de origen es en {origen['moneda']} y la de destino en {destino['moneda']}: indique "
                        "el monto que llega a la de destino"
                    )
                monto_destino = _redondear(monto_destino)
                dop, usd = (monto, monto_destino) if origen["moneda"] == "DOP" else (monto_destino, monto)
                tasa = (dop / usd).quantize(Decimal("0.0001"))
            concepto = campos.get("concepto")
            transferencia_id = await conn.fetchval(
                """
                INSERT INTO bancos.transferencias
                    (cuenta_origen_id, cuenta_destino_id, fecha, monto, monto_destino, tasa_cambio, concepto, referencia)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                RETURNING id
                """,
                origen_id, destino_id, fecha, monto, monto_destino, tasa, concepto, campos.get("referencia"),
            )
            for cuenta, otra, tipo, importe in (
                (origen, destino, "transferencia_salida", monto),
                (destino, origen, "transferencia_entrada", monto_destino),
            ):
                direccion = "a" if tipo == "transferencia_salida" else "desde"
                await conn.execute(
                    """
                    INSERT INTO bancos.movimientos
                        (cuenta_id, fecha, tipo, monto, descripcion, referencia, tasa_cambio, transferencia_id)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                    """,
                    cuenta["id"], fecha, tipo, importe,
                    f"Transferencia {direccion} {_nombre_cuenta(otra)}{f' · {concepto}' if concepto else ''}",
                    campos.get("referencia"), tasa if cuenta["moneda"] != "DOP" else None, transferencia_id,
                )
            return await _transferencia(conn, transferencia_id)


async def anular_transferencia(pool: asyncpg.Pool, transferencia_id: uuid.UUID) -> dict[str, Any]:
    """Anula la transferencia y sus dos movimientos juntos (409 si alguna de
    las dos cuentas está conciliada en esa fecha)."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            fila = await conn.fetchrow("SELECT * FROM bancos.transferencias WHERE id = $1", transferencia_id)
            if fila is None:
                raise BancoNoEncontradoError(str(transferencia_id))
            for cuenta_id in sorted((fila["cuenta_origen_id"], fila["cuenta_destino_id"]), key=str):
                await _bloquear_cuenta(conn, cuenta_id)
            fila = await conn.fetchrow("SELECT * FROM bancos.transferencias WHERE id = $1 FOR UPDATE", transferencia_id)
            if fila["anulada"]:
                raise ConflictoBancosError("La transferencia ya está anulada")
            for cuenta_id in (fila["cuenta_origen_id"], fila["cuenta_destino_id"]):
                await _exigir_abierta(conn, await _cuenta_fila(conn, cuenta_id), fila["fecha"])
            movimientos = await conn.fetch(
                "SELECT id FROM bancos.movimientos WHERE transferencia_id = $1", transferencia_id
            )
            for mov in movimientos:
                await _desconciliar_movimiento(conn, "manual", mov["id"])
            await conn.execute(
                "UPDATE bancos.movimientos SET anulado = true, anulado_en = now() WHERE transferencia_id = $1",
                transferencia_id,
            )
            await conn.execute(
                "UPDATE bancos.transferencias SET anulada = true, anulada_en = now() WHERE id = $1", transferencia_id
            )
            return await _transferencia(conn, transferencia_id)


# --- Extractos ---------------------------------------------------------

def _resumen_extracto(extracto: bancos_calculos.Extracto) -> dict[str, Any]:
    return {
        "separador": {"\t": "tabulador"}.get(extracto.separador, extracto.separador),
        "fila_encabezado": extracto.fila_encabezado,
        "columnas": extracto.columnas,
        "mapeo": extracto.mapeo_por_nombre(),
    }


def _linea_dict(linea: bancos_calculos.LineaExtracto) -> dict[str, Any]:
    return {
        "fila": linea.fila, "fecha": linea.fecha, "descripcion": linea.descripcion, "referencia": linea.referencia,
        "monto": linea.monto, "saldo": linea.saldo,
    }


def previsualizar_extracto(contenido: bytes, mapeo: dict[str, Any] | None) -> dict[str, Any]:
    """Lee el CSV sin guardar nada: columnas detectadas, mapeo usado,
    primeras líneas y errores, para la pantalla de "mapeo de columnas"."""
    try:
        extracto = bancos_calculos.leer_extracto(contenido, mapeo)
    except ExtractoInvalidoError as exc:
        raise DatosInvalidosError(str(exc)) from exc
    return _resumen_extracto(extracto) | {
        "total_lineas": len(extracto.lineas),
        "lineas": [_linea_dict(x) for x in extracto.lineas[:20]],
        "errores": extracto.errores,
    }


# $1 = la cuenta de los extractos.
_SELECT_EXTRACTOS = f"""
    WITH {_cte_cuenta('$1')}
    SELECT e.id, e.cuenta_id, e.nombre_archivo, e.desde, e.hasta, e.saldo_final, e.importado_en,
           count(l.id)::int AS lineas, count(l.m_id)::int AS conciliadas
    FROM bancos.extractos e
    LEFT JOIN lineas l ON l.extracto_id = e.id
"""


async def _extracto(conn: asyncpg.Connection, extracto_id: uuid.UUID) -> dict[str, Any]:
    fila = await conn.fetchrow(
        f"{_SELECT_EXTRACTOS} WHERE e.cuenta_id = $1 AND e.id = $2 GROUP BY e.id",
        await conn.fetchval("SELECT cuenta_id FROM bancos.extractos WHERE id = $1", extracto_id),
        extracto_id,
    )
    if fila is None:
        raise BancoNoEncontradoError(str(extracto_id))
    return dict(fila)


async def listar_extractos(pool: asyncpg.Pool, cuenta_id: uuid.UUID) -> list[dict[str, Any]]:
    async with pool.acquire() as conn:
        await _cuenta_fila(conn, cuenta_id)
        filas = await conn.fetch(
            f"{_SELECT_EXTRACTOS} WHERE e.cuenta_id = $1 GROUP BY e.id ORDER BY e.importado_en DESC", cuenta_id
        )
    return [dict(f) for f in filas]


async def importar_extracto(
    pool: asyncpg.Pool,
    cuenta_id: uuid.UUID,
    contenido: bytes,
    *,
    nombre_archivo: str | None,
    mapeo: dict[str, Any] | None,
    saldo_final: Decimal | None,
) -> dict[str, Any]:
    """Importa las líneas del CSV a la cuenta. Las que ya estaban (misma
    huella: volver a subir el mismo archivo) se cuentan como `duplicadas`;
    las de fechas ya conciliadas se omiten (van a `errores`). Si no queda
    ninguna línea nueva no se crea el extracto."""
    try:
        extracto = bancos_calculos.leer_extracto(contenido, mapeo, cuenta=str(cuenta_id))
    except ExtractoInvalidoError as exc:
        raise DatosInvalidosError(str(exc)) from exc
    if not extracto.lineas:
        raise DatosInvalidosError(
            "El archivo no tiene líneas con fecha y monto"
            + (f" ({len(extracto.errores)} filas con errores)" if extracto.errores else "")
        )
    errores = list(extracto.errores)
    async with pool.acquire() as conn:
        async with conn.transaction():
            await _bloquear_cuenta(conn, cuenta_id)
            cuenta = await _cuenta_fila(conn, cuenta_id)
            conciliada = await _conciliada_hasta(conn, cuenta_id)
            lineas = []
            for linea in extracto.lineas:
                if conciliada is not None and linea.fecha <= conciliada:
                    errores.append({
                        "fila": linea.fila,
                        "motivo": f"Fecha {linea.fecha:%d/%m/%Y} dentro de la conciliación cerrada al {conciliada:%d/%m/%Y}",
                    })
                else:
                    lineas.append(linea)
            extracto_id = await conn.fetchval(
                """
                INSERT INTO bancos.extractos (cuenta_id, nombre_archivo, desde, hasta, saldo_final, columnas)
                VALUES ($1, $2, $3, $4, $5, $6::jsonb)
                RETURNING id
                """,
                cuenta["id"],
                nombre_archivo,
                min((x.fecha for x in lineas), default=None),
                max((x.fecha for x in lineas), default=None),
                _redondear(saldo_final) if saldo_final is not None else None,
                json.dumps(extracto.mapeo_por_nombre()),
            )
            importadas = 0
            for linea in lineas:
                insertada = await conn.fetchval(
                    """
                    INSERT INTO bancos.extracto_lineas
                        (extracto_id, cuenta_id, fila, fecha, descripcion, referencia, monto, saldo, huella)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                    ON CONFLICT (cuenta_id, huella) DO NOTHING
                    RETURNING id
                    """,
                    extracto_id, cuenta["id"], linea.fila, linea.fecha, linea.descripcion, linea.referencia,
                    linea.monto, linea.saldo, linea.huella,
                )
                importadas += insertada is not None
            if importadas == 0:
                await conn.execute("DELETE FROM bancos.extractos WHERE id = $1", extracto_id)
                registro = None
            else:
                registro = await _extracto(conn, extracto_id)
    return _resumen_extracto(extracto) | {
        "extracto": registro,
        "importadas": importadas,
        "duplicadas": len(lineas) - importadas,
        "errores": errores,
    }


async def eliminar_extracto(pool: asyncpg.Pool, extracto_id: uuid.UUID) -> None:
    async with pool.acquire() as conn:
        async with conn.transaction():
            cuenta_id = await conn.fetchval("SELECT cuenta_id FROM bancos.extractos WHERE id = $1", extracto_id)
            if cuenta_id is None:
                raise BancoNoEncontradoError(str(extracto_id))
            await _bloquear_cuenta(conn, cuenta_id)
            extracto = await _extracto(conn, extracto_id)
            if extracto["conciliadas"]:
                raise ConflictoBancosError(
                    f"El extracto tiene {extracto['conciliadas']} línea(s) conciliada(s): desconcílielas antes de "
                    "eliminarlo"
                )
            conciliada = await _conciliada_hasta(conn, cuenta_id)
            if conciliada is not None and extracto["desde"] is not None and extracto["desde"] <= conciliada:
                raise ConflictoBancosError(
                    f"El extracto tiene líneas dentro de la conciliación cerrada al {conciliada:%d/%m/%Y}"
                )
            await conn.execute("DELETE FROM bancos.extractos WHERE id = $1", extracto_id)


# --- Conciliación ---------------------------------------------------------

def _fila_linea(fila: asyncpg.Record) -> dict[str, Any]:
    movimiento = None
    if fila["m_id"] is not None:
        movimiento = {
            "origen": fila["m_origen"], "id": fila["m_id"], "fecha": fila["m_fecha"], "tipo": fila["m_tipo"],
            "monto": fila["m_monto"], "descripcion": fila["m_descripcion"], "referencia": fila["m_referencia"],
        }
    return {
        "id": fila["id"], "extracto_id": fila["extracto_id"], "cuenta_id": fila["cuenta_id"], "fila": fila["fila"],
        "fecha": fila["fecha"], "descripcion": fila["descripcion"], "referencia": fila["referencia"],
        "monto": fila["monto"], "saldo": fila["saldo"], "conciliada": movimiento is not None,
        "conciliada_en": fila["conciliada_en"] if movimiento is not None else None, "movimiento": movimiento,
    }


async def _lineas(
    conn: asyncpg.Connection, cuenta_id: uuid.UUID, *, despues_de: date | None, hasta: date | None,
    solo_pendientes: bool = False,
) -> list[asyncpg.Record]:
    return await conn.fetch(
        f"""
        WITH {_cte_cuenta('$1')}
        SELECT * FROM lineas
        WHERE ($2::date IS NULL OR fecha > $2) AND ($3::date IS NULL OR fecha <= $3)
          AND (NOT $4 OR m_id IS NULL)
        ORDER BY fecha, fila, id
        """,
        cuenta_id, despues_de, hasta, solo_pendientes,
    )


async def _movimientos_pendientes(
    conn: asyncpg.Connection, cuenta_id: uuid.UUID, *, hasta: date | None
) -> list[asyncpg.Record]:
    """Movimientos sin conciliar con ninguna línea (de cualquier fecha desde
    el saldo inicial, hasta `hasta`)."""
    return await conn.fetch(
        f"""
        WITH {_cte_movimientos('$1')}
        SELECT m.* FROM movs_c m JOIN bancos.cuentas c ON c.id = m.cuenta_id
        WHERE m.conciliado_con IS NULL AND m.fecha >= c.fecha_saldo_inicial
          AND ($2::date IS NULL OR m.fecha <= $2)
        ORDER BY m.fecha, m.creado_en
        """,
        cuenta_id, hasta,
    )


async def _cuadre(conn: asyncpg.Connection, cuenta: asyncpg.Record, hasta: date) -> dict[str, Any]:
    """Saldos al corte `hasta`: según libros, partidas en tránsito
    (movimientos no conciliados con una línea de fecha <= hasta) y líneas
    del banco sin conciliar después de la última conciliación."""
    fila = await conn.fetchrow(
        f"""
        WITH {_cte_cuenta('$1')}
        SELECT
            (SELECT COALESCE(sum(m.monto), 0) FROM movs m
             WHERE m.cuenta_id = $1 AND m.fecha >= $2 AND m.fecha <= $3) AS neto_libros,
            (SELECT COALESCE(sum(m.monto) FILTER (WHERE m.monto > 0), 0) FROM movs_c m
             WHERE m.cuenta_id = $1 AND m.fecha >= $2 AND m.fecha <= $3
               AND (m.conciliado_con IS NULL OR m.fecha_linea > $3)) AS transito_entradas,
            (SELECT COALESCE(-sum(m.monto) FILTER (WHERE m.monto < 0), 0) FROM movs_c m
             WHERE m.cuenta_id = $1 AND m.fecha >= $2 AND m.fecha <= $3
               AND (m.conciliado_con IS NULL OR m.fecha_linea > $3)) AS transito_salidas,
            (SELECT count(*) FROM movs_c m
             WHERE m.cuenta_id = $1 AND m.fecha >= $2 AND m.fecha <= $3
               AND (m.conciliado_con IS NULL OR m.fecha_linea > $3)) AS movimientos_en_transito,
            (SELECT count(*) FROM lineas l
             WHERE l.cuenta_id = $1 AND ($4::date IS NULL OR l.fecha > $4) AND l.fecha <= $3
               AND (l.m_id IS NULL OR l.m_fecha > $3)) AS lineas_pendientes,
            (SELECT count(*) FROM lineas l
             WHERE l.cuenta_id = $1 AND ($4::date IS NULL OR l.fecha > $4) AND l.fecha <= $3
               AND l.m_id IS NOT NULL AND l.m_fecha <= $3) AS lineas_conciliadas,
            (SELECT COALESCE(sum(l.monto), 0) FROM lineas l
             WHERE l.cuenta_id = $1 AND l.fecha >= $2 AND ($4::date IS NULL OR l.fecha > $4) AND l.fecha <= $3)
                AS neto_lineas,
            (SELECT e.saldo_final FROM bancos.extractos e
             WHERE e.cuenta_id = $1 AND e.hasta = $3 AND e.saldo_final IS NOT NULL
             ORDER BY e.importado_en DESC LIMIT 1) AS saldo_extracto,
            (SELECT saldo_banco FROM bancos.conciliaciones WHERE cuenta_id = $1 AND hasta = $4) AS saldo_anterior
        """,
        cuenta["id"], cuenta["fecha_saldo_inicial"], hasta, await _conciliada_hasta(conn, cuenta["id"]),
    )
    saldo_libros = cuenta["saldo_inicial"] + fila["neto_libros"]
    advertencias: list[str] = []
    saldo_linea = None
    if fila["saldo_extracto"] is None:
        saldo_linea, advertencia = await _saldo_ultima_linea(conn, cuenta["id"], hasta)
        advertencias += [advertencia] if advertencia else []
    if fila["saldo_extracto"] is not None:
        saldo_banco, origen = fila["saldo_extracto"], "extracto"
    elif saldo_linea is not None:
        saldo_banco, origen = saldo_linea, "ultima_linea"
    else:
        base = fila["saldo_anterior"] if fila["saldo_anterior"] is not None else cuenta["saldo_inicial"]
        saldo_banco, origen = base + fila["neto_lineas"], "calculado"
    return {
        "hasta": hasta,
        "saldo_libros": saldo_libros,
        "transito_entradas": fila["transito_entradas"],
        "transito_salidas": fila["transito_salidas"],
        "movimientos_en_transito": fila["movimientos_en_transito"],
        "libros_ajustado": saldo_libros - fila["transito_entradas"] + fila["transito_salidas"],
        "lineas_pendientes": fila["lineas_pendientes"],
        "lineas_conciliadas": fila["lineas_conciliadas"],
        "saldo_banco_sugerido": saldo_banco,
        "origen_saldo_banco": origen,
        "advertencias": advertencias,
    }


async def _saldo_ultima_linea(
    conn: asyncpg.Connection, cuenta_id: uuid.UUID, hasta: date
) -> tuple[Decimal | None, str | None]:
    """Saldo (columna "Balance") de la última línea, en el tiempo, con fecha
    <= `hasta`: la del extracto que tiene la fecha más reciente (el último
    importado si hay varios). Dentro del extracto el orden (más antiguas o
    más recientes primero) sale del saldo corrido; ver
    `bancos_calculos.saldo_al_corte`."""
    extracto_id = await conn.fetchval(
        """
        SELECT l.extracto_id FROM bancos.extracto_lineas l JOIN bancos.extractos e ON e.id = l.extracto_id
        WHERE l.cuenta_id = $1 AND l.fecha <= $2 AND l.saldo IS NOT NULL
        ORDER BY l.fecha DESC, e.importado_en DESC
        LIMIT 1
        """,
        cuenta_id, hasta,
    )
    if extracto_id is None:
        return None, None
    lineas = await conn.fetch(
        "SELECT fila, fecha, monto, saldo FROM bancos.extracto_lineas WHERE extracto_id = $1 ORDER BY fila, id",
        extracto_id,
    )
    return bancos_calculos.saldo_al_corte([dict(x) for x in lineas], hasta)


async def conciliacion(pool: asyncpg.Pool, cuenta_id: uuid.UUID, *, hasta: date | None = None) -> dict[str, Any]:
    """Pantalla de conciliación: líneas del extracto posteriores a la última
    conciliación cerrada (hasta `hasta`), movimientos sin conciliar y el
    cuadre al corte (por defecto, la fecha de la última línea o hoy)."""
    async with pool.acquire() as conn:
        cuenta = await _cuenta_fila(conn, cuenta_id)
        conciliada = await _conciliada_hasta(conn, cuenta_id)
        if hasta is None:
            hasta = await conn.fetchval(
                "SELECT max(fecha) FROM bancos.extracto_lineas WHERE cuenta_id = $1 AND ($2::date IS NULL OR fecha > $2)",
                cuenta_id, conciliada,
            ) or hoy()
        lineas = await _lineas(conn, cuenta_id, despues_de=conciliada, hasta=hasta)
        pendientes = await _movimientos_pendientes(conn, cuenta_id, hasta=hasta)
        cuadre = await _cuadre(conn, cuenta, hasta)
        ultima = await conn.fetchrow(
            "SELECT * FROM bancos.conciliaciones WHERE cuenta_id = $1 ORDER BY hasta DESC LIMIT 1", cuenta_id
        )
        resumen = await _cuenta(conn, cuenta_id)
    diferencia = cuadre["saldo_banco_sugerido"] - cuadre["libros_ajustado"]
    return {
        "cuenta": resumen,
        "conciliada_hasta": conciliada,
        "hasta": hasta,
        "lineas": [_fila_linea(f) for f in lineas],
        "movimientos_sin_conciliar": [_fila_movimiento(f) for f in pendientes],
        "resumen": {
            **{k: cuadre[k] for k in (
                "saldo_libros", "transito_entradas", "transito_salidas", "movimientos_en_transito", "libros_ajustado",
                "lineas_pendientes", "lineas_conciliadas", "origen_saldo_banco", "advertencias",
            )},
            "lineas_total": len(lineas),
            "saldo_banco": cuadre["saldo_banco_sugerido"],
            "diferencia": diferencia,
            "puede_cerrar": cuadre["lineas_pendientes"] == 0 and diferencia == 0 and (conciliada is None or hasta > conciliada),
        },
        "ultima_conciliacion": dict(ultima) if ultima else None,
    }


async def _linea_para_cambiar(conn: asyncpg.Connection, linea_id: uuid.UUID) -> tuple[asyncpg.Record, asyncpg.Record]:
    cuenta_id = await conn.fetchval("SELECT cuenta_id FROM bancos.extracto_lineas WHERE id = $1", linea_id)
    if cuenta_id is None:
        raise BancoNoEncontradoError(str(linea_id))
    await _bloquear_cuenta(conn, cuenta_id)
    linea = await conn.fetchrow(
        f"WITH {_cte_cuenta('$2')} SELECT * FROM lineas WHERE id = $1", linea_id, cuenta_id
    )
    cuenta = await _cuenta_fila(conn, cuenta_id)
    conciliada = await _conciliada_hasta(conn, cuenta_id)
    if conciliada is not None and linea["fecha"] <= conciliada:
        raise ConflictoBancosError(
            f"La línea del {linea['fecha']:%d/%m/%Y} está en la conciliación cerrada al {conciliada:%d/%m/%Y}: "
            "reábrala para cambiarla"
        )
    return linea, cuenta


async def _conciliar(conn: asyncpg.Connection, linea: asyncpg.Record, origen: str, movimiento_id: uuid.UUID) -> None:
    if linea["m_id"] is not None:
        raise ConflictoBancosError("La línea ya está conciliada: desconcíliela para cambiar el movimiento")
    movimiento = await _movimiento(conn, origen, movimiento_id)
    if movimiento is None:
        raise BancoNoEncontradoError(f"{origen}:{movimiento_id}")
    if movimiento["cuenta_id"] != linea["cuenta_id"]:
        raise DatosInvalidosError("El movimiento es de otra cuenta bancaria")
    if movimiento["monto"] != linea["monto"]:
        raise DatosInvalidosError(
            f"Los montos no coinciden (extracto {linea['monto']}, sistema {movimiento['monto']}): registre la diferencia "
            "como un movimiento aparte"
        )
    if movimiento["conciliado"]:
        otra = await conn.fetchval("SELECT fecha FROM bancos.extracto_lineas WHERE id = $1", movimiento["extracto_linea_id"])
        raise ConflictoBancosError(
            f"El movimiento ya está conciliado con la línea del extracto del {otra:%d/%m/%Y}"
        )
    try:
        await conn.execute(
            "UPDATE bancos.extracto_lineas SET mov_origen = $2, mov_id = $3, conciliada_en = now() WHERE id = $1",
            linea["id"], origen, movimiento_id,
        )
    except asyncpg.UniqueViolationError as exc:  # pragma: no cover (el candado de la cuenta lo evita)
        raise ConflictoBancosError("El movimiento ya está conciliado con otra línea") from exc


async def _linea(conn: asyncpg.Connection, linea_id: uuid.UUID) -> dict[str, Any]:
    fila = await conn.fetchrow(
        f"WITH {_cte_cuenta('(SELECT cuenta_id FROM bancos.extracto_lineas WHERE id = $1)')} "
        "SELECT * FROM lineas WHERE id = $1",
        linea_id,
    )
    return _fila_linea(fila)


async def conciliar_linea(
    pool: asyncpg.Pool, linea_id: uuid.UUID, *, origen: str, movimiento_id: uuid.UUID
) -> dict[str, Any]:
    async with pool.acquire() as conn:
        async with conn.transaction():
            linea, _ = await _linea_para_cambiar(conn, linea_id)
            await _conciliar(conn, linea, origen, movimiento_id)
            return await _linea(conn, linea_id)


async def desconciliar_linea(pool: asyncpg.Pool, linea_id: uuid.UUID) -> dict[str, Any]:
    async with pool.acquire() as conn:
        async with conn.transaction():
            linea, _ = await _linea_para_cambiar(conn, linea_id)
            if linea["mov_id"] is None:
                raise ConflictoBancosError("La línea no está conciliada")
            await conn.execute(
                "UPDATE bancos.extracto_lineas SET mov_origen = NULL, mov_id = NULL, conciliada_en = NULL WHERE id = $1",
                linea_id,
            )
            return await _linea(conn, linea_id)


async def crear_movimiento_desde_linea(pool: asyncpg.Pool, linea_id: uuid.UUID, **campos: Any) -> dict[str, Any]:
    """Una línea del banco sin registro en el sistema (cargo bancario,
    comisión, interés, depósito no registrado...) se registra como
    movimiento con su fecha, monto y referencia, y queda conciliada."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            linea, cuenta = await _linea_para_cambiar(conn, linea_id)
            if linea["m_id"] is not None:
                raise ConflictoBancosError("La línea ya está conciliada")
            tipo = campos.get("tipo") or ("deposito" if linea["monto"] > 0 else "cargo_bancario")
            entra = tipo in TIPOS_MOVIMIENTO_ENTRADA
            if entra != (linea["monto"] > 0):
                raise DatosInvalidosError(
                    f"La línea es un {'crédito' if linea['monto'] > 0 else 'débito'} del banco: el tipo «{tipo}» "
                    f"{'resta' if not entra else 'suma'} a la cuenta"
                )
            mov_id = await _insertar_movimiento(
                conn, cuenta, fecha=linea["fecha"], tipo=tipo, monto=abs(linea["monto"]),
                descripcion=campos.get("descripcion") or linea["descripcion"], referencia=linea["referencia"],
                cuenta_contable_id=campos.get("cuenta_contable_id"), tasa_cambio=campos.get("tasa_cambio"),
            )
            await _conciliar(conn, linea, "manual", mov_id)
            return await _linea(conn, linea_id)


def _sugerencia(s: dict[str, Any]) -> dict[str, Any]:
    return {
        "linea": _fila_linea(s["linea"]["fila"]),
        "movimiento": _fila_movimiento(s["movimiento"]["fila"]),
        "puntaje": s["puntaje"],
        "motivo": s["motivo"],
    }


async def _calcular_sugerencias(
    conn: asyncpg.Connection, cuenta_id: uuid.UUID, *, dias: int, hasta: date | None
) -> list[dict[str, Any]]:
    conciliada = await _conciliada_hasta(conn, cuenta_id)
    lineas = await _lineas(conn, cuenta_id, despues_de=conciliada, hasta=hasta, solo_pendientes=True)
    movimientos = await _movimientos_pendientes(conn, cuenta_id, hasta=None)
    return bancos_calculos.sugerir_conciliacion(
        [{"id": f["id"], "fecha": f["fecha"], "monto": f["monto"], "referencia": f["referencia"],
          "descripcion": f["descripcion"], "fila": f} for f in lineas],
        [{"origen": f["origen"], "id": f["id"], "fecha": f["fecha"], "monto": f["monto"],
          "referencia": f["referencia"], "descripcion": f["descripcion"], "fila": f} for f in movimientos],
        dias=dias,
        hasta=hasta,
    )


async def sugerencias(
    pool: asyncpg.Pool, cuenta_id: uuid.UUID, *, dias: int = 3, hasta: date | None = None
) -> list[dict[str, Any]]:
    async with pool.acquire() as conn:
        await _cuenta_fila(conn, cuenta_id)
        return [_sugerencia(s) for s in await _calcular_sugerencias(conn, cuenta_id, dias=dias, hasta=hasta)]


async def conciliar_automatico(
    pool: asyncpg.Pool, cuenta_id: uuid.UUID, *, dias: int = 3, hasta: date | None = None,
    lineas: list[uuid.UUID] | None = None,
) -> dict[str, Any]:
    """Aplica las sugerencias (todas, o solo las de `lineas`)."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            await _bloquear_cuenta(conn, cuenta_id)
            await _cuenta_fila(conn, cuenta_id)
            elegidas = set(lineas) if lineas else None
            aplicadas = []
            for s in await _calcular_sugerencias(conn, cuenta_id, dias=dias, hasta=hasta):
                if elegidas is not None and s["linea"]["id"] not in elegidas:
                    continue
                await conn.execute(
                    "UPDATE bancos.extracto_lineas SET mov_origen = $2, mov_id = $3, conciliada_en = now() WHERE id = $1",
                    s["linea"]["id"], s["movimiento"]["origen"], s["movimiento"]["id"],
                )
                aplicadas.append(_sugerencia(s))
    return {"conciliadas": len(aplicadas), "sugerencias": aplicadas}


async def listar_conciliaciones(pool: asyncpg.Pool, cuenta_id: uuid.UUID) -> list[dict[str, Any]]:
    async with pool.acquire() as conn:
        await _cuenta_fila(conn, cuenta_id)
        filas = await conn.fetch(
            "SELECT * FROM bancos.conciliaciones WHERE cuenta_id = $1 ORDER BY hasta DESC", cuenta_id
        )
    return [dict(f) for f in filas]


async def cerrar_conciliacion(
    pool: asyncpg.Pool, cuenta_id: uuid.UUID, *, hasta: date, saldo_banco: Decimal | None, notas: str | None
) -> dict[str, Any]:
    """Cierra la conciliación de la cuenta al corte `hasta`. 409 si ya está
    conciliada hasta esa fecha o después, si quedan líneas del extracto sin
    conciliar hasta el corte, o si el saldo según el banco (`saldo_banco`, o
    el del extracto / su última línea) no cuadra con libros menos las
    partidas en tránsito."""
    if hasta > hoy():
        raise DatosInvalidosError("No se puede conciliar una fecha futura")
    async with pool.acquire() as conn:
        async with conn.transaction():
            await _bloquear_cuenta(conn, cuenta_id)
            cuenta = await _cuenta_fila(conn, cuenta_id)
            conciliada = await _conciliada_hasta(conn, cuenta_id)
            if conciliada is not None and hasta <= conciliada:
                raise ConflictoBancosError(
                    f"La cuenta {_nombre_cuenta(cuenta)} ya está conciliada hasta el {conciliada:%d/%m/%Y}"
                )
            if hasta < cuenta["fecha_saldo_inicial"]:
                raise DatosInvalidosError("El corte es anterior al saldo inicial de la cuenta")
            cuadre = await _cuadre(conn, cuenta, hasta)
            if cuadre["lineas_pendientes"]:
                raise ConflictoBancosError(
                    f"Hay {cuadre['lineas_pendientes']} línea(s) del extracto sin conciliar hasta el {hasta:%d/%m/%Y}: "
                    "concílielas con su movimiento o regístrelas (cargo bancario, comisión, interés…) antes de cerrar"
                )
            saldo = _redondear(saldo_banco) if saldo_banco is not None else cuadre["saldo_banco_sugerido"]
            diferencia = saldo - cuadre["libros_ajustado"]
            if diferencia != 0:
                raise ConflictoBancosError(
                    f"El saldo según el banco (RD$ {saldo:,.2f}) no cuadra con el saldo en libros menos las partidas en "
                    f"tránsito (RD$ {cuadre['libros_ajustado']:,.2f}): diferencia RD$ {diferencia:,.2f}. Revise que el "
                    "extracto esté completo y el saldo inicial de la cuenta"
                )
            fila = await conn.fetchrow(
                """
                INSERT INTO bancos.conciliaciones
                    (cuenta_id, hasta, saldo_banco, saldo_libros, transito_entradas, transito_salidas, diferencia,
                     lineas_conciliadas, movimientos_en_transito, notas)
                VALUES ($1, $2, $3, $4, $5, $6, 0, $7, $8, $9)
                RETURNING *
                """,
                cuenta_id, hasta, saldo, cuadre["saldo_libros"], cuadre["transito_entradas"], cuadre["transito_salidas"],
                cuadre["lineas_conciliadas"], cuadre["movimientos_en_transito"], notas,
            )
    return dict(fila)


async def reabrir_conciliacion(pool: asyncpg.Pool, conciliacion_id: uuid.UUID) -> None:
    """Borra la conciliación (solo la última de su cuenta)."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            fila = await conn.fetchrow("SELECT * FROM bancos.conciliaciones WHERE id = $1", conciliacion_id)
            if fila is None:
                raise BancoNoEncontradoError(str(conciliacion_id))
            await _bloquear_cuenta(conn, fila["cuenta_id"])
            if await _conciliada_hasta(conn, fila["cuenta_id"]) != fila["hasta"]:
                raise ConflictoBancosError("Solo se puede reabrir la última conciliación de la cuenta")
            await conn.execute("DELETE FROM bancos.conciliaciones WHERE id = $1", conciliacion_id)
