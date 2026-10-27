"""Servicio ERP de ventas: contactos (clientes/proveedores), cotizaciones,
cobros y cuentas por cobrar, y un resumen de los documentos de venta; y el
módulo de impuestos (formatos 607/608, pre-cálculos IT-1/IR-17/IR-2 y
declaraciones), con sus rutas en `impuestos.py`; y Compras (bandeja de
entrada de e-CF recibidos, cuentas por pagar, pagos a proveedores y
retenciones), con sus rutas en `compras.py`; Bancos (cuentas, movimientos,
transferencias y conciliación) en `bancos.py`; Contabilidad (catálogo,
asientos, reportes y activos fijos) en `contabilidad.py`; y Kontara Nómina
(empleados, períodos de nómina, TSS e ISR de asalariados) en `nomina.py`.

Vive en sus propios esquemas `ventas`, `impuestos`, `compras`, `bancos` y
`contabilidad` (más `recepcion.ecf_recibidos`), que este servicio crea
por sí mismo al arrancar (ver `migraciones.py`). Las cuentas por cobrar, el saldo de cada
cliente y `/documentos-venta` se calculan LEYENDO `facturacion.facturas`
(de `facturas_api`) -- este servicio nunca la modifica.

El contrato con el frontend es `frontend/src/lib/erpApi.ts` (y
`frontend/src/lib/impuestosApi.ts` para impuestos,
`frontend/src/lib/comprasApi.ts` para compras, `bancosApi.ts` y
`contabilidadApi.ts` para bancos y contabilidad y
`frontend/src/lib/solucionesApi.ts` para Soluciones: planes, portal de
clientes, Kontara Ventas y POS, con sus rutas en `soluciones.py`).
"""
import asyncio
import logging
import uuid
from contextlib import asynccontextmanager, suppress
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from libs.ecf_common.auth.sesion_supabase import instalar_verificacion_sesion
from libs.ecf_common.db import cerrar_pool, crear_pool
from libs.ecf_common.empresa import diagnosticar, instalar_empresa, registrar_usuario_previo

from . import (
    ambiente_dgii,
    bancos,
    compras,
    configuracion,
    contabilidad,
    empresas,
    impuestos,
    nomina,
    padron,
    pagos,
    plataforma,
    reportes,
    repository,
    sincronizacion,
    soluciones,
    tareas,
)
from .config import ErpApiSettings, get_settings
from .migraciones import aplicar_migraciones
from .repository import (
    ClienteConCotizacionesError,
    ClienteDuplicadoError,
    ClienteNoEncontradoError,
    CobroEstadoInvalidoError,
    CobroNoEncontradoError,
    CotizacionEstadoInvalidoError,
    CotizacionNoEncontradaError,
    DatosInvalidosError,
)
from .schemas import (
    CambioEstadoCotizacion,
    Cliente,
    ClientePayload,
    Cobro,
    CobroPayload,
    Cotizacion,
    CotizacionPayload,
    CotizacionResumen,
    CuentaPorCobrar,
    DocumentoVenta,
    EstadoCotizacion,
    MarcarFacturada,
    TipoContacto,
)

settings = get_settings()
logging.basicConfig(level=settings.log_level)
logger = logging.getLogger(__name__)


