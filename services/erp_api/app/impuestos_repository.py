"""Acceso a Postgres del módulo de impuestos: formatos 607/608, pre-cálculos
IT-1/IR-17/IR-2 y el registro de declaraciones presentadas.

Lee (nunca escribe) `facturacion.facturas` (e-CF de venta),
`facturacion.envios_dgii` (anulaciones de e-NCF de `anulacion_encf`),
`compras606.registros` (Formato 606) e `inventario.productos` (Kardex). Lo
único que se guarda vive en el esquema `impuestos` (ver migraciones.py). Los
cálculos están en formatos.py y precalculos.py (funciones puras).
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

import asyncpg

from libs.ecf_common.compras606 import validar_periodo

from . import formatos, precalculos
from .repository import _FECHA_EMISION, _ZONA_RD, DatosInvalidosError, hoy


class AnulacionNoEncontradaError(Exception):
    pass


class ConflictoImpuestosError(Exception):
    """NCF ya registrado, o el período ya está marcado como presentado --
    main/impuestos.py lo traduce a 409 con el mensaje tal cual."""


_ESTADO_ACEPTADO = "aceptado"
_ESTADO_PENDIENTE_DGII = "enviado"

# `codigo` que anulacion_encf guarda en facturacion.envios_dgii cuando la
# DGII acepta la anulación de rangos (respuesta `{rnc, codigo, nombre,
# mensajes}` de /api/operaciones/anularrango, docs/dgii/SERVICIOS_DGII.md).
# Un envío sin `error` pero con otro código (o sin código) no se da por
# anulado.
_CODIGO_ANECF_ACEPTADO = "1"

# Nota de crédito en el 606 (e-NCF E34, NCF B04 o tipo 04 del formato de 19
# posiciones): resta en compras e ITBIS adelantado.
_SIGNO_606 = (
    "(CASE WHEN ncf ~* '^(E34|B04)' OR (length(ncf) = 19 AND substr(ncf, 10, 2) = '04') THEN -1 ELSE 1 END)"
)


# --- Validación de períodos ---------------------------------------------------------

def validar_periodo_mensual(periodo: str) -> str:
    if not validar_periodo(periodo or ""):
        raise DatosInvalidosError(f"Período inválido: {periodo!r} (formato AAAAMM, desde 201805)")
    return periodo


def validar_anio(anio: str) -> str:
    if not re.fullmatch(r"20[0-9]{2}", anio or ""):
        raise DatosInvalidosError(f"Año inválido: {anio!r} (formato AAAA)")
    return anio


def validar_periodo_formulario(formulario: str, periodo: str) -> str:
    return validar_anio(periodo) if formulario == "ir2" else validar_periodo_mensual(periodo)


def _rango_mes(periodo: str) -> tuple[date, date]:
    desde = date(int(periodo[:4]), int(periodo[4:6]), 1)
    hasta = date(desde.year + 1, 1, 1) if desde.month == 12 else date(desde.year, desde.month + 1, 1)
    return desde, hasta


def _rango_anio(anio: str) -> tuple[date, date]:
    return date(int(anio), 1, 1), date(int(anio) + 1, 1, 1)


# --- Lecturas de facturacion.facturas ---------------------------------------------------------

async def _facturas_venta(
    conn: asyncpg.Connection, desde: date, hasta: date, estado: str = _ESTADO_ACEPTADO
) -> list[dict[str, Any]]:
    """e-CF de venta (31, 32, 33, 34, 44, 45, 46) en `estado` con
    FechaEmision en [desde, hasta)."""
    filas = await conn.fetch(
        f"""
        SELECT f.id, f.tipo_ecf, f.encf, f.datos::text AS datos
        FROM facturacion.facturas f
        CROSS JOIN LATERAL (SELECT f.datos::jsonb AS d) x
        WHERE f.tipo_ecf = ANY($1::int[])
          AND f.estado = $2
          AND {_FECHA_EMISION} >= $3 AND {_FECHA_EMISION} < $4
        ORDER BY {_FECHA_EMISION}, f.encf
        """,
        list(formatos.TIPOS_VENTA_607),
        estado,
        desde,
        hasta,
    )
    return [
        {"id": str(f["id"]), "tipo_ecf": f["tipo_ecf"], "encf": f["encf"], "datos": json.loads(f["datos"])}
        for f in filas
    ]


async def _cantidad_pendientes_dgii(conn: asyncpg.Connection, desde: date, hasta: date) -> int:
    return await conn.fetchval(
        f"""
        SELECT count(*)
        FROM facturacion.facturas f
        CROSS JOIN LATERAL (SELECT f.datos::jsonb AS d) x
        WHERE f.tipo_ecf = ANY($1::int[])
          AND f.estado = $2
          AND {_FECHA_EMISION} >= $3 AND {_FECHA_EMISION} < $4
        """,
        list(formatos.TIPOS_VENTA_607),
        _ESTADO_PENDIENTE_DGII,
        desde,
        hasta,
    ) or 0


def _advertencia_pendientes(cantidad: int) -> list[str]:
    if not cantidad:
        return []
    return [
        f"{cantidad} e-CF de venta del período siguen 'enviado' (sin respuesta de aceptación de la DGII) y no se "
        "incluyeron: consulte su estado antes de declarar."
    ]


# --- Formato 607 ---------------------------------------------------------

async def formato_607(
    pool: asyncpg.Pool, periodo: str, rnc_emisor: str, *, emisor_100_electronico: bool = True
) -> dict[str, Any]:
    validar_periodo_mensual(periodo)
    desde, hasta = _rango_mes(periodo)
    async with pool.acquire() as conn:
        facturas = await _facturas_venta(conn, desde, hasta)
        pendientes = await _cantidad_pendientes_dgii(conn, desde, hasta)
    filas, totales, advertencias = formatos.armar_607(facturas)
    encabezado: list[str] = []
    if facturas:
        encabezado.append(formatos.advertencia_ecf_607(emisor_100_electronico))
    if not rnc_emisor:
        encabezado.append("El RNC del emisor no está configurado (EMISOR_RNC): no se puede generar el archivo.")
    return {
        "periodo": periodo,
        "rnc_emisor": rnc_emisor,
        "filas": filas,
        "totales": totales,
        "advertencias": encabezado + _advertencia_pendientes(pendientes) + advertencias,
    }


def _exigir_rnc(rnc_emisor: str) -> None:
    if not re.fullmatch(r"[0-9]{9}|[0-9]{11}", rnc_emisor or ""):
        raise DatosInvalidosError(
            "El RNC del emisor no está configurado (variable EMISOR_RNC de erp-api): sin él no se puede "
            "generar el archivo"
        )


async def archivo_607(
    pool: asyncpg.Pool, periodo: str, rnc_emisor: str, *, emisor_100_electronico: bool = True
) -> tuple[str, bytes]:
    """(nombre, contenido) del TXT. Solo lleva comprobantes que la DGII
    acepta en el 607 (no e-CF, ver formatos.py); 422 si no hay ninguno o si
    alguno es inválido."""
    _exigir_rnc(rnc_emisor)
    datos = await formato_607(pool, periodo, rnc_emisor, emisor_100_electronico=emisor_100_electronico)
    reportables = [fila for fila in datos["filas"] if not formatos.es_encf(fila["ncf"])]
    if not reportables:
        raise DatosInvalidosError(formatos.sin_registros_607(periodo, emisor_100_electronico))
    errores = [
        f"{fila['ncf']}: {error}" for fila in reportables for error in formatos.errores_fila_607(fila)
    ]
    if errores:
        raise DatosInvalidosError(f"El 607 tiene {len(errores)} error(es): " + "; ".join(errores[:10]))
    contenido = formatos.construir_archivo_607(rnc=rnc_emisor, periodo=periodo, filas=reportables)
    return formatos.nombre_archivo("607", rnc_emisor, periodo), contenido


# --- Formato 608 ---------------------------------------------------------

def _fecha_anulacion(respuesta: dict[str, Any], creado_en: datetime) -> date:
    """Fecha de la anulación: FechaHoraAnulacioneNCF del ANECF (dd-MM-AAAA
    HH:mm:ss) si se guardó; si no, la fecha (hora de RD) del envío."""
    valor = str(respuesta.get("fecha_hora_anulacion") or "")[:10]
    fecha = formatos.fecha_dgii(valor)
    return fecha if fecha is not None else creado_en.astimezone(_ZONA_RD).date()


async def _anulaciones_encf(
    conn: asyncpg.Connection, desde: date, hasta: date
) -> tuple[list[dict[str, Any]], list[str]]:
    """e-NCF anulados con ANECF (envíos de `anulacion_encf` sin error y con
    el código de aceptación de la DGII) cuya fecha de anulación cae en
    [desde, hasta). Los rangos se despliegan uno por e-NCF."""
    envios = await conn.fetch(
        """
        SELECT id, codigo, respuesta::text AS respuesta, creado_en
        FROM facturacion.envios_dgii
        WHERE modulo = 'anulacion_encf' AND error IS NULL
          AND creado_en >= $1 AND creado_en < $2
        ORDER BY creado_en, id
        """,
        datetime.combine(desde - timedelta(days=7), datetime.min.time(), _ZONA_RD),
        datetime.combine(hasta + timedelta(days=7), datetime.min.time(), _ZONA_RD),
    )
    filas: list[dict[str, Any]] = []
    advertencias: list[str] = []
    sin_rangos = 0
    no_aceptadas = 0
    truncado = False
    for envio in envios:
        respuesta = json.loads(envio["respuesta"]) if envio["respuesta"] else {}
        fecha = _fecha_anulacion(respuesta, envio["creado_en"])
        if not (desde <= fecha < hasta):
            continue
        if (envio["codigo"] or "").strip() != _CODIGO_ANECF_ACEPTADO:
            no_aceptadas += 1
            continue
        rangos = respuesta.get("rangos")
        if not rangos:
            sin_rangos += 1
            continue
        mensajes = " ".join(str(m) for m in respuesta.get("mensajes") or [])
        if re.search(r"excepci[oó]n|no fue posible", mensajes, re.IGNORECASE):
            advertencias.append(
                f"La anulación del {fecha:%d/%m/%Y} fue parcial según la DGII ({mensajes.strip()}): verifique qué "
                "secuencias quedaron anuladas."
            )
        for rango in rangos:
            inicio, fin = str(rango.get("desde") or ""), str(rango.get("hasta") or "")
            encfs = formatos.expandir_rango(inicio, fin, max(formatos.MAXIMO_ENCF_ANULADOS - len(filas), 0))
            truncado = truncado or len(encfs) < formatos.cantidad_rango(inicio, fin)
            filas.extend(
                {
                    "id": None,
                    "origen": "anulacion_encf",
                    "ncf": encf,
                    "fecha_comprobante": fecha,
                    "tipo_anulacion": formatos.TIPO_ANULACION_ANECF,
                    "tipo_anulacion_label": formatos.TIPOS_ANULACION[formatos.TIPO_ANULACION_ANECF],
                }
                for encf in encfs
            )
    if no_aceptadas:
        advertencias.append(
            f"{no_aceptadas} envío(s) de anulación de e-NCF del período no tienen el código de aceptación de la "
            f"DGII ({_CODIGO_ANECF_ACEPTADO}) y no se listan: verifique su resultado antes de darlos por anulados."
        )
    if sin_rangos:
        advertencias.append(
            f"{sin_rangos} anulación(es) de e-NCF del período se registraron sin el detalle de rangos (envíos "
            "anteriores a esta versión): no se pueden listar."
        )
    if truncado:
        advertencias.append(
            f"Se listan solo los primeros {formatos.MAXIMO_ENCF_ANULADOS} e-NCF anulados del período."
        )
    return filas, advertencias


def _fila_manual(fila: asyncpg.Record) -> dict[str, Any]:
    return {
        "id": str(fila["id"]),
        "origen": "manual",
        "ncf": fila["ncf"],
        "fecha_comprobante": fila["fecha_comprobante"],
        "tipo_anulacion": fila["tipo_anulacion"],
        "tipo_anulacion_label": formatos.TIPOS_ANULACION.get(fila["tipo_anulacion"], ""),
    }


async def formato_608(
    pool: asyncpg.Pool, periodo: str, rnc_emisor: str, *, emisor_100_electronico: bool = True
) -> dict[str, Any]:
    validar_periodo_mensual(periodo)
    desde, hasta = _rango_mes(periodo)
    async with pool.acquire() as conn:
        encf, advertencias = await _anulaciones_encf(conn, desde, hasta)
        manuales = await conn.fetch(
            """
            SELECT id, ncf, fecha_comprobante, tipo_anulacion
            FROM impuestos.anulaciones_608
            WHERE fecha_comprobante >= $1 AND fecha_comprobante < $2
            """,
            desde,
            hasta,
        )
    filas = sorted(encf + [_fila_manual(f) for f in manuales], key=lambda f: (f["fecha_comprobante"], f["ncf"]))
    encabezado: list[str] = []
    if encf:
        encabezado.append(formatos.advertencia_ecf_608(emisor_100_electronico))
    if not rnc_emisor:
        encabezado.append("El RNC del emisor no está configurado (EMISOR_RNC): no se puede generar el archivo.")
    return {"periodo": periodo, "rnc_emisor": rnc_emisor, "filas": filas, "advertencias": encabezado + advertencias}


async def archivo_608(
    pool: asyncpg.Pool, periodo: str, rnc_emisor: str, *, emisor_100_electronico: bool = True
) -> tuple[str, bytes]:
    """(nombre, contenido) del TXT: solo las anulaciones manuales (NCF serie
    B); 422 si no hay ninguna."""
    _exigir_rnc(rnc_emisor)
    datos = await formato_608(pool, periodo, rnc_emisor, emisor_100_electronico=emisor_100_electronico)
    reportables = [fila for fila in datos["filas"] if fila["origen"] == "manual"]
    if not reportables:
        raise DatosInvalidosError(formatos.sin_registros_608(periodo, emisor_100_electronico))
    errores = [f["ncf"] for f in reportables if not formatos.validar_ncf_608(f["ncf"])]
    if errores:
        raise DatosInvalidosError("NCF inválidos para el 608: " + ", ".join(errores[:10]))
    contenido = formatos.construir_archivo_608(rnc=rnc_emisor, periodo=periodo, filas=reportables)
    return formatos.nombre_archivo("608", rnc_emisor, periodo), contenido


async def _bloquear_declaracion(conn: asyncpg.Connection, formulario: str, periodo: str) -> None:
    """Candado de transacción por (formulario, período): serializa las altas
    y bajas del 608 con la marca de "presentada", para que nadie agregue una
    anulación mientras otro marca el período como presentado. Se libera al
    terminar la transacción (debe llamarse dentro de una)."""
    await conn.execute(
        "SELECT pg_advisory_xact_lock(hashtext('impuestos.declaracion'), hashtext($1 || ':' || $2))",
        formulario,
        periodo,
    )


async def _exigir_no_presentada(conn: asyncpg.Connection, formulario: str, periodo: str) -> None:
    """Toma el candado del período y falla (409) si ya está presentado.
    Dentro de una transacción."""
    await _bloquear_declaracion(conn, formulario, periodo)
    presentada = await conn.fetchval(
        "SELECT presentada_en FROM impuestos.declaraciones_presentadas WHERE formulario = $1 AND periodo = $2",
        formulario,
        periodo,
    )
    if presentada is not None:
        raise ConflictoImpuestosError(
            f"El {formulario} de {periodo} ya está marcado como presentado ({presentada:%d/%m/%Y}): desmárquelo "
            "para modificarlo"
        )


async def agregar_anulacion_608(
    pool: asyncpg.Pool, *, ncf: str, fecha_comprobante: date, tipo_anulacion: str
) -> dict[str, Any]:
    ncf = re.sub(r"\s", "", ncf).upper()
    tipo = tipo_anulacion.strip().zfill(2)
    if tipo not in formatos.TIPOS_ANULACION:
        raise DatosInvalidosError("Tipo de anulación inválido (01-10)")
    if formatos.es_encf(ncf):
        raise DatosInvalidosError(
            "Los e-NCF no se reportan en el 608: anúlelos con la Anulación de e-NCF (ANECF) si no se enviaron, "
            "o con una nota de crédito electrónica si ya se enviaron"
        )
    if not formatos.validar_ncf_608(ncf):
        raise DatosInvalidosError(
            "NCF inválido para el 608: debe tener 11 posiciones (B + tipo + 8 dígitos) o 19 (formato anterior)"
        )
    periodo = f"{fecha_comprobante:%Y%m}"
    async with pool.acquire() as conn:
        async with conn.transaction():
            await _exigir_no_presentada(conn, "608", periodo)
            try:
                fila = await conn.fetchrow(
                    """
                    INSERT INTO impuestos.anulaciones_608 (ncf, fecha_comprobante, tipo_anulacion)
                    VALUES ($1, $2, $3)
                    RETURNING id, ncf, fecha_comprobante, tipo_anulacion
                    """,
                    ncf,
                    fecha_comprobante,
                    tipo,
                )
            except asyncpg.UniqueViolationError as exc:
                raise ConflictoImpuestosError(f"El NCF {ncf} ya está registrado como anulado") from exc
    return _fila_manual(fila)


async def eliminar_anulacion_608(pool: asyncpg.Pool, anulacion_id: uuid.UUID) -> None:
    """Solo existen como filas borrables las anulaciones manuales (las de
    e-NCF no tienen id). 409 si el 608 de su período ya se presentó."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            fecha = await conn.fetchval(
                "SELECT fecha_comprobante FROM impuestos.anulaciones_608 WHERE id = $1 FOR UPDATE", anulacion_id
            )
            if fecha is None:
                raise AnulacionNoEncontradaError(str(anulacion_id))
            await _exigir_no_presentada(conn, "608", f"{fecha:%Y%m}")
            await conn.execute("DELETE FROM impuestos.anulaciones_608 WHERE id = $1", anulacion_id)


