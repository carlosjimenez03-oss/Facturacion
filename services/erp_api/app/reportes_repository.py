"""Reportes (Fase 6 · Operación): agregaciones de solo lectura sobre los
datos de cada módulo, con los mismos criterios que sus pantallas y
declaraciones:

- Ventas: e-CF de venta ACEPTADOS (31, 32, 33, 34, 44, 45, 46; la E34
  resta), con el mismo desglose por tasa que el IT-1 y el 607
  (`formatos.desglose_por_tasa`). Por producto, las líneas del e-CF.
- ITBIS ventas vs. compras: ventas como el IT-1 (`precalculos.acumular_ventas`)
  y compras del 606 por período (ITBIS facturado, llevado al costo,
  adelantado) y retenido a proveedores por mes de pago.
- Compras: renglones del 606 por fecha del comprobante (las notas de crédito
  restan), por proveedor, categoría (tipo de bienes y servicios) o mes.
- Antigüedad de saldos: los CTE de /cuentas-por-cobrar y
  /compras/cuentas-por-pagar con los días vencidos a hoy.
- Cobros y pagos: `ventas.cobros` y `compras.pagos` no anulados.
- Inventario valorizado, rotación y más vendidos: `inventario.*` (de
  inventario_api, solo lectura), existencia × costo promedio como el IR-2 y
  los movimientos del Kardex.
- Retenciones: las efectuadas a proveedores (606, por fecha de pago, como el
  IT-1 y el IR-17) y las que nos hicieron los clientes en e-CF aceptados.

Todo se agrega en SQL o, cuando hace falta leer el JSON del e-CF, en una sola
consulta por reporte (sin N+1). Los rangos de fecha son [desde, hasta] y los
días de RD.
"""
from __future__ import annotations

import json
import re
import uuid
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

import asyncpg

from . import formatos, impuestos_repository, precalculos
from .compras_repository import (
    _ES_NOTA_CREDITO,
    _JOINS_PROVEEDOR,
    _NOMBRE_PROVEEDOR,
    _SELECT_CXP_CON_ESTADO,
    _SELECT_PAGOS,
    _TOTAL_606,
)
from .repository import (
    _FECHA_EMISION,
    _NOMBRE_COMPRADOR,
    _RNC_COMPRADOR,
    _SELECT_CXC_CON_ESTADO,
    _ZONA_RD,
    DatosInvalidosError,
    _monto_dgii,
    hoy,
)

_CERO = Decimal("0")
_CIEN = Decimal("100")
MAXIMO_DIAS = 1100  # ~3 años por consulta

TIPOS_ECF = {
    31: "Crédito fiscal",
    32: "Consumo",
    33: "Nota de débito",
    34: "Nota de crédito",
    44: "Regímenes especiales",
    45: "Gubernamental",
    46: "Exportaciones",
}

_SIGNO_606 = f"(CASE WHEN {_ES_NOTA_CREDITO} THEN -1 ELSE 1 END)"


def _rango(desde: date, hasta: date) -> None:
    if desde > hasta:
        raise DatosInvalidosError("La fecha inicial es posterior a la final")
    if (hasta - desde).days > MAXIMO_DIAS:
        raise DatosInvalidosError(f"El período no puede pasar de {MAXIMO_DIAS} días")


def _aaaammdd(fecha: date) -> str:
    return f"{fecha:%Y%m%d}"


def _instante(fecha: date) -> datetime:
    """Inicio del día en hora de RD (para filtrar columnas timestamptz)."""
    return datetime.combine(fecha, time.min, tzinfo=_ZONA_RD)


def _periodos(desde: date, hasta: date) -> list[str]:
    """Meses AAAAMM de [desde, hasta]."""
    resultado = []
    anio, mes = desde.year, desde.month
    while (anio, mes) <= (hasta.year, hasta.month):
        resultado.append(f"{anio}{mes:02d}")
        anio, mes = (anio + 1, 1) if mes == 12 else (anio, mes + 1)
    return resultado


def _nombre_mes(periodo: str) -> str:
    nombre = precalculos.nombre_periodo(periodo)
    return nombre[:1].upper() + nombre[1:]


def _rnc(valor: str | None) -> str:
    return re.sub(r"[^0-9A-Za-z]", "", valor or "").upper()


def _redondear(valor: Decimal) -> Decimal:
    return precalculos.redondear(Decimal(valor))


# --- Ventas ---------------------------------------------------------

_MONTOS_VENTA = ("gravado_18", "gravado_16", "gravado_0", "exento", "itbis_18", "itbis_16", "subtotal", "itbis", "total")


def _montos(desglose: dict[str, Decimal], signo: Decimal, total: Decimal | None = None) -> dict[str, Decimal]:
    subtotal = desglose["g18"] + desglose["g16"] + desglose["g0"] + desglose["exento"]
    itbis = desglose["i18"] + desglose["i16"]
    return {
        "gravado_18": signo * desglose["g18"],
        "gravado_16": signo * desglose["g16"],
        "gravado_0": signo * desglose["g0"],
        "exento": signo * desglose["exento"],
        "itbis_18": signo * desglose["i18"],
        "itbis_16": signo * desglose["i16"],
        "subtotal": signo * subtotal,
        "itbis": signo * itbis,
        # Total del e-CF (con selectivo, otros impuestos y propina) o, por línea, subtotal + ITBIS.
        "total": signo * (total if total is not None else subtotal + itbis),
    }


