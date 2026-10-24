"""Esquemas de Configuración y Plataforma (fase 6): empresa, secuencias
e-NCF, vendedores, usuarios (GoTrue) y estado de las soluciones.

Contrato con el frontend: `frontend/src/lib/configuracionApi.ts` (nombres y
tipos 1:1). Igual que `schemas.py`: montos/porcentajes como string decimal,
fechas "YYYY-MM-DD", marcas de tiempo ISO 8601.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from .schemas import Monto

TIPOS_ECF = (31, 32, 33, 34, 41, 43, 44, 45, 46, 47)

EstadoSecuencia = Literal[
    "sin_configurar", "inactiva", "vencida", "agotada", "por_vencer", "por_agotarse", "vigente"
]


class _Entrada(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)


# --- Empresa ---------------------------------------------------------

class EmpresaPayload(_Entrada):
    razon_social: str = Field(..., min_length=1, max_length=250)
    nombre_comercial: str | None = Field(None, max_length=250)
    rnc: str | None = None
    actividad_economica: str | None = Field(None, max_length=250)
    direccion: str | None = Field(None, max_length=500)
    provincia: str | None = Field(None, max_length=120)
    municipio: str | None = Field(None, max_length=120)
    telefono: str | None = Field(None, max_length=40)
    correo: str | None = Field(None, max_length=250)
    # data URL de una imagen pequeña (PNG/JPEG/WebP/SVG), ver configuracion_repository.
    logo: str | None = None


class Empresa(BaseModel):
    configurada: bool
    razon_social: str
    nombre_comercial: str | None = None
    rnc: str | None = None
    actividad_economica: str | None = None
    direccion: str | None = None
    provincia: str | None = None
    municipio: str | None = None
    telefono: str | None = None
    correo: str | None = None
    logo: str | None = None
    # Solo lectura (variables de entorno del despliegue).
    ambiente: str
    ambiente_dgii: str
    emisor_rnc_configurado: str | None = None
    actualizado_en: datetime | None = None


# --- Secuencias e-NCF ---------------------------------------------------------

class SecuenciaPayload(_Entrada):
    desde: int = Field(..., ge=1, le=9_999_999_999)
    hasta: int = Field(..., ge=1, le=9_999_999_999)
    # None = seguir desde `desde` (o desde el mayor e-NCF ya usado).
    proximo: int | None = Field(None, ge=1, le=9_999_999_999)
    fecha_vencimiento: date | None = None
    activa: bool = True


class Secuencia(BaseModel):
    tipo_ecf: int
    nombre: str
    prefijo: str
    configurada: bool
    desde: int | None = None
    hasta: int | None = None
    proximo: int | None = None
    fecha_vencimiento: date | None = None
    activa: bool = False
    # Calculados: mayor e-NCF ya guardado en facturacion.facturas y el siguiente a usar.
    ultimo_usado: int | None = None
    siguiente: int | None = None
    siguiente_encf: str | None = None
    usados: int = 0
    disponibles: int = 0
    consumo_pct: float = 0
    estado: EstadoSecuencia
    avisos: list[str] = []
    actualizado_en: datetime | None = None


# --- Vendedores ---------------------------------------------------------

class VendedorPayload(_Entrada):
    nombre: str = Field(..., min_length=1, max_length=200)
    correo: str | None = Field(None, max_length=250)
    comision_pct: Decimal = Field(Decimal("0"), ge=0, le=100)
    activo: bool = True


class Vendedor(BaseModel):
    id: UUID
    nombre: str
    correo: str | None = None
    comision_pct: Monto
    activo: bool
    creado_en: datetime
    actualizado_en: datetime


# --- Usuarios (Supabase Auth) ---------------------------------------------------------

class UsuarioPayload(_Entrada):
    correo: str = Field(..., min_length=3, max_length=250)
    # Sin contraseña = invitación por correo (requiere SMTP en Supabase).
    contrasena: str | None = Field(None, min_length=8, max_length=72)


class Usuario(BaseModel):
    id: str
    correo: str | None = None
    creado_en: datetime | None = None
    ultimo_acceso: datetime | None = None
    confirmado: bool = False
    activo: bool = True
    invitado: bool = False
    # app_metadata.kontara: puede entrar a Kontara (los demás usuarios del
    # mismo GoTrue se listan aparte, "sin acceso").
    acceso: bool = False
    admin: bool = False


class Usuarios(BaseModel):
    configurado: bool
    usuarios: list[Usuario] = []
    # Usuarios del mismo Supabase Auth SIN acceso a Kontara (no entran).
    sin_acceso: list[Usuario] = []
    mensaje: str | None = None


# --- Estado de las soluciones ---------------------------------------------------------

EstadoServicio = Literal["ok", "error", "caido", "no_configurado"]


class ServicioEstado(BaseModel):
    id: str
    nombre: str
    grupo: str
    estado: EstadoServicio
    latencia_ms: int | None = None
    detalle: str | None = None


class EstadoServicios(BaseModel):
    ambiente: str
    ambiente_dgii: str
    verificado_en: datetime
    servicios: list[ServicioEstado]


# --- Certificado digital ---------------------------------------------------------

class CertificadoEstado(BaseModel):
    """Datos públicos del certificado con que se firma (nunca la llave ni la
    contraseña). `origen`: cargado (desde Configuración) | variables
    (CERTIFICADO_PATH). `dias_para_vencer` alimenta los recordatorios a 60,
    30 y 7 días."""

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
    carga_disponible: bool = False


# --- Inventario ---------------------------------------------------------

class PreferenciaInventario(BaseModel):
    # Solo preferencia de vista (Kardex): inventario-api calcula el costo
    # siempre por promedio ponderado.
    metodo: Literal["promedio", "peps"] = "promedio"


# --- Envío automático (correo y WhatsApp) ---------------------------------------------------------

class CorreoPayload(_Entrada):
    host: str = Field("", max_length=250)
    puerto: int = Field(587, ge=1, le=65535)
    seguridad: Literal["starttls", "ssl", "ninguna"] = "starttls"
    usuario: str = Field("", max_length=250)
    # None o "" = conservar la que ya está guardada.
    contrasena: str | None = Field(None, max_length=500)
    remitente: str = Field("", max_length=250)
    cco: str = Field("", max_length=250)
    adjuntar_xml: bool = True
    envio_automatico: bool = False


class CorreoConfig(BaseModel):
    host: str
    puerto: int
    seguridad: str
    usuario: str
    remitente: str
    cco: str
    adjuntar_xml: bool
    envio_automatico: bool
    contrasena_configurada: bool
    configurado: bool


class WhatsappPayload(_Entrada):
    phone_number_id: str = Field("", max_length=64)
    numero: str = Field("", max_length=40)
    token: str | None = Field(None, max_length=2000)
    plantilla: str = Field("hello_world", max_length=120)
    idioma: str = Field("en_US", max_length=20)
    envio_automatico: bool = False
    # Plantilla aprobada en Meta (cuerpo con un parámetro {{1}}) para los
    # mensajes de Kontara Ventas fuera de la ventana de 24 h. Vacía = texto libre.
    plantilla_mensajes: str = Field("", max_length=120, pattern=r"^[a-z0-9_]*$")
    idioma_mensajes: str = Field("es", max_length=20)


class WhatsappConfig(BaseModel):
    phone_number_id: str
    numero: str
    plantilla: str
    idioma: str
    envio_automatico: bool
    plantilla_mensajes: str = ""
    idioma_mensajes: str = "es"
    token_configurado: bool
    conectado: bool


class EnvioConfig(BaseModel):
    correo: CorreoConfig
    whatsapp: WhatsappConfig
    enviados_mes: dict[str, int]
    cifrado_disponible: bool


class PruebaPayload(_Entrada):
    destino: str = Field(..., min_length=3, max_length=250)


class PruebaResultado(BaseModel):
    ok: bool
    mensaje: str
