"""Formato 606 (Compras de Bienes y Servicios, Norma General 07-2018/05-2019).

Catálogos, patrones de validación y el writer best-effort compartido por
`services/facturas_api` (comprobantes autoemitidos tipo 41/43/47) y
`services/receptor_recepcion` (e-CF recibidos de proveedores) para poblar
`compras606.registros` -- ver docs/dgii/FORMATO_606.md para el formato
completo del archivo de texto y el detalle de cada regla.

Los patrones de NCF/periodo/fecha están derivados literalmente de las
constantes `strRegexNcf`/`strRegexPeriodo`/`strRegexFecha` de la plantilla
oficial de Excel de la DGII (módulo `modConstantesGlobales`), y las reglas
de longitud de `clsFormato606Validator`/`clsValidator` -- no inventadas.
"""
from __future__ import annotations

import logging
import re
from decimal import Decimal

import asyncpg

logger = logging.getLogger(__name__)

TIPOS_BIENES_SERVICIOS: frozenset[int] = frozenset(range(1, 12))
TIPOS_RETENCION_ISR: frozenset[int] = frozenset(range(1, 10))
FORMAS_PAGO: frozenset[int] = frozenset(range(1, 8))

PERIODO_MINIMO = 201805

_REGEX_PERIODO = re.compile(r"^(200[7-9]|20[1-9][0-9])(0[1-9]|1[0-2])$")
_REGEX_FECHA = re.compile(
    r"^(19[5-9][0-9]|20[0-9][0-9])"
    r"(((01|03|05|0[7-8]|10|12)(0[1-9]|[1-2][0-9]|3[0-1]))"
    r"|((04|06|09|11)(0[1-9]|[1-2][0-9]|30))"
    r"|(02(0[1-9]|[1-2][0-9])))$"
)
# 19 posiciones (formato antiguo pre e-CF): (A|Q|P) + 8 dígitos + tipo + 8 dígitos.
_REGEX_NCF_19 = re.compile(r"^[AQP][0-9]{8}((0[1-4])|(1[1-5]))[0-9]{8}$", re.IGNORECASE)
# 11 posiciones (NCF de comprobantes en papel, aún vigentes junto al e-CF).
_REGEX_NCF_11 = re.compile(r"^B((0[1-4])|(1[1-5])|17)[0-9]{8}$", re.IGNORECASE)
# 13 posiciones (e-NCF).
_REGEX_NCF_13 = re.compile(r"^E((3[1-4])|(4[1-5])|47)[0-9]{10}$", re.IGNORECASE)


def tipo_identificacion(rnc_cedula: str) -> int:
    """1 = RNC (9 dígitos), 2 = Cédula (11 dígitos) -- mismo criterio (por
    longitud) que usa la plantilla oficial de la DGII para clasificar el
    campo "Tipo Id" automáticamente."""
    return 2 if len(rnc_cedula) > 9 else 1


def validar_rnc_cedula(valor: str) -> bool:
    """Solo longitud + numérico -- la plantilla oficial NO valida el dígito
    verificador en este flujo, así que este módulo tampoco lo exige."""
    return valor.isdigit() and len(valor) in (9, 11)


def validar_periodo(periodo: str) -> bool:
    return bool(_REGEX_PERIODO.match(periodo)) and periodo.isdigit() and int(periodo) >= PERIODO_MINIMO


def validar_fecha(fecha: str) -> bool:
    return len(fecha) == 8 and fecha.isdigit() and bool(_REGEX_FECHA.match(fecha))


def validar_ncf(ncf: str) -> bool:
    if not ncf:
        return False
    if ncf[:1].upper() == "E":
        return len(ncf) == 13 and bool(_REGEX_NCF_13.match(ncf))
    if len(ncf) not in (11, 19):
        return False
    return bool(_REGEX_NCF_11.match(ncf) or _REGEX_NCF_19.match(ncf))


def periodo_de_fecha(fecha_aaaammdd: str) -> str:
    return fecha_aaaammdd[:6]


def fecha_dgii_a_aaaammdd(fecha_dgii: str) -> str | None:
    """Convierte "DD-MM-YYYY" (formato de fecha en el XSD de e-CF, ver
    xml_builder.py) a "AAAAMMDD" (formato que exige el Formato 606). Usado
    tanto por `facturas_api` (comprobantes autoemitidos) como por
    `receptor_recepcion` (e-CF recibidos de proveedores)."""
    partes = fecha_dgii.split("-")
    if len(partes) != 3 or not all(parte.isdigit() for parte in partes):
        return None
    dia, mes, anio = partes
    return f"{anio}{mes.zfill(2)}{dia.zfill(2)}"


async def registrar_compra_606(
    pool: asyncpg.Pool | None,
    *,
    origen: str,
    referencia_tipo: str | None,
    referencia_id: str | None,
    rnc_cedula: str,
    ncf: str,
    ncf_modificado: str | None,
    fecha_comprobante: str,
    monto_servicios: Decimal,
    monto_bienes: Decimal,
    itbis_facturado: Decimal,
    impuesto_selectivo_consumo: Decimal = Decimal("0"),
    otros_impuestos: Decimal = Decimal("0"),
    monto_propina_legal: Decimal = Decimal("0"),
) -> None:
    """Registra un renglón pendiente de clasificar en compras606.registros,
    a partir de los datos que sí trae un e-CF (propio o de un proveedor) --
    los campos que ningún e-CF trae (Tipo de Bienes, Forma de Pago,
    retenciones) se completan luego desde `services/formato_606`. El
    selectivo, otros impuestos y la propina legal (casillas 20-22) salen de
    Totales/ImpuestosAdicionales del e-CF (`ecf_recibidos.datos_606_desde_ecf`).

    Best-effort (nunca lanza), igual que `libs.ecf_common.db.registrar_envio`:
    un fallo aquí no debe interrumpir la recepción de un e-CF ni el envío de
    una factura, que ya sucedieron. Idempotente vía `ON CONFLICT DO NOTHING`
    sobre (rnc_cedula, ncf) -- un reintento de red no duplica el renglón.
    """
    if pool is None:
        return
    if not rnc_cedula or not ncf or not fecha_comprobante:
        logger.warning(
            "registrar_compra_606: datos incompletos, no se registra (rnc=%r ncf=%r fecha=%r)",
            rnc_cedula,
            ncf,
            fecha_comprobante,
        )
        return
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO compras606.registros
                    (periodo, origen, referencia_tipo, referencia_id, rnc_cedula, tipo_id,
                     ncf, ncf_modificado, fecha_comprobante, monto_servicios, monto_bienes, itbis_facturado,
                     impuesto_selectivo_consumo, otros_impuestos, monto_propina_legal)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
                ON CONFLICT DO NOTHING
                """,
                periodo_de_fecha(fecha_comprobante),
                origen,
                referencia_tipo,
                referencia_id,
                rnc_cedula,
                tipo_identificacion(rnc_cedula),
                ncf,
                ncf_modificado,
                fecha_comprobante,
                monto_servicios,
                monto_bienes,
                itbis_facturado,
                impuesto_selectivo_consumo,
                otros_impuestos,
                monto_propina_legal,
            )
    except Exception:
        logger.exception(
            "No se pudo registrar el renglón de compras606 (rnc=%s ncf=%s)", rnc_cedula, ncf
        )
