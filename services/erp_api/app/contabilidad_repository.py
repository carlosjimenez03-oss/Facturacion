"""Acceso a Postgres de Contabilidad (fase 5): catálogo de cuentas, cuentas
por defecto, asientos manuales y automáticos, reportes (libro diario, mayor,
balanza de comprobación, estado de resultados, balance general) y activos
fijos con su depreciación.

**Asientos automáticos** (ver contabilidad_calculos y docs/ERP_API.md,
"Contabilidad"): `generar_asientos(desde, hasta)` lee los documentos del
rango -- e-CF de venta aceptados, cobros de facturas aceptadas, renglones
del 606, pagos a proveedores, movimientos y transferencias bancarias y el
ajuste mensual por proporcionalidad del ITBIS --, arma su asiento,
resuelve los conceptos a cuentas con `contabilidad.cuentas_por_defecto` y:

- crea el que falta (idempotente por (`origen`, `origen_id`));
- deja igual el que ya existe con la misma `huella`;
- si el documento o las cuentas por defecto cambiaron, anula el anterior y
  crea uno nuevo;
- anula el de un documento que ya no existe o se anuló (cobro/pago anulado,
  e-CF rechazado, renglón del 606 borrado).

Lo que no se puede contabilizar (sin cuenta por defecto, 606 sin
clasificar, venta gratuita...) va a `no_contabilizados` con el motivo (y si
el documento tenía asiento, se anula). La compra y los pagos de un mismo
renglón del 606 se regeneran juntos (`_relacionados_606`). Nunca
corre dentro de las transacciones de ventas/compras/bancos: es a pedido
(`POST /contabilidad/asientos/generar`) y no bloquea esos flujos.
"""
from __future__ import annotations

import json
import re
import time
import uuid
from datetime import date
from decimal import Decimal
from typing import Any

import asyncpg

from . import contabilidad_calculos as cc
from . import impuestos_repository, precalculos
from .impuestos_repository import ConflictoImpuestosError
from .compras_repository import _ES_NOTA_CREDITO, _TOTAL_CXP, _exigir_no_presentada, _fecha_606
from .contabilidad_calculos import AsientoPropuesto, NoContabilizableError
from .formatos import TIPOS_VENTA_607
from .precalculos import nombre_periodo
from .repository import _FECHA_EMISION, DatosInvalidosError, _redondear, hoy

_CERO = Decimal("0")


class ContabilidadNoEncontradaError(Exception):
    """Cuenta, asiento o activo inexistente (404)."""


class ConflictoContabilidadError(Exception):
    """La operación no aplica en el estado actual (409)."""


def numero_asiento(numero: int) -> str:
    return f"AS-{numero:04d}"


def _validar_rango(desde: date, hasta: date) -> None:
    if desde > hasta:
        raise DatosInvalidosError("«desde» es posterior a «hasta»")


# --- Catálogo de cuentas ---------------------------------------------------------

_SELECT_CUENTAS = """
    SELECT c.*, p.codigo AS padre_codigo,
           (SELECT count(*) FROM contabilidad.cuentas h WHERE h.padre_id = c.id)::int AS hijos,
           EXISTS (SELECT 1 FROM contabilidad.asiento_lineas l WHERE l.cuenta_id = c.id) AS tiene_movimientos
    FROM contabilidad.cuentas c
    LEFT JOIN contabilidad.cuentas p ON p.id = c.padre_id
"""


def _nivel(codigo: str) -> int:
    return codigo.count(".") + 1


def _con_signo(naturaleza: str, debe_menos_haber: Decimal) -> Decimal:
    return debe_menos_haber if naturaleza == "deudora" else -debe_menos_haber


async def _saldos_brutos(
    conn: asyncpg.Connection, *, desde: date | None = None, hasta: date | None = None
) -> dict[uuid.UUID, tuple[Decimal, Decimal]]:
    """(débitos, créditos) contabilizados por cuenta con fecha en [desde, hasta]."""
    filas = await conn.fetch(
        """
        SELECT l.cuenta_id, sum(l.debito) AS debitos, sum(l.credito) AS creditos
        FROM contabilidad.asiento_lineas l
        JOIN contabilidad.asientos a ON a.id = l.asiento_id
        WHERE a.estado = 'contabilizado' AND ($1::date IS NULL OR a.fecha >= $1) AND ($2::date IS NULL OR a.fecha <= $2)
        GROUP BY l.cuenta_id
        """,
        desde, hasta,
    )
    return {f["cuenta_id"]: (f["debitos"], f["creditos"]) for f in filas}


def _acumular(cuentas: list[dict[str, Any]], brutos: dict[uuid.UUID, tuple[Decimal, Decimal]]) -> dict[uuid.UUID, tuple[Decimal, Decimal]]:
    """Suma a cada cuenta los débitos/créditos de sus descendientes (por código)."""
    por_codigo = {c["codigo"]: c["id"] for c in cuentas}
    total: dict[uuid.UUID, list[Decimal]] = {c["id"]: [_CERO, _CERO] for c in cuentas}
    codigos = {c["id"]: c["codigo"] for c in cuentas}
    for cuenta_id, (debitos, creditos) in brutos.items():
        codigo = codigos.get(cuenta_id)
        while codigo is not None:
            destino = por_codigo.get(codigo)
            if destino is not None:
                total[destino][0] += debitos
                total[destino][1] += creditos
            codigo = cc.codigo_padre(codigo)
    return {k: (v[0], v[1]) for k, v in total.items()}


async def _catalogo(conn: asyncpg.Connection) -> list[dict[str, Any]]:
    filas = await conn.fetch(f"{_SELECT_CUENTAS} ORDER BY string_to_array(c.codigo, '.')::int[]")
    return [dict(f) | {"nivel": _nivel(f["codigo"])} for f in filas]


async def listar_cuentas(
    pool: asyncpg.Pool, *, hasta: date | None = None, q: str | None = None, solo_movimiento: bool = False,
    activa: bool | None = None,
) -> list[dict[str, Any]]:
    """Catálogo ordenado por código (árbol aplanado, con `nivel` y `hijos`) y
    el saldo de cada cuenta al `hasta` (por defecto hoy), con sus
    descendientes, según su naturaleza."""
    async with pool.acquire() as conn:
        cuentas = await _catalogo(conn)
        brutos = await _saldos_brutos(conn, hasta=hasta or hoy())
    acumulado = _acumular(cuentas, brutos)
    resultado = []
    for c in cuentas:
        if q and q.lower() not in c["nombre"].lower() and not c["codigo"].startswith(q):
            continue
        if solo_movimiento and not c["acepta_movimientos"]:
            continue
        if activa is not None and c["activa"] != activa:
            continue
        debitos, creditos = acumulado[c["id"]]
        resultado.append(c | {"saldo": _con_signo(c["naturaleza"], debitos - creditos)})
    return resultado


async def _cuenta(conn: asyncpg.Connection, cuenta_id: uuid.UUID) -> dict[str, Any]:
    fila = await conn.fetchrow(f"{_SELECT_CUENTAS} WHERE c.id = $1", cuenta_id)
    if fila is None:
        raise ContabilidadNoEncontradaError(str(cuenta_id))
    brutos = await _saldos_brutos(conn, hasta=hoy())
    debitos, creditos = _acumular([dict(f) for f in await conn.fetch(
        "SELECT id, codigo FROM contabilidad.cuentas WHERE codigo = $1 OR codigo LIKE $1 || '.%'", fila["codigo"]
    )], brutos)[fila["id"]]
    return dict(fila) | {"nivel": _nivel(fila["codigo"]), "saldo": _con_signo(fila["naturaleza"], debitos - creditos)}


async def obtener_cuenta(pool: asyncpg.Pool, cuenta_id: uuid.UUID) -> dict[str, Any]:
    async with pool.acquire() as conn:
        return await _cuenta(conn, cuenta_id)


_REGEX_CODIGO = re.compile(r"^[0-9]+(\.[0-9]+)*$")


async def _padre_de(conn: asyncpg.Connection, codigo: str) -> asyncpg.Record | None:
    padre = cc.codigo_padre(codigo)
    if padre is None:
        return None
    fila = await conn.fetchrow(
        "SELECT c.*, EXISTS (SELECT 1 FROM contabilidad.asiento_lineas l WHERE l.cuenta_id = c.id) AS tiene_movimientos"
        " FROM contabilidad.cuentas c WHERE codigo = $1 FOR UPDATE",
        padre,
    )
    if fila is None:
        raise DatosInvalidosError(f"No existe la cuenta {padre}: créela antes que {codigo}")
    return fila


async def _preparar_padre(conn: asyncpg.Connection, padre: asyncpg.Record) -> None:
    """Una cuenta con hijas es de grupo: deja de aceptar movimientos (409 si ya
    tiene líneas de asiento o es una cuenta por defecto / de un banco)."""
    if not padre["acepta_movimientos"]:
        return
    if padre["tiene_movimientos"]:
        raise ConflictoContabilidadError(
            f"La cuenta {padre['codigo']} {padre['nombre']} tiene movimientos: no puede tener subcuentas"
        )
    await _exigir_sin_usos(conn, padre, "tener subcuentas")
    await conn.execute(
        "UPDATE contabilidad.cuentas SET acepta_movimientos = false, actualizado_en = now() WHERE id = $1", padre["id"]
    )


async def _exigir_sin_usos(conn: asyncpg.Connection, cuenta: asyncpg.Record, accion: str) -> None:
    usos = await conn.fetchrow(
        """
        SELECT (SELECT string_agg(concepto, ', ') FROM contabilidad.cuentas_por_defecto WHERE cuenta_id = $1) AS conceptos,
               (SELECT string_agg(nombre, ', ') FROM bancos.cuentas WHERE cuenta_contable_id = $1) AS bancos,
               (SELECT count(*) FROM contabilidad.activos_fijos WHERE cuenta_activo_id = $1) AS activos,
               (SELECT count(*) FROM bancos.movimientos WHERE cuenta_contable_id = $1) AS movimientos
        """,
        cuenta["id"],
    )
    if usos["conceptos"]:
        etiquetas = ", ".join(cc.CONCEPTOS.get(c, ("", c))[1] for c in usos["conceptos"].split(", "))
        raise ConflictoContabilidadError(
            f"La cuenta {cuenta['codigo']} {cuenta['nombre']} es la cuenta por defecto de: {etiquetas}. Cámbiela en "
            f"«Cuentas por defecto» para poder {accion}"
        )
    if usos["bancos"]:
        raise ConflictoContabilidadError(
            f"La cuenta {cuenta['codigo']} es la cuenta contable de {usos['bancos']} (Bancos): no se puede {accion}"
        )
    if usos["activos"] or usos["movimientos"]:
        raise ConflictoContabilidadError(
            f"La cuenta {cuenta['codigo']} está asignada a activos fijos o movimientos bancarios: no se puede {accion}"
        )


async def crear_cuenta(pool: asyncpg.Pool, **campos: Any) -> dict[str, Any]:
    """El padre sale del código ("1.1.01.05" -> "1.1.01"); el tipo, del
    padre (o del primer dígito en una cuenta raíz); la naturaleza por
    defecto, del tipo. Si el padre aceptaba movimientos, pasa a ser de
    grupo."""
    codigo = campos["codigo"].strip()
    if not _REGEX_CODIGO.fullmatch(codigo):
        raise DatosInvalidosError("Código inválido: use dígitos separados por puntos (p. ej. 1.1.01.04)")
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext('contabilidad.catalogo'))")
            if await conn.fetchval("SELECT 1 FROM contabilidad.cuentas WHERE codigo = $1", codigo):
                raise ConflictoContabilidadError(f"Ya existe la cuenta {codigo}")
            padre = await _padre_de(conn, codigo)
            tipo = padre["tipo"] if padre is not None else (campos.get("tipo") or cc.GRUPO_TIPO.get(codigo[0]))
            if tipo is None:
                raise DatosInvalidosError("Indique el tipo de la cuenta (activo, pasivo, patrimonio, ingreso, costo o gasto)")
            if padre is not None and campos.get("tipo") and campos["tipo"] != tipo:
                raise DatosInvalidosError(f"La cuenta {padre['codigo']} es de {tipo}: sus subcuentas también")
            naturaleza = campos.get("naturaleza") or (
                padre["naturaleza"] if padre is not None else cc.NATURALEZA_POR_TIPO[tipo]
            )
            if padre is not None:
                await _preparar_padre(conn, padre)
            cuenta_id = await conn.fetchval(
                """
                INSERT INTO contabilidad.cuentas
                    (codigo, nombre, tipo, naturaleza, acepta_movimientos, padre_id, activa, descripcion)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                RETURNING id
                """,
                codigo, campos["nombre"], tipo, naturaleza, campos.get("acepta_movimientos", True),
                padre["id"] if padre is not None else None, campos.get("activa", True), campos.get("descripcion"),
            )
            return await _cuenta(conn, cuenta_id)