# --- Pre-cálculos ---------------------------------------------------------

async def _leer_ajustes(conn: asyncpg.Connection, formulario: str, periodo: str) -> dict[str, Decimal]:
    filas = await conn.fetch(
        "SELECT clave, valor FROM impuestos.ajustes_precalculo WHERE formulario = $1 AND periodo = $2",
        formulario,
        periodo,
    )
    return {f["clave"]: f["valor"] for f in filas}


async def _datos_it1(
    conn: asyncpg.Connection, periodo: str
) -> tuple[precalculos.VentasItbis, precalculos.Compras606, Decimal]:
    desde, hasta = _rango_mes(periodo)
    ventas = precalculos.acumular_ventas(await _facturas_venta(conn, desde, hasta))
    compras = await conn.fetchrow(
        f"""
        SELECT COALESCE(sum({_SIGNO_606} * itbis_facturado), 0) AS itbis_facturado,
               COALESCE(sum({_SIGNO_606} * itbis_costo), 0) AS itbis_costo,
               COALESCE(sum({_SIGNO_606} * itbis_proporcionalidad), 0) AS itbis_proporcionalidad
        FROM compras606.registros
        WHERE periodo = $1
        """,
        periodo,
    )
    retenido = await conn.fetchval(
        "SELECT COALESCE(sum(itbis_retenido), 0) FROM compras606.registros WHERE fecha_pago LIKE $1 || '%'",
        periodo,
    )
    return ventas, precalculos.Compras606(**dict(compras)), retenido