class _Grupo:
    def __init__(self, clave: str, etiqueta: str, detalle: str | None = None):
        self.clave, self.etiqueta, self.detalle = clave, etiqueta, detalle
        self.documentos: set[str] = set()
        self.cantidad = _CERO
        self.montos = dict.fromkeys(_MONTOS_VENTA, _CERO)

    def sumar(self, documento: str, montos: dict[str, Decimal], cantidad: Decimal = _CERO) -> None:
        self.documentos.add(documento)
        self.cantidad += cantidad
        for k, v in montos.items():
            self.montos[k] += v

    def fila(self, con_cantidad: bool) -> dict[str, Any]:
        return {
            "clave": self.clave, "etiqueta": self.etiqueta, "detalle": self.detalle,
            "documentos": len(self.documentos), "cantidad": self.cantidad if con_cantidad else None,
            **{k: _redondear(v) for k, v in self.montos.items()},
        }


def _items(datos: dict[str, Any]) -> list[dict[str, Any]]:
    item = (datos.get("DetallesItems") or {}).get("Item")
    lista = item if isinstance(item, list) else ([item] if item else [])
    return [i for i in lista if isinstance(i, dict)]


async def reporte_ventas(
    pool: asyncpg.Pool,
    *,
    desde: date,
    hasta: date,
    agrupar: str = "mes",
    cliente_rnc: str | None = None,
    tipo_ecf: int | None = None,
) -> dict[str, Any]:
    _rango(desde, hasta)
    valores: list[Any] = [list(formatos.TIPOS_VENTA_607), desde, hasta + timedelta(days=1)]
    filtros = ""
    if cliente_rnc:
        valores.append(_rnc(cliente_rnc))
        filtros += f" AND {_RNC_COMPRADOR} = ${len(valores)}"
    if tipo_ecf is not None:
        valores.append(tipo_ecf)
        filtros += f" AND f.tipo_ecf = ${len(valores)}"
    async with pool.acquire() as conn:
        facturas = await conn.fetch(
            f"""
            SELECT f.id, f.tipo_ecf, f.datos::text AS datos, {_FECHA_EMISION} AS fecha,
                   {_RNC_COMPRADOR} AS rnc, {_NOMBRE_COMPRADOR} AS nombre
            FROM facturacion.facturas f
            CROSS JOIN LATERAL (SELECT f.datos::jsonb AS d) x
            WHERE f.tipo_ecf = ANY($1::int[])
              AND f.estado = 'aceptado'
              AND {_FECHA_EMISION} >= $2 AND {_FECHA_EMISION} < $3
              {filtros}
            ORDER BY {_FECHA_EMISION}, f.encf
            """,
            *valores,
        )
        pendientes = await impuestos_repository._cantidad_pendientes_dgii(conn, desde, hasta + timedelta(days=1))

    grupos: dict[str, _Grupo] = {}
    totales = _Grupo("total", "Total")

    def grupo(clave: str, etiqueta: str, detalle: str | None = None) -> _Grupo:
        if clave not in grupos:
            grupos[clave] = _Grupo(clave, etiqueta, detalle)
        return grupos[clave]

    for f in facturas:
        datos = json.loads(f["datos"])
        signo = Decimal(-1) if f["tipo_ecf"] == 34 else Decimal(1)
        documento = str(f["id"])
        if agrupar == "producto":
            itbis_incluido = str(((datos.get("Encabezado") or {}).get("IdDoc") or {}).get("IndicadorMontoGravado") or "0") == "1"
            for item in _items(datos):
                if str(item.get("IndicadorFacturacion") or "") not in ("1", "2", "3", "4"):
                    continue  # 0 = no facturable
                nombre = str(item.get("NombreItem") or "").strip() or "(sin descripción)"
                montos = _montos(formatos._desglose_lineas([item], itbis_incluido), signo)
                cantidad = signo * formatos.a_decimal(item.get("CantidadItem"))
                grupo(nombre.casefold(), nombre).sumar(documento, montos, cantidad)
                totales.sumar(documento, montos, cantidad)
            continue
        totales_ecf = (datos.get("Encabezado") or {}).get("Totales") or {}
        montos = _montos(formatos.desglose_por_tasa(datos), signo, formatos.a_decimal(totales_ecf.get("MontoTotal")))
        totales.sumar(documento, montos)
        if agrupar == "dia":
            g = grupo(f["fecha"].isoformat(), f"{f['fecha']:%d/%m/%Y}")
        elif agrupar == "cliente":
            g = grupo(f["rnc"] or "", f["nombre"] or ("Consumidor final" if not f["rnc"] else f["rnc"]), f["rnc"])
            if f["nombre"]:
                g.etiqueta = f["nombre"]  # el nombre más reciente
        elif agrupar == "tipo_ecf":
            g = grupo(str(f["tipo_ecf"]), f"E{f['tipo_ecf']} · {TIPOS_ECF.get(f['tipo_ecf'], 'e-CF')}")
        else:
            periodo = f"{f['fecha']:%Y%m}"
            g = grupo(periodo, _nombre_mes(periodo))
        g.sumar(documento, montos)

    if agrupar in ("mes", "dia", "tipo_ecf"):
        orden = sorted(grupos.values(), key=lambda g: g.clave.zfill(8))
    else:
        orden = sorted(grupos.values(), key=lambda g: (-g.montos["total"], g.etiqueta))
    advertencias = impuestos_repository._advertencia_pendientes(pendientes)
    if agrupar == "producto" and facturas:
        advertencias.append(
            "Por producto, los montos salen de las líneas de cada e-CF (sin descuentos ni recargos globales) y "
            "pueden diferir levemente de los totales por tasa."
        )
    fila_total = totales.fila(agrupar == "producto")
    return {
        "desde": desde,
        "hasta": hasta,
        "agrupar": agrupar,
        "filas": [g.fila(agrupar == "producto") for g in orden],
        "totales": {k: v for k, v in fila_total.items() if k not in ("clave", "etiqueta", "detalle", "cantidad")},
        "advertencias": advertencias,
    }


