"""Rutas de Soluciones de erp-api (fase 6): planes y suscripción (prueba
gratis, consumo del mes, solicitud de plan sin pasarela de pago), portal de
clientes (enlaces con token y la vista pública de solo lectura, sin login),
Kontara Ventas (automatizaciones y su cola de envíos) y POS (sesiones de
caja, ventas y timbre del ticket). Contrato con el frontend:
`frontend/src/lib/solucionesApi.ts`.

Las rutas `/portal/publico/...` son las ÚNICAS pensadas para abrirse sin
sesión: solo responden con un token vigente y solo con los documentos del
cliente de ese token (404 para todo lo demás).
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, HTTPException, Query, Request
from fastapi.responses import Response

from . import gotrue_admin
from . import soluciones_calculos as calc
from . import soluciones_repository as repo
from . import soluciones_tareas
from .config import get_settings
from .repository import DatosInvalidosError
from .soluciones_canales import (
    CanalError,
    FacturasApiError,
    canales_de_envio,
    get_soluciones_settings,
    representacion_impresa,
)
from .soluciones_repository import ConflictoSolucionesError, NoEncontradoError
from .soluciones_schemas import (
    AbrirSesionPayload,
    ActivarPayload,
    Automatizacion,
    AutomatizacionPayload,
    Canales,
    CerrarSesionPayload,
    ClaveAutomatizacion,
    ConfigPortal,
    ConfigPortalPayload,
    Consumo,
    CorreoPayload,
    EnlacePortal,
    EnlacePortalPayload,
    Envio,
    EstadoEnvio,
    EstadoVentas,
    MarcarEnvioPayload,
    Plan,
    PortalPublico,
    ResultadoEjecucion,
    SesionCaja,
    SolicitudPlan,
    SolicitudPlanPayload,
    SolicitudPlanRespuesta,
    Suscripcion,
    Timbre,
    VentaPos,
    VentaPosPayload,
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Tarea diaria de Kontara Ventas. Corre después del lifespan de main.py
    (que crea el pool): FastAPI anida los lifespans de los routers."""
    settings = get_soluciones_settings()
    tarea = None
    if settings.soluciones_tarea_activa:
        tarea = asyncio.create_task(soluciones_tareas.bucle(lambda: app.state.db_pool, settings))
    yield
    if tarea is not None:
        tarea.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await tarea


router = APIRouter(tags=["soluciones"], lifespan=_lifespan)


def _pool(request: Request):
    return request.app.state.db_pool


def _uuid(valor: str, nombre: str = "id") -> uuid.UUID:
    try:
        return uuid.UUID(valor)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"{nombre} inválido") from exc


async def _llamar(funcion, *args, **kwargs):
    """Errores del repositorio -> HTTP: 422 datos inválidos, 404 no
    encontrado, 409 conflicto de estado."""
    try:
        return await funcion(*args, **kwargs)
    except DatosInvalidosError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except NoEncontradoError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ConflictoSolucionesError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


async def _canales(request: Request) -> Canales:
    salida = await canales_de_envio(_pool(request), get_soluciones_settings())
    return Canales(**salida.configurados())


@router.get("/soluciones/canales", response_model=Canales)
async def canales(request: Request):
    """Qué canales de salida hay configurados (SMTP / WhatsApp Cloud API de
    Configuración → WhatsApp y correo, o de las variables de entorno)."""
    return await _canales(request)


# --- Planes y suscripción ---------------------------------------------------------

@router.get("/planes", response_model=list[Plan])
def listar_planes():
    return [
        {
            "clave": clave,
            **{k: v for k, v in p.items() if k != "precio_mensual"},
            "precio_mensual": calc.precio_plan(clave, "mensual")["subtotal"],
            "precio_anual": calc.precio_plan(clave, "anual")["subtotal"],
        }
        for clave, p in calc.PLANES.items()
    ]


@router.get("/suscripcion", response_model=Suscripcion)
async def obtener_suscripcion(request: Request):
    """Plan actual y días que quedan de la prueba gratis (la tarjeta de
    prueba del menú lateral lee esta ruta)."""
    return await repo.obtener_suscripcion(_pool(request))


async def _usuarios_activos() -> int | None:
    """Usuarios activos de la empresa en Supabase Auth (Configuración →
    Usuarios), por la API admin de GoTrue. None si erp-api no tiene
    SUPABASE_SERVICE_ROLE_KEY o GoTrue no responde."""
    settings = get_settings()
    if not gotrue_admin.configurado(settings):
        return None
    try:
        usuarios = await gotrue_admin.listar_usuarios(settings)
    except (gotrue_admin.GoTrueError, gotrue_admin.GoTrueNoConfiguradoError) as exc:
        logger.warning("No se pudieron contar los usuarios de Supabase Auth: %s", exc)
        return None
    return sum(1 for u in usuarios if u["activo"])


@router.get("/suscripcion/consumo", response_model=Consumo)
async def obtener_consumo(request: Request):
    return await repo.consumo(_pool(request), usuarios=await _usuarios_activos())