async def _precalculo_it1(conn: asyncpg.Connection, periodo: str) -> dict[str, Any]:
    ventas, compras, retenido = await _datos_it1(conn, periodo)
    ajustes = await _leer_ajustes(conn, "it1", periodo)
    # Saldo a favor sugerido: el nuevo saldo a favor del mes anterior,
    # calculado con sus datos y sus ajustes guardados (sin arrastrar el de
    # meses previos si no se guardó -- es lo conservador). Se calcula aunque
    # haya un valor manual, para mostrarlo como sugerencia y poder volver a él.
    anterior = precalculos.periodo_anterior(periodo)
    saldo_sugerido = Decimal("0")
    if validar_periodo(anterior):
        v_ant, c_ant, r_ant = await _datos_it1(conn, anterior)
        _, saldo_sugerido = precalculos.calcular_it1(
            periodo=anterior,
            ventas=v_ant,
            compras=c_ant,
            itbis_retenido_proveedores=r_ant,
            ajustes=await _leer_ajustes(conn, "it1", anterior),
            saldo_favor_sugerido=Decimal("0"),
            hoy=hoy(),
        )
    resultado, _ = precalculos.calcular_it1(
        periodo=periodo,
        ventas=ventas,
        compras=compras,
        itbis_retenido_proveedores=retenido,
        ajustes=ajustes,
        saldo_favor_sugerido=saldo_sugerido,
        hoy=hoy(),
    )
    desde, hasta = _rango_mes(periodo)
    resultado["advertencias"] = _advertencia_pendientes(
        await _cantidad_pendientes_dgii(conn, desde, hasta)
    ) + resultado["advertencias"]
    return resultado


