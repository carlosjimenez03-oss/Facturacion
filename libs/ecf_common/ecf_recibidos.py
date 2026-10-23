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

La recepción tampoco ESPERA por la base: la escritura no toma candados de
filas que otro proceso tenga (`ON CONFLICT DO NOTHING` + `FOR UPDATE SKIP
LOCKED`) y corre con `lock_timeout`/`statement_timeout` cortos. Si la fila
del e-CF está ocupada (p. ej. erp-api registrando la aprobación comercial),
el reenvío simplemente no la actualiza.

La firma digital (XML-DSig) del e-CF se verifica al guardarlo
(`verificar_firma_ecf`) y queda en `firma_valida`/`firma_detalle`; nunca
impide devolver el ARECF. El primer e-CF guardado para (RNC emisor, e-NCF)
se conserva, salvo que su firma no fuera válida y llegue otra copia con
firma válida: entonces la copia válida lo reemplaza (si aún está pendiente
de aprobación comercial).

Las funciones de extracción son puras (sin base de datos) para que
`erp_api` las reutilice al mostrar el detalle o al crear el renglón del 606
de un e-CF que no lo tenía.
"""
from __future__ import annotations

import logging
import re
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

import asyncpg
from cryptography.x509.oid import NameOID
from lxml import etree

from libs.ecf_common.auth.xml_signature import FirmaInvalidaError, rnc_del_certificado, verificar_firma
from libs.ecf_common.compras606 import fecha_dgii_a_aaaammdd, periodo_de_fecha

logger = logging.getLogger(__name__)

_CERO = Decimal("0")
_CENTAVO = Decimal("0.01")

# IndicadorFacturacion de cada línea: 1 = ITBIS 18 %, 2 = 16 %, 3 = 0 %, 4 = exento.
_TASA_ITBIS = {"1": Decimal("0.18"), "2": Decimal("0.16"), "3": _CERO, "4": _CERO}
# Código de la tabla de impuestos adicionales del e-CF que corresponde a la
# propina legal (10 %, Ley 54-32) -- mismo criterio que el 607
# (`services/erp_api/app/formatos.py`).
_CODIGO_PROPINA_LEGAL = "001"

_REGEX_DECLARACION = re.compile(rb"""^\s*<\?xml[^>]*encoding\s*=\s*["']([A-Za-z0-9._-]+)["']""")


def _decimal(valor: str | None) -> Decimal:
    try:
        return Decimal((valor or "0").strip() or "0")
    except InvalidOperation:
        return _CERO


def _redondear(valor: Decimal) -> Decimal:
    return valor.quantize(_CENTAVO, rounding=ROUND_HALF_UP)


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
    """Parser seguro para XML que viene de terceros: sin entidades ni red.

    Un `str` (el XML guardado en la base, ya decodificado) se lee como UTF-8
    aunque su declaración diga otra codificación (ISO-8859-1, UTF-16...)."""
    if isinstance(xml, str):
        parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False, encoding="utf-8")
        return etree.fromstring(xml.encode("utf-8"), parser=parser)
    parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False)
    return etree.fromstring(xml, parser=parser)


def texto_xml(xml_bytes: bytes, root: etree._Element | None = None) -> str:
    """El XML recibido como texto para guardarlo tal cual: decodificado con
    la codificación de su declaración (la que detectó el parser), sin
    caracteres NUL (Postgres no los admite en `text` y XML 1.0 tampoco)."""
    codificacion = root.getroottree().docinfo.encoding if root is not None else None
    if not codificacion:
        encontrada = _REGEX_DECLARACION.match(xml_bytes)
        codificacion = encontrada.group(1).decode("ascii") if encontrada else "utf-8"
    try:
        texto = xml_bytes.decode(codificacion)
    except (LookupError, UnicodeDecodeError):
        texto = xml_bytes.decode("utf-8", errors="replace")
    return texto.replace("\x00", "")