# --- ITBIS ventas vs. compras ---------------------------------------------------------

async def reporte_itbis(pool: asyncpg.Pool, *, desde: date, hasta: date) -> dict[str, Any]:
    """Por mes (los meses completos que toca [desde, hasta])."""
    _rango(desde, hasta)
    periodos = _periodos(desde, hasta)
    inicio = date(desde.year, desde.month, 1)
    fin = impuestos_repository._rango_mes(periodos[-1])[1]
    async with pool.acquire() as conn:
        facturas = await impuestos_repository._facturas_venta(conn, inicio, fin)
        compras = await conn.fetch(
            f"""
            SELECT periodo,
                   sum({impuestos_repository._SIGNO_606} * (monto_servicios + monto_bienes)) AS compras,
                   sum({impuestos_repository._SIGNO_606} * itbis_facturado) AS itbis_facturado,
                   sum({impuestos_repository._SIGNO_606} * itbis_costo) AS itbis_costo
            FROM compras606.registros
            WHERE periodo >= $1 AND periodo <= $2
            GROUP BY periodo
            """,
            periodos[0],
            periodos[-1],
        )
        retenido = await conn.fetch(
            """
            SELECT substr(fecha_pago, 1, 6) AS periodo, sum(itbis_retenido) AS itbis_retenido
            FROM compras606.registros
            WHERE fecha_pago >= $1 AND fecha_pago < $2 AND itbis_retenido <> 0
            GROUP BY 1
            """,
            f"{periodos[0]}01",
            f"{periodos[-1]}99",
        )
        pendientes = await impuestos_repository._cantidad_pendientes_dgii(conn, inicio, fin)
    por_mes: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for factura in facturas:
        fecha = formatos.fecha_dgii(((factura["datos"].get("Encabezado") or {}).get("Emisor") or {}).get("FechaEmision"))
        if fecha is not None:
            por_mes[f"{fecha:%Y%m}"].append(factura)
    c_por_mes = {c["periodo"]: c for c in compras}
    r_por_mes = {r["periodo"]: r["itbis_retenido"] for r in retenido}
    filas = []
    for periodo in periodos:
        ventas = precalculos.acumular_ventas(por_mes.get(periodo, []))
        c = c_por_mes.get(periodo)
        itbis_compras = c["itbis_facturado"] if c else _CERO
        itbis_costo = c["itbis_costo"] if c else _CERO
        itbis_ventas = ventas.i18 + ventas.i16
        adelantado = itbis_compras - itbis_costo
        filas.append({
            "periodo": periodo,
            "ventas_gravadas": _redondear(ventas.g18 + ventas.g16 + ventas.exportaciones),
            "itbis_ventas": _redondear(itbis_ventas),
            "compras": _redondear(c["compras"] if c else _CERO),
            "itbis_compras": _redondear(itbis_compras),
            "itbis_costo": _redondear(itbis_costo),
            "itbis_adelantado": _redondear(adelantado),
            "itbis_retenido": _redondear(r_por_mes.get(periodo, _CERO)),
            "diferencia": _redondear(itbis_ventas - adelantado),
        })
    campos = [k for k in filas[0] if k != "periodo"]
    totales = {"periodo": "total", **{k: sum((f[k] for f in filas), _CERO) for k in campos}}
    return {
        "desde": inicio,
        "hasta": fin - timedelta(days=1),
        "filas": filas,
        "totales": totales,
        "advertencias": impuestos_repository._advertencia_pendientes(pendientes),
    }


# --- Compras (606) ---------------------------------------------------------

_AGRUPAR_COMPRAS = {
    "proveedor": ("r.rnc_cedula", f"max({_NOMBRE_PROVEEDOR})", "max(r.rnc_cedula)"),
    "categoria": ("COALESCE(r.tipo_bienes_servicios::text, '')", "NULL", "NULL"),
    "mes": ("substr(r.fecha_comprobante, 1, 6)", "NULL", "NULL"),
}