async def _precalculo_ir17(conn: asyncpg.Connection, periodo: str) -> dict[str, Any]:
    grupos = await conn.fetch(
        """
        SELECT tipo_retencion_isr,
               sum(monto_retencion_renta) AS monto_retencion_renta,
               sum(monto_servicios + monto_bienes) AS monto_pagado
        FROM compras606.registros
        WHERE fecha_pago LIKE $1 || '%' AND monto_retencion_renta > 0
        GROUP BY tipo_retencion_isr
        """,
        periodo,
    )
    return precalculos.calcular_ir17(
        periodo=periodo,
        retenciones=[dict(g) for g in grupos],
        ajustes=await _leer_ajustes(conn, "ir17", periodo),
        hoy=hoy(),
    )


async def _precalculo_ir2(conn: asyncpg.Connection, anio: str) -> dict[str, Any]:
    desde, hasta = _rango_anio(anio)
    ventas = precalculos.acumular_ventas(await _facturas_venta(conn, desde, hasta))
    ingresos = ventas.g18 + ventas.g16 + ventas.exportaciones + ventas.exentas
    compras = await conn.fetch(
        f"""
        SELECT tipo_bienes_servicios,
               sum({_SIGNO_606} * (monto_servicios + monto_bienes + itbis_costo)) AS monto
        FROM compras606.registros
        WHERE periodo LIKE $1 || '%'
        GROUP BY tipo_bienes_servicios
        """,
        anio,
    )
    valuacion = await conn.fetchval(
        "SELECT COALESCE(sum(GREATEST(stock_actual, 0) * costo_promedio), 0) FROM inventario.productos"
    )
    resultado = precalculos.calcular_ir2(
        anio=anio,
        ingresos=ingresos,
        compras_por_tipo={c["tipo_bienes_servicios"]: c["monto"] for c in compras},
        valuacion_inventario=precalculos.redondear(Decimal(valuacion)),
        ajustes=await _leer_ajustes(conn, "ir2", anio),
        hoy=hoy(),
    )
    resultado["advertencias"] = _advertencia_pendientes(
        await _cantidad_pendientes_dgii(conn, desde, hasta)
    ) + resultado["advertencias"]
    return resultado