async def actualizar_cuenta(pool: asyncpg.Pool, cuenta_id: uuid.UUID, campos: dict[str, Any]) -> dict[str, Any]:
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext('contabilidad.catalogo'))")
            actual = await conn.fetchrow(f"{_SELECT_CUENTAS} WHERE c.id = $1", cuenta_id)
            if actual is None:
                raise ContabilidadNoEncontradaError(str(cuenta_id))
            nuevos = {k: v for k, v in campos.items() if k in (
                "codigo", "nombre", "naturaleza", "acepta_movimientos", "activa", "descripcion",
            )}
            for requerido in ("codigo", "nombre", "naturaleza", "acepta_movimientos", "activa"):
                if requerido in nuevos and nuevos[requerido] is None:
                    raise DatosInvalidosError(f"«{requerido}» no puede quedar vacío")
            if "codigo" in nuevos and nuevos["codigo"] != actual["codigo"]:
                codigo = nuevos["codigo"].strip()
                nuevos["codigo"] = codigo
                if not _REGEX_CODIGO.fullmatch(codigo):
                    raise DatosInvalidosError("Código inválido: use dígitos separados por puntos")
                if actual["hijos"]:
                    raise ConflictoContabilidadError("La cuenta tiene subcuentas: no se puede cambiar su código")
                if await conn.fetchval("SELECT 1 FROM contabilidad.cuentas WHERE codigo = $1", codigo):
                    raise ConflictoContabilidadError(f"Ya existe la cuenta {codigo}")
                padre = await _padre_de(conn, codigo)
                if padre is not None and padre["id"] == cuenta_id:
                    raise DatosInvalidosError("Una cuenta no puede ser su propio padre")
                tipo_padre = padre["tipo"] if padre is not None else cc.GRUPO_TIPO.get(codigo[0], actual["tipo"])
                if tipo_padre != actual["tipo"]:
                    raise DatosInvalidosError(f"El código {codigo} es de {tipo_padre}; la cuenta es de {actual['tipo']}")
                if padre is not None:
                    await _preparar_padre(conn, padre)
                nuevos["padre_id"] = padre["id"] if padre is not None else None
            if nuevos.get("acepta_movimientos") is True and actual["hijos"]:
                raise ConflictoContabilidadError("Una cuenta con subcuentas es de grupo: no acepta movimientos")
            if nuevos.get("acepta_movimientos") is False and actual["acepta_movimientos"]:
                if actual["tiene_movimientos"]:
                    raise ConflictoContabilidadError("La cuenta tiene movimientos: debe seguir aceptándolos")
                await _exigir_sin_usos(conn, actual, "convertirla en cuenta de grupo")
            if nuevos.get("activa") is False and actual["activa"]:
                await _exigir_sin_usos(conn, actual, "desactivarla")
            if nuevos:
                columnas = list(nuevos)
                asignaciones = ", ".join(f"{c} = ${i + 2}" for i, c in enumerate(columnas))
                await conn.execute(
                    f"UPDATE contabilidad.cuentas SET {asignaciones}, actualizado_en = now() WHERE id = $1",
                    cuenta_id, *[nuevos[c] for c in columnas],
                )
            return await _cuenta(conn, cuenta_id)


async def eliminar_cuenta(pool: asyncpg.Pool, cuenta_id: uuid.UUID) -> None:
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext('contabilidad.catalogo'))")
            actual = await conn.fetchrow(f"{_SELECT_CUENTAS} WHERE c.id = $1", cuenta_id)
            if actual is None:
                raise ContabilidadNoEncontradaError(str(cuenta_id))
            if actual["hijos"]:
                raise ConflictoContabilidadError("La cuenta tiene subcuentas: elimínelas primero")
            if actual["tiene_movimientos"]:
                raise ConflictoContabilidadError(
                    f"La cuenta {actual['codigo']} tiene movimientos: desactívela en lugar de eliminarla"
                )
            await _exigir_sin_usos(conn, actual, "eliminarla")
            await conn.execute("DELETE FROM contabilidad.cuentas WHERE id = $1", cuenta_id)
            # Sin hijas, el padre vuelve a ser una cuenta de movimiento.
            await conn.execute(
                """
                UPDATE contabilidad.cuentas p SET acepta_movimientos = true, actualizado_en = now()
                WHERE p.id = $1 AND NOT EXISTS (SELECT 1 FROM contabilidad.cuentas h WHERE h.padre_id = p.id)
                """,
                actual["padre_id"],
            )


# --- Cuentas por defecto ---------------------------------------------------------

async def listar_cuentas_por_defecto(pool: asyncpg.Pool) -> list[dict[str, Any]]:
    async with pool.acquire() as conn:
        filas = await conn.fetch(
            """
            SELECT d.concepto, c.id AS cuenta_id, c.codigo AS cuenta_codigo, c.nombre AS cuenta_nombre
            FROM contabilidad.cuentas_por_defecto d JOIN contabilidad.cuentas c ON c.id = d.cuenta_id
            """
        )
    asignadas = {f["concepto"]: f for f in filas}
    return [
        {
            "concepto": concepto, "grupo": grupo, "etiqueta": etiqueta, "codigo_sugerido": codigo,
            "cuenta_id": asignadas[concepto]["cuenta_id"] if concepto in asignadas else None,
            "cuenta_codigo": asignadas[concepto]["cuenta_codigo"] if concepto in asignadas else None,
            "cuenta_nombre": asignadas[concepto]["cuenta_nombre"] if concepto in asignadas else None,
        }
        for concepto, (grupo, etiqueta, codigo) in cc.CONCEPTOS.items()
    ]


async def actualizar_cuentas_por_defecto(pool: asyncpg.Pool, valores: dict[str, uuid.UUID | None]) -> list[dict[str, Any]]:
    """`valores`: concepto -> cuenta (null = sin cuenta). Los asientos
    automáticos ya generados se actualizan la próxima vez que se generan."""
    desconocidos = sorted(set(valores) - set(cc.CONCEPTOS))
    if desconocidos:
        raise DatosInvalidosError(f"Concepto(s) desconocido(s): {', '.join(desconocidos)}")
    async with pool.acquire() as conn:
        async with conn.transaction():
            for concepto, cuenta_id in valores.items():
                if cuenta_id is None:
                    await conn.execute("DELETE FROM contabilidad.cuentas_por_defecto WHERE concepto = $1", concepto)
                    continue
                cuenta = await conn.fetchrow(
                    "SELECT codigo, nombre, acepta_movimientos, activa FROM contabilidad.cuentas WHERE id = $1", cuenta_id
                )
                if cuenta is None:
                    raise DatosInvalidosError(f"La cuenta de «{cc.CONCEPTOS[concepto][1]}» no existe")
                if not cuenta["acepta_movimientos"] or not cuenta["activa"]:
                    raise DatosInvalidosError(
                        f"{cuenta['codigo']} {cuenta['nombre']} no acepta movimientos: elija una subcuenta para "
                        f"«{cc.CONCEPTOS[concepto][1]}»"
                    )
                await conn.execute(
                    """
                    INSERT INTO contabilidad.cuentas_por_defecto (concepto, cuenta_id) VALUES ($1, $2)
                    ON CONFLICT (concepto) DO UPDATE SET cuenta_id = EXCLUDED.cuenta_id, actualizado_en = now()
                    """,
                    concepto, cuenta_id,
                )
    return await listar_cuentas_por_defecto(pool)


# --- Asientos ---------------------------------------------------------

_ETIQUETA_ORIGEN = {
    "manual": "Manual", "venta": "Factura", "nota_credito": "Nota de crédito", "nota_debito": "Nota de débito",
    "cobro": "Cobro", "compra": "Compra", "pago": "Pago a proveedor", "movimiento": "Banco",
    "transferencia": "Transferencia", "depreciacion": "Depreciación",
}

_SELECT_ASIENTOS = """
    SELECT a.*,
           COALESCE((SELECT sum(debito) FROM contabilidad.asiento_lineas WHERE asiento_id = a.id), 0) AS total_debito,
           COALESCE((SELECT sum(credito) FROM contabilidad.asiento_lineas WHERE asiento_id = a.id), 0) AS total_credito
    FROM contabilidad.asientos a
"""


def _fila_asiento(fila: asyncpg.Record) -> dict[str, Any]:
    datos = dict(fila)
    datos.pop("huella", None)
    datos["numero"] = numero_asiento(datos["numero"])
    datos["automatico"] = datos["origen"] != "manual"
    datos["origen_etiqueta"] = _ETIQUETA_ORIGEN.get(datos["origen"], datos["origen"])
    datos["cuadrado"] = datos["total_debito"] == datos["total_credito"] and datos["total_debito"] > 0
    return datos


async def _asiento(conn: asyncpg.Connection, asiento_id: uuid.UUID) -> dict[str, Any]:
    fila = await conn.fetchrow(f"{_SELECT_ASIENTOS} WHERE a.id = $1", asiento_id)
    if fila is None:
        raise ContabilidadNoEncontradaError(str(asiento_id))
    lineas = await conn.fetch(
        """
        SELECT l.id, l.linea, l.cuenta_id, c.codigo AS cuenta_codigo, c.nombre AS cuenta_nombre, l.descripcion,
               l.debito, l.credito
        FROM contabilidad.asiento_lineas l JOIN contabilidad.cuentas c ON c.id = l.cuenta_id
        WHERE l.asiento_id = $1 ORDER BY l.linea
        """,
        asiento_id,
    )
    return _fila_asiento(fila) | {"lineas": [dict(x) for x in lineas]}


async def obtener_asiento(pool: asyncpg.Pool, asiento_id: uuid.UUID) -> dict[str, Any]:
    async with pool.acquire() as conn:
        return await _asiento(conn, asiento_id)


async def listar_asientos(
    pool: asyncpg.Pool,
    *,
    desde: date | None = None,
    hasta: date | None = None,
    origen: str | None = None,
    estado: str | None = None,
    q: str | None = None,
    cuenta_id: uuid.UUID | None = None,
    limite: int = 200,
    offset: int = 0,
) -> list[dict[str, Any]]:
    condiciones, valores = [], []
    for columna, valor, operador in (("a.fecha", desde, ">="), ("a.fecha", hasta, "<="), ("a.estado", estado, "=")):
        if valor is not None:
            valores.append(valor)
            condiciones.append(f"{columna} {operador} ${len(valores)}")
    if origen == "automatico":
        condiciones.append("a.origen <> 'manual'")
    elif origen is not None:
        valores.append(origen)
        condiciones.append(f"a.origen = ${len(valores)}")
    if q:
        valores.append(f"%{q}%")
        n = len(valores)
        condiciones.append(f"(a.concepto ILIKE ${n} OR a.referencia ILIKE ${n} OR 'AS-' || lpad(a.numero::text, 4, '0') ILIKE ${n})")
    if cuenta_id is not None:
        valores.append(cuenta_id)
        condiciones.append(
            f"EXISTS (SELECT 1 FROM contabilidad.asiento_lineas l WHERE l.asiento_id = a.id AND l.cuenta_id = ${len(valores)})"
        )
    where = f"WHERE {' AND '.join(condiciones)}" if condiciones else ""
    valores.extend([limite, offset])
    async with pool.acquire() as conn:
        filas = await conn.fetch(
            f"{_SELECT_ASIENTOS} {where} ORDER BY a.fecha DESC, a.numero DESC "
            f"LIMIT ${len(valores) - 1} OFFSET ${len(valores)}",
            *valores,
        )
    return [_fila_asiento(f) for f in filas]