async def reporte_compras(
    pool: asyncpg.Pool,
    *,
    desde: date,
    hasta: date,
    agrupar: str = "proveedor",
    proveedor_rnc: str | None = None,
    tipo_bienes_servicios: int | None = None,
) -> dict[str, Any]:
    _rango(desde, hasta)
    clave, nombre, detalle = _AGRUPAR_COMPRAS[agrupar]
    valores: list[Any] = [_aaaammdd(desde), _aaaammdd(hasta)]
    filtros = ""
    if proveedor_rnc:
        valores.append(_rnc(proveedor_rnc))
        filtros += f" AND r.rnc_cedula = ${len(valores)}"
    if tipo_bienes_servicios is not None:
        valores.append(tipo_bienes_servicios)
        filtros += f" AND r.tipo_bienes_servicios = ${len(valores)}"
    s = _SIGNO_606
    async with pool.acquire() as conn:
        filas = await conn.fetch(
            f"""
            SELECT {clave} AS clave, {nombre} AS nombre, {detalle} AS detalle,
                   count(*) AS documentos,
                   sum({s} * r.monto_servicios) AS monto_servicios,
                   sum({s} * r.monto_bienes) AS monto_bienes,
                   sum({s} * (r.monto_servicios + r.monto_bienes)) AS monto_facturado,
                   sum({s} * r.itbis_facturado) AS itbis_facturado,
                   sum(r.itbis_retenido) AS itbis_retenido,
                   sum(r.monto_retencion_renta) AS isr_retenido,
                   sum({s} * {_TOTAL_606}) AS total
            FROM compras606.registros r
            {_JOINS_PROVEEDOR}
            WHERE r.fecha_comprobante >= $1 AND r.fecha_comprobante <= $2 {filtros}
            GROUP BY 1
            """,
            *valores,
        )
    resultado = []
    for f in filas:
        datos = dict(f)
        if agrupar == "proveedor":
            datos["etiqueta"] = datos.pop("nombre") or datos["clave"]
        elif agrupar == "categoria":
            tipo = int(datos["clave"]) if datos["clave"] else None
            datos.pop("nombre")
            datos["etiqueta"] = (
                f"{tipo:02d} · {precalculos.TIPOS_BIENES_SERVICIOS.get(tipo, 'Otro')}" if tipo else "Sin clasificar"
            )
        else:
            datos.pop("nombre")
            datos["etiqueta"] = _nombre_mes(datos["clave"])
        resultado.append(datos)
    if agrupar == "mes":
        resultado.sort(key=lambda f: f["clave"])
    elif agrupar == "categoria":
        resultado.sort(key=lambda f: (f["clave"] == "", f["clave"].zfill(2)))
    else:
        resultado.sort(key=lambda f: (-f["total"], f["etiqueta"]))
    montos = ("monto_servicios", "monto_bienes", "monto_facturado", "itbis_facturado", "itbis_retenido", "isr_retenido", "total")
    totales = {
        "clave": "total", "etiqueta": "Total", "detalle": None,
        "documentos": sum(f["documentos"] for f in resultado),
        **{k: sum((f[k] for f in resultado), _CERO) for k in montos},
    }
    advertencias = []
    sin_clasificar = next((f for f in resultado if agrupar == "categoria" and f["clave"] == ""), None)
    if sin_clasificar:
        advertencias.append(
            f"{sin_clasificar['documentos']} comprobante(s) del período no tienen tipo de bienes y servicios: "
            "clasifíquelos en el Formato 606."
        )
    return {"desde": desde, "hasta": hasta, "agrupar": agrupar, "filas": resultado, "totales": totales,
            "advertencias": advertencias}


# --- Antigüedad de saldos ---------------------------------------------------------

_TRAMOS = ("por_vencer", "d1_30", "d31_60", "d61_90", "mas_90")


def _tramo(dias_vencida: int) -> str:
    if dias_vencida <= 0:
        return "por_vencer"
    if dias_vencida <= 30:
        return "d1_30"
    if dias_vencida <= 60:
        return "d31_60"
    if dias_vencida <= 90:
        return "d61_90"
    return "mas_90"