_PRECALCULOS = {"it1": _precalculo_it1, "ir17": _precalculo_ir17, "ir2": _precalculo_ir2}


async def precalculo(pool: asyncpg.Pool, formulario: str, periodo: str) -> dict[str, Any]:
    validar_periodo_formulario(formulario, periodo)
    async with pool.acquire() as conn:
        return await _PRECALCULOS[formulario](conn, periodo)


def _parsear_ajustes(formulario: str, valores: dict[str, str | None]) -> dict[str, Decimal | None]:
    desconocidas = sorted(set(valores) - set(precalculos.AJUSTES[formulario]))
    if desconocidas:
        raise DatosInvalidosError(
            f"Claves de ajuste desconocidas para {formulario}: {', '.join(desconocidas)} "
            f"(válidas: {', '.join(precalculos.AJUSTES[formulario])})"
        )
    resultado: dict[str, Decimal | None] = {}
    for clave, texto in valores.items():
        texto = (texto or "").strip().replace(",", "")
        if not texto:
            resultado[clave] = None
            continue
        try:
            valor = Decimal(texto)
        except InvalidOperation as exc:
            raise DatosInvalidosError(f"Valor inválido para {clave}: {texto!r}") from exc
        if not valor.is_finite() or abs(valor) >= Decimal("1e16"):
            raise DatosInvalidosError(f"Valor inválido para {clave}: {texto!r}")
        resultado[clave] = precalculos.redondear(valor)
    return resultado