_POOL_REINTENTOS = 10
_POOL_ESPERA_SEGUNDOS = 3


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Mismo patrón de reintento con espera que inventario_api/facturas_api:
    # `depends_on: condition: service_healthy` solo se respeta en el `up`
    # inicial de Docker Compose.
    pool = None
    for intento in range(1, _POOL_REINTENTOS + 1):
        pool = await crear_pool(settings.database_url)
        if pool is not None:
            break
        logger.warning(
            "No se pudo conectar a la base de datos (intento %s/%s), reintentando en %ss...",
            intento,
            _POOL_REINTENTOS,
            _POOL_ESPERA_SEGUNDOS,
        )
        await asyncio.sleep(_POOL_ESPERA_SEGUNDOS)
    if pool is None:
        raise RuntimeError(
            "erp_api requiere DATABASE_URL configurado y una base de datos "
            "alcanzable -- clientes, cotizaciones y cobros viven únicamente en Postgres."
        )
    # En Supabase nadie ejecuta db/init: el esquema `ventas` se crea aquí.
    await aplicar_migraciones(pool)
    await diagnosticar(pool)
    app.state.db_pool = pool
    # Envío automático por correo de las facturas aceptadas (Configuración →
    # WhatsApp y correo; no hace nada mientras no se encienda allí).
    envio = None
    if settings.envio_automatico_cada_segundos > 0:
        envio = asyncio.create_task(configuracion.iniciar_envio_automatico(pool, settings))
    # Padrón de RNC de la DGII (docs/PADRON_RNC.md): descarga diaria.
    actualizar_padron = None
    if settings.padron_activo:
        actualizar_padron = asyncio.create_task(padron.bucle_actualizacion(pool, settings))
    yield
    for tarea in (envio, actualizar_padron):
        if tarea is not None:
            tarea.cancel()
            with suppress(asyncio.CancelledError):
                await tarea
    await cerrar_pool(app.state.db_pool)


app = FastAPI(title=settings.app_name, debug=settings.debug, lifespan=lifespan)
app.state.db_pool = None

# ERP_REQUIRE_AUTH (o REQUIRE_AUTH): la API misma exige sesión (ver
# docs/AUTH.md). /auth/verificar hace su propia verificación (es el que
# consulta nginx) y el portal público de clientes (/portal/publico/<token>,
# Soluciones) se abre sin sesión: lo protege su token.
RUTA_PORTAL_PUBLICO = "/portal/publico/"


async def _marcar_acceso(request, usuario) -> None:
    await plataforma.marcar_acceso_si_falta(settings, usuario)
    await registrar_usuario_previo(request.app.state.db_pool, usuario)


# Empresa de cada request (multiempresa, docs/MULTIEMPRESA.md): por dentro
# de la verificación de sesión (se instala antes), para ver al usuario. El
# portal público entra a la empresa del enlace o del pago.
instalar_empresa(
    app,
    settings,
    rutas_libres={"/auth/verificar"},
    es_libre=lambda ruta: ruta.startswith(RUTA_PORTAL_PUBLICO),
    empresa_libre=empresas.empresa_de_ruta_publica,
)

instalar_verificacion_sesion(
    app,
    settings,
    rutas_publicas={"/auth/verificar"},
    es_publica=lambda ruta: ruta.startswith(RUTA_PORTAL_PUBLICO),
    revocacion=plataforma.REVOCACION,
    al_verificar=_marcar_acceso,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_methods=["*"],
    allow_headers=["*"],
    # El frontend lee el nombre de los TXT del 607/608 (fetch + blob) de
    # este header -- igual que formato_606.
    expose_headers=["Content-Disposition"],
)

app.include_router(impuestos.router)
app.include_router(compras.router)
app.include_router(bancos.router)
app.include_router(contabilidad.router)
# Fase 6 · Operación: Mis tareas, Centro de sincronización y Reportes.
app.include_router(tareas.router)
app.include_router(sincronizacion.router)
app.include_router(reportes.router)
app.include_router(configuracion.router)
app.include_router(ambiente_dgii.router)
app.include_router(plataforma.router)
# Mis empresas (multiempresa).
app.include_router(empresas.router)
app.include_router(nomina.router)
app.include_router(soluciones.router)
# Fase 7 · Pagos con tarjeta en línea desde el portal de clientes (docs/PAGOS.md).
app.include_router(pagos.router)
# Padrón de RNC de la DGII (global): consulta por RNC/cédula e importación.
app.include_router(padron.router)


def _pool():
    return app.state.db_pool


def _parse_id(id_: str, *, nombre_campo: str = "id") -> uuid.UUID:
    try:
        return uuid.UUID(id_)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"{nombre_campo} inválido") from exc