async def reporte_antiguedad(pool: asyncpg.Pool, tipo: str, *, rnc: str | None = None) -> dict[str, Any]:
    """Saldos pendientes por cliente (`cxc`) o proveedor (`cxp`) en tramos
    de días vencidos a hoy: por vencer, 1-30, 31-60, 61-90, más de 90."""
    hoy_ = hoy()
    if tipo == "cxc":
        consulta = f"""
            SELECT factura_id::text AS id, encf AS documento, cliente_rnc AS rnc, cliente_nombre AS nombre,
                   fecha_emision, vence, dias_vencida, pendiente
            FROM ({_SELECT_CXC_CON_ESTADO}) c
            WHERE estado <> 'cobrada' __FILTRO__
        """
        columna_rnc = "cliente_rnc"
    else:
        consulta = f"""
            SELECT registro606_id::text AS id, ncf AS documento, proveedor_rnc AS rnc, proveedor_nombre AS nombre,
                   fecha_comprobante AS fecha_emision, vence, dias_vencida, pendiente
            FROM ({_SELECT_CXP_CON_ESTADO}) c
            WHERE estado <> 'pagada' __FILTRO__
        """
        columna_rnc = "proveedor_rnc"
    valores: list[Any] = [hoy_]
    filtro = ""
    if rnc:
        valores.append(_rnc(rnc))
        filtro = f"AND {columna_rnc} = $2"
    async with pool.acquire() as conn:
        filas = await conn.fetch(consulta.replace("__FILTRO__", filtro) + " ORDER BY nombre, vence, documento", *valores)
    documentos = []
    por_contacto: dict[str, dict[str, Any]] = {}
    for f in filas:
        tramo = _tramo(f["dias_vencida"])
        nombre = f["nombre"] or ("Consumidor final" if not f["rnc"] else f["rnc"])
        documentos.append(dict(f) | {"nombre": nombre, "tramo": tramo})
        fila = por_contacto.setdefault(
            f["rnc"] or nombre,
            {"rnc": f["rnc"], "nombre": nombre, "documentos": 0, **dict.fromkeys(_TRAMOS, _CERO), "total": _CERO},
        )
        fila["documentos"] += 1
        fila[tramo] += f["pendiente"]
        fila["total"] += f["pendiente"]
    contactos = sorted(por_contacto.values(), key=lambda c: (-c["total"], c["nombre"]))
    totales = {
        "rnc": None, "nombre": "Total", "documentos": len(documentos),
        **{k: sum((c[k] for c in contactos), _CERO) for k in (*_TRAMOS, "total")},
    }
    return {"tipo": tipo, "fecha_corte": hoy_, "filas": contactos, "documentos": documentos, "totales": totales}


# --- Cobros y pagos ---------------------------------------------------------

async def reporte_cobros_pagos(pool: asyncpg.Pool, *, desde: date, hasta: date) -> dict[str, Any]:
    _rango(desde, hasta)
    async with pool.acquire() as conn:
        cobros = await conn.fetch(
            """
            SELECT co.id, co.fecha, f.encf AS documento,
                   COALESCE(NULLIF(f.datos::jsonb #>> '{Encabezado,Comprador,RazonSocialComprador}', ''), '') AS contraparte,
                   co.metodo, co.referencia, co.monto
            FROM ventas.cobros co
            LEFT JOIN facturacion.facturas f ON f.id = co.factura_id
            WHERE NOT co.anulado AND co.fecha >= $1 AND co.fecha <= $2
            """,
            desde,
            hasta,
        )
        pagos = await conn.fetch(
            f"""
            SELECT * FROM ({_SELECT_PAGOS}) p
            WHERE NOT p.anulado AND p.fecha >= $1 AND p.fecha <= $2
            """,
            desde,
            hasta,
        )
    movimientos = [
        {"id": str(c["id"]), "tipo": "cobro", "fecha": c["fecha"], "documento": c["documento"],
         "contraparte": c["contraparte"], "metodo": c["metodo"], "referencia": c["referencia"], "monto": c["monto"]}
        for c in cobros
    ] + [
        {"id": str(p["id"]), "tipo": "pago", "fecha": p["fecha"], "documento": p["ncf"],
         "contraparte": p["proveedor_nombre"] or p["proveedor_rnc"] or "", "metodo": p["metodo"],
         "referencia": p["referencia"], "monto": p["monto"]}
        for p in pagos
    ]
    movimientos.sort(key=lambda m: (m["fecha"], m["tipo"], m["documento"] or ""))
    por_mes = {p: {"periodo": p, "cobros": _CERO, "cantidad_cobros": 0, "pagos": _CERO, "cantidad_pagos": 0}
               for p in _periodos(desde, hasta)}
    por_metodo: dict[str, dict[str, Any]] = {}
    for m in movimientos:
        mes = por_mes[f"{m['fecha']:%Y%m}"]
        metodo = por_metodo.setdefault(m["metodo"], {"metodo": m["metodo"], "cobros": _CERO, "pagos": _CERO})
        campo = "cobros" if m["tipo"] == "cobro" else "pagos"
        mes[campo] += m["monto"]
        mes[f"cantidad_{campo}"] += 1
        metodo[campo] += m["monto"]
    filas = [f | {"neto": f["cobros"] - f["pagos"]} for f in por_mes.values()]
    totales = {
        "periodo": "total",
        "cobros": sum((f["cobros"] for f in filas), _CERO),
        "cantidad_cobros": sum(f["cantidad_cobros"] for f in filas),
        "pagos": sum((f["pagos"] for f in filas), _CERO),
        "cantidad_pagos": sum(f["cantidad_pagos"] for f in filas),
    }
    totales["neto"] = totales["cobros"] - totales["pagos"]
    return {
        "desde": desde,
        "hasta": hasta,
        "filas": filas,
        "por_metodo": sorted(por_metodo.values(), key=lambda m: -(m["cobros"] + m["pagos"])),
        "movimientos": movimientos,
        "totales": totales,
        "advertencias": [],
    }