def bytes_xml(texto: str) -> tuple[bytes, str]:
    """(contenido, codificación) para descargar el XML guardado con la misma
    codificación que declara (si no se puede, UTF-8)."""
    encontrada = _REGEX_DECLARACION.match(texto.encode("utf-8", errors="replace"))
    codificacion = encontrada.group(1).decode("ascii") if encontrada else "utf-8"
    if codificacion.lower().replace("_", "-") not in ("utf-8", "utf8"):
        try:
            return texto.encode(codificacion), codificacion
        except (LookupError, UnicodeEncodeError):
            pass
    return texto.encode("utf-8"), "utf-8"


def _texto(root: etree._Element, ruta: str) -> str | None:
    valor = root.findtext(ruta)
    valor = valor.strip() if valor else None
    return valor or None


def campos_ecf_recibido(root: etree._Element) -> dict[str, Any]:
    """Los datos del encabezado que la bandeja muestra y filtra.
    `monto_total` es Totales/MontoTotal: lo que el proveedor cobra (con
    ITBIS, selectivo, otros impuestos y propina); es el total de la cuenta
    por pagar."""
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


def impuestos_adicionales(root: etree._Element) -> tuple[Decimal, Decimal, Decimal]:
    """(selectivo al consumo, otros impuestos/tasas, propina legal) de
    Totales/ImpuestosAdicionales -- las casillas 20, 21 y 22 del 606. Mismo
    criterio que el 607: el selectivo es el específico + el ad valorem; los
    `OtrosImpuestosAdicionales` del código 001 son la propina legal y los
    demás, "otros impuestos". Si el e-CF solo trae `MontoImpuestoAdicional`
    (sin la tabla), va a "otros impuestos"."""
    selectivo = otros = propina = _CERO
    for impuesto in root.findall(".//Totales/ImpuestosAdicionales/ImpuestoAdicional"):
        selectivo += _decimal(_texto(impuesto, "MontoImpuestoSelectivoConsumoEspecifico"))
        selectivo += _decimal(_texto(impuesto, "MontoImpuestoSelectivoConsumoAdvalorem"))
        otro = _decimal(_texto(impuesto, "OtrosImpuestosAdicionales"))
        if (_texto(impuesto, "TipoImpuesto") or "") == _CODIGO_PROPINA_LEGAL:
            propina += otro
        else:
            otros += otro
    if selectivo == otros == propina == _CERO:
        otros = _decimal(_texto(root, ".//Totales/MontoImpuestoAdicional"))
    return selectivo, otros, propina


def separar_bienes_servicios(root: etree._Element) -> tuple[Decimal, Decimal]:
    """Monto facturado (sin ITBIS ni otros impuestos) en servicios y en
    bienes, casillas 8 y 9 del 606 -- el formato exige la proporción por
    separado y ningún e-CF la trae ya calculada. Devuelve (servicios, bienes).

    - La proporción sale de DetallesItems/Item por IndicadorBienoServicio
      (1 = Bien, 2 = Servicio). Con `IndicadorMontoGravado` = 1 los
      `MontoItem` traen el ITBIS incluido: se le quita a cada línea según su
      IndicadorFacturacion.
    - El monto, de los totales: MontoTotal − TotalITBIS − selectivo − otros
      impuestos − propina (así cubre descuentos/recargos globales y el 606
      suma exactamente el MontoTotal del e-CF). Si el e-CF no trae
      MontoTotal, la suma de las líneas.
    """
    con_itbis = _texto(root, ".//IdDoc/IndicadorMontoGravado") == "1"
    servicios = _CERO
    bienes = _CERO
    for item in root.findall(".//DetallesItems/Item"):
        try:
            monto = Decimal(item.findtext("MontoItem") or "0")
        except InvalidOperation:
            continue
        if con_itbis:
            monto = monto / (1 + _TASA_ITBIS.get(_texto(item, "IndicadorFacturacion") or "", _CERO))
        if (item.findtext("IndicadorBienoServicio") or "") == "2":
            servicios += monto
        else:
            bienes += monto
    lineas = servicios + bienes
    monto_total = _decimal(_texto(root, ".//Totales/MontoTotal"))
    base = monto_total - _decimal(_texto(root, ".//Totales/TotalITBIS")) - sum(impuestos_adicionales(root), _CERO)
    if monto_total <= 0 or base < 0:
        return _redondear(servicios), _redondear(bienes)
    if lineas <= 0:
        return _CERO, _redondear(base)
    servicios_base = _redondear(base * servicios / lineas)
    return servicios_base, _redondear(base) - servicios_base


