"""Esquemas de la API de facturas.

`datos` se valida a nivel de forma (debe ser un objeto JSON) pero NO se
valida campo por campo aquí -- la validación autoritativa de estructura,
campos requeridos, patrones y enumeraciones la hace `xml_builder` contra el
XSD oficial del `tipo_ecf` (ver POST /facturas/{id}/generar-xml). Intentar
replicar en Pydantic las ~150+ reglas de cada uno de los 10 XSD sería
enorme y quedaría desincronizado de la fuente de verdad (el propio XSD).
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from .xml_builder import TIPOS_SOPORTADOS


class ItemInventario(BaseModel):
    """Vincula una línea de la factura (por NumeroLinea) con un producto del
    catálogo de inventario -- a propósito NO vive dentro de `datos` (que
    refleja 1:1 el XSD del e-CF, ver xml_builder.py): esto es metadata propia
    de facturas_api/inventario_api, ajena a la DGII."""

    numero_linea: str
    producto_id: UUID
    cantidad: Decimal = Field(..., gt=0)
    # Almacén de donde sale la mercancía (None = el principal) y lote (None =
    # inventario_api lo toma por FEFO si el producto maneja lotes).
    almacen_id: UUID | None = None
    lote_id: UUID | None = None


class FacturaCrear(BaseModel):
    tipo_ecf: int = Field(..., description=f"Tipo de e-CF: {sorted(TIPOS_SOPORTADOS)}")
    encf: str | None = Field(None, description="e-NCF asignado (rango autorizado por la DGII)")
    datos: dict[str, Any] = Field(..., description="Estructura del e-CF (Encabezado, DetallesItems, ...)")
    items_inventario: list[ItemInventario] | None = Field(
        None, description="Vínculo de cada línea con un producto del catálogo (ver ItemInventario)"
    )
    # Vendedor (configuracion.vendedores de erp_api). Igual que
    # items_inventario: metadata de la aplicación, fuera de `datos`.
    vendedor_id: UUID | None = None


class FacturaActualizar(BaseModel):
    encf: str | None = None
    datos: dict[str, Any]
    items_inventario: list[ItemInventario] | None = None
    vendedor_id: UUID | None = None


class FacturaResumen(BaseModel):
    id: UUID
    tipo_ecf: int
    ambiente: str
    rnc_emisor: str
    encf: str | None
    estado: str
    track_id: str | None = None
    dgii_estado: str | None = None
    dgii_codigo: str | None = None
    error: str | None = None
    inventario_sincronizado: bool = False
    inventario_error: str | None = None
    # Firma del XML que se envía (y que lleva la representación impresa) y
    # modo contingencia (ver app.contingencia): `emitida_en_contingencia` se
    # conserva tras enviarse (la RI sigue llevando la leyenda).
    firmado_en: datetime | None = None
    emitida_en_contingencia: bool = False
    contingencia_en: datetime | None = None
    lote_id: str | None = None
    creado_en: datetime
    actualizado_en: datetime


class FacturaDetalle(FacturaResumen):
    intentos_envio: int = 0
    ultimo_intento_envio: datetime | None = None
    datos: dict[str, Any]
    items_inventario: list[ItemInventario] | None = None
    vendedor_id: UUID | None = None
    xml_generado: str | None = None
    xml_firmado: str | None = None
    dgii_respuesta: dict[str, Any] | None = None


class GenerarXmlResponse(BaseModel):
    id: UUID
    estado: str
    xml_generado: str | None = None
    errores: list[str] = []


class EnviarResponse(BaseModel):
    """`estado = 'contingencia'` (con `contingencia = true` y `mensaje`): la
    DGII no respondió; el e-CF quedó firmado y se enviará solo."""

    id: UUID
    estado: str
    track_id: str | None = None
    error: str | None = None
    contingencia: bool = False
    mensaje: str | None = None
    firmado_en: datetime | None = None


class LoteContingencia(BaseModel):
    """Lote de envío de lo pendiente de una contingencia (A-0001...)."""

    id: str
    documentos: int
    enviados: int
    aceptados: int
    rechazados: int
    pendientes: int
    estado: str  # en_cola | enviando | interrumpido | completado
    ultimo_error: str | None = None
    creado_en: datetime
    iniciado_en: datetime | None = None
    completado_en: datetime | None = None
    actualizado_en: datetime


class EstadoContingencia(BaseModel):
    activa: bool
    desde: datetime | None = None
    errores_consecutivos: int = 0
    ultimo_intento: datetime | None = None
    ultimo_error: str | None = None
    ultimo_exito: datetime | None = None
    pendientes: int = 0
    monto_pendiente: Decimal = Decimal("0")
    mas_antigua: datetime | None = None
    # Plazo de la DGII para enviar lo pendiente (72 h): vence_en = la
    # pendiente más antigua + horas_limite.
    vence_en: datetime | None = None
    horas_limite: int
    lote_tamano: int
    envio_en_curso: bool = False
    lotes: list[LoteContingencia] = []


class ReintentoContingencia(BaseModel):
    disponible: bool
    detalle: str | None = None
    envio_iniciado: bool = False
    estado: EstadoContingencia


class CertificadoEstado(BaseModel):
    """Datos públicos del certificado .p12 con que se firma (nunca la llave
    ni la contraseña). `estado`: vigente | por_vencer (<= 60 días) | vencido
    | no_configurado | error. `origen`: cargado (desde Configuración) |
    variables (CERTIFICADO_PATH)."""

    estado: str
    origen: str | None = None
    archivo: str | None = None
    titular: str | None = None
    firmante: str | None = None
    documento_firmante: str | None = None
    sujeto: str | None = None
    emisor: str | None = None
    rnc: str | None = None
    numero_serie: str | None = None
    valido_desde: datetime | None = None
    vence: datetime | None = None
    dias_restantes: int | None = None
    dias_para_vencer: int | None = None
    vigencia_consumida_pct: int | None = None
    coincide_rnc_emisor: bool | None = None
    error: str | None = None