@app.get("/health")
def health(settings: ErpApiSettings = Depends(get_settings)) -> dict:
    return {"status": "ok", "app_env": settings.app_env}


# --- Clientes / contactos ---------------------------------------------------------

_CLIENTE_NO_ENCONTRADO = "Cliente no encontrado"


@app.get("/clientes", response_model=list[Cliente])
async def listar_clientes(
    q: str | None = None,
    tipo: TipoContacto | None = None,
    activo: bool | None = None,
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    return await repository.listar_clientes(
        _pool(), q=q, tipo=tipo, activo=activo, limite=limit, offset=offset
    )


@app.get("/clientes/por-rnc/{rnc}", response_model=Cliente)
async def cliente_por_rnc(rnc: str):
    try:
        return await repository.obtener_cliente_por_rnc(_pool(), rnc)
    except ClienteNoEncontradoError as exc:
        raise HTTPException(status_code=404, detail="No hay ningún contacto con ese RNC/cédula") from exc


@app.get("/clientes/{cliente_id}", response_model=Cliente)
async def obtener_cliente(cliente_id: str):
    try:
        return await repository.obtener_cliente(_pool(), _parse_id(cliente_id))
    except ClienteNoEncontradoError as exc:
        raise HTTPException(status_code=404, detail=_CLIENTE_NO_ENCONTRADO) from exc


@app.post("/clientes", response_model=Cliente, status_code=201)
async def crear_cliente(payload: ClientePayload):
    try:
        return await repository.crear_cliente(_pool(), **payload.model_dump())
    except DatosInvalidosError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ClienteDuplicadoError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.put("/clientes/{cliente_id}", response_model=Cliente)
async def actualizar_cliente(cliente_id: str, payload: ClientePayload):
    try:
        return await repository.actualizar_cliente(_pool(), _parse_id(cliente_id), **payload.model_dump())
    except ClienteNoEncontradoError as exc:
        raise HTTPException(status_code=404, detail=_CLIENTE_NO_ENCONTRADO) from exc
    except DatosInvalidosError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ClienteDuplicadoError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.delete("/clientes/{cliente_id}", status_code=204)
async def eliminar_cliente(cliente_id: str):
    try:
        await repository.eliminar_cliente(_pool(), _parse_id(cliente_id))
    except ClienteNoEncontradoError as exc:
        raise HTTPException(status_code=404, detail=_CLIENTE_NO_ENCONTRADO) from exc
    except ClienteConCotizacionesError as exc:
        raise HTTPException(
            status_code=409,
            detail="El cliente tiene cotizaciones y no se puede eliminar; desactívelo en su lugar",
        ) from exc


# --- Cotizaciones ---------------------------------------------------------

_COTIZACION_NO_ENCONTRADA = "Cotización no encontrada"


def _campos_cotizacion(payload: CotizacionPayload) -> dict:
    return {
        "cliente_id": payload.cliente_id,
        "cliente_nombre": payload.cliente_nombre,
        "cliente_rnc": payload.cliente_rnc,
        "fecha": payload.fecha,
        "vence": payload.vence,
        "items": [item.model_dump() for item in payload.items],
        "notas": payload.notas,
    }


@app.get("/cotizaciones", response_model=list[CotizacionResumen])
async def listar_cotizaciones(
    q: str | None = None,
    estado: EstadoCotizacion | None = None,
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    return await repository.listar_cotizaciones(_pool(), q=q, estado=estado, limite=limit, offset=offset)


@app.get("/cotizaciones/{cotizacion_id}", response_model=Cotizacion)
async def obtener_cotizacion(cotizacion_id: str):
    try:
        return await repository.obtener_cotizacion(_pool(), _parse_id(cotizacion_id))
    except CotizacionNoEncontradaError as exc:
        raise HTTPException(status_code=404, detail=_COTIZACION_NO_ENCONTRADA) from exc


@app.post("/cotizaciones", response_model=Cotizacion, status_code=201)
async def crear_cotizacion(payload: CotizacionPayload):
    try:
        return await repository.crear_cotizacion(_pool(), **_campos_cotizacion(payload))
    except DatosInvalidosError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.put("/cotizaciones/{cotizacion_id}", response_model=Cotizacion)
async def actualizar_cotizacion(cotizacion_id: str, payload: CotizacionPayload):
    try:
        return await repository.actualizar_cotizacion(
            _pool(), _parse_id(cotizacion_id), **_campos_cotizacion(payload)
        )
    except CotizacionNoEncontradaError as exc:
        raise HTTPException(status_code=404, detail=_COTIZACION_NO_ENCONTRADA) from exc
    except CotizacionEstadoInvalidoError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except DatosInvalidosError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/cotizaciones/{cotizacion_id}/estado", response_model=Cotizacion)
async def cambiar_estado_cotizacion(cotizacion_id: str, payload: CambioEstadoCotizacion):
    try:
        return await repository.cambiar_estado_cotizacion(_pool(), _parse_id(cotizacion_id), payload.estado)
    except CotizacionNoEncontradaError as exc:
        raise HTTPException(status_code=404, detail=_COTIZACION_NO_ENCONTRADA) from exc
    except CotizacionEstadoInvalidoError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/cotizaciones/{cotizacion_id}/facturada", response_model=Cotizacion)
async def marcar_cotizacion_facturada(cotizacion_id: str, payload: MarcarFacturada):
    try:
        return await repository.marcar_cotizacion_facturada(
            _pool(), _parse_id(cotizacion_id), payload.factura_id
        )
    except CotizacionNoEncontradaError as exc:
        raise HTTPException(status_code=404, detail=_COTIZACION_NO_ENCONTRADA) from exc
    except CotizacionEstadoInvalidoError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except DatosInvalidosError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.delete("/cotizaciones/{cotizacion_id}", status_code=204)
async def eliminar_cotizacion(cotizacion_id: str):
    try:
        await repository.eliminar_cotizacion(_pool(), _parse_id(cotizacion_id))
    except CotizacionNoEncontradaError as exc:
        raise HTTPException(status_code=404, detail=_COTIZACION_NO_ENCONTRADA) from exc
    except CotizacionEstadoInvalidoError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


# --- Cuentas por cobrar y cobros ---------------------------------------------------------

@app.get("/cuentas-por-cobrar", response_model=list[CuentaPorCobrar])
async def listar_cuentas_por_cobrar(
    estado: Literal["pendiente", "vencida", "cobrada", "todas"] | None = None,
    cliente_rnc: str | None = None,
):
    """Sin `estado`: lo que falta por cobrar (pendientes + vencidas)."""
    return await repository.listar_cuentas_por_cobrar(_pool(), estado=estado, cliente_rnc=cliente_rnc)


@app.get("/cobros", response_model=list[Cobro])
async def listar_cobros(
    factura_id: str | None = None,
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    fid = _parse_id(factura_id, nombre_campo="factura_id") if factura_id else None
    return await repository.listar_cobros(_pool(), factura_id=fid, limite=limit, offset=offset)


@app.post("/cobros", response_model=Cobro, status_code=201)
async def registrar_cobro(payload: CobroPayload):
    try:
        return await repository.registrar_cobro(_pool(), **payload.model_dump())
    except DatosInvalidosError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/cobros/{cobro_id}/anular", response_model=Cobro)
async def anular_cobro(cobro_id: str):
    try:
        return await repository.anular_cobro(_pool(), _parse_id(cobro_id))
    except CobroNoEncontradoError as exc:
        raise HTTPException(status_code=404, detail="Cobro no encontrado") from exc
    except CobroEstadoInvalidoError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


# --- Resumen de documentos de venta ---------------------------------------------------------

@app.get("/documentos-venta", response_model=list[DocumentoVenta])
async def listar_documentos_venta(
    tipo_ecf: int | None = None,
    estado: str | None = None,
    q: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    return await repository.listar_documentos_venta(
        _pool(), tipo_ecf=tipo_ecf, estado=estado, q=q, limite=limit, offset=offset
    )