async def guardar_ajustes(
    pool: asyncpg.Pool, formulario: str, periodo: str, valores: dict[str, str | None]
) -> dict[str, Any]:
    """Guarda (o, con valor vacío, borra) los valores manuales y devuelve el
    pre-cálculo recalculado."""
    validar_periodo_formulario(formulario, periodo)
    ajustes = _parsear_ajustes(formulario, valores)
    async with pool.acquire() as conn:
        async with conn.transaction():
            for clave, valor in ajustes.items():
                if valor is None:
                    await conn.execute(
                        "DELETE FROM impuestos.ajustes_precalculo WHERE formulario = $1 AND periodo = $2 AND clave = $3",
                        formulario,
                        periodo,
                        clave,
                    )
                else:
                    await conn.execute(
                        """
                        INSERT INTO impuestos.ajustes_precalculo (formulario, periodo, clave, valor)
                        VALUES ($1, $2, $3, $4)
                        ON CONFLICT (formulario, periodo, clave)
                        DO UPDATE SET valor = EXCLUDED.valor, actualizado_en = now()
                        """,
                        formulario,
                        periodo,
                        clave,
                        valor,
                    )
        return await _PRECALCULOS[formulario](conn, periodo)


# --- Declaraciones ---------------------------------------------------------

def _declaracion(
    formulario: str, periodo: str, marca: dict[str, Any] | None, hoy_: date, emisor_100_electronico: bool
) -> dict[str, Any]:
    fecha_vence = precalculos.vence(formulario, periodo)
    aplica = not (emisor_100_electronico and formulario in precalculos.FORMATOS_SOLO_SERIE_B)
    return {
        "formulario": formulario,
        "periodo": periodo,
        "vence": fecha_vence,
        "estado": precalculos.estado_declaracion(fecha_vence, marca is not None, hoy_, aplica=aplica),
        "presentada_en": marca["presentada_en"] if marca else None,
        "monto": marca["monto"] if marca else None,
        "referencia": marca["referencia"] if marca else None,
    }


