"""Kardex valorizado de un producto (pantalla Kardex): cada movimiento con
su cantidad, costo unitario, costo del movimiento y el saldo acumulado
(existencia, costo promedio, valor), más el saldo inicial del período.

El saldo se calcula al leer, re-ejecutando TODOS los movimientos del
producto (o del producto en un almacén) desde el primero:

- 'promedio' (promedio ponderado, el método con que el servicio valúa el
  inventario): cada movimiento vale lo que quedó grabado en
  `movimientos.costo_unitario` (las salidas se graban al costo promedio
  vigente). Movimientos de antes de la Fase 4 sin costo se valúan al
  promedio acumulado del Kardex. Igual que `stock.costo_promedio_ponderado`,
  una entrada sin existencia previa positiva (cero o negativa por ventas
  forzadas) no se promedia con lo anterior: el saldo pasa a valer
  existencia × costo de esa entrada, así que el saldo final coincide con
  `stock_actual × costo_promedio` del producto.
- 'peps' (primero en entrar, primero en salir): mismas entradas, pero cada
  salida consume las capas de entrada más antiguas. Es solo una vista: no
  cambia el costo con que se registran las salidas.

Sin `almacen_id` las transferencias entre almacenes no se muestran (cada
una es una salida y una entrada del mismo producto que se anulan).

Orden: `movimientos.secuencia`, que se asigna al insertar con el producto
ya bloqueado (stock.py) -- el orden en que de verdad se aplicaron. No
`creado_en`: hasta la migración 11 era now() (inicio de la transacción), y
una transacción que empezó antes pero bloqueó el producto después quedaba
antes en el Kardex, con un saldo distinto del real.
"""
from __future__ import annotations

import csv
import io
import uuid
from collections import deque
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import asyncpg

from . import stock
from .errores import AlmacenNoEncontradoError, ProductoNoEncontradoError
from .repository import rango_fechas

METODOS = ("promedio", "peps")

_CERO = Decimal("0")
_CENTAVOS = Decimal("0.01")
_COSTO = Decimal("0.0001")


def _dinero(valor: Decimal) -> Decimal:
    return valor.quantize(_CENTAVOS, rounding=ROUND_HALF_UP)


def _costo(valor: Decimal) -> Decimal:
    return valor.quantize(_COSTO, rounding=ROUND_HALF_UP)


def concepto(mov: dict[str, Any]) -> str:
    """Texto de la columna "Tipo de movimiento" del Kardex."""
    entrada = mov["tipo"] == "entrada"
    referencia = mov.get("referencia_tipo")
    if referencia == "compra":
        return "Compra"
    if referencia == "factura":
        return "Venta" if not entrada else "Devolución de venta"
    if referencia == "transferencia":
        return "Transferencia recibida" if entrada else "Transferencia enviada"
    if referencia == "anulacion_ajuste":
        return "Anulación de ajuste"
    if referencia == "lote":
        return "Entrada de lote"
    if referencia == "ajuste":
        if mov.get("motivo") == "Stock inicial":
            return "Stock inicial"
        return "Ajuste de entrada" if entrada else "Ajuste de salida"
    return "Entrada" if entrada else "Salida"


def _saldo(cantidad: Decimal, valor: Decimal) -> dict[str, Decimal]:
    return {
        "cantidad": cantidad,
        "costo_promedio": _costo(valor / cantidad) if cantidad > 0 else _costo(_CERO),
        "valor": _dinero(valor),
    }