_INSTRUCCIONES_TRANSFERENCIA = [
    "Recibimos su solicitud. Un asesor le contactará en horario laborable para confirmar el plan.",
    "El pago se realiza por transferencia bancaria: el asesor le enviará los datos de la cuenta y el monto con ITBIS.",
    "El plan se activa al confirmar el depósito; mientras tanto puede seguir usando su prueba gratis.",
]
_INSTRUCCIONES_TARJETA = [
    "Recibimos su solicitud. El pago con tarjeta en línea todavía no está disponible.",
    "Un asesor le contactará para completar el pago por transferencia bancaria o enviarle un enlace de pago.",
    "El plan se activa al confirmar el pago; mientras tanto puede seguir usando su prueba gratis.",
]


@router.post("/suscripcion/solicitudes", response_model=SolicitudPlanRespuesta, status_code=201)
async def solicitar_plan(request: Request, payload: SolicitudPlanPayload):
    """No cobra nada: registra la solicitud (montos con el precio de lista)."""
    solicitud = await _llamar(repo.crear_solicitud_plan, _pool(request), **payload.model_dump())
    instrucciones = _INSTRUCCIONES_TARJETA if payload.forma_pago == "tarjeta" else _INSTRUCCIONES_TRANSFERENCIA
    return {"solicitud": solicitud, "instrucciones": instrucciones}


@router.get("/suscripcion/solicitudes", response_model=list[SolicitudPlan])
async def listar_solicitudes(request: Request):
    return await repo.listar_solicitudes_plan(_pool(request))


# --- Portal de clientes ---------------------------------------------------------

@router.get("/portal/enlaces", response_model=list[EnlacePortal])
async def listar_enlaces(request: Request, cliente_id: str | None = None):
    cid = _uuid(cliente_id, "cliente_id") if cliente_id else None
    return await repo.listar_enlaces(_pool(request), cliente_id=cid)


@router.post("/portal/enlaces", response_model=EnlacePortal, status_code=201)
async def crear_enlace(request: Request, payload: EnlacePortalPayload):
    """El `token` de la respuesta es la única copia en claro del enlace."""
    return await _llamar(
        repo.crear_enlace,
        _pool(request),
        cliente_id=payload.cliente_id,
        metodos_pago=list(payload.metodos_pago),
        dias_validez=payload.dias_validez,
    )


@router.post("/portal/enlaces/{enlace_id}/revocar", response_model=EnlacePortal)
async def revocar_enlace(request: Request, enlace_id: str):
    return await _llamar(repo.revocar_enlace, _pool(request), _uuid(enlace_id))


@router.get("/portal/config", response_model=ConfigPortal)
async def obtener_config_portal(request: Request):
    return await repo.obtener_config_portal(_pool(request))


@router.put("/portal/config", response_model=ConfigPortal)
async def guardar_config_portal(request: Request, payload: ConfigPortalPayload):
    return await repo.guardar_config_portal(_pool(request), **payload.model_dump())


@router.post("/portal/enviar-correo", status_code=202)
async def enviar_correo_portal(request: Request, payload: CorreoPayload):
    """Envía el enlace por el SMTP configurado; sin SMTP responde 409 y el
    frontend ofrece abrir el correo del usuario (mailto)."""
    salida = await canales_de_envio(_pool(request), get_soluciones_settings())
    if not salida.configurados()["correo"]:
        raise HTTPException(status_code=409, detail="No hay un servidor de correo (SMTP) configurado")
    if not calc.correo_valido(payload.para):
        raise HTTPException(status_code=422, detail="Correo del destinatario inválido")
    try:
        await salida.enviar_correo(payload.para, payload.asunto, payload.mensaje)
    except CanalError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"enviado": True}


_ENLACE_NO_VALIDO = "Enlace no válido, vencido o revocado"


@router.get("/portal/publico/{token}", response_model=PortalPublico)
async def portal_publico(request: Request, token: str, vista_previa: bool = False):
    try:
        return await repo.portal_publico(
            _pool(request),
            token,
            empresa_configurada=get_settings().emisor_razon_social,
            registrar_acceso=not vista_previa,
        )
    except NoEncontradoError as exc:
        raise HTTPException(status_code=404, detail=_ENLACE_NO_VALIDO) from exc


async def _factura_publica(request: Request, token: str, factura_id: str) -> dict:
    try:
        fid = uuid.UUID(factura_id)
        return await repo.factura_del_portal(_pool(request), token, fid)
    except (ValueError, NoEncontradoError) as exc:
        # Mismo 404 para token inválido y factura ajena: no revela cuál falló.
        raise HTTPException(status_code=404, detail="Documento no encontrado") from exc


@router.get("/portal/publico/{token}/facturas/{factura_id}/pdf")
async def portal_pdf(request: Request, token: str, factura_id: str):
    factura = await _factura_publica(request, token, factura_id)
    try:
        pdf = await representacion_impresa(
            get_soluciones_settings(), factura["id"], firmada=factura["xml_firmado"] is not None
        )
    except FacturasApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{factura["encf"] or factura["id"]}.pdf"'},
    )