# --- Inventario ---------------------------------------------------------

async def reporte_inventario_valorizado(
    pool: asyncpg.Pool,
    *,
    almacen_id: uuid.UUID | None = None,
    categoria: str | None = None,
    incluir_sin_existencia: bool = False,
) -> dict[str, Any]:
    """Existencia actual × costo promedio (como el IR-2 de erp_api y la
    valuación de inventario_api); negativos valen 0."""
    valores: list[Any] = []
    if almacen_id is not None:
        valores.append(almacen_id)
        existencia = "COALESCE(e.cantidad, 0)"
        join = f"LEFT JOIN inventario.existencias e ON e.producto_id = p.id AND e.almacen_id = ${len(valores)}"
    else:
        existencia, join = "p.stock_actual", ""
    condiciones = ["p.tipo = 'bien'", f"(p.activo OR {existencia} <> 0)"]
    if categoria:
        valores.append(categoria)
        condiciones.append(f"p.categoria = ${len(valores)}")
    if not incluir_sin_existencia:
        condiciones.append(f"{existencia} <> 0")
    async with pool.acquire() as conn:
        filas = await conn.fetch(
            f"""
            SELECT p.id::text AS producto_id, p.sku, p.nombre, p.categoria, p.unidad_medida,
                   {existencia} AS existencia, p.costo_promedio, p.precio_venta, p.stock_minimo,
                   round(GREATEST({existencia}, 0) * p.costo_promedio, 2) AS valor,
                   round(GREATEST({existencia}, 0) * p.precio_venta, 2) AS valor_venta
            FROM inventario.productos p
            {join}
            WHERE {' AND '.join(condiciones)}
            ORDER BY valor DESC, p.nombre
            """,
            *valores,
        )
    resultado = [
        dict(f) | {"bajo_minimo": f["stock_minimo"] > 0 and f["existencia"] <= f["stock_minimo"]} for f in filas
    ]
    negativos = sum(1 for f in resultado if f["existencia"] < 0)
    return {
        "fecha_corte": hoy(),
        "almacen_id": str(almacen_id) if almacen_id else None,
        "filas": resultado,
        "totales": {
            "productos": len(resultado),
            "existencia": sum((f["existencia"] for f in resultado), _CERO),
            "valor": sum((f["valor"] for f in resultado), _CERO),
            "valor_venta": sum((f["valor_venta"] for f in resultado), _CERO),
        },
        "advertencias": (
            [f"{negativos} producto(s) con existencia negativa se valoran en 0: revise sus movimientos."]
            if negativos else []
        ),
    }


async def reporte_rotacion(pool: asyncpg.Pool, *, desde: date, hasta: date, categoria: str | None = None) -> dict[str, Any]:
    """Entradas y salidas del período (sin las transferencias entre almacenes,
    que no cambian el total), existencia inicial y final reconstruidas desde
    la actual con el Kardex, y la rotación = salidas / existencia promedio."""
    _rango(desde, hasta)
    valores: list[Any] = [_instante(desde), _instante(hasta + timedelta(days=1))]
    filtro = ""
    if categoria:
        valores.append(categoria)
        filtro = f"AND p.categoria = ${len(valores)}"
    async with pool.acquire() as conn:
        filas = await conn.fetch(
            f"""
            WITH mov AS (
                SELECT producto_id,
                       COALESCE(sum(CASE WHEN tipo = 'entrada' THEN cantidad ELSE -cantidad END)
                                FILTER (WHERE creado_en < $2), 0) AS neto_periodo,
                       COALESCE(sum(CASE WHEN tipo = 'entrada' THEN cantidad ELSE -cantidad END)
                                FILTER (WHERE creado_en >= $2), 0) AS neto_posterior,
                       COALESCE(sum(cantidad) FILTER (
                           WHERE tipo = 'entrada' AND creado_en < $2
                             AND COALESCE(referencia_tipo, '') <> 'transferencia'), 0) AS entradas,
                       COALESCE(sum(cantidad) FILTER (
                           WHERE tipo = 'salida' AND creado_en < $2
                             AND COALESCE(referencia_tipo, '') <> 'transferencia'), 0) AS salidas,
                       COALESCE(sum(cantidad * COALESCE(costo_unitario, 0)) FILTER (
                           WHERE tipo = 'salida' AND creado_en < $2
                             AND COALESCE(referencia_tipo, '') <> 'transferencia'), 0) AS costo_salidas
                FROM inventario.movimientos
                WHERE creado_en >= $1
                GROUP BY producto_id
            )
            SELECT p.id::text AS producto_id, p.sku, p.nombre, p.categoria, p.stock_actual,
                   COALESCE(m.neto_periodo, 0) AS neto_periodo, COALESCE(m.neto_posterior, 0) AS neto_posterior,
                   COALESCE(m.entradas, 0) AS entradas, COALESCE(m.salidas, 0) AS salidas,
                   COALESCE(m.costo_salidas, 0) AS costo_salidas
            FROM inventario.productos p
            LEFT JOIN mov m ON m.producto_id = p.id
            WHERE p.tipo = 'bien' AND (m.producto_id IS NOT NULL OR p.stock_actual <> 0) {filtro}
            """,
            *valores,
        )
    dias = Decimal((hasta - desde).days + 1)

    def calcular(inicial: Decimal, final: Decimal, salidas: Decimal) -> tuple[Decimal, Decimal | None, Decimal | None]:
        promedio = (inicial + final) / 2
        if promedio <= 0:
            return promedio, None, None
        rotacion = salidas / promedio
        return promedio, rotacion.quantize(Decimal("0.01")), (dias / rotacion).quantize(Decimal("0.1")) if rotacion else None

    resultado = []
    for f in filas:
        final = f["stock_actual"] - f["neto_posterior"]
        inicial = final - f["neto_periodo"]
        promedio, rotacion, dias_inv = calcular(inicial, final, f["salidas"])
        resultado.append({
            "producto_id": f["producto_id"], "sku": f["sku"], "nombre": f["nombre"], "categoria": f["categoria"],
            "existencia_inicial": inicial, "entradas": f["entradas"], "salidas": f["salidas"],
            "existencia_final": final, "costo_salidas": _redondear(f["costo_salidas"]),
            "inventario_promedio": promedio.quantize(Decimal("0.0001")), "rotacion": rotacion,
            "dias_inventario": dias_inv,
        })
    resultado.sort(key=lambda f: (f["rotacion"] is None, -(f["rotacion"] or _CERO), f["nombre"]))
    t_inicial = sum((f["existencia_inicial"] for f in resultado), _CERO)
    t_final = sum((f["existencia_final"] for f in resultado), _CERO)
    t_salidas = sum((f["salidas"] for f in resultado), _CERO)
    promedio, rotacion, dias_inv = calcular(t_inicial, t_final, t_salidas)
    totales = {
        "producto_id": "total", "sku": "", "nombre": "Total", "categoria": None,
        "existencia_inicial": t_inicial, "entradas": sum((f["entradas"] for f in resultado), _CERO),
        "salidas": t_salidas, "existencia_final": t_final,
        "costo_salidas": sum((f["costo_salidas"] for f in resultado), _CERO),
        "inventario_promedio": promedio.quantize(Decimal("0.0001")), "rotacion": rotacion, "dias_inventario": dias_inv,
    }
    return {
        "desde": desde, "hasta": hasta, "filas": resultado, "totales": totales,
        "advertencias": ["La rotación es en unidades; sumar unidades de productos distintos es solo orientativo."]
        if len(resultado) > 1 else [],
    }