def datos_606_desde_ecf(root: etree._Element) -> dict[str, Any] | None:
    """Los campos del 606 que trae un e-CF recibido (los argumentos de
    `registrar_compra_606` sin origen/referencia), o None si no tiene una
    FechaEmision válida."""
    fecha_emision = root.findtext(".//FechaEmision")
    fecha_comprobante = fecha_dgii_a_aaaammdd(fecha_emision.strip()) if fecha_emision else None
    if not fecha_comprobante:
        return None
    servicios, bienes = separar_bienes_servicios(root)
    selectivo, otros, propina = impuestos_adicionales(root)
    return {
        "rnc_cedula": _texto(root, ".//RNCEmisor") or "",
        "ncf": _texto(root, ".//eNCF") or "",
        "ncf_modificado": _texto(root, ".//InformacionReferencia/NCFModificado"),
        "fecha_comprobante": fecha_comprobante,
        "monto_servicios": servicios,
        "monto_bienes": bienes,
        "itbis_facturado": _decimal(_texto(root, ".//Totales/TotalITBIS")),
        "impuesto_selectivo_consumo": _redondear(selectivo),
        "otros_impuestos": _redondear(otros),
        "monto_propina_legal": _redondear(propina),
    }


def verificar_firma_ecf(xml_bytes: bytes, rnc_emisor: str) -> tuple[bool, str]:
    """(firma_valida, detalle) del e-CF recibido. Nunca lanza.

    Válida = la firma XML-DSig corresponde al contenido y al certificado que
    el propio XML embebe (`libs.ecf_common.auth.xml_signature`) Y el campo SN
    (serialNumber) de ese certificado es el RNCEmisor -- el estándar de
    firmado exige que el SN sea el RNC/cédula del propietario. Igual que en
    los demás receptores, NO se valida la cadena de confianza hasta la CA de
    INDOTEL (su bundle no está en el repositorio): un certificado
    autofirmado con el SN correcto pasaría. Un certificado a nombre de una
    persona física (SN = cédula del delegado) queda como no válido, con el
    motivo en el detalle, para que el usuario lo revise.
    """
    try:
        certificado = verificar_firma(xml_bytes)
    except FirmaInvalidaError as exc:
        return False, str(exc)
    except Exception as exc:  # cualquier otro fallo de verificación
        return False, f"No se pudo verificar la firma: {exc}"
    try:
        sn = rnc_del_certificado(certificado)
    except FirmaInvalidaError as exc:
        return False, f"Firma íntegra, pero {exc}"
    nombre = ", ".join(str(a.value) for a in certificado.subject.get_attributes_for_oid(NameOID.COMMON_NAME)) or sn
    if re.sub(r"\D", "", sn) == re.sub(r"\D", "", rnc_emisor or "") and rnc_emisor:
        return True, f"Firmado por {nombre} (SN {sn})"
    return False, f"Firma íntegra, pero el certificado ({nombre}, SN {sn}) no es del RNC emisor {rnc_emisor}"


# 606 sin enlazar del mismo (RNC, e-NCF): un renglón ya vinculado a OTRO
# e-CF (p. ej. desde la bandeja) no se toca -- violaría el índice único.
_SUB_606_LIBRE = """
    (SELECT r.id FROM compras606.registros r
     WHERE r.rnc_cedula = $2 AND r.ncf = $3
       AND NOT EXISTS (SELECT 1 FROM recepcion.ecf_recibidos e WHERE e.compra606_id = r.id))
"""


