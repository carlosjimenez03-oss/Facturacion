"""Esquemas de Soluciones (fase 6): planes y suscripción, portal de
clientes, Kontara Ventas y POS. Contrato con el frontend:
`frontend/src/lib/solucionesApi.ts` (mismos nombres y tipos). Montos como
string con 2 decimales, fechas "YYYY-MM-DD", marcas de tiempo ISO 8601.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from .schemas import Monto, _Entrada

PlanClave = Literal["emprendedor", "pyme", "empresarial"]
Periodicidad = Literal["mensual", "anual"]
MetodoPagoPortal = Literal["tarjeta", "transferencia", "pago_movil", "deposito"]
ClaveAutomatizacion = Literal["motivar", "cobranza", "fidelizacion", "reactivacion"]
CanalEnvio = Literal["whatsapp", "correo", "ambos"]
EstadoEnvio = Literal["pendiente_canal", "enviado", "enviado_manual", "error", "descartado"]
MetodoPos = Literal["efectivo", "tarjeta", "transferencia"]


# --- Planes y suscripción ---------------------------------------------------------

class Plan(BaseModel):
    clave: PlanClave
    nombre: str
    descripcion: str
    precio_mensual: Monto
    precio_anual: Monto
    ecf_mes: int | None
    usuarios: int | None
    almacenamiento_bytes: int
    popular: bool
    caracteristicas: list[str]


class SolicitudPlan(BaseModel):
    id: UUID
    plan: PlanClave
    periodicidad: Periodicidad
    forma_pago: Literal["transferencia", "tarjeta"]
    subtotal: Monto
    itbis: Monto
    total: Monto
    contacto_nombre: str | None = None
    contacto_correo: str | None = None
    contacto_telefono: str | None = None
    notas: str | None = None
    estado: Literal["pendiente", "atendida", "cancelada"]
    creado_en: datetime


class Suscripcion(BaseModel):
    plan: PlanClave | None = None
    plan_nombre: str | None = None
    periodicidad: Periodicidad | None = None
    estado: Literal["prueba", "activa", "prueba_vencida"]
    prueba_inicio: date
    prueba_fin: date
    # None con un plan activo.
    dias_restantes: int | None = None
    proxima_factura: date | None = None
    # Plan cuyos límites aplican (en prueba: Pyme).
    plan_limites: PlanClave
    solicitud_pendiente: SolicitudPlan | None = None


class Consumo(BaseModel):
    mes: date
    plan_limites: PlanClave
    ecf_emitidos: int
    ecf_limite: int | None
    ecf_porcentaje: int | None
    # None = la tabla de usuarios de la plataforma aún no existe.
    usuarios: int | None
    usuarios_limite: int | None
    usuarios_porcentaje: int | None
    almacenamiento_bytes: int
    almacenamiento_limite_bytes: int
    almacenamiento_texto: str
    almacenamiento_limite_texto: str
    almacenamiento_porcentaje: int | None


class SolicitudPlanPayload(_Entrada):
    plan: PlanClave
    periodicidad: Periodicidad = "mensual"
    forma_pago: Literal["transferencia", "tarjeta"] = "transferencia"
    contacto_nombre: str | None = Field(None, max_length=200)
    contacto_correo: str | None = Field(None, max_length=200)
    contacto_telefono: str | None = Field(None, max_length=40)
    notas: str | None = Field(None, max_length=1000)


class SolicitudPlanRespuesta(BaseModel):
    solicitud: SolicitudPlan
    # Qué pasa ahora (sin pasarela de pago: lo atiende un asesor).
    instrucciones: list[str]


class Canales(BaseModel):
    correo: bool
    whatsapp: bool


# --- Portal de clientes ---------------------------------------------------------

class EnlacePortal(BaseModel):
    id: UUID
    cliente_id: UUID
    cliente_nombre: str
    cliente_rnc: str | None = None
    cliente_correo: str | None = None
    cliente_telefono: str | None = None
    metodos_pago: list[MetodoPagoPortal]
    expira_en: datetime
    revocado_en: datetime | None = None
    ultimo_acceso_en: datetime | None = None
    accesos: int
    estado: Literal["activo", "vencido", "revocado"]
    creado_en: datetime
    # Solo en la respuesta de creación: no se guarda en claro.
    token: str | None = None


class EnlacePortalPayload(_Entrada):
    cliente_id: UUID
    metodos_pago: list[MetodoPagoPortal] = ["transferencia"]
    dias_validez: int = Field(90, ge=1, le=365)


class ConfigPortal(BaseModel):
    banco: str | None = None
    tipo_cuenta: str | None = None
    numero_cuenta: str | None = None
    titular: str | None = None
    rnc_titular: str | None = None
    instrucciones: str | None = None
    actualizado_en: datetime | None = None


class ConfigPortalPayload(_Entrada):
    banco: str | None = Field(None, max_length=120)
    tipo_cuenta: str | None = Field(None, max_length=60)
    numero_cuenta: str | None = Field(None, max_length=60)
    titular: str | None = Field(None, max_length=200)
    rnc_titular: str | None = Field(None, max_length=20)
    instrucciones: str | None = Field(None, max_length=1000)


class DatosTransferencia(BaseModel):
    banco: str | None = None
    tipo_cuenta: str | None = None
    numero_cuenta: str | None = None
    titular: str | None = None
    rnc_titular: str | None = None
    instrucciones: str | None = None


class FacturaPortal(BaseModel):
    id: UUID
    encf: str | None = None
    tipo_ecf: int
    fecha_emision: date
    total: Monto
    pendiente: Monto
    vence: date | None = None
    estado_pago: Literal["contado", "pagada", "pendiente", "vencida", "nota_credito"]


class PortalPublico(BaseModel):
    empresa: str
    cliente_nombre: str
    cliente_rnc: str | None = None
    expira_en: datetime
    metodos_pago: list[MetodoPagoPortal]
    transferencia: DatosTransferencia | None = None
    saldo_pendiente: Monto
    facturas: list[FacturaPortal]


class CorreoPayload(_Entrada):
    para: str = Field(..., max_length=200)
    asunto: str = Field(..., max_length=300)
    mensaje: str = Field(..., max_length=5000)


# --- Kontara Ventas ---------------------------------------------------------

class Automatizacion(BaseModel):
    clave: ClaveAutomatizacion
    categoria: str
    titulo: str
    descripcion: str
    variables: list[str]
    activa: bool
    canal: CanalEnvio
    dias: int
    asunto: str
    plantilla: str
    activada_en: datetime | None = None
    # Envíos por estado ({"pendiente_canal": 3, ...}).
    envios: dict[str, int]


class EstadoVentas(BaseModel):
    activo: bool
    activado_en: datetime | None = None
    prueba_hasta: date | None = None
    # Activo y en prueba (o con plan Pyme/Empresarial).
    habilitado: bool
    ultima_ejecucion: datetime | None = None
    ultimo_resultado: dict | None = None
    automatizaciones: list[Automatizacion]
    canales: Canales | None = None


class ActivarPayload(BaseModel):
    activo: bool


class AutomatizacionPayload(_Entrada):
    activa: bool | None = None
    canal: CanalEnvio | None = None
    dias: int | None = Field(None, ge=0, le=365)
    asunto: str | None = Field(None, max_length=300)
    plantilla: str | None = Field(None, max_length=2000)


class Envio(BaseModel):
    id: UUID
    automatizacion: ClaveAutomatizacion
    canal: Literal["whatsapp", "correo"]
    cliente_id: UUID | None = None
    cliente_nombre: str
    destino: str
    factura_id: UUID | None = None
    encf: str | None = None
    asunto: str
    mensaje: str
    estado: EstadoEnvio
    error: str | None = None
    intentos: int
    enviado_en: datetime | None = None
    creado_en: datetime


class MarcarEnvioPayload(BaseModel):
    estado: Literal["enviado_manual", "descartado", "pendiente_canal"]


class ResultadoEjecucion(BaseModel):
    creados: int
    omitidos_sin_contacto: int = 0
    por_automatizacion: dict[str, int] = {}
    motivo: str | None = None
    entrega: dict[str, int] | None = None


# --- POS ---------------------------------------------------------

class PagoPos(_Entrada):
    metodo: MetodoPos
    monto: Decimal = Field(..., gt=0)
    referencia: str | None = Field(None, max_length=100)


class PagoPosRespuesta(BaseModel):
    metodo: MetodoPos
    monto: Monto
    referencia: str | None = None


class VentaPos(BaseModel):
    id: UUID
    sesion_id: UUID
    factura_id: UUID
    encf: str | None = None
    tipo_ecf: int
    cliente_rnc: str | None = None
    cliente_nombre: str | None = None
    total: Monto
    pagos: list[PagoPosRespuesta]
    recibido: Monto
    cambio: Monto
    creado_en: datetime


class MontosPorMetodo(BaseModel):
    efectivo: Monto = Decimal("0")
    tarjeta: Monto = Decimal("0")
    transferencia: Monto = Decimal("0")


class SesionCaja(BaseModel):
    id: UUID
    caja: str
    almacen_id: UUID | None = None
    cajero: str | None = None
    fondo_inicial: Monto
    abierta_en: datetime
    cerrada_en: datetime | None = None
    estado: Literal["abierta", "cerrada"]
    esperado: MontosPorMetodo | None = None
    contado: MontosPorMetodo | None = None
    diferencias: MontosPorMetodo | None = None
    notas_cierre: str | None = None
    cantidad_ventas: int
    total_ventas: Monto
    ventas: list[VentaPos]


class AbrirSesionPayload(_Entrada):
    caja: str = Field("Caja principal", min_length=1, max_length=80)
    fondo_inicial: Decimal = Field(Decimal("0"), ge=0)
    cajero: str | None = Field(None, max_length=120)
    almacen_id: UUID | None = None


class VentaPosPayload(BaseModel):
    factura_id: UUID
    pagos: list[PagoPos] = Field(..., min_length=1, max_length=6)


class CerrarSesionPayload(_Entrada):
    contado: MontosPorMetodo
    notas: str | None = Field(None, max_length=1000)


class Timbre(BaseModel):
    encf: str | None = None
    # None si la factura aún no tiene XML firmado.
    codigo_seguridad: str | None = None
    fecha_firma: str | None = None
    url_qr: str | None = None
    qr_svg: str | None = None