async def reporte_mas_vendidos(
    pool: asyncpg.Pool, *, desde: date, hasta: date, limite: int = 20, categoria: str | None = None
) -> dict[str, Any]:
    """Salidas por factura del Kardex (las que registra inventario_api cuando
    facturas_api envía el e-CF), por cantidad."""
    _rango(desde, hasta)
    valores: list[Any] = [_instante(desde), _instante(hasta + timedelta(days=1)), limite]
    filtro = ""
    if categoria:
        valores.append(categoria)
        filtro = f"AND p.categoria = ${len(valores)}"
    async with pool.acquire() as conn:
        filas = await conn.fetch(
            f"""
            SELECT p.id::text AS producto_id, p.sku, p.nombre, p.categoria,
                   sum(m.cantidad) AS cantidad, count(DISTINCT m.referencia_id) AS facturas,
                   round(sum(m.cantidad * COALESCE(m.costo_unitario, 0)), 2) AS costo,
                   sum(sum(m.cantidad)) OVER () AS total_cantidad,
                   sum(round(sum(m.cantidad * COALESCE(m.costo_unitario, 0)), 2)) OVER () AS total_costo
            FROM inventario.movimientos m
            JOIN inventario.productos p ON p.id = m.producto_id
            WHERE m.tipo = 'salida' AND m.referencia_tipo = 'factura'
              AND m.creado_en >= $1 AND m.creado_en < $2 {filtro}
            GROUP BY p.id
            ORDER BY cantidad DESC, p.nombre
            LIMIT $3
            """,
            *valores,
        )
    total_cantidad = filas[0]["total_cantidad"] if filas else _CERO
    total_costo = filas[0]["total_costo"] if filas else _CERO
    return {
        "desde": desde,
        "hasta": hasta,
        "filas": [
            {
                "posicion": i, "producto_id": f["producto_id"], "sku": f["sku"], "nombre": f["nombre"],
                "categoria": f["categoria"], "cantidad": f["cantidad"], "facturas": f["facturas"], "costo": f["costo"],
                "participacion": (f["cantidad"] * _CIEN / total_cantidad).quantize(Decimal("0.01")) if total_cantidad else _CERO,
            }
            for i, f in enumerate(filas, start=1)
        ],
        "total_cantidad": total_cantidad,
        "total_costo": total_costo,
        "advertencias": [],
    }


# --- Retenciones ---------------------------------------------------------

def _fila_retencion(clave: str, etiqueta: str, detalle: str | None, f: Any) -> dict[str, Any]:
    return {
        "clave": clave, "etiqueta": etiqueta, "detalle": detalle, "documentos": f["documentos"],
        "monto_base": f["monto_base"], "isr_retenido": f["isr_retenido"], "itbis_retenido": f["itbis_retenido"],
        "total": f["isr_retenido"] + f["itbis_retenido"],
    }