def calcular_kardex(
    movimientos: list[dict[str, Any]], *, metodo: str = "promedio", inicio: datetime | None = None
) -> dict[str, Any]:
    """Recorre `movimientos` (en el orden en que se aplicaron -- `secuencia`
    --, todos los del producto hasta el fin del período) y arma las filas de los que caen desde
    `inicio`. Función pura: los tests la usan directamente."""
    if metodo not in METODOS:
        raise ValueError(f"Método de valuación desconocido: {metodo}")
    cantidad = _CERO
    valor = _CERO
    capas: deque[list[Decimal]] = deque()  # [cantidad, costo] -- solo PEPS
    ultimo_costo = _CERO
    saldo_inicial: dict[str, Decimal] | None = None
    filas: list[dict[str, Any]] = []
    entradas = _CERO
    salidas = _CERO

    for mov in movimientos:
        # Una vez dentro del período, todo lo que sigue (en orden de
        # secuencia) también lo está, aunque un movimiento viejo tenga un
        # `creado_en` (inicio de su transacción) apenas anterior.
        en_periodo = saldo_inicial is not None or inicio is None or mov["creado_en"] >= inicio
        if en_periodo and saldo_inicial is None:
            saldo_inicial = _saldo(cantidad, valor)
        q: Decimal = mov["cantidad"]
        promedio = valor / cantidad if cantidad > 0 else ultimo_costo
        if mov["tipo"] == "entrada":
            unitario = mov["costo_unitario"] if mov["costo_unitario"] is not None else promedio
            total = q * unitario
            if cantidad <= 0 or cantidad + q <= 0:
                # Misma regla que stock.costo_promedio_ponderado: sin
                # existencia previa positiva, el costo es el de la entrada.
                valor = (cantidad + q) * unitario
            else:
                valor += total
            cantidad += q
            capas.append([q, unitario])
            ultimo_costo = unitario
            signo = Decimal("1")
        else:
            if metodo == "peps":
                total = _CERO
                pendiente = q
                while pendiente > 0 and capas:
                    capa = capas[0]
                    tomar = min(pendiente, capa[0])
                    total += tomar * capa[1]
                    capa[0] -= tomar
                    pendiente -= tomar
                    if capa[0] <= 0:
                        capas.popleft()
                if pendiente > 0:  # existencia negativa: al último costo conocido
                    total += pendiente * ultimo_costo
            else:
                unitario_salida = mov["costo_unitario"] if mov["costo_unitario"] is not None else promedio
                total = q * unitario_salida
            unitario = total / q if q else _CERO
            cantidad -= q
            valor -= total
            signo = Decimal("-1")
        if cantidad == 0:
            valor = _CERO  # sin existencia no queda valor (residuos de redondeo)

        if not en_periodo:
            continue
        if signo > 0:
            entradas += q
        else:
            salidas += q
        filas.append(
            {
                "movimiento_id": mov["id"],
                "fecha": mov["creado_en"],
                "tipo": mov["tipo"],
                "concepto": concepto(mov),
                "documento": mov.get("documento"),
                "motivo": mov.get("motivo"),
                "referencia_tipo": mov.get("referencia_tipo"),
                "referencia_id": mov.get("referencia_id"),
                "almacen_id": mov.get("almacen_id"),
                "almacen_nombre": mov.get("almacen_nombre"),
                "lote_id": mov.get("lote_id"),
                "lote_numero": mov.get("lote_numero"),
                "cantidad": signo * q,
                "costo_unitario": _costo(unitario),
                "costo_total": _dinero(signo * total),
                "saldo_cantidad": cantidad,
                "costo_promedio": _saldo(cantidad, valor)["costo_promedio"],
                "saldo_valor": _dinero(valor),
            }
        )

    if saldo_inicial is None:
        saldo_inicial = _saldo(cantidad, valor)
    return {
        "metodo": metodo,
        "saldo_inicial": saldo_inicial,
        "saldo_final": _saldo(cantidad, valor),
        "entradas": entradas,
        "salidas": salidas,
        "filas": filas,
    }


