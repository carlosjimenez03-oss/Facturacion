"""e-CF recibidos de proveedores (bandeja de entrada de Compras).

`services/receptor_recepcion` guarda aquí (best-effort, igual que
`libs.ecf_common.db.registrar_envio`) el XML de cada e-CF que otro
contribuyente nos emite, y `services/erp_api` lo lee para la bandeja de
entrada: listado, detalle con sus líneas, aprobación/rechazo comercial y
vínculo con el Formato 606 y con una compra de inventario.

La tabla `recepcion.ecf_recibidos` la crea `erp_api` al arrancar
(`services/erp_api/app/migraciones.py`, `DDL_COMPRAS`; copia en
`db/init/010_compras.sql`): `receptor_recepcion` no tiene migraciones, así
que si arranca antes que `erp_api` contra una base nueva la tabla todavía
no existe y la inserción simplemente se omite con un aviso en el log -- la
recepción del e-CF (el ARECF que exige la DGII) nunca falla por esto.

Las funciones de extracción son puras (sin base de datos) para que
`erp_api` las reutilice al mostrar el detalle o al crear el renglón del 606
de un e-CF que no lo tenía.
"""
from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

import asyncpg
from lxml import etree

from libs.ecf_common.compras606 import fecha_dgii_a_aaaammdd

logger = logging.getLogger(__name__)

_CERO = Decimal("0")


def _decimal(valor: str | None) -> Decimal:
    try:
        return Decimal((valor or "0").strip() or "0")
    except InvalidOperation:
        return _CERO


def fecha_dgii_a_date(valor: str | None) -> date | None:
    """"DD-MM-YYYY" (formato de fecha del XSD de e-CF) -> date; None si falta
    o no es una fecha real (31-02-2026)."""
    aaaammdd = fecha_dgii_a_aaaammdd(valor.strip()) if valor else None
    if not aaaammdd or len(aaaammdd) != 8:
        return None
    try:
        return date(int(aaaammdd[:4]), int(aaaammdd[4:6]), int(aaaammdd[6:]))
    except ValueError:
        return None


def parsear_xml(xml: bytes | str) -> etree._Element:
    """Parser seguro para XML que viene de terceros: sin entidades ni red."""
    if isinstance(xml, str):
        xml = xml.encode("utf-8")
    parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False)
    return etree.fromstring(xml, parser=parser)


def _texto(root: etree._Element, ruta: str) -> str | None:
    valor = root.findtext(ruta)
    valor = valor.strip() if valor else None
    return valor or None


def campos_ecf_recibido(root: etree._Element) -> dict[str, Any]:
    """Los datos del encabezado que la bandeja muestra y filtra."""
    tipo = _texto(root, ".//IdDoc/TipoeCF")
    encf = _texto(root, ".//eNCF") or ""
    if not (tipo and tipo.isdigit()) and len(encf) >= 3 and encf[1:3].isdigit():
        tipo = encf[1:3]
    return {
        "rnc_emisor": _texto(root, ".//RNCEmisor") or "",
        "razon_social_emisor": _texto(root, ".//Emisor/RazonSocialEmisor"),
        "rnc_comprador": _texto(root, ".//RNCComprador"),
        "encf": encf,
        "tipo_ecf": int(tipo) if tipo and tipo.isdigit() else None,
        "fecha_emision": fecha_dgii_a_date(_texto(root, ".//FechaEmision")),
        "fecha_limite_pago": fecha_dgii_a_date(_texto(root, ".//IdDoc/FechaLimitePago")),
        "monto_total": _decimal(_texto(root, ".//Totales/MontoTotal")),
        "itbis_total": _decimal(_texto(root, ".//Totales/TotalITBIS")),
    }


def items_ecf(root: etree._Element) -> list[dict[str, Any]]:
    """Las líneas (DetallesItems/Item) del e-CF, para el detalle de la bandeja."""
    items = []
    for posicion, item in enumerate(root.findall(".//DetallesItems/Item"), start=1):
        linea = _texto(item, "NumeroLinea")
        items.append(
            {
                "linea": int(linea) if linea and linea.isdigit() else posicion,
                "descripcion": _texto(item, "NombreItem") or _texto(item, "DescripcionItem") or "",
                "cantidad": _decimal(_texto(item, "CantidadItem")),
                "precio_unitario": _decimal(_texto(item, "PrecioUnitarioItem")),
                "monto": _decimal(_texto(item, "MontoItem")),
                # IndicadorBienoServicio: 1 = Bien, 2 = Servicio.
                "bien_o_servicio": "servicio" if _texto(item, "IndicadorBienoServicio") == "2" else "bien",
                # IndicadorFacturacion: 1 = ITBIS 18 %, 2 = 16 %, 3 = 0 %, 4 = exento.
                "indicador_facturacion": _texto(item, "IndicadorFacturacion"),
            }
        )
    return items