def _total_retenciones(filas: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "clave": "total", "etiqueta": "Total", "detalle": None,
        "documentos": sum(f["documentos"] for f in filas),
        **{k: sum((f[k] for f in filas), _CERO) for k in ("monto_base", "isr_retenido", "itbis_retenido", "total")},
    }


async def reporte_retenciones(pool: asyncpg.Pool, *, desde: date, hasta: date) -> dict[str, Any]:
    _rango(desde, hasta)
    isr_ecf = _monto_dgii("{Encabezado,Totales,TotalISRRetencion}")
    itbis_ecf = _monto_dgii("{Encabezado,Totales,TotalITBISRetenido}")
    async with pool.acquire() as conn:
        efectuadas = await conn.fetch(
            f"""
            SELECT r.rnc_cedula AS clave, max({_NOMBRE_PROVEEDOR}) AS nombre, count(*) AS documentos,
                   sum(r.monto_servicios + r.monto_bienes) AS monto_base,
                   sum(r.monto_retencion_renta) AS isr_retenido, sum(r.itbis_retenido) AS itbis_retenido
            FROM compras606.registros r
            {_JOINS_PROVEEDOR}
            WHERE r.fecha_pago >= $1 AND r.fecha_pago <= $2
              AND (r.itbis_retenido > 0 OR r.monto_retencion_renta > 0)
            GROUP BY 1
            """,
            _aaaammdd(desde),
            _aaaammdd(hasta),
        )
        por_tipo = await conn.fetch(
            """
            SELECT tipo_retencion_isr AS tipo, count(*) AS documentos,
                   sum(monto_servicios + monto_bienes) AS monto_base,
                   sum(monto_retencion_renta) AS isr_retenido, 0::numeric AS itbis_retenido
            FROM compras606.registros
            WHERE fecha_pago >= $1 AND fecha_pago <= $2 AND monto_retencion_renta > 0
            GROUP BY 1
            UNION ALL
            SELECT -1, count(*), sum(monto_servicios + monto_bienes), 0, sum(itbis_retenido)
            FROM compras606.registros
            WHERE fecha_pago >= $1 AND fecha_pago <= $2 AND itbis_retenido > 0
            HAVING count(*) > 0
            """,
            _aaaammdd(desde),
            _aaaammdd(hasta),
        )
        sufridas = await conn.fetch(
            f"""
            SELECT COALESCE({_RNC_COMPRADOR}, '') AS clave, max({_NOMBRE_COMPRADOR}) AS nombre, count(*) AS documentos,
                   sum(s.signo * {_monto_dgii("{Encabezado,Totales,MontoTotal}")}) AS monto_base,
                   sum(s.signo * {isr_ecf}) AS isr_retenido, sum(s.signo * {itbis_ecf}) AS itbis_retenido
            FROM facturacion.facturas f
            CROSS JOIN LATERAL (SELECT f.datos::jsonb AS d) x
            CROSS JOIN LATERAL (SELECT CASE WHEN f.tipo_ecf = 34 THEN -1 ELSE 1 END AS signo) s
            WHERE f.tipo_ecf = ANY($1::int[]) AND f.estado = 'aceptado'
              AND {_FECHA_EMISION} >= $2 AND {_FECHA_EMISION} <= $3
              AND ({isr_ecf} <> 0 OR {itbis_ecf} <> 0)
            GROUP BY 1
            """,
            list(formatos.TIPOS_VENTA_607),
            desde,
            hasta,
        )
    filas_efectuadas = sorted(
        (_fila_retencion(f["clave"], f["nombre"] or f["clave"], f["clave"], f) for f in efectuadas),
        key=lambda f: (-f["total"], f["etiqueta"]),
    )
    filas_tipo = []
    for f in sorted(por_tipo, key=lambda f: (f["tipo"] is None, f["tipo"] if f["tipo"] is not None else 99)):
        if f["tipo"] == -1:
            filas_tipo.append(_fila_retencion("itbis", "ITBIS retenido (IT-1)", None, f))
        else:
            retencion = precalculos.RETENCIONES_ISR.get(f["tipo"])
            etiqueta = f"ISR · {retencion.etiqueta}" if retencion else "ISR · sin tipo de retención"
            filas_tipo.append(_fila_retencion(f"isr{f['tipo'] or ''}", etiqueta, "IR-17", f))
    filas_sufridas = sorted(
        (_fila_retencion(f["clave"], f["nombre"] or f["clave"] or "Consumidor final", f["clave"] or None, f)
         for f in sufridas),
        key=lambda f: (-f["total"], f["etiqueta"]),
    )
    return {
        "desde": desde,
        "hasta": hasta,
        "efectuadas": filas_efectuadas,
        "por_tipo": filas_tipo,
        "totales_efectuadas": _total_retenciones(filas_efectuadas),
        "sufridas": filas_sufridas,
        "totales_sufridas": _total_retenciones(filas_sufridas),
        "advertencias": [],
    }