@router.get("/portal/publico/{token}/facturas/{factura_id}/xml")
async def portal_xml(request: Request, token: str, factura_id: str):
    factura = await _factura_publica(request, token, factura_id)
    xml = factura["xml_firmado"]
    if xml is None:
        # Igual que el PDF: se genera la vista previa firmada en facturas_api.
        try:
            await representacion_impresa(get_soluciones_settings(), factura["id"], firmada=False)
        except FacturasApiError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        xml = await repo.xml_firmado_factura(_pool(request), uuid.UUID(factura["id"]))
    if not xml:
        raise HTTPException(status_code=404, detail="Documento no encontrado")
    return Response(
        content=xml.encode("utf-8"),
        media_type="application/xml",
        headers={"Content-Disposition": f'attachment; filename="{factura["encf"] or factura["id"]}.xml"'},
    )


# --- Kontara Ventas ---------------------------------------------------------

@router.get("/ventas-auto", response_model=EstadoVentas)
async def estado_ventas(request: Request):
    return await repo.estado_ventas(_pool(request)) | {"canales": await _canales(request)}


@router.post("/ventas-auto/activar", response_model=EstadoVentas)
async def activar_ventas(request: Request, payload: ActivarPayload):
    return await repo.activar_ventas(_pool(request), payload.activo) | {"canales": await _canales(request)}


@router.put("/ventas-auto/automatizaciones/{clave}", response_model=Automatizacion)
async def actualizar_automatizacion(request: Request, clave: ClaveAutomatizacion, payload: AutomatizacionPayload):
    return await _llamar(repo.actualizar_automatizacion, _pool(request), clave, **payload.model_dump())


@router.post("/ventas-auto/ejecutar", response_model=ResultadoEjecucion)
async def ejecutar_automatizaciones(request: Request):
    """Lo mismo que hace la tarea diaria, a pedido."""
    return await soluciones_tareas.ejecutar_ciclo(_pool(request), get_soluciones_settings())


@router.get("/ventas-auto/envios", response_model=list[Envio])
async def listar_envios(
    request: Request,
    automatizacion: ClaveAutomatizacion | None = None,
    estado: EstadoEnvio | None = None,
    limit: int = Query(200, ge=1, le=500),
):
    return await repo.listar_envios(_pool(request), automatizacion=automatizacion, estado=estado, limite=limit)


@router.post("/ventas-auto/envios/{envio_id}/marcar", response_model=Envio)
async def marcar_envio(request: Request, envio_id: str, payload: MarcarEnvioPayload):
    return await _llamar(repo.marcar_envio, _pool(request), _uuid(envio_id), payload.estado)


# --- POS ---------------------------------------------------------

@router.get("/pos/sesiones", response_model=list[SesionCaja])
async def listar_sesiones(request: Request, limit: int = Query(30, ge=1, le=200)):
    return await repo.listar_sesiones(_pool(request), limite=limit)


@router.get("/pos/sesiones/abierta", response_model=SesionCaja | None)
async def sesion_abierta(request: Request, caja: str = "Caja principal"):
    return await repo.sesion_abierta(_pool(request), caja)


@router.post("/pos/sesiones", response_model=SesionCaja, status_code=201)
async def abrir_sesion(request: Request, payload: AbrirSesionPayload):
    return await _llamar(repo.abrir_sesion, _pool(request), **payload.model_dump())


@router.get("/pos/sesiones/{sesion_id}", response_model=SesionCaja)
async def obtener_sesion(request: Request, sesion_id: str):
    return await _llamar(repo.obtener_sesion, _pool(request), _uuid(sesion_id))


@router.post("/pos/sesiones/{sesion_id}/ventas", response_model=VentaPos, status_code=201)
async def registrar_venta(request: Request, sesion_id: str, payload: VentaPosPayload):
    return await _llamar(
        repo.registrar_venta,
        _pool(request),
        _uuid(sesion_id),
        factura_id=payload.factura_id,
        pagos=[p.model_dump() for p in payload.pagos],
    )


@router.post("/pos/sesiones/{sesion_id}/cerrar", response_model=SesionCaja)
async def cerrar_sesion(request: Request, sesion_id: str, payload: CerrarSesionPayload):
    return await _llamar(
        repo.cerrar_sesion,
        _pool(request),
        _uuid(sesion_id),
        contado=payload.contado.model_dump(),
        notas=payload.notas,
    )


@router.get("/pos/facturas/{factura_id}/timbre", response_model=Timbre)
async def timbre(request: Request, factura_id: str):
    """QR y código de seguridad para el ticket, desde el XML firmado."""
    factura = await _llamar(repo.factura_para_timbre, _pool(request), _uuid(factura_id))
    return calc.timbre_desde_xml(
        factura["xml_firmado"],
        rnc_emisor=factura["rnc_emisor"],
        encf=factura["encf"],
        datos=factura["datos"],
        ambiente=get_settings().dgii_environment,
    )