async def _enlazar_606(conn: asyncpg.Connection, ecf_id: Any, rnc: str, encf: str) -> None:
    try:
        async with conn.transaction():  # savepoint: un choque no tumba la fila del e-CF
            await conn.execute(
                f"""
                UPDATE recepcion.ecf_recibidos SET compra606_id = {_SUB_606_LIBRE}, actualizado_en = now()
                WHERE id = $1 AND compra606_id IS NULL AND {_SUB_606_LIBRE} IS NOT NULL
                """,
                ecf_id,
                rnc,
                encf,
            )
    except asyncpg.UniqueViolationError:
        logger.info("El renglón del 606 de %s %s ya quedó vinculado a otro e-CF recibido", rnc, encf)


async def _corregir_606(conn: asyncpg.Connection, compra606_id: Any, root: etree._Element, encf: str) -> None:
    """Al reemplazar una copia con firma inválida por la válida, corrige los
    montos de su renglón del 606 -- solo si vino de receptor_recepcion, no
    tiene pagos vigentes y el 606/IT-1 de su período no están presentados."""
    datos = datos_606_desde_ecf(root)
    if compra606_id is None or datos is None:
        return
    try:
        async with conn.transaction():
            resultado = await conn.execute(
                """
                UPDATE compras606.registros r
                SET periodo = $2, fecha_comprobante = $3, ncf_modificado = $4, monto_servicios = $5,
                    monto_bienes = $6, itbis_facturado = $7, impuesto_selectivo_consumo = $8,
                    otros_impuestos = $9, monto_propina_legal = $10, actualizado_en = now()
                WHERE r.id = $1 AND r.origen = 'receptor_recepcion'
                  AND NOT EXISTS (SELECT 1 FROM compras.pagos p WHERE p.registro606_id = r.id AND NOT p.anulado)
                  AND NOT EXISTS (SELECT 1 FROM impuestos.declaraciones_presentadas d
                                  WHERE d.formulario IN ('606', 'it1') AND d.periodo IN (r.periodo, $2))
                """,
                compra606_id,
                periodo_de_fecha(datos["fecha_comprobante"]),
                datos["fecha_comprobante"],
                datos["ncf_modificado"],
                datos["monto_servicios"],
                datos["monto_bienes"],
                datos["itbis_facturado"],
                datos["impuesto_selectivo_consumo"],
                datos["otros_impuestos"],
                datos["monto_propina_legal"],
            )
    except Exception:
        resultado = None
        logger.exception("No se pudo corregir el 606 del e-CF %s con la copia de firma válida", encf)
    if resultado != "UPDATE 1":
        logger.warning(
            "El 606 del e-CF %s conserva los montos de la copia con firma inválida (tiene pagos, ya se "
            "presentó o no vino del receptor): corríjalo a mano",
            encf,
        )


async def ecf_rechazado(pool: asyncpg.Pool | None, rnc_emisor: str, encf: str) -> bool:
    """True si ese e-CF ya fue rechazado comercialmente: erp-api quitó su
    renglón del 606 (no es una compra válida) y un reenvío del proveedor no
    debe volver a registrarlo. Best-effort: sin base o sin la tabla, False."""
    if pool is None:
        return False
    try:
        async with pool.acquire() as conn:
            return bool(
                await conn.fetchval(
                    "SELECT 1 FROM recepcion.ecf_recibidos"
                    " WHERE rnc_emisor = $1 AND encf = $2 AND estado_aprobacion = 'rechazado'",
                    rnc_emisor,
                    encf,
                )
            )
    except Exception:
        return False