_ORDEN_FORMULARIOS = ("ir17", "606", "607", "608", "it1", "ir2")


async def listar_declaraciones(
    pool: asyncpg.Pool, anio: str, *, emisor_100_electronico: bool = True
) -> list[dict[str, Any]]:
    """Las obligaciones de los 12 períodos mensuales del año (606, 607, 608,
    IT-1, IR-17) y el IR-2 del ejercicio, ordenadas por vencimiento. Con
    `emisor_100_electronico`, el 607 y el 608 sin marcar salen "no_aplica"."""
    validar_anio(anio)
    async with pool.acquire() as conn:
        marcas = await conn.fetch(
            """
            SELECT formulario, periodo, presentada_en, monto, referencia
            FROM impuestos.declaraciones_presentadas
            WHERE periodo LIKE $1 || '%'
            """,
            anio,
        )
    por_clave = {(m["formulario"], m["periodo"]): dict(m) for m in marcas}
    hoy_ = hoy()
    declaraciones = [
        _declaracion(
            formulario, f"{anio}{mes:02d}", por_clave.get((formulario, f"{anio}{mes:02d}")), hoy_,
            emisor_100_electronico,
        )
        for mes in range(1, 13)
        for formulario in precalculos.FORMULARIOS_MENSUALES
    ]
    declaraciones.append(_declaracion("ir2", anio, por_clave.get(("ir2", anio)), hoy_, emisor_100_electronico))
    return sorted(declaraciones, key=lambda d: (d["vence"], _ORDEN_FORMULARIOS.index(d["formulario"])))


async def marcar_presentada(
    pool: asyncpg.Pool,
    formulario: str,
    periodo: str,
    *,
    monto: Decimal | None,
    referencia: str | None,
    presentada_en: date | None,
    emisor_100_electronico: bool = True,
) -> dict[str, Any]:
    validar_periodo_formulario(formulario, periodo)
    monto = precalculos.redondear(monto) if monto is not None else None
    async with pool.acquire() as conn, conn.transaction():
        await _bloquear_declaracion(conn, formulario, periodo)
        fila = await conn.fetchrow(
            """
            INSERT INTO impuestos.declaraciones_presentadas (formulario, periodo, presentada_en, monto, referencia)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (formulario, periodo)
            DO UPDATE SET presentada_en = EXCLUDED.presentada_en, monto = EXCLUDED.monto,
                          referencia = EXCLUDED.referencia
            RETURNING presentada_en, monto, referencia
            """,
            formulario,
            periodo,
            presentada_en or hoy(),
            monto,
            referencia or None,
        )
    return _declaracion(formulario, periodo, dict(fila), hoy(), emisor_100_electronico)


async def desmarcar_presentada(
    pool: asyncpg.Pool, formulario: str, periodo: str, *, emisor_100_electronico: bool = True
) -> dict[str, Any]:
    """Quita la marca (idempotente) y devuelve la declaración con su estado recalculado."""
    validar_periodo_formulario(formulario, periodo)
    async with pool.acquire() as conn, conn.transaction():
        await _bloquear_declaracion(conn, formulario, periodo)
        await conn.execute(
            "DELETE FROM impuestos.declaraciones_presentadas WHERE formulario = $1 AND periodo = $2",
            formulario,
            periodo,
        )
    return _declaracion(formulario, periodo, None, hoy(), emisor_100_electronico)