async def _validar_lineas(conn: asyncpg.Connection, lineas: list[dict[str, Any]], *, exigir_cuadre: bool) -> list[dict[str, Any]]:
    if len(lineas) < 2:
        raise DatosInvalidosError("El asiento necesita al menos dos líneas")
    limpias = []
    for numero, linea in enumerate(lineas, start=1):
        debito = _redondear(linea.get("debito") or _CERO)
        credito = _redondear(linea.get("credito") or _CERO)
        if debito < 0 or credito < 0:
            raise DatosInvalidosError(f"Línea {numero}: el débito y el crédito no pueden ser negativos")
        if (debito > 0) == (credito > 0):
            raise DatosInvalidosError(f"Línea {numero}: indique un débito o un crédito (uno solo)")
        cuenta = await conn.fetchrow(
            "SELECT codigo, nombre, acepta_movimientos, activa FROM contabilidad.cuentas WHERE id = $1",
            linea["cuenta_id"],
        )
        if cuenta is None:
            raise DatosInvalidosError(f"Línea {numero}: la cuenta no existe")
        if not cuenta["acepta_movimientos"]:
            raise DatosInvalidosError(
                f"Línea {numero}: {cuenta['codigo']} {cuenta['nombre']} es una cuenta de grupo: use una subcuenta"
            )
        if not cuenta["activa"]:
            raise DatosInvalidosError(f"Línea {numero}: la cuenta {cuenta['codigo']} {cuenta['nombre']} está inactiva")
        limpias.append({"cuenta_id": linea["cuenta_id"], "descripcion": linea.get("descripcion"), "debito": debito,
                        "credito": credito})
    if exigir_cuadre:
        debito, credito = cc.totales(limpias)
        if debito != credito:
            raise DatosInvalidosError(
                f"El asiento no cuadra: débitos RD$ {debito:,.2f}, créditos RD$ {credito:,.2f} "
                f"(diferencia RD$ {abs(debito - credito):,.2f})"
            )
    return limpias


async def _insertar_lineas(conn: asyncpg.Connection, asiento_id: uuid.UUID, lineas: list[dict[str, Any]]) -> None:
    await conn.executemany(
        """
        INSERT INTO contabilidad.asiento_lineas (asiento_id, linea, cuenta_id, descripcion, debito, credito)
        VALUES ($1, $2, $3, $4, $5, $6)
        """,
        [(asiento_id, i, x["cuenta_id"], x.get("descripcion"), x["debito"], x["credito"]) for i, x in enumerate(lineas, 1)],
    )


async def crear_asiento(pool: asyncpg.Pool, **campos: Any) -> dict[str, Any]:
    """Asiento manual. Borrador: puede quedar descuadrado; `contabilizar`:
    debe cuadrar (422)."""
    contabilizar = bool(campos.get("contabilizar"))
    async with pool.acquire() as conn:
        async with conn.transaction():
            lineas = await _validar_lineas(conn, campos["lineas"], exigir_cuadre=contabilizar)
            asiento_id = await conn.fetchval(
                """
                INSERT INTO contabilidad.asientos (fecha, concepto, referencia, estado, origen, total, contabilizado_en)
                VALUES ($1, $2, $3, $4, 'manual', $5, CASE WHEN $4 = 'contabilizado' THEN now() END)
                RETURNING id
                """,
                campos.get("fecha") or hoy(), campos["concepto"], campos.get("referencia"),
                "contabilizado" if contabilizar else "borrador", cc.totales(lineas)[0],
            )
            await _insertar_lineas(conn, asiento_id, lineas)
            return await _asiento(conn, asiento_id)


async def _asiento_bloqueado(conn: asyncpg.Connection, asiento_id: uuid.UUID) -> asyncpg.Record:
    fila = await conn.fetchrow("SELECT * FROM contabilidad.asientos WHERE id = $1 FOR UPDATE", asiento_id)
    if fila is None:
        raise ContabilidadNoEncontradaError(str(asiento_id))
    return fila


def _exigir_manual(fila: asyncpg.Record, accion: str) -> None:
    if fila["origen"] != "manual":
        raise ConflictoContabilidadError(
            f"El asiento {numero_asiento(fila['numero'])} lo genera el sistema desde «{_ETIQUETA_ORIGEN[fila['origen']]}»: "
            f"no se puede {accion}. Corrija el documento de origen y vuelva a generar los asientos"
        )


async def actualizar_asiento(pool: asyncpg.Pool, asiento_id: uuid.UUID, **campos: Any) -> dict[str, Any]:
    async with pool.acquire() as conn:
        async with conn.transaction():
            fila = await _asiento_bloqueado(conn, asiento_id)
            _exigir_manual(fila, "editar")
            if fila["estado"] != "borrador":
                raise ConflictoContabilidadError(
                    f"El asiento {numero_asiento(fila['numero'])} está {fila['estado']}: solo se editan borradores "
                    "(anúlelo y registre otro)"
                )
            contabilizar = bool(campos.get("contabilizar"))
            lineas = await _validar_lineas(conn, campos["lineas"], exigir_cuadre=contabilizar)
            await conn.execute("DELETE FROM contabilidad.asiento_lineas WHERE asiento_id = $1", asiento_id)
            await _insertar_lineas(conn, asiento_id, lineas)
            await conn.execute(
                """
                UPDATE contabilidad.asientos
                SET fecha = $2, concepto = $3, referencia = $4, total = $5, estado = $6,
                    contabilizado_en = CASE WHEN $6 = 'contabilizado' THEN now() END, actualizado_en = now()
                WHERE id = $1
                """,
                asiento_id, campos.get("fecha") or fila["fecha"], campos["concepto"], campos.get("referencia"),
                cc.totales(lineas)[0], "contabilizado" if contabilizar else "borrador",
            )
            return await _asiento(conn, asiento_id)


async def contabilizar_asiento(pool: asyncpg.Pool, asiento_id: uuid.UUID) -> dict[str, Any]:
    async with pool.acquire() as conn:
        async with conn.transaction():
            fila = await _asiento_bloqueado(conn, asiento_id)
            if fila["estado"] != "borrador":
                raise ConflictoContabilidadError(f"El asiento {numero_asiento(fila['numero'])} ya está {fila['estado']}")
            lineas = await conn.fetch(
                "SELECT cuenta_id, descripcion, debito, credito FROM contabilidad.asiento_lineas WHERE asiento_id = $1",
                asiento_id,
            )
            await _validar_lineas(conn, [dict(x) for x in lineas], exigir_cuadre=True)
            await conn.execute(
                "UPDATE contabilidad.asientos SET estado = 'contabilizado', contabilizado_en = now(), actualizado_en = now()"
                " WHERE id = $1",
                asiento_id,
            )
            return await _asiento(conn, asiento_id)


async def _anular(conn: asyncpg.Connection, asiento_id: uuid.UUID, motivo: str | None) -> None:
    await conn.execute(
        """
        UPDATE contabilidad.asientos
        SET estado = 'anulado', anulado_en = now(), motivo_anulacion = $2, actualizado_en = now()
        WHERE id = $1
        """,
        asiento_id, motivo,
    )
    # La depreciación del asiento deja de estar registrada (se puede volver a calcular).
    await conn.execute("DELETE FROM contabilidad.depreciaciones WHERE asiento_id = $1", asiento_id)