async def obtener_kardex(
    pool: asyncpg.Pool,
    *,
    producto_id: uuid.UUID,
    almacen_id: uuid.UUID | None = None,
    desde: date | None = None,
    hasta: date | None = None,
    metodo: str = "promedio",
    tipo: str | None = None,
    referencia_tipo: str | None = None,
) -> dict[str, Any]:
    """Kardex del producto (en un almacén o en todos) del período
    [desde, hasta]. `tipo` ('entrada'/'salida') y `referencia_tipo`
    ('compra', 'factura', 'ajuste', ...) solo filtran las filas devueltas:
    los saldos siempre cuentan todos los movimientos."""
    inicio, fin = rango_fechas(desde, hasta)
    async with pool.acquire() as conn:
        producto = await conn.fetchrow(
            "SELECT id, sku, nombre, unidad_medida, stock_actual, stock_minimo, costo_promedio "
            "FROM inventario.productos WHERE id = $1",
            producto_id,
        )
        if producto is None:
            raise ProductoNoEncontradoError(str(producto_id))
        almacen = None
        if almacen_id is not None:
            almacen = await conn.fetchrow("SELECT id, nombre FROM inventario.almacenes WHERE id = $1", almacen_id)
            if almacen is None:
                raise AlmacenNoEncontradoError(str(almacen_id))
        valores: list[Any] = [producto_id]
        condiciones = ["m.producto_id = $1"]
        if almacen_id is not None:
            valores.append(almacen_id)
            condiciones.append(f"m.almacen_id = ${len(valores)}")
        else:
            condiciones.append("m.referencia_tipo IS DISTINCT FROM 'transferencia'")
        if fin is not None:
            valores.append(fin)
            condiciones.append(f"m.creado_en < ${len(valores)}")
        filas = await conn.fetch(
            f"""
            SELECT m.*, a.nombre AS almacen_nombre, l.numero AS lote_numero
            FROM inventario.movimientos m
            LEFT JOIN inventario.almacenes a ON a.id = m.almacen_id
            LEFT JOIN inventario.lotes l ON l.id = m.lote_id
            WHERE {' AND '.join(condiciones)}
            ORDER BY m.secuencia
            """,
            *valores,
        )
    resultado = calcular_kardex([dict(fila) for fila in filas], metodo=metodo, inicio=inicio)
    if tipo or referencia_tipo:
        resultado["filas"] = [
            fila
            for fila in resultado["filas"]
            if (not tipo or fila["tipo"] == tipo)
            and (not referencia_tipo or fila["referencia_tipo"] == referencia_tipo)
        ]
    return {
        "producto_id": producto["id"],
        "producto_sku": producto["sku"],
        "producto_nombre": producto["nombre"],
        "unidad_medida": producto["unidad_medida"],
        "stock_minimo": producto["stock_minimo"],
        "almacen_id": almacen["id"] if almacen else None,
        "almacen_nombre": almacen["nombre"] if almacen else None,
        "desde": desde,
        "hasta": hasta,
        **resultado,
    }


def _num(valor: Decimal) -> str:
    return format(valor.normalize() if valor == valor.to_integral() else valor, "f")


def kardex_csv(kardex: dict[str, Any]) -> str:
    """El Kardex como CSV para Excel: UTF-8 con BOM, separado por comas,
    números sin separador de miles y con punto decimal, fecha DD/MM/AAAA."""
    salida = io.StringIO()
    escritor = csv.writer(salida, lineterminator="\r\n")
    escritor.writerow(
        [
            "Fecha",
            "Documento",
            "Tipo de movimiento",
            "Almacén",
            "Lote",
            "Cantidad",
            "Costo unitario",
            "Costo del movimiento",
            "Costo promedio",
            "Existencia",
            "Valor total",
        ]
    )
    inicial = kardex["saldo_inicial"]
    escritor.writerow(
        [
            kardex["desde"].strftime("%d/%m/%Y") if kardex.get("desde") else "",
            "",
            "Saldo inicial",
            kardex.get("almacen_nombre") or "",
            "",
            "",
            "",
            "",
            _num(inicial["costo_promedio"]),
            _num(inicial["cantidad"]),
            _num(inicial["valor"]),
        ]
    )
    for fila in kardex["filas"]:
        escritor.writerow(
            [
                fila["fecha"].astimezone(stock.ZONA_RD).strftime("%d/%m/%Y"),
                fila["documento"] or "",
                fila["concepto"],
                fila["almacen_nombre"] or "",
                fila["lote_numero"] or "",
                _num(fila["cantidad"]),
                _num(fila["costo_unitario"]),
                _num(fila["costo_total"]),
                _num(fila["costo_promedio"]),
                _num(fila["saldo_cantidad"]),
                _num(fila["saldo_valor"]),
            ]
        )
    return "﻿" + salida.getvalue()