def separar_bienes_servicios(root: etree._Element) -> tuple[Decimal, Decimal]:
    """Suma DetallesItems/Item por IndicadorBienoServicio (1=Bien, 2=Servicio)
    -- el Formato 606 exige la proporción Bienes/Servicios por separado y
    ningún e-CF la trae ya calculada. Devuelve (servicios, bienes)."""
    servicios = _CERO
    bienes = _CERO
    for item in root.findall(".//DetallesItems/Item"):
        try:
            monto = Decimal(item.findtext("MontoItem") or "0")
        except InvalidOperation:
            continue
        if (item.findtext("IndicadorBienoServicio") or "") == "2":
            servicios += monto
        else:
            bienes += monto
    return servicios, bienes


def datos_606_desde_ecf(root: etree._Element) -> dict[str, Any] | None:
    """Los campos del 606 que trae un e-CF recibido (los argumentos de
    `registrar_compra_606` sin origen/referencia), o None si no tiene una
    FechaEmision válida."""
    fecha_emision = root.findtext(".//FechaEmision")
    fecha_comprobante = fecha_dgii_a_aaaammdd(fecha_emision) if fecha_emision else None
    if not fecha_comprobante:
        return None
    servicios, bienes = separar_bienes_servicios(root)
    return {
        "rnc_cedula": root.findtext(".//RNCEmisor") or "",
        "ncf": root.findtext(".//eNCF") or "",
        "ncf_modificado": root.findtext(".//InformacionReferencia/NCFModificado"),
        "fecha_comprobante": fecha_comprobante,
        "monto_servicios": servicios,
        "monto_bienes": bienes,
        "itbis_facturado": _decimal(root.findtext(".//TotalITBIS")),
    }


async def registrar_ecf_recibido(pool: asyncpg.Pool | None, xml_bytes: bytes, root: etree._Element) -> None:
    """Guarda el e-CF recibido para la bandeja de entrada.

    Best-effort (nunca lanza): el ARECF ya se construyó y la recepción no
    debe fallar por esto. Idempotente sobre (rnc_emisor, encf): un reenvío
    del mismo e-CF no duplica la fila ni pisa la decisión comercial ya
    tomada; solo completa el vínculo con el 606 si faltaba. Llamar DESPUÉS
    de `registrar_compra_606`, para enlazar su renglón (`compra606_id`).
    """
    if pool is None:
        return
    try:
        campos = campos_ecf_recibido(root)
        if not campos["rnc_emisor"] or not campos["encf"]:
            return
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO recepcion.ecf_recibidos
                    (rnc_emisor, razon_social_emisor, rnc_comprador, encf, tipo_ecf, fecha_emision,
                     fecha_limite_pago, monto_total, itbis_total, xml, compra606_id)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10,
                        (SELECT id FROM compras606.registros WHERE rnc_cedula = $1 AND ncf = $4))
                ON CONFLICT (rnc_emisor, encf) DO UPDATE
                SET compra606_id = COALESCE(recepcion.ecf_recibidos.compra606_id, EXCLUDED.compra606_id)
                """,
                campos["rnc_emisor"],
                campos["razon_social_emisor"],
                campos["rnc_comprador"],
                campos["encf"],
                campos["tipo_ecf"],
                campos["fecha_emision"],
                campos["fecha_limite_pago"],
                campos["monto_total"],
                campos["itbis_total"],
                xml_bytes.decode("utf-8", errors="replace"),
            )
    except (asyncpg.UndefinedTableError, asyncpg.InvalidSchemaNameError):
        logger.warning(
            "recepcion.ecf_recibidos no existe todavía (la crea erp-api al arrancar): "
            "el e-CF no queda en la bandeja de entrada"
        )
    except Exception:
        logger.exception("No se pudo guardar el e-CF recibido en la bandeja de entrada")