async def anular_asiento(pool: asyncpg.Pool, asiento_id: uuid.UUID, motivo: str | None = None) -> dict[str, Any]:
    """Manual contabilizado o depreciación del mes. Los demás automáticos se
    anulan solos al anular su documento (409)."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            fila = await _asiento_bloqueado(conn, asiento_id)
            if fila["origen"] != "depreciacion":
                _exigir_manual(fila, "anular")
            if fila["estado"] == "anulado":
                raise ConflictoContabilidadError(f"El asiento {numero_asiento(fila['numero'])} ya está anulado")
            if fila["estado"] == "borrador":
                raise ConflictoContabilidadError("Un borrador no se anula: elimínelo")
            await _anular(conn, asiento_id, motivo)
            return await _asiento(conn, asiento_id)


async def eliminar_asiento(pool: asyncpg.Pool, asiento_id: uuid.UUID) -> None:
    async with pool.acquire() as conn:
        async with conn.transaction():
            fila = await _asiento_bloqueado(conn, asiento_id)
            if fila["estado"] != "borrador":
                raise ConflictoContabilidadError(
                    f"El asiento {numero_asiento(fila['numero'])} está {fila['estado']}: solo se eliminan borradores"
                )
            await conn.execute("DELETE FROM contabilidad.asientos WHERE id = $1", asiento_id)


# --- Generación de asientos automáticos ---------------------------------------------------------

class _Contexto:
    """Cuentas por defecto y cuentas bancarias, para resolver las claves de
    las líneas propuestas a cuentas del catálogo."""

    def __init__(self, mapeo: dict[str, asyncpg.Record], cuentas: dict[uuid.UUID, asyncpg.Record],
                 bancos: dict[uuid.UUID, asyncpg.Record]):
        self.mapeo = mapeo
        self.cuentas = cuentas
        self.bancos = bancos

    def clave_banco(self, cuenta_bancaria_id: uuid.UUID | None, metodo: str | None, *, pago: bool) -> Any:
        """Cuenta contable del lado banco/caja de un cobro o pago."""
        if cuenta_bancaria_id is not None:
            banco = self.bancos.get(cuenta_bancaria_id)
            if banco is not None:
                return self.clave_cuenta_bancaria(banco)
        if metodo == "efectivo":
            return "caja"
        if metodo == "tarjeta" and pago:
            return "tarjeta_credito"
        return "banco"

    def clave_cuenta_bancaria(self, banco: asyncpg.Record) -> Any:
        if banco["cuenta_contable_id"] is not None:
            return banco["cuenta_contable_id"]
        return {"caja_chica": "caja_chica", "tarjeta": "tarjeta_credito"}.get(banco["tipo"], "banco")

    def resolver(self, asiento: AsientoPropuesto) -> list[dict[str, Any]]:
        lineas = []
        for linea in asiento.lineas:
            if isinstance(linea.clave, uuid.UUID):
                cuenta = self.cuentas.get(linea.clave)
                if cuenta is None:
                    raise NoContabilizableError("La cuenta contable asignada ya no existe")
            else:
                cuenta = self.mapeo.get(linea.clave)
                if cuenta is None:
                    etiqueta = cc.CONCEPTOS[linea.clave][1]
                    raise NoContabilizableError(
                        f"Falta la cuenta por defecto «{etiqueta}» (Contabilidad → Cuentas por defecto)"
                    )
            if not cuenta["acepta_movimientos"] or not cuenta["activa"]:
                raise NoContabilizableError(
                    f"La cuenta {cuenta['codigo']} {cuenta['nombre']} no acepta movimientos o está inactiva"
                )
            lineas.append({"cuenta_id": cuenta["id"], "descripcion": linea.descripcion, "debito": linea.debito,
                           "credito": linea.credito})
        debito, credito = cc.totales(lineas)
        if debito != credito or debito <= 0:  # pragma: no cover (los armados siempre cuadran)
            raise NoContabilizableError(f"El asiento no cuadra (débitos {debito}, créditos {credito})")
        return lineas


async def _contexto(conn: asyncpg.Connection) -> _Contexto:
    cuentas = {f["id"]: f for f in await conn.fetch("SELECT * FROM contabilidad.cuentas")}
    mapeo = {
        f["concepto"]: cuentas[f["cuenta_id"]]
        for f in await conn.fetch("SELECT concepto, cuenta_id FROM contabilidad.cuentas_por_defecto")
    }
    bancos = {f["id"]: f for f in await conn.fetch("SELECT * FROM bancos.cuentas")}
    return _Contexto(mapeo, cuentas, bancos)


# Cada fuente devuelve [(clave (origen, origen_id), AsientoPropuesto | dict con el motivo)].
Propuesta = tuple[tuple[str, str], Any]


def _no_contabilizable(origen: str, origen_id: str, fecha: date | None, referencia: str | None,
                       descripcion: str, monto: Decimal | None, motivo: str) -> dict[str, Any]:
    return {"origen": origen, "origen_id": origen_id, "fecha": fecha, "referencia": referencia,
            "descripcion": descripcion, "monto": monto, "motivo": motivo}


def _filtro(columna_id: str, columna_fecha: str, ids: list[str] | None) -> str:
    """Documentos con fecha en [$1, $2] o, si se pasan ids ($3), esos (de
    cualquier fecha: un documento que cambió de fecha)."""
    return (
        f"(CASE WHEN $3::text[] IS NULL THEN {columna_fecha} BETWEEN $1::date AND $2::date"
        f" ELSE {columna_id}::text = ANY($3::text[]) END)"
    )


async def _fuente_ventas(conn, ctx, desde, hasta, ids=None) -> list[Propuesta]:
    filas = await conn.fetch(
        f"""
        SELECT f.id, f.tipo_ecf, f.encf, f.datos::text AS datos, fe.fecha,
               COALESCE(o.datos::jsonb #>> '{{Encabezado,IdDoc,TipoPago}}',
                        d #>> '{{Encabezado,IdDoc,TipoPago}}') AS tipo_pago_afectada
        FROM facturacion.facturas f
        CROSS JOIN LATERAL (SELECT f.datos::jsonb AS d) x
        CROSS JOIN LATERAL (SELECT COALESCE({_FECHA_EMISION}, f.creado_en::date) AS fecha) fe
        LEFT JOIN LATERAL (
            SELECT o.datos FROM facturacion.facturas o
            WHERE f.tipo_ecf = 34 AND o.encf = d #>> '{{InformacionReferencia,NCFModificado}}' AND o.id <> f.id
            ORDER BY (o.estado = 'aceptado') DESC LIMIT 1
        ) o ON true
        WHERE f.tipo_ecf = ANY($4::int[]) AND f.estado = 'aceptado' AND {_filtro('f.id', 'fe.fecha', ids)}
        """,
        desde, hasta, ids, list(TIPOS_VENTA_607),
    )
    resultado = []
    for f in filas:
        datos = json.loads(f["datos"])
        tipo_pago = str(((datos.get("Encabezado") or {}).get("IdDoc") or {}).get("TipoPago") or "1")
        a_credito = (f["tipo_pago_afectada"] if f["tipo_ecf"] == 34 else tipo_pago) == "2"
        origen = "nota_credito" if f["tipo_ecf"] == 34 else "nota_debito" if f["tipo_ecf"] == 33 else "venta"
        try:
            propuesta = cc.asiento_venta(
                factura_id=str(f["id"]), tipo_ecf=f["tipo_ecf"], encf=f["encf"], fecha=f["fecha"], datos=datos,
                a_credito=a_credito,
            )
        except NoContabilizableError as exc:
            total = cc.redondear(cc.a_decimal(((datos.get("Encabezado") or {}).get("Totales") or {}).get("MontoTotal")))
            propuesta = _no_contabilizable(origen, str(f["id"]), f["fecha"], f["encf"],
                                           f"{cc.NOMBRE_TIPO_ECF.get(f['tipo_ecf'], 'e-CF')} {f['encf'] or ''}", total,
                                           str(exc))
        resultado.append(((origen, str(f["id"])), propuesta))
    return resultado


async def _fuente_cobros(conn, ctx, desde, hasta, ids=None) -> list[Propuesta]:
    """Solo los cobros de facturas aceptadas por la DGII (la venta sin
    aceptar no se contabiliza: el cobro iría contra una CxC que el mayor no
    tiene); los demás van a `no_contabilizados`."""
    filas = await conn.fetch(
        f"""
        SELECT co.*, f.encf, f.estado AS estado_factura,
               COALESCE(f.datos::jsonb #>> '{{Encabezado,Comprador,RazonSocialComprador}}', '') AS cliente
        FROM ventas.cobros co LEFT JOIN facturacion.facturas f ON f.id = co.factura_id
        WHERE NOT co.anulado AND {_filtro('co.id', 'co.fecha', ids)}
        """,
        desde, hasta, ids,
    )
    resultado = []
    for f in filas:
        if f["estado_factura"] != "aceptado":
            propuesta = _no_contabilizable(
                "cobro", str(f["id"]), f["fecha"], f["encf"], f"Cobro {f['encf'] or ''} · {f['cliente']}".strip(" ·"),
                f["monto"],
                f"La factura {f['encf'] or ''} no está aceptada por la DGII (estado «{f['estado_factura'] or 'sin factura'}»): "
                "su venta no está contabilizada; el cobro se contabiliza cuando la DGII la acepte",
            )
        else:
            propuesta = cc.asiento_cobro(
                cobro_id=str(f["id"]), fecha=f["fecha"], monto=f["monto"], encf=f["encf"], cliente=f["cliente"],
                cuenta_banco=ctx.clave_banco(f["cuenta_bancaria_id"], f["metodo"], pago=False),
            )
        resultado.append((("cobro", str(f["id"])), propuesta))
    return resultado


async def _fuente_compras(conn, ctx, desde, hasta, ids=None) -> list[Propuesta]:
    filas = await conn.fetch(
        f"""
        SELECT r.id, r.ncf, r.tipo_bienes_servicios, r.itbis_facturado, r.itbis_costo, fc.fecha,
               {_TOTAL_CXP}::numeric(18, 2) AS total, {_ES_NOTA_CREDITO} AS es_nota_credito,
               COALESCE(NULLIF(cf.proveedor_nombre, ''), er.razon_social_emisor, r.rnc_cedula) AS proveedor,
               r.itbis_retenido, r.monto_retencion_renta,
               EXISTS (SELECT 1 FROM compras.pagos p WHERE p.registro606_id = r.id AND NOT p.anulado) AS tiene_pagos
        FROM compras606.registros r
        LEFT JOIN compras.facturas cf ON cf.registro606_id = r.id
        LEFT JOIN recepcion.ecf_recibidos er ON er.compra606_id = r.id
        CROSS JOIN LATERAL (SELECT COALESCE({_fecha_606("r.fecha_comprobante")}, r.creado_en::date) AS fecha) fc
        WHERE COALESCE(er.estado_aprobacion, '') <> 'rechazado' AND {_filtro('r.id', 'fc.fecha', ids)}
        """,
        desde, hasta, ids,
    )
    resultado = []
    for f in filas:
        try:
            propuesta = cc.asiento_compra(
                registro_id=str(f["id"]), fecha=f["fecha"], ncf=f["ncf"], proveedor=f["proveedor"],
                tipo_bienes_servicios=f["tipo_bienes_servicios"], total=f["total"],
                itbis_facturado=f["itbis_facturado"], itbis_costo=f["itbis_costo"],
                es_nota_credito=f["es_nota_credito"], pagada_al_contado=f["ncf"].upper().startswith("E43"),
                # Retenciones del 606 sin pagos: las lleva la compra (con pagos, el último pago).
                itbis_retenido_606=_CERO if f["tiene_pagos"] else f["itbis_retenido"],
                isr_retenido_606=_CERO if f["tiene_pagos"] else f["monto_retencion_renta"],
            )
        except NoContabilizableError as exc:
            propuesta = _no_contabilizable("compra", str(f["id"]), f["fecha"], f["ncf"], f"Compra {f['ncf']} · {f['proveedor']}",
                                           f["total"], str(exc))
        resultado.append((("compra", str(f["id"])), propuesta))
    return resultado


async def _fuente_pagos(conn, ctx, desde, hasta, ids=None) -> list[Propuesta]:
    filas = await conn.fetch(
        f"""
        SELECT pg.*, r.ncf, COALESCE(NULLIF(cf.proveedor_nombre, ''), er.razon_social_emisor, r.rnc_cedula, '') AS proveedor,
               COALESCE(r.itbis_retenido, 0) AS itbis_606, COALESCE(r.monto_retencion_renta, 0) AS isr_606,
               pg.id = (
                   SELECT u.id FROM compras.pagos u WHERE u.registro606_id = pg.registro606_id AND NOT u.anulado
                   ORDER BY u.fecha DESC, u.creado_en DESC, u.id DESC LIMIT 1
               ) AS es_ultimo,
               EXISTS (
                   SELECT 1 FROM compras.pagos o
                   WHERE o.registro606_id = pg.registro606_id AND NOT o.anulado
                     AND (o.retencion_isr > 0 OR o.retencion_itbis > 0)
               ) AS algun_pago_retiene
        FROM compras.pagos pg
        LEFT JOIN compras606.registros r ON r.id = pg.registro606_id
        LEFT JOIN compras.facturas cf ON cf.registro606_id = pg.registro606_id
        LEFT JOIN recepcion.ecf_recibidos er ON er.compra606_id = pg.registro606_id
        WHERE NOT pg.anulado AND {_filtro('pg.id', 'pg.fecha', ids)}
        """,
        desde, hasta, ids,
    )
    resultado = []
    for f in filas:
        # Retenciones que están en el 606 sin que ningún pago las lleve
        # (clasificadas antes de pagar, p. ej. un E41): van con el último pago.
        lleva_606 = f["es_ultimo"] and not f["algun_pago_retiene"]
        resultado.append((("pago", str(f["id"])), cc.asiento_pago(
            pago_id=str(f["id"]), fecha=f["fecha"], ncf=f["ncf"], proveedor=f["proveedor"], monto=f["monto"],
            retencion_isr=f["retencion_isr"], retencion_itbis=f["retencion_itbis"],
            cuenta_banco=ctx.clave_banco(f["cuenta_bancaria_id"], f["metodo"], pago=True),
            itbis_retenido_606=f["itbis_606"] if lleva_606 else _CERO,
            isr_retenido_606=f["isr_606"] if lleva_606 else _CERO,
        )))
    return resultado


async def _fuente_movimientos(conn, ctx, desde, hasta, ids=None) -> list[Propuesta]:
    filas = await conn.fetch(
        f"""
        SELECT m.* FROM bancos.movimientos m
        WHERE NOT m.anulado AND m.transferencia_id IS NULL AND {_filtro('m.id', 'm.fecha', ids)}
        """,
        desde, hasta, ids,
    )
    return [
        (("movimiento", str(f["id"])), cc.asiento_movimiento(
            movimiento_id=str(f["id"]), fecha=f["fecha"], tipo=f["tipo"],
            monto_dop=f["monto"] * (f["tasa_cambio"] or 1), descripcion=f["descripcion"], referencia=f["referencia"],
            cuenta_banco=ctx.clave_cuenta_bancaria(ctx.bancos[f["cuenta_id"]]), contrapartida=f["cuenta_contable_id"],
        ))
        for f in filas
    ]


async def _fuente_transferencias(conn, ctx, desde, hasta, ids=None) -> list[Propuesta]:
    filas = await conn.fetch(
        f"SELECT t.* FROM bancos.transferencias t WHERE NOT t.anulada AND {_filtro('t.id', 't.fecha', ids)}",
        desde, hasta, ids,
    )
    resultado = []
    for f in filas:
        origen, destino = ctx.bancos[f["cuenta_origen_id"]], ctx.bancos[f["cuenta_destino_id"]]
        if origen["moneda"] == "DOP":
            monto_dop = f["monto"]
        elif destino["moneda"] == "DOP":
            monto_dop = f["monto_destino"]
        else:
            monto_dop = f["monto"] * (f["tasa_cambio"] or 1)
        resultado.append((("transferencia", str(f["id"])), cc.asiento_transferencia(
            transferencia_id=str(f["id"]), fecha=f["fecha"], monto_dop=monto_dop,
            concepto=f"Transferencia {origen['nombre']} → {destino['nombre']}"
                     + (f" · {f['concepto']}" if f["concepto"] else ""),
            referencia=f["referencia"], cuenta_origen=ctx.clave_cuenta_bancaria(origen),
            cuenta_destino=ctx.clave_cuenta_bancaria(destino),
        )))
    return resultado


async def _fuente_proporcionalidad(conn, ctx, desde, hasta, ids=None) -> list[Propuesta]:
    """Ajuste por proporcionalidad de cada mes que termina en [desde,
    hasta] (o de los períodos `ids`), con el coeficiente del IT-1 del mes
    (`precalculos.itbis_adelantado_admitido`). Opcional: sin cuenta por
    defecto `itbis_no_admitido` no se genera (y se anulan los que había)."""
    if "itbis_no_admitido" not in ctx.mapeo:
        return []
    if ids is None:
        periodos, periodo = [], cc.periodo_de(desde)
        while periodo <= cc.periodo_de(hasta):
            if desde <= cc.ultimo_dia(periodo) <= hasta:
                periodos.append(periodo)
            periodo = cc.siguiente_periodo(periodo)
    else:
        periodos = ids
    resultado = []
    for periodo in periodos:
        ventas, compras, _ = await impuestos_repository._datos_it1(conn, periodo)
        calculo = precalculos.itbis_adelantado_admitido(ventas, compras)
        if calculo.no_admitido > 0:
            porcentaje = cc.redondear(calculo.proporcion * 100)
            resultado.append((("proporcionalidad", periodo), cc.asiento_proporcionalidad(
                periodo, calculo.no_admitido, porcentaje, nombre_periodo(periodo),
            )))
    return resultado


_FUENTES = {
    "venta": _fuente_ventas, "nota_credito": _fuente_ventas, "nota_debito": _fuente_ventas,
    "cobro": _fuente_cobros, "compra": _fuente_compras, "pago": _fuente_pagos,
    "movimiento": _fuente_movimientos, "transferencia": _fuente_transferencias,
    "proporcionalidad": _fuente_proporcionalidad,
}


async def _relacionados_606(
    conn: asyncpg.Connection, desde: date, hasta: date, compras: list[str]
) -> list[tuple[str, str]]:
    """La compra y los pagos de cada renglón del 606 que tiene una compra o
    un pago (vigente o anulado) en el rango: las retenciones del 606 sin
    pago que las lleve van en la compra o en el último pago, así que un
    pago nuevo o anulado cambia también el asiento de la compra o de otro
    pago del mismo renglón, aunque sean de otro mes."""
    filas = await conn.fetch(
        """
        WITH renglones AS (
            SELECT registro606_id AS id FROM compras.pagos
            WHERE registro606_id IS NOT NULL AND fecha BETWEEN $1 AND $2
            UNION
            SELECT id FROM compras606.registros WHERE id::text = ANY($3::text[])
        )
        SELECT 'compra' AS origen, r.id::text AS origen_id FROM compras606.registros r JOIN renglones x ON x.id = r.id
        UNION ALL
        SELECT 'pago', p.id::text FROM compras.pagos p JOIN renglones x ON x.id = p.registro606_id WHERE NOT p.anulado
        """,
        desde, hasta, compras,
    )
    return [(f["origen"], f["origen_id"]) for f in filas]


async def _sincronizar(conn: asyncpg.Connection, desde: date, hasta: date, *, aplicar: bool) -> dict[str, Any]:
    ctx = await _contexto(conn)
    propuestas: dict[tuple[str, str], Any] = {}
    for fuente in dict.fromkeys(_FUENTES.values()):
        for clave, propuesta in await fuente(conn, ctx, desde, hasta):
            propuestas[clave] = propuesta
    relacionados: dict[Any, list[str]] = {}
    compras = [origen_id for origen, origen_id in propuestas if origen == "compra"]
    for origen, origen_id in await _relacionados_606(conn, desde, hasta, compras):
        if (origen, origen_id) not in propuestas:
            relacionados.setdefault(_FUENTES[origen], []).append(origen_id)
    for fuente, ids in relacionados.items():
        for clave, propuesta in await fuente(conn, ctx, desde, hasta, ids):
            propuestas[clave] = propuesta
    claves = [origen_id for _, origen_id in propuestas]
    existentes = {
        (f["origen"], f["origen_id"]): f
        for f in await conn.fetch(
            """
            SELECT id, numero, origen, origen_id, huella, fecha, concepto, total FROM contabilidad.asientos
            WHERE estado <> 'anulado' AND origen = ANY($1::text[])
              AND (fecha BETWEEN $2 AND $3 OR origen_id = ANY($4::text[]))
            """,
            list(cc.ORIGENES_AUTOMATICOS), desde, hasta, claves,
        )
    }
    # Asientos del rango cuyo documento no salió en el rango: o cambió de
    # fecha (se busca por id) o ya no existe / se anuló (se anula).
    faltantes: dict[Any, list[str]] = {}
    for origen, origen_id in existentes:
        if (origen, origen_id) not in propuestas:
            faltantes.setdefault(_FUENTES[origen], []).append(origen_id)
    for fuente, ids in faltantes.items():
        for clave, propuesta in await fuente(conn, ctx, desde, hasta, ids):
            propuestas[clave] = propuesta
    resultado: dict[str, Any] = {
        "desde": desde, "hasta": hasta, "creados": 0, "actualizados": 0, "anulados": 0, "sin_cambios": 0,
        "no_contabilizados": [], "pendientes": [],
    }
    for clave, existente in existentes.items():
        if clave not in propuestas:
            resultado["anulados"] += 1
            resultado["pendientes"].append({
                "accion": "anular", "origen": clave[0], "origen_id": clave[1], "fecha": existente["fecha"],
                "descripcion": existente["concepto"], "monto": existente["total"],
                "asiento_numero": numero_asiento(existente["numero"]),
            })
            if aplicar:
                await _anular(conn, existente["id"], "El documento de origen se anuló o ya no existe")
    for clave in sorted(propuestas, key=lambda k: (str(_fecha_de(propuestas[k])), k)):
        propuesta = propuestas[clave]
        existente = existentes.get(clave)
        if isinstance(propuesta, dict):
            resultado["no_contabilizados"].append(propuesta)
            if existente is not None:
                # El documento ya no se puede contabilizar (p. ej. el 606 quedó
                # sin clasificar): su asiento anterior no puede quedar vigente.
                resultado["anulados"] += 1
                resultado["pendientes"].append({
                    "accion": "anular", "origen": clave[0], "origen_id": clave[1], "fecha": existente["fecha"],
                    "descripcion": existente["concepto"], "monto": existente["total"],
                    "asiento_numero": numero_asiento(existente["numero"]),
                })
                if aplicar:
                    await _anular(conn, existente["id"], f"El documento ya no se puede contabilizar: {propuesta['motivo']}")
            continue
        try:
            lineas = ctx.resolver(propuesta)
        except NoContabilizableError as exc:
            resultado["no_contabilizados"].append(_no_contabilizable(
                propuesta.origen, propuesta.origen_id, propuesta.fecha, propuesta.referencia, propuesta.concepto,
                sum((x.debito for x in propuesta.lineas), _CERO), str(exc),
            ))
            continue
        huella = cc.huella(lineas, propuesta.fecha, propuesta.concepto, propuesta.referencia)
        if existente is not None and existente["huella"] == huella:
            resultado["sin_cambios"] += 1
            continue
        accion = "crear" if existente is None else "actualizar"
        resultado["creados" if existente is None else "actualizados"] += 1
        resultado["pendientes"].append({
            "accion": accion, "origen": propuesta.origen, "origen_id": propuesta.origen_id, "fecha": propuesta.fecha,
            "descripcion": propuesta.concepto, "monto": cc.totales(lineas)[0],
            "asiento_numero": numero_asiento(existente["numero"]) if existente is not None else None,
        })
        if not aplicar:
            continue
        if existente is not None:
            await _anular(conn, existente["id"], "Regenerado: cambió el documento o las cuentas por defecto")
        asiento_id = await conn.fetchval(
            """
            INSERT INTO contabilidad.asientos
                (fecha, concepto, referencia, estado, origen, origen_id, huella, total, contabilizado_en)
            VALUES ($1, $2, $3, 'contabilizado', $4, $5, $6, $7, now())
            RETURNING id
            """,
            propuesta.fecha, propuesta.concepto, propuesta.referencia, propuesta.origen, propuesta.origen_id, huella,
            cc.totales(lineas)[0],
        )
        await _insertar_lineas(conn, asiento_id, lineas)
    return resultado


def _fecha_de(propuesta: Any) -> date | None:
    return propuesta["fecha"] if isinstance(propuesta, dict) else propuesta.fecha


async def generar_asientos(pool: asyncpg.Pool, desde: date, hasta: date) -> dict[str, Any]:
    """Crea/actualiza/anula los asientos automáticos de los documentos con
    fecha en [desde, hasta] (ver docstring del módulo). Un candado evita que
    dos generaciones corran a la vez."""
    _validar_rango(desde, hasta)
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext('contabilidad.generar'))")
            resultado = await _sincronizar(conn, desde, hasta, aplicar=True)
    _CONTEOS_PENDIENTES.clear()
    return resultado


async def pendientes_de_contabilizar(pool: asyncpg.Pool, desde: date, hasta: date) -> dict[str, Any]:
    """Lo que haría `generar_asientos` sin hacerlo (`pendientes`) y los
    documentos que no se pueden contabilizar con su motivo."""
    _validar_rango(desde, hasta)
    async with pool.acquire() as conn:
        return await _sincronizar(conn, desde, hasta, aplicar=False)


# --- Reportes ---------------------------------------------------------

async def libro_diario(
    pool: asyncpg.Pool, desde: date, hasta: date, *, limite: int = 500, offset: int = 0
) -> dict[str, Any]:
    _validar_rango(desde, hasta)
    async with pool.acquire() as conn:
        asientos = await conn.fetch(
            f"""
            {_SELECT_ASIENTOS}
            WHERE a.estado = 'contabilizado' AND a.fecha BETWEEN $1 AND $2
            ORDER BY a.fecha, a.numero
            LIMIT $3 OFFSET $4
            """,
            desde, hasta, limite, offset,
        )
        lineas = await conn.fetch(
            """
            SELECT l.asiento_id, l.id, l.linea, l.cuenta_id, c.codigo AS cuenta_codigo, c.nombre AS cuenta_nombre,
                   l.descripcion, l.debito, l.credito
            FROM contabilidad.asiento_lineas l JOIN contabilidad.cuentas c ON c.id = l.cuenta_id
            WHERE l.asiento_id = ANY($1::uuid[])
            ORDER BY l.linea
            """,
            [a["id"] for a in asientos],
        )
        totales = await conn.fetchrow(
            """
            SELECT COALESCE(sum(l.debito), 0) AS debitos, COALESCE(sum(l.credito), 0) AS creditos, count(DISTINCT a.id) AS n
            FROM contabilidad.asientos a JOIN contabilidad.asiento_lineas l ON l.asiento_id = a.id
            WHERE a.estado = 'contabilizado' AND a.fecha BETWEEN $1 AND $2
            """,
            desde, hasta,
        )
    por_asiento: dict[uuid.UUID, list[dict[str, Any]]] = {}
    for linea in lineas:
        por_asiento.setdefault(linea["asiento_id"], []).append({k: linea[k] for k in linea.keys() if k != "asiento_id"})
    return {
        "desde": desde, "hasta": hasta,
        "asientos": [_fila_asiento(a) | {"lineas": por_asiento.get(a["id"], [])} for a in asientos],
        "total_asientos": totales["n"], "total_debitos": totales["debitos"], "total_creditos": totales["creditos"],
    }


async def mayor(pool: asyncpg.Pool, cuenta_id: uuid.UUID, desde: date, hasta: date) -> dict[str, Any]:
    """Movimientos de la cuenta (y sus subcuentas) en el rango, con el saldo
    según su naturaleza antes de `desde` y después de cada línea."""
    _validar_rango(desde, hasta)
    async with pool.acquire() as conn:
        cuenta = await _cuenta(conn, cuenta_id)
        filtro = "c.codigo = $1 OR c.codigo LIKE $1 || '.%'"
        anterior = await conn.fetchrow(
            f"""
            SELECT COALESCE(sum(l.debito), 0) AS d, COALESCE(sum(l.credito), 0) AS c
            FROM contabilidad.asiento_lineas l
            JOIN contabilidad.asientos a ON a.id = l.asiento_id
            JOIN contabilidad.cuentas c ON c.id = l.cuenta_id
            WHERE a.estado = 'contabilizado' AND a.fecha < $2 AND ({filtro})
            """,
            cuenta["codigo"], desde,
        )
        filas = await conn.fetch(
            f"""
            SELECT a.id AS asiento_id, a.numero, a.fecha, a.concepto, a.origen, a.referencia, l.descripcion,
                   c.codigo AS cuenta_codigo, c.nombre AS cuenta_nombre, l.debito, l.credito
            FROM contabilidad.asiento_lineas l
            JOIN contabilidad.asientos a ON a.id = l.asiento_id
            JOIN contabilidad.cuentas c ON c.id = l.cuenta_id
            WHERE a.estado = 'contabilizado' AND a.fecha BETWEEN $2 AND $3 AND ({filtro})
            ORDER BY a.fecha, a.numero, l.linea
            """,
            cuenta["codigo"], desde, hasta,
        )
    saldo = _con_signo(cuenta["naturaleza"], anterior["d"] - anterior["c"])
    saldo_inicial = saldo
    movimientos = []
    for f in filas:
        saldo += _con_signo(cuenta["naturaleza"], f["debito"] - f["credito"])
        movimientos.append(dict(f) | {"numero": numero_asiento(f["numero"]), "saldo": saldo})
    return {
        "cuenta": cuenta, "desde": desde, "hasta": hasta, "saldo_inicial": saldo_inicial,
        "total_debitos": sum((f["debito"] for f in filas), _CERO),
        "total_creditos": sum((f["credito"] for f in filas), _CERO),
        "saldo_final": saldo, "movimientos": movimientos,
    }


async def balanza(pool: asyncpg.Pool, desde: date, hasta: date, *, nivel: int | None = None) -> dict[str, Any]:
    """Balanza de comprobación: por cuenta (con las de grupo acumuladas),
    saldo inicial, débitos y créditos del período y saldo final, en columnas
    deudor/acreedor. Cuadra si los débitos = créditos y los saldos deudores
    = acreedores (sumando solo las cuentas de movimiento)."""
    _validar_rango(desde, hasta)
    async with pool.acquire() as conn:
        cuentas = await _catalogo(conn)
        antes = await _saldos_brutos(conn, hasta=date.fromordinal(desde.toordinal() - 1))
        periodo = await _saldos_brutos(conn, desde=desde, hasta=hasta)
    acumulado_antes = _acumular(cuentas, antes)
    acumulado_periodo = _acumular(cuentas, periodo)
    filas = []
    tot = {"saldo_inicial_deudor": _CERO, "saldo_inicial_acreedor": _CERO, "debitos": _CERO, "creditos": _CERO,
           "saldo_final_deudor": _CERO, "saldo_final_acreedor": _CERO}
    for c in cuentas:
        d0, c0 = acumulado_antes[c["id"]]
        d, cr = acumulado_periodo[c["id"]]
        inicial = d0 - c0
        final = inicial + d - cr
        if not (d0 or c0 or d or cr):
            continue
        fila = {
            "cuenta_id": c["id"], "codigo": c["codigo"], "nombre": c["nombre"], "tipo": c["tipo"],
            "naturaleza": c["naturaleza"], "nivel": c["nivel"], "acepta_movimientos": c["acepta_movimientos"],
            "saldo_inicial": _con_signo(c["naturaleza"], inicial), "debitos": d, "creditos": cr,
            "saldo_final": _con_signo(c["naturaleza"], final),
            "saldo_final_deudor": max(final, _CERO), "saldo_final_acreedor": max(-final, _CERO),
        }
        if c["acepta_movimientos"]:
            tot["saldo_inicial_deudor"] += max(inicial, _CERO)
            tot["saldo_inicial_acreedor"] += max(-inicial, _CERO)
            tot["debitos"] += d
            tot["creditos"] += cr
            tot["saldo_final_deudor"] += fila["saldo_final_deudor"]
            tot["saldo_final_acreedor"] += fila["saldo_final_acreedor"]
        if nivel is None or c["nivel"] <= nivel:
            filas.append(fila)
    return {
        "desde": desde, "hasta": hasta, "cuentas": filas, "totales": tot,
        "cuadrada": tot["debitos"] == tot["creditos"] and tot["saldo_final_deudor"] == tot["saldo_final_acreedor"],
    }


def _seccion(cuentas: list[dict[str, Any]], acumulado, tipo: str, signo_resultado: int) -> dict[str, Any]:
    """Cuentas de un tipo con su monto (positivo = lo normal del grupo)."""
    filas = []
    total = _CERO
    for c in cuentas:
        if c["tipo"] != tipo:
            continue
        d, cr = acumulado[c["id"]]
        if not (d or cr):
            continue
        monto = (d - cr) * signo_resultado
        if c["nivel"] == 1:
            total += monto
        filas.append({"cuenta_id": c["id"], "codigo": c["codigo"], "nombre": c["nombre"], "nivel": c["nivel"],
                      "acepta_movimientos": c["acepta_movimientos"], "monto": monto})
    if not any(f["nivel"] == 1 for f in filas):
        total = sum((f["monto"] for f in filas if f["acepta_movimientos"]), _CERO)
    return {"cuentas": filas, "total": total}


async def estado_resultados(pool: asyncpg.Pool, desde: date, hasta: date) -> dict[str, Any]:
    _validar_rango(desde, hasta)
    async with pool.acquire() as conn:
        cuentas = await _catalogo(conn)
        acumulado = _acumular(cuentas, await _saldos_brutos(conn, desde=desde, hasta=hasta))
    ingresos = _seccion(cuentas, acumulado, "ingreso", -1)
    costos = _seccion(cuentas, acumulado, "costo", 1)
    gastos = _seccion(cuentas, acumulado, "gasto", 1)
    utilidad_bruta = ingresos["total"] - costos["total"]
    utilidad_neta = utilidad_bruta - gastos["total"]
    margen = (utilidad_neta / ingresos["total"] * 100).quantize(Decimal("0.01")) if ingresos["total"] else _CERO
    return {
        "desde": desde, "hasta": hasta, "ingresos": ingresos, "costos": costos, "gastos": gastos,
        "utilidad_bruta": utilidad_bruta, "utilidad_neta": utilidad_neta, "margen_neto_pct": margen,
    }


async def balance_general(pool: asyncpg.Pool, fecha: date) -> dict[str, Any]:
    """Activos, pasivos y patrimonio al `fecha`. Sin asientos de cierre, el
    resultado (ingresos − costos − gastos) se muestra como "Resultado del
    ejercicio" (desde el 1 de enero del año de `fecha`) y "Resultados de
    ejercicios anteriores no cerrados"."""
    inicio_anio = date(fecha.year, 1, 1)
    async with pool.acquire() as conn:
        cuentas = await _catalogo(conn)
        acumulado = _acumular(cuentas, await _saldos_brutos(conn, hasta=fecha))
        anteriores = _acumular(cuentas, await _saldos_brutos(conn, hasta=date.fromordinal(inicio_anio.toordinal() - 1)))
    activos = _seccion(cuentas, acumulado, "activo", 1)
    pasivos = _seccion(cuentas, acumulado, "pasivo", -1)
    patrimonio = _seccion(cuentas, acumulado, "patrimonio", -1)

    def resultado(acum) -> Decimal:
        return sum((-(acum[c["id"]][0] - acum[c["id"]][1]) for c in cuentas
                    if c["nivel"] == 1 and c["tipo"] in ("ingreso", "costo", "gasto")), _CERO)

    total_resultado = resultado(acumulado)
    anteriores_no_cerrados = resultado(anteriores)
    del_ejercicio = total_resultado - anteriores_no_cerrados
    total_patrimonio = patrimonio["total"] + total_resultado
    return {
        "fecha": fecha, "activos": activos, "pasivos": pasivos, "patrimonio": patrimonio,
        "resultado_ejercicio": del_ejercicio, "resultados_anteriores_no_cerrados": anteriores_no_cerrados,
        "total_patrimonio": total_patrimonio, "total_pasivo_patrimonio": pasivos["total"] + total_patrimonio,
        "cuadrado": activos["total"] == pasivos["total"] + total_patrimonio,
    }


# Conteo de documentos por contabilizar / no contabilizables del tablero:
# (pool, desde, hasta) -> (momento, por contabilizar, no contabilizables). La
# simulación completa (`pendientes_de_contabilizar`) lee y arma todos los
# documentos del rango: el tablero la reutiliza unos segundos en lugar de
# repetirla en cada carga. `generar_asientos` lo vacía.
_CONTEOS_PENDIENTES: dict[tuple[int, date, date], tuple[float, int, int]] = {}
_SEGUNDOS_CONTEOS = 60.0


async def _conteo_pendientes(pool: asyncpg.Pool, desde: date, hasta: date) -> tuple[int, int]:
    ahora = time.monotonic()
    clave = (id(pool), desde, hasta)
    guardado = _CONTEOS_PENDIENTES.get(clave)
    if guardado is not None and ahora - guardado[0] < _SEGUNDOS_CONTEOS:
        return guardado[1], guardado[2]
    pendientes = await pendientes_de_contabilizar(pool, desde, hasta)
    conteo = (len(pendientes["pendientes"]), len(pendientes["no_contabilizados"]))
    if len(_CONTEOS_PENDIENTES) > 64:
        _CONTEOS_PENDIENTES.clear()
    _CONTEOS_PENDIENTES[clave] = (ahora, *conteo)
    return conteo


async def resumen(pool: asyncpg.Pool, desde: date, hasta: date) -> dict[str, Any]:
    """Tablero de Contabilidad: totales del balance al `hasta`, resultado del
    período, asientos del período y documentos pendientes de contabilizar
    (conteo de hasta `_SEGUNDOS_CONTEOS` de antigüedad; el detalle exacto es
    `GET /contabilidad/asientos/pendientes`)."""
    _validar_rango(desde, hasta)
    balance = await balance_general(pool, hasta)
    resultados = await estado_resultados(pool, desde, hasta)
    por_contabilizar, no_contabilizables = await _conteo_pendientes(pool, desde, hasta)
    async with pool.acquire() as conn:
        conteo = await conn.fetchrow(
            """
            SELECT count(*) FILTER (WHERE estado = 'contabilizado') AS contabilizados,
                   count(*) FILTER (WHERE estado = 'contabilizado' AND origen = 'manual') AS manuales,
                   count(*) FILTER (WHERE estado = 'borrador') AS borradores
            FROM contabilidad.asientos WHERE fecha BETWEEN $1 AND $2
            """,
            desde, hasta,
        )
    return {
        "desde": desde, "hasta": hasta,
        "activos": balance["activos"]["total"], "pasivos": balance["pasivos"]["total"],
        "patrimonio": balance["total_patrimonio"],
        "ingresos": resultados["ingresos"]["total"], "costos": resultados["costos"]["total"],
        "gastos": resultados["gastos"]["total"], "utilidad_neta": resultados["utilidad_neta"],
        "asientos_contabilizados": conteo["contabilizados"], "asientos_manuales": conteo["manuales"],
        "borradores": conteo["borradores"],
        "documentos_por_contabilizar": por_contabilizar,
        "documentos_no_contabilizables": no_contabilizables,
    }


# --- Activos fijos y depreciación ---------------------------------------------------------

def _activo_calculo(fila: asyncpg.Record | dict[str, Any]) -> cc.Activo:
    baja = None
    if fila["fecha_baja"] is not None:
        baja = cc.siguiente_periodo(cc.periodo_de(fila["fecha_baja"]))
    return cc.Activo(
        categoria=fila["categoria"], metodo=fila["metodo"], costo=fila["costo"], valor_residual=fila["valor_residual"],
        vida_util_meses=fila["vida_util_meses"], depreciar_desde=fila["depreciar_desde"],
        depreciacion_acumulada_inicial=fila["depreciacion_acumulada_inicial"], baja_periodo=baja,
        fecha_adquisicion=fila["fecha_adquisicion"], fecha_baja=fila["fecha_baja"], valor_venta=fila["valor_venta"],
    )


# Campos que cambian la depreciación fiscal (IR-2) del activo.
_CAMPOS_FISCALES = ("categoria", "fecha_adquisicion", "costo", "depreciar_desde", "depreciacion_acumulada_inicial")


async def _exigir_ir2_abierto(conn: asyncpg.Connection, desde_anio: int) -> None:
    """409 si el IR-2 de `desde_anio` o de un año posterior (hasta el actual)
    ya está marcado como presentado: la depreciación fiscal de las
    categorías 2 y 3 pasa de un año al siguiente (cuenta de la categoría),
    así que cambiar un activo cambia la de todos los años desde que entró.
    Toma el candado de cada declaración (dentro de una transacción)."""
    for anio in range(desde_anio, hoy().year + 1):
        try:
            await _exigir_no_presentada(conn, "ir2", f"{anio:04d}")
        except ConflictoImpuestosError as exc:
            raise ConflictoContabilidadError(f"El cambio altera la depreciación fiscal del IR-2. {exc}") from None


_SELECT_ACTIVOS = """
    SELECT a.*, ca.codigo AS cuenta_activo_codigo, ca.nombre AS cuenta_activo_nombre,
           COALESCE(d.registrada, 0) AS depreciacion_registrada, d.ultimo AS ultimo_periodo_depreciado
    FROM contabilidad.activos_fijos a
    LEFT JOIN contabilidad.cuentas ca ON ca.id = a.cuenta_activo_id
    LEFT JOIN (
        SELECT activo_id, sum(monto) AS registrada, max(periodo) AS ultimo
        FROM contabilidad.depreciaciones GROUP BY activo_id
    ) d ON d.activo_id = a.id
"""


def _fila_activo(fila: asyncpg.Record) -> dict[str, Any]:
    datos = dict(fila)
    activo = _activo_calculo(fila)
    acumulada = datos["depreciacion_acumulada_inicial"] + datos["depreciacion_registrada"]
    siguiente = cc.siguiente_periodo(datos["ultimo_periodo_depreciado"]) if datos["ultimo_periodo_depreciado"] else max(
        datos["depreciar_desde"], cc.periodo_de(hoy()))
    cuota = cc.cuotas_depreciacion(activo, siguiente).get(siguiente, _CERO)
    if datos["fecha_baja"] is not None:
        estado = "baja"
    elif datos["metodo"] == "linea_recta" and acumulada >= datos["costo"] - datos["valor_residual"]:
        estado = "depreciado"
    else:
        estado = "activo"
    datos.update(
        categoria_nombre=cc.NOMBRE_CATEGORIA[datos["categoria"]],
        tasa_dgii=cc.TASAS_CATEGORIA[datos["categoria"]] * 100,
        depreciacion_acumulada=acumulada,
        valor_en_libros=datos["costo"] - acumulada,
        cuota_mensual=cuota,
        estado=estado,
    )
    return datos


async def listar_activos(pool: asyncpg.Pool, *, estado: str | None = None) -> list[dict[str, Any]]:
    async with pool.acquire() as conn:
        filas = await conn.fetch(f"{_SELECT_ACTIVOS} ORDER BY a.fecha_adquisicion, a.descripcion")
    activos = [_fila_activo(f) for f in filas]
    return [a for a in activos if estado is None or a["estado"] == estado]


async def _activo(conn: asyncpg.Connection, activo_id: uuid.UUID) -> dict[str, Any]:
    fila = await conn.fetchrow(f"{_SELECT_ACTIVOS} WHERE a.id = $1", activo_id)
    if fila is None:
        raise ContabilidadNoEncontradaError(str(activo_id))
    return _fila_activo(fila)


async def obtener_activo(pool: asyncpg.Pool, activo_id: uuid.UUID) -> dict[str, Any]:
    async with pool.acquire() as conn:
        return await _activo(conn, activo_id)


_CAMPOS_ACTIVO = (
    "descripcion", "categoria", "fecha_adquisicion", "costo", "valor_residual", "metodo", "vida_util_meses",
    "depreciar_desde", "depreciacion_acumulada_inicial", "cuenta_activo_id", "registro606_id", "numero_serie",
    "ubicacion", "notas",
)
# Cambiar cualquiera de estos cambiaría la depreciación ya registrada.
_CAMPOS_DEPRECIACION = (
    "categoria", "fecha_adquisicion", "costo", "valor_residual", "metodo", "vida_util_meses", "depreciar_desde",
    "depreciacion_acumulada_inicial",
)


def _validar_activo(datos: dict[str, Any]) -> None:
    if datos["costo"] is None or datos["costo"] <= 0:
        raise DatosInvalidosError("El costo debe ser mayor que cero")
    if datos["valor_residual"] < 0 or datos["valor_residual"] >= datos["costo"]:
        raise DatosInvalidosError("El valor residual debe ser menor que el costo")
    if datos["depreciacion_acumulada_inicial"] < 0 or (
        datos["depreciacion_acumulada_inicial"] > datos["costo"] - datos["valor_residual"]
    ):
        raise DatosInvalidosError("La depreciación acumulada inicial no puede superar el costo menos el valor residual")
    if datos["metodo"] == "linea_recta" and not datos.get("vida_util_meses"):
        raise DatosInvalidosError("La línea recta requiere la vida útil en meses")
    if not re.fullmatch(r"[0-9]{4}(0[1-9]|1[0-2])", datos["depreciar_desde"] or ""):
        raise DatosInvalidosError("«depreciar_desde» debe ser un período AAAAMM")
    if datos["depreciar_desde"] < cc.periodo_de(datos["fecha_adquisicion"]):
        raise DatosInvalidosError("No se puede depreciar antes del mes de adquisición")


async def crear_activo(pool: asyncpg.Pool, **campos: Any) -> dict[str, Any]:
    """`depreciar_desde` por defecto: el mes siguiente al de adquisición."""
    datos = {k: campos.get(k) for k in _CAMPOS_ACTIVO}
    datos["valor_residual"] = _redondear(datos["valor_residual"] or _CERO)
    datos["depreciacion_acumulada_inicial"] = _redondear(datos["depreciacion_acumulada_inicial"] or _CERO)
    datos["costo"] = _redondear(datos["costo"])
    datos["metodo"] = datos["metodo"] or "dgii"
    datos["depreciar_desde"] = datos["depreciar_desde"] or cc.siguiente_periodo(cc.periodo_de(datos["fecha_adquisicion"]))
    _validar_activo(datos)
    async with pool.acquire() as conn:
        async with conn.transaction():
            await _validar_cuenta_activo(conn, datos["cuenta_activo_id"])
            nuevo = _activo_calculo(datos | {"fecha_baja": None, "valor_venta": None})
            await _exigir_ir2_abierto(conn, cc.anio_de_entrada(nuevo))
            columnas = list(datos)
            activo_id = await conn.fetchval(
                f"INSERT INTO contabilidad.activos_fijos ({', '.join(columnas)}) "
                f"VALUES ({', '.join(f'${i}' for i in range(1, len(columnas) + 1))}) RETURNING id",
                *[datos[c] for c in columnas],
            )
            return await _activo(conn, activo_id)


async def _validar_cuenta_activo(conn: asyncpg.Connection, cuenta_id: uuid.UUID | None) -> None:
    if cuenta_id is None:
        return
    cuenta = await conn.fetchrow(
        "SELECT codigo, nombre, tipo, acepta_movimientos FROM contabilidad.cuentas WHERE id = $1", cuenta_id
    )
    if cuenta is None:
        raise DatosInvalidosError("La cuenta contable del activo no existe")
    if cuenta["tipo"] != "activo" or not cuenta["acepta_movimientos"]:
        raise DatosInvalidosError(f"{cuenta['codigo']} {cuenta['nombre']} no es una cuenta de activo de movimiento")


async def actualizar_activo(pool: asyncpg.Pool, activo_id: uuid.UUID, campos: dict[str, Any]) -> dict[str, Any]:
    async with pool.acquire() as conn:
        async with conn.transaction():
            actual = await conn.fetchrow(
                "SELECT * FROM contabilidad.activos_fijos WHERE id = $1 FOR UPDATE", activo_id
            )
            if actual is None:
                raise ContabilidadNoEncontradaError(str(activo_id))
            nuevos = {k: v for k, v in campos.items() if k in _CAMPOS_ACTIVO}
            registrada = await conn.fetchval(
                "SELECT count(*) FROM contabilidad.depreciaciones WHERE activo_id = $1", activo_id
            )
            cambian = [k for k in _CAMPOS_DEPRECIACION if k in nuevos and nuevos[k] != actual[k]]
            if registrada and cambian:
                raise ConflictoContabilidadError(
                    "El activo ya tiene depreciación registrada: no se pueden cambiar costo, categoría, método ni "
                    "fechas (anule los asientos de depreciación para corregirlo)"
                )
            datos = dict(actual) | nuevos
            for campo in ("descripcion", "categoria", "fecha_adquisicion", "costo", "metodo", "depreciar_desde"):
                if datos.get(campo) is None:
                    raise DatosInvalidosError(f"«{campo}» no puede quedar vacío")
            datos["valor_residual"] = _redondear(datos["valor_residual"] or _CERO)
            datos["depreciacion_acumulada_inicial"] = _redondear(datos["depreciacion_acumulada_inicial"] or _CERO)
            _validar_activo(datos)
            if any(datos[k] != actual[k] for k in _CAMPOS_FISCALES):
                await _exigir_ir2_abierto(conn, min(
                    cc.anio_de_entrada(_activo_calculo(actual)), cc.anio_de_entrada(_activo_calculo(datos))
                ))
            if "cuenta_activo_id" in nuevos:
                await _validar_cuenta_activo(conn, nuevos["cuenta_activo_id"])
            columnas = [c for c in _CAMPOS_ACTIVO if c in nuevos]
            if columnas:
                asignaciones = ", ".join(f"{c} = ${i + 2}" for i, c in enumerate(columnas))
                await conn.execute(
                    f"UPDATE contabilidad.activos_fijos SET {asignaciones}, actualizado_en = now() WHERE id = $1",
                    activo_id, *[datos[c] for c in columnas],
                )
            return await _activo(conn, activo_id)


async def dar_baja_activo(
    pool: asyncpg.Pool, activo_id: uuid.UUID, fecha: date | None, valor_venta: Decimal | None = None
) -> dict[str, Any]:
    """Deja de depreciarse desde el mes siguiente a la baja (409 si ya hay
    depreciación registrada después de ese mes). `valor_venta` = lo que se
    cobró si se vendió (rebaja la cuenta fiscal de su categoría; ver
    `contabilidad_calculos.depreciacion_fiscal_del_anio`). `fecha` null =
    revertir la baja. 409 si cambia un IR-2 ya presentado."""
    if valor_venta is not None:
        valor_venta = _redondear(valor_venta)
        if valor_venta < 0:
            raise DatosInvalidosError("El valor de venta no puede ser negativo")
        if fecha is None:
            raise DatosInvalidosError("El valor de venta requiere la fecha de la baja")
    async with pool.acquire() as conn:
        async with conn.transaction():
            actual = await conn.fetchrow("SELECT * FROM contabilidad.activos_fijos WHERE id = $1 FOR UPDATE", activo_id)
            if actual is None:
                raise ContabilidadNoEncontradaError(str(activo_id))
            if (fecha, valor_venta) != (actual["fecha_baja"], actual["valor_venta"]):
                anios = [f.year for f in (fecha, actual["fecha_baja"]) if f is not None]
                await _exigir_ir2_abierto(conn, max(min(anios), cc.anio_de_entrada(_activo_calculo(actual))))
            if fecha is not None:
                if fecha < actual["fecha_adquisicion"]:
                    raise DatosInvalidosError("La baja no puede ser anterior a la adquisición")
                posterior = await conn.fetchval(
                    "SELECT max(periodo) FROM contabilidad.depreciaciones WHERE activo_id = $1 AND periodo > $2",
                    activo_id, cc.periodo_de(fecha),
                )
                if posterior:
                    raise ConflictoContabilidadError(
                        f"El activo tiene depreciación registrada hasta {nombre_periodo(posterior)}: anule esos asientos "
                        "antes de darlo de baja en esa fecha"
                    )
            await conn.execute(
                "UPDATE contabilidad.activos_fijos SET fecha_baja = $2, valor_venta = $3, actualizado_en = now()"
                " WHERE id = $1",
                activo_id, fecha, valor_venta,
            )
            return await _activo(conn, activo_id)


async def eliminar_activo(pool: asyncpg.Pool, activo_id: uuid.UUID) -> None:
    async with pool.acquire() as conn:
        async with conn.transaction():
            actual = await conn.fetchrow("SELECT * FROM contabilidad.activos_fijos WHERE id = $1 FOR UPDATE", activo_id)
            if actual is None:
                raise ContabilidadNoEncontradaError(str(activo_id))
            await _exigir_ir2_abierto(conn, cc.anio_de_entrada(_activo_calculo(actual)))
            if await conn.fetchval("SELECT 1 FROM contabilidad.depreciaciones WHERE activo_id = $1 LIMIT 1", activo_id):
                raise ConflictoContabilidadError(
                    "El activo tiene depreciación registrada: dele de baja en lugar de eliminarlo"
                )
            await conn.execute("DELETE FROM contabilidad.activos_fijos WHERE id = $1", activo_id)


async def tabla_depreciacion(pool: asyncpg.Pool, activo_id: uuid.UUID, *, hasta: str | None = None) -> dict[str, Any]:
    """Cuadro de depreciación mensual del activo (método del activo) hasta
    `hasta` (AAAAMM; por defecto diciembre del año en curso), con lo ya
    registrado, y la depreciación fiscal (método DGII) de cada año."""
    async with pool.acquire() as conn:
        activo = await _activo(conn, activo_id)
        todos = await conn.fetch(_SELECT_ACTIVOS_FISCAL)
        registradas = {
            f["periodo"]: f for f in await conn.fetch(
                """
                SELECT d.periodo, d.monto, a.numero FROM contabilidad.depreciaciones d
                JOIN contabilidad.asientos a ON a.id = d.asiento_id WHERE d.activo_id = $1
                """,
                activo_id,
            )
        }
    hasta = hasta or f"{hoy().year:04d}12"
    calculo = _activo_calculo(activo)
    cuotas = cc.cuotas_depreciacion(calculo, hasta)
    acumulada = activo["depreciacion_acumulada_inicial"]
    filas = []
    for periodo, cuota in cuotas.items():
        acumulada += cuota
        registro = registradas.get(periodo)
        filas.append({
            "periodo": periodo, "cuota": cuota, "acumulada": acumulada, "valor_en_libros": activo["costo"] - acumulada,
            "registrada": registro is not None, "monto_registrado": registro["monto"] if registro else None,
            "asiento_numero": numero_asiento(registro["numero"]) if registro else None,
        })
    # Fiscal: la parte del activo en la depreciación del año (cuenta de su
    # categoría para la 2 y la 3; ver depreciacion_fiscal_del_anio).
    calculos = [_activo_calculo(f) for f in todos]
    indice = next(i for i, f in enumerate(todos) if f["id"] == activo_id)
    fiscal = [
        {"anio": f"{a:04d}", "depreciacion": cc.depreciacion_fiscal_del_anio(calculos, a).por_activo.get(indice, _CERO)}
        for a in range(cc.anio_de_entrada(calculo), int(hasta[:4]) + 1)
    ]
    return {"activo": activo, "hasta": hasta, "cuotas": filas, "fiscal": fiscal}


async def _cuotas_periodo(conn: asyncpg.Connection, periodo: str) -> list[dict[str, Any]]:
    filas = await conn.fetch(
        f"{_SELECT_ACTIVOS} WHERE a.depreciar_desde <= $1 ORDER BY a.categoria, a.descripcion", periodo
    )
    registradas = {
        f["activo_id"] for f in await conn.fetch(
            "SELECT activo_id FROM contabilidad.depreciaciones WHERE periodo = $1", periodo
        )
    }
    resultado = []
    for fila in filas:
        calculo = _activo_calculo(fila)
        cuota = cc.cuotas_depreciacion(calculo, periodo).get(periodo, _CERO)
        if cuota <= 0:
            continue
        anteriores = await conn.fetchval(
            "SELECT count(*) FROM contabilidad.depreciaciones WHERE activo_id = $1 AND periodo < $2", fila["id"], periodo
        )
        meses_previos = sum(1 for p in cc.cuotas_depreciacion(calculo, periodo) if p < periodo)
        resultado.append({
            "activo_id": fila["id"], "descripcion": fila["descripcion"], "categoria": fila["categoria"],
            "metodo": fila["metodo"], "cuota": cuota, "registrada": fila["id"] in registradas,
            "meses_sin_registrar_antes": max(meses_previos - anteriores, 0),
        })
    return resultado


def _validar_periodo_depreciacion(periodo: str) -> None:
    if not re.fullmatch(r"20[0-9]{2}(0[1-9]|1[0-2])", periodo or ""):
        raise DatosInvalidosError(f"Período inválido: {periodo!r} (formato AAAAMM)")
    if periodo > cc.periodo_de(hoy()):
        raise DatosInvalidosError("No se puede depreciar un mes futuro")


async def depreciacion_del_periodo(pool: asyncpg.Pool, periodo: str) -> dict[str, Any]:
    """Vista previa de la depreciación del mes por activo y si ya está registrada."""
    _validar_periodo_depreciacion(periodo)
    async with pool.acquire() as conn:
        cuotas = await _cuotas_periodo(conn, periodo)
        asiento = await conn.fetchrow(
            "SELECT id, numero FROM contabilidad.asientos WHERE origen = 'depreciacion' AND origen_id = $1"
            " AND estado <> 'anulado'",
            periodo,
        )
    return {
        "periodo": periodo, "nombre_periodo": nombre_periodo(periodo),
        "total": sum((c["cuota"] for c in cuotas), _CERO),
        "registrada": asiento is not None,
        "asiento_id": asiento["id"] if asiento else None,
        "asiento_numero": numero_asiento(asiento["numero"]) if asiento else None,
        "activos": cuotas,
        "advertencias": _advertencias_depreciacion(cuotas, periodo),
    }


def _advertencias_depreciacion(cuotas: list[dict[str, Any]], periodo: str) -> list[str]:
    return [
        f"«{c['descripcion']}» tiene {c['meses_sin_registrar_antes']} mes(es) anteriores a {nombre_periodo(periodo)} "
        "sin depreciación registrada: regístrelos también"
        for c in cuotas if c["meses_sin_registrar_antes"]
    ]


async def registrar_depreciacion(pool: asyncpg.Pool, periodo: str) -> dict[str, Any]:
    """Asiento de depreciación del mes (Débito gasto de depreciación ·
    Crédito depreciación acumulada por categoría) con la cuota de cada
    activo según su método. 409 si ya está registrada (anule el asiento para
    recalcularla); 422 si no hay nada que depreciar o falta una cuenta."""
    _validar_periodo_depreciacion(periodo)
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext('contabilidad.depreciacion'))")
            existente = await conn.fetchrow(
                "SELECT numero FROM contabilidad.asientos WHERE origen = 'depreciacion' AND origen_id = $1"
                " AND estado <> 'anulado'",
                periodo,
            )
            if existente is not None:
                raise ConflictoContabilidadError(
                    f"La depreciación de {nombre_periodo(periodo)} ya está registrada en el asiento "
                    f"{numero_asiento(existente['numero'])}: anúlelo para recalcularla"
                )
            cuotas = [c for c in await _cuotas_periodo(conn, periodo) if not c["registrada"]]
            if not cuotas:
                raise DatosInvalidosError(f"No hay activos que depreciar en {nombre_periodo(periodo)}")
            por_categoria: dict[int, Decimal] = {}
            for c in cuotas:
                por_categoria[c["categoria"]] = por_categoria.get(c["categoria"], _CERO) + c["cuota"]
            propuesta = cc.asiento_depreciacion(periodo, por_categoria, nombre_periodo(periodo))
            try:
                lineas = (await _contexto(conn)).resolver(propuesta)
            except NoContabilizableError as exc:
                raise DatosInvalidosError(str(exc)) from exc
            total = cc.totales(lineas)[0]
            asiento_id = await conn.fetchval(
                """
                INSERT INTO contabilidad.asientos
                    (fecha, concepto, referencia, estado, origen, origen_id, huella, total, contabilizado_en)
                VALUES ($1, $2, $3, 'contabilizado', 'depreciacion', $4, $5, $6, now())
                RETURNING id
                """,
                propuesta.fecha, propuesta.concepto, propuesta.referencia, periodo,
                cc.huella(lineas, propuesta.fecha, propuesta.concepto, propuesta.referencia), total,
            )
            await _insertar_lineas(conn, asiento_id, lineas)
            await conn.executemany(
                "INSERT INTO contabilidad.depreciaciones (activo_id, periodo, monto, asiento_id) VALUES ($1, $2, $3, $4)",
                [(c["activo_id"], periodo, c["cuota"], asiento_id) for c in cuotas],
            )
            asiento = await _asiento(conn, asiento_id)
    return {"asiento": asiento, "total": total, "activos": cuotas, "advertencias": _advertencias_depreciacion(cuotas, periodo)}


# Todos los activos en un orden fijo (el reparto de centavos de la cuenta
# de cada categoría depende del orden).
_SELECT_ACTIVOS_FISCAL = "SELECT * FROM contabilidad.activos_fijos ORDER BY categoria, descripcion, id"


async def depreciacion_fiscal(conn: asyncpg.Connection, anio: str) -> dict[str, Any]:
    """Depreciación fiscal del año (Art. 287 para todos los activos, sea
    cual sea el método de sus asientos; categorías 2 y 3 por cuenta de
    categoría, ver `contabilidad_calculos.depreciacion_fiscal_del_anio`): la
    sugerencia del ajuste "Depreciación del ejercicio" del IR-2. `total` es
    None si no hay activos registrados hasta ese año (el IR-2 sigue
    pidiéndola a mano). `ingreso_gravable`: ventas de activos que superan la
    cuenta de su categoría (renta gravable del año)."""
    filas = await conn.fetch(_SELECT_ACTIVOS_FISCAL)
    calculos = [_activo_calculo(f) for f in filas]
    calculo = cc.depreciacion_fiscal_del_anio(calculos, int(anio))
    activos = [
        {
            "activo_id": fila["id"], "descripcion": fila["descripcion"], "categoria": fila["categoria"],
            "tasa_dgii": cc.TASAS_CATEGORIA[fila["categoria"]] * 100, "depreciacion": calculo.por_activo[i],
        }
        for i, fila in enumerate(filas) if calculo.por_activo.get(i)
    ]
    categorias = [
        {
            "categoria": c.categoria, "nombre": cc.NOMBRE_CATEGORIA[c.categoria],
            "tasa_dgii": cc.TASAS_CATEGORIA[c.categoria] * 100, "valor_inicio": c.valor_inicio,
            "adiciones": c.adiciones, "retiros": c.retiros, "base": c.base, "depreciacion": c.depreciacion,
            "valor_final": c.valor_final, "ingreso_gravable": c.ingreso_gravable,
        }
        for c in calculo.categorias
    ]
    hay_activos = any(cc.anio_de_entrada(a) <= int(anio) for a in calculos)
    return {
        "anio": anio, "total": calculo.total if hay_activos else None, "activos": activos, "categorias": categorias,
        "ingreso_gravable": calculo.ingreso_gravable,
    }


async def obtener_depreciacion_fiscal(pool: asyncpg.Pool, anio: str) -> dict[str, Any]:
    if not re.fullmatch(r"20[0-9]{2}", anio or ""):
        raise DatosInvalidosError(f"Año inválido: {anio!r} (formato AAAA)")
    async with pool.acquire() as conn:
        resultado = await depreciacion_fiscal(conn, anio)
    return resultado | {"total": resultado["total"] or _CERO}