async def registrar_ecf_recibido(pool: asyncpg.Pool | None, xml_bytes: bytes, root: etree._Element) -> None:
    """Guarda el e-CF recibido para la bandeja de entrada.

    Best-effort (nunca lanza) y sin esperar candados (ver el docstring del
    módulo): el ARECF ya se construyó y la recepción no debe fallar ni
    demorarse por esto. Idempotente sobre (rnc_emisor, encf): un reenvío del
    mismo e-CF no duplica la fila ni pisa la decisión comercial ya tomada;
    solo completa el vínculo con el 606 si faltaba, o reemplaza una copia
    con firma inválida por una con firma válida. Llamar DESPUÉS de
    `registrar_compra_606`, para enlazar su renglón (`compra606_id`).
    """
    if pool is None:
        return
    try:
        campos = campos_ecf_recibido(root)
        if not campos["rnc_emisor"] or not campos["encf"]:
            return
        firma_valida, firma_detalle = verificar_firma_ecf(xml_bytes, campos["rnc_emisor"])
        if not firma_valida:
            logger.warning("e-CF %s %s con firma no válida: %s", campos["rnc_emisor"], campos["encf"], firma_detalle)
        xml = texto_xml(xml_bytes, root)
        valores = (
            campos["razon_social_emisor"],
            campos["rnc_comprador"],
            campos["tipo_ecf"],
            campos["fecha_emision"],
            campos["fecha_limite_pago"],
            campos["monto_total"],
            campos["itbis_total"],
            xml,
            firma_valida,
            firma_detalle,
        )
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("SET LOCAL lock_timeout = '2s'")
                await conn.execute("SET LOCAL statement_timeout = '10s'")
                ecf_id = await conn.fetchval(
                    """
                    INSERT INTO recepcion.ecf_recibidos
                        (rnc_emisor, encf, razon_social_emisor, rnc_comprador, tipo_ecf, fecha_emision,
                         fecha_limite_pago, monto_total, itbis_total, xml, firma_valida, firma_detalle)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                    ON CONFLICT (rnc_emisor, encf) DO NOTHING
                    RETURNING id
                    """,
                    campos["rnc_emisor"],
                    campos["encf"],
                    *valores,
                )
                if ecf_id is not None:
                    await _enlazar_606(conn, ecf_id, campos["rnc_emisor"], campos["encf"])
                    return
                existente = await conn.fetchrow(
                    """
                    SELECT id, compra606_id, firma_valida, xml, estado_aprobacion FROM recepcion.ecf_recibidos
                    WHERE rnc_emisor = $1 AND encf = $2
                    FOR UPDATE SKIP LOCKED
                    """,
                    campos["rnc_emisor"],
                    campos["encf"],
                )
                if existente is None:
                    logger.info(
                        "e-CF %s %s reenviado mientras su fila está ocupada: no se actualiza",
                        campos["rnc_emisor"],
                        campos["encf"],
                    )
                    return
                if firma_valida and existente["firma_valida"] is not True and existente["xml"] != xml:
                    if existente["estado_aprobacion"] != "pendiente":
                        logger.warning(
                            "e-CF %s %s: llegó una copia con firma válida que difiere de la guardada (firma no "
                            "válida), pero ya tiene decisión comercial: no se reemplaza",
                            campos["rnc_emisor"],
                            campos["encf"],
                        )
                        return
                    logger.warning(
                        "e-CF %s %s: la copia guardada (firma no válida) se reemplaza por la de firma válida",
                        campos["rnc_emisor"],
                        campos["encf"],
                    )
                    await conn.execute(
                        """
                        UPDATE recepcion.ecf_recibidos
                        SET razon_social_emisor = $2, rnc_comprador = $3, tipo_ecf = $4, fecha_emision = $5,
                            fecha_limite_pago = $6, monto_total = $7, itbis_total = $8, xml = $9,
                            firma_valida = $10, firma_detalle = $11, recibido_en = now(), error_aprobacion = NULL,
                            actualizado_en = now()
                        WHERE id = $1
                        """,
                        existente["id"],
                        *valores,
                    )
                    await _corregir_606(conn, existente["compra606_id"], root, campos["encf"])
                if existente["compra606_id"] is None:
                    await _enlazar_606(conn, existente["id"], campos["rnc_emisor"], campos["encf"])
    except (asyncpg.UndefinedTableError, asyncpg.InvalidSchemaNameError) as exc:
        logger.warning(
            "Falta una tabla para la bandeja de entrada (%s; las crea erp-api al arrancar o db/init): "
            "el e-CF no queda en la bandeja",
            exc,
        )
    except Exception:
        logger.exception("No se pudo guardar el e-CF recibido en la bandeja de entrada")
