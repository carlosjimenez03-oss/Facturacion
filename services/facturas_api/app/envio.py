"""Firma y envío de un e-CF a la DGII (vía `recepcion`) y lo que sigue a un
envío exitoso. Lo usan `POST /facturas/{id}/enviar` (app.main) y el envío por
lotes de lo pendiente de una contingencia (app.contingencia).

**Firma.** El XML firmado que se envía es el mismo que se guarda en
`xml_firmado`, del que salen el código de seguridad y el QR de la
representación impresa: facturas_api firma con el mismo `firmar_xml` que
usa `recepcion` y se lo manda con `firmado=true`, que lo envía tal cual. Así,
el XML que recibe la DGII (aunque sea 72 horas después, en contingencia) es
byte a byte el que se le entregó al comprador. Si ya hay una firma guardada
para el XML actual (`firmado_en`: la vista previa de POST /firmar o un envío
anterior que no llegó), se reutiliza; si no, se firma y se guarda ANTES de
enviar. Sin certificado en este servicio se cae al flujo anterior (envía el
XML sin firmar y `recepcion` lo firma), sin contingencia posible.

**Resultado del envío** (`ResultadoEnvio`): trackId; rechazo (la DGII o
`recepcion` respondieron con error: 4xx, 502 = la DGII rechazó, 500 =
configuración); o `sin_conexion` (no se llegó a la DGII: error de conexión o
timeout hacia `recepcion`, o `recepcion` responde 503/504 porque la DGII no
responde) -- solo esto último lleva la factura a contingencia.

**Tras un envío exitoso** (`aplicar_flujo_tras_envio`), best-effort porque
el e-CF ya llegó a la DGII: salida de inventario y registro en el 606 de
los tipos 41/43/47.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from libs.ecf_common.auth.signing import cargar_certificado_p12, firmar_xml
from libs.ecf_common.compras606 import fecha_dgii_a_aaaammdd, registrar_compra_606
from libs.ecf_common.xml_tools import nombre_archivo_ecf

from . import inventario_client, repository
from .config import FacturasApiSettings
from .inventario_client import InventarioApiError

logger = logging.getLogger(__name__)

# Respuestas de `recepcion` que significan "la DGII no responde" (ver
# services/recepcion/app/main.py): 503 cuando la DGII no contesta o da 5xx,
# 504 de un proxy intermedio. 502 es un rechazo de la DGII y 500 un error de
# configuración (certificado): no son contingencia.
ESTADOS_HTTP_SIN_CONEXION = frozenset({503, 504})

# Tipos de e-CF autoemitidos que también forman parte del Formato 606
# (Compras de Bienes y Servicios) -- ver docs/dgii/FORMATO_606.md.
TIPOS_COMPRAS_606 = frozenset({41, 43, 47})


class FirmaNoDisponibleError(Exception):
    """Este servicio no tiene un certificado con que firmar."""


@dataclass
class ResultadoEnvio:
    track_id: str | None = None
    cuerpo: dict[str, Any] | None = None
    error: str | None = None
    sin_conexion: bool = False
    status_code: int | None = None

    @property
    def exitoso(self) -> bool:
        return bool(self.track_id)


def tiene_firma_vigente(factura: dict[str, Any]) -> bool:
    """Si `xml_firmado` corresponde al XML actual: `firmado_en` solo lo
    ponen la firma (vista previa o envío) y se borra al regenerar el XML
    (repository.guardar_xml_generado). Las facturas anteriores a este campo
    no lo tienen y se firman de nuevo."""
    return bool(factura.get("xml_firmado") and factura.get("firmado_en"))


async def asegurar_firma(pool, factura: dict[str, Any], settings: FacturasApiSettings) -> dict[str, Any]:
    """Devuelve la factura con `xml_firmado`/`firmado_en` del XML que se
    va a enviar, firmándolo y guardándolo si hace falta. Lanza
    `FirmaNoDisponibleError` si no hay certificado."""
    if tiene_firma_vigente(factura):
        return factura
    try:
        credencial = cargar_certificado_p12(settings.certificado_path, settings.certificado_password)
        xml_firmado = firmar_xml(factura["xml_generado"].encode("utf-8"), credencial)
    except (FileNotFoundError, ValueError) as exc:
        raise FirmaNoDisponibleError(str(exc)) from exc
    return await repository.guardar_firma_envio(pool, uuid.UUID(str(factura["id"])), xml_firmado.decode("utf-8"))


async def enviar_a_recepcion(
    settings: FacturasApiSettings,
    factura: dict[str, Any],
    *,
    firmado: bool,
    timeout: float | None = None,
) -> ResultadoEnvio:
    """POST {recepcion}/ecf con el XML firmado (`firmado=True`) o sin firmar."""
    encf = factura["encf"]
    nombre_archivo = nombre_archivo_ecf(factura["rnc_emisor"], encf)
    contenido = (factura["xml_firmado"] if firmado else factura["xml_generado"]).encode("utf-8")
    datos = {"encf": encf, "firmado": "true"} if firmado else {"encf": encf}
    try:
        async with httpx.AsyncClient(timeout=timeout or settings.timeout_envio_segundos) as client:
            respuesta = await client.post(
                f"{settings.recepcion_service_url}/ecf",
                data=datos,
                files={"xml": (nombre_archivo, contenido, "text/xml")},
            )
    except httpx.HTTPError as exc:
        detalle = f"No se pudo contactar recepcion: {exc.__class__.__name__} {exc}".strip()
        return ResultadoEnvio(error=detalle, sin_conexion=True)
    if respuesta.status_code >= 400:
        return ResultadoEnvio(
            error=_detalle(respuesta),
            sin_conexion=respuesta.status_code in ESTADOS_HTTP_SIN_CONEXION,
            status_code=respuesta.status_code,
        )
    try:
        cuerpo = respuesta.json()
    except ValueError:
        return ResultadoEnvio(error=f"Respuesta inválida de recepcion: {respuesta.text[:200]}", status_code=respuesta.status_code)
    return ResultadoEnvio(
        track_id=cuerpo.get("trackId"),
        cuerpo=cuerpo,
        error=cuerpo.get("error") or cuerpo.get("mensaje"),
        status_code=respuesta.status_code,
    )


def _detalle(respuesta: httpx.Response) -> str:
    try:
        cuerpo = respuesta.json()
    except ValueError:
        return respuesta.text
    if isinstance(cuerpo, dict) and isinstance(cuerpo.get("detail"), str):
        return cuerpo["detail"]
    return respuesta.text


# --- Tras un envío exitoso ---------------------------------------------------------

def rnc_cedula_para_606(tipo_ecf: int, datos: dict, settings: FacturasApiSettings) -> str | None:
    """A quién le compramos, para el campo "RNC o Cédula" del 606 -- distinto
    según el tipo (ver docs/dgii/FORMATO_606.md, "RNC/Cédula a reportar por
    tipo"): tipo 41 (Compras) reporta al proveedor informal (campo
    `Comprador` de la factura, ya que `Emisor` siempre somos nosotros); 43
    (Gastos Menores) y 47 (Pagos al Exterior) no tienen una contraparte
    identificada en su XSD y, por regla explícita del PDF oficial, reportan
    el RNC del propio encabezado."""
    if tipo_ecf == 41:
        return (datos.get("Encabezado", {}).get("Comprador") or {}).get("RNCComprador")
    return settings.emisor_rnc


def separar_bienes_servicios(datos: dict) -> tuple[Decimal, Decimal]:
    """Suma DetallesItems.Item por IndicadorBienoServicio (1=Bien, 2=Servicio)
    -- el Formato 606 exige la proporción Bienes/Servicios por separado y
    ningún e-CF la trae ya calculada."""
    servicios = Decimal("0")
    bienes = Decimal("0")
    items = (datos.get("DetallesItems") or {}).get("Item") or []
    if isinstance(items, dict):
        items = [items]
    for item in items:
        try:
            monto = Decimal(str(item.get("MontoItem") or "0"))
        except InvalidOperation:
            continue
        if str(item.get("IndicadorBienoServicio")) == "2":
            servicios += monto
        else:
            bienes += monto
    return servicios, bienes


async def registrar_compra_606_autoemitida(pool, factura: dict, encf: str, settings: FacturasApiSettings) -> None:
    """Best-effort: la factura ya se envió exitosamente a la DGII (no se
    puede deshacer); un fallo aquí solo implica que ese renglón habrá que
    agregarlo a mano en el Formato 606, nunca debe afectar la respuesta."""
    try:
        datos = factura["datos"]
        fecha_emision = (datos.get("Encabezado", {}).get("Emisor") or {}).get("FechaEmision")
        fecha_comprobante = fecha_dgii_a_aaaammdd(fecha_emision) if fecha_emision else None
        if not fecha_comprobante:
            logger.warning(
                "Factura %s sin FechaEmision válida, no se registra en compras606", factura["id"]
            )
            return
        rnc_cedula = rnc_cedula_para_606(factura["tipo_ecf"], datos, settings)
        if not rnc_cedula:
            logger.warning(
                "Factura %s sin RNC/Cédula de contraparte, no se registra en compras606",
                factura["id"],
            )
            return
        ncf_modificado = (datos.get("InformacionReferencia") or {}).get("NCFModificado")
        try:
            itbis_facturado = Decimal(str((datos.get("Encabezado", {}).get("Totales") or {}).get("TotalITBIS") or "0"))
        except InvalidOperation:
            itbis_facturado = Decimal("0")
        servicios, bienes = separar_bienes_servicios(datos)
        await registrar_compra_606(
            pool,
            origen="auto_emitido",
            referencia_tipo="factura",
            referencia_id=str(factura["id"]),
            rnc_cedula=rnc_cedula,
            ncf=encf,
            ncf_modificado=ncf_modificado,
            fecha_comprobante=fecha_comprobante,
            monto_servicios=servicios,
            monto_bienes=bienes,
            itbis_facturado=itbis_facturado,
        )
    except Exception:
        logger.exception("No se pudo registrar la factura %s en compras606", factura.get("id"))


async def aplicar_flujo_tras_envio(pool, factura: dict[str, Any], settings: FacturasApiSettings) -> None:
    """Salida de inventario y 606 de una factura que la DGII ya recibió
    (trackId en mano). Nunca lanza: el e-CF ya se envió (documento fiscal
    irreversible), un fallo solo se deja registrado para resolverlo (la
    salida de inventario la reintenta la tarea de fondo)."""
    fid = uuid.UUID(str(factura["id"]))
    encf = factura.get("encf")
    items_inventario = factura.get("items_inventario") or []
    if items_inventario:
        try:
            await inventario_client.registrar_salida_por_factura(
                settings.inventario_api_url, str(fid), items_inventario, documento=encf
            )
            await repository.registrar_sincronizacion_inventario(pool, fid, sincronizado=True, error=None)
        except InventarioApiError as exc:
            logger.error("No se pudo registrar la salida de inventario de la factura %s: %s", fid, exc)
            await repository.registrar_sincronizacion_inventario(pool, fid, sincronizado=False, error=str(exc))
    if factura["tipo_ecf"] in TIPOS_COMPRAS_606:
        await registrar_compra_606_autoemitida(pool, factura, encf, settings)
