"""Esquemas de la API de facturas.

`datos` se valida a nivel de forma (debe ser un objeto JSON) pero NO se
valida campo por campo aquí -- la validación autoritativa de estructura,
campos requeridos, patrones y enumeraciones la hace `xml_builder` contra el
XSD oficial del `tipo_ecf` (ver POST /facturas/{id}/generar-xml). Intentar
replicar en Pydantic las ~150+ reglas de cada uno de los 10 XSD sería
enorme y quedaría desincronizado de la fuente de verdad (el propio XSD).
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from .xml_builder import TIPOS_SOPORTADOS


class FacturaCrear(BaseModel):
    tipo_ecf: int = Field(..., description=f"Tipo de e-CF: {sorted(TIPOS_SOPORTADOS)}")
    encf: str | None = Field(None, description="e-NCF asignado (rango autorizado por la DGII)")
    datos: dict[str, Any] = Field(..., description="Estructura del e-CF (Encabezado, DetallesItems, ...)")


class FacturaActualizar(BaseModel):
    encf: str | None = None
    datos: dict[str, Any]


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
    creado_en: str
    actualizado_en: str


class FacturaDetalle(FacturaResumen):
    datos: dict[str, Any]
    xml_generado: str | None = None
    xml_firmado: str | None = None
    dgii_respuesta: dict[str, Any] | None = None


class GenerarXmlResponse(BaseModel):
    id: UUID
    estado: str
    xml_generado: str | None = None
    errores: list[str] = []


class EnviarResponse(BaseModel):
    id: UUID
    estado: str
    track_id: str | None = None
    error: str | None = None
