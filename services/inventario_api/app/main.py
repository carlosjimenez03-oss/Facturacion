"""Servicio de Inventario: catálogo de productos, proveedores, compras
(entrada de mercancía), kardex de movimientos y, desde la Fase 4,
almacenes (existencia por almacén y transferencias), lotes con vencimiento
(FEFO y alertas), ajustes de inventario, Kardex valorizado y valuación.
Ver docs/INVENTARIO.md.

La salida de mercancía por venta NO se registra aquí por iniciativa propia
-- `facturas_api` la dispara vía `POST /movimientos/validar-disponibilidad`
(antes de enviar el e-CF a la DGII, para bloquear la operación si no hay
stock) y `POST /movimientos/salida-por-factura` (después de un envío
exitoso, para descontar el stock realmente). Ver
`services/facturas_api/app/inventario_client.py`.

Al arrancar aplica sus propias migraciones (`migraciones.py`): en Supabase
`db/init` no se ejecuta solo.
"""
import asyncio
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import date
from decimal import Decimal
from typing import Literal

import asyncpg
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

from libs.ecf_common.auth.sesion_supabase import instalar_verificacion_sesion
from libs.ecf_common.db import cerrar_pool, crear_pool

from . import ajustes_repository, almacenes_repository, kardex, lotes_repository, repository
from .config import InventarioApiSettings, get_settings
from .errores import (
    AjusteNoEncontradoError,
    AlmacenNoEncontradoError,
    LoteNoEncontradoError,
    OperacionInvalidaError,
    StockInsuficienteError,
    TransferenciaNoEncontradaError,
)
from .migraciones import aplicar_migraciones
from .repository import (
    CompraEstadoInvalidoError,
    CompraNoEncontradaError,
    ProductoNoEncontradoError,
    ProveedorNoEncontradoError,
)
from .schemas import (
    Ajuste,
    AjusteCrear,
    AlertasLotes,
    Almacen,
    AlmacenActualizar,
    AlmacenCrear,
    Compra,
    CompraCrear,
    ExistenciaAlmacen,
    ExistenciasProducto,
    Kardex,
    Lote,
    LoteActualizar,
    LoteCrear,
    Movimiento,
    Producto,
    ProductoActualizar,
    ProductoCrear,
    Proveedor,
    ProveedorActualizar,
    ProveedorCrear,
    SalidaPorFacturaRequest,
    SalidaPorFacturaResponse,
    SugerenciaFefo,
    Transferencia,
    TransferenciaCrear,
    Valuacion,
    ValidarDisponibilidadRequest,
    ValidarDisponibilidadResponse,
)

settings = get_settings()
logging.basicConfig(level=settings.log_level)
logger = logging.getLogger(__name__)


_POOL_REINTENTOS = 10
_POOL_ESPERA_SEGUNDOS = 3


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Mismo patrón de reintento con espera que facturas_api: `depends_on:
    # condition: service_healthy` solo se respeta en el `up` inicial de
    # Docker Compose.
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
            "inventario_api requiere DATABASE_URL configurado y una base de datos "
            "alcanzable -- el catálogo y el kardex viven únicamente en Postgres."
        )
    await aplicar_migraciones(pool)
    app.state.db_pool = pool
    yield
    await cerrar_pool(app.state.db_pool)


app = FastAPI(title=settings.app_name, debug=settings.debug, lifespan=lifespan)
app.state.db_pool = None

# REQUIRE_AUTH=true: exige la sesión de Supabase Auth (ver docs/AUTH.md).
instalar_verificacion_sesion(app, settings)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Errores de dominio que pueden salir de cualquier endpoint (un almacén o
# lote referenciado en el cuerpo, una salida sin existencia...). Los
# endpoints con el id en la ruta siguen atrapando su "no encontrado" para
# responder 404 con su propio mensaje.
@app.exception_handler(StockInsuficienteError)
async def _stock_insuficiente(request: Request, exc: StockInsuficienteError):
    return JSONResponse(status_code=409, content={"detail": str(exc), "faltante": exc.como_dict()})


@app.exception_handler(OperacionInvalidaError)
async def _operacion_invalida(request: Request, exc: OperacionInvalidaError):
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.exception_handler(asyncpg.UniqueViolationError)
async def _duplicado(request: Request, exc: asyncpg.UniqueViolationError):
    return JSONResponse(
        status_code=409, content={"detail": f"Ya existe un registro con ese valor ({exc.constraint_name})"}
    )


@app.exception_handler(ProductoNoEncontradoError)
async def _producto_no_encontrado(request: Request, exc: ProductoNoEncontradoError):
    return JSONResponse(status_code=422, content={"detail": f"Producto no encontrado: {exc}"})


@app.exception_handler(AlmacenNoEncontradoError)
async def _almacen_no_encontrado(request: Request, exc: AlmacenNoEncontradoError):
    return JSONResponse(status_code=404, content={"detail": f"Almacén no encontrado: {exc}"})


@app.exception_handler(LoteNoEncontradoError)
async def _lote_no_encontrado(request: Request, exc: LoteNoEncontradoError):
    return JSONResponse(status_code=404, content={"detail": f"Lote no encontrado: {exc}"})


def _pool():
    return app.state.db_pool


def _parse_id(id_: str, *, nombre_campo: str = "id") -> uuid.UUID:
    try:
        return uuid.UUID(id_)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"{nombre_campo} inválido") from exc


def _parse_id_opcional(id_: str | None, *, nombre_campo: str) -> uuid.UUID | None:
    return _parse_id(id_, nombre_campo=nombre_campo) if id_ else None


@app.get("/health")
def health(settings: InventarioApiSettings = Depends(get_settings)) -> dict:
    return {"status": "ok", "app_env": settings.app_env}


# --- Productos ---------------------------------------------------------

@app.post("/productos", response_model=Producto, status_code=201)
async def crear_producto(payload: ProductoCrear):
    return await repository.crear_producto(
        _pool(),
        sku=payload.sku,
        nombre=payload.nombre,
        descripcion=payload.descripcion,
        unidad_medida=payload.unidad_medida,
        precio_venta=payload.precio_venta,
        stock_inicial=payload.stock_inicial,
        tipo=payload.tipo,
        tasa_itbis=payload.tasa_itbis,
        codigo_barras=payload.codigo_barras,
        categoria=payload.categoria,
        stock_minimo=payload.stock_minimo,
        maneja_lotes=payload.maneja_lotes,
        costo_unitario_inicial=payload.costo_unitario_inicial,
        almacen_id=payload.almacen_id,
    )


@app.get("/productos", response_model=list[Producto])
async def listar_productos(
    q: str | None = None,
    activo: bool | None = None,
    categoria: str | None = None,
    tipo: Literal["bien", "servicio"] | None = None,
    alerta_stock: bool | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    return await repository.listar_productos(
        _pool(),
        q=q,
        activo=activo,
        categoria=categoria,
        tipo=tipo,
        alerta_stock=alerta_stock,
        limite=limit,
        offset=offset,
    )


@app.get("/productos/categorias", response_model=list[str])
async def listar_categorias():
    return await repository.listar_categorias(_pool())


@app.get("/productos/{producto_id}", response_model=Producto)
async def obtener_producto(producto_id: str):
    try:
        return await repository.obtener_producto(_pool(), _parse_id(producto_id))
    except ProductoNoEncontradoError as exc:
        raise HTTPException(status_code=404, detail="Producto no encontrado") from exc


@app.put("/productos/{producto_id}", response_model=Producto)
async def actualizar_producto(producto_id: str, payload: ProductoActualizar):
    opcionales = {
        campo: getattr(payload, campo)
        for campo in repository.CAMPOS_PRODUCTO_OPCIONALES
        if campo in payload.model_fields_set
    }
    for campo in ("tipo", "tasa_itbis", "stock_minimo", "maneja_lotes"):
        if campo in opcionales and opcionales[campo] is None:
            raise HTTPException(status_code=422, detail=f"{campo} no puede ser null")
    try:
        return await repository.actualizar_producto(
            _pool(),
            _parse_id(producto_id),
            sku=payload.sku,
            nombre=payload.nombre,
            descripcion=payload.descripcion,
            unidad_medida=payload.unidad_medida,
            precio_venta=payload.precio_venta,
            activo=payload.activo,
            **opcionales,
        )
    except ProductoNoEncontradoError as exc:
        raise HTTPException(status_code=404, detail="Producto no encontrado") from exc


@app.get("/productos/{producto_id}/existencias", response_model=ExistenciasProducto)
async def existencias_producto(producto_id: str):
    try:
        return await almacenes_repository.existencias_producto(
            _pool(), _parse_id(producto_id), dias_alerta=settings.dias_alerta_vencimiento
        )
    except ProductoNoEncontradoError as exc:
        raise HTTPException(status_code=404, detail="Producto no encontrado") from exc


# --- Proveedores ---------------------------------------------------------

@app.post("/proveedores", response_model=Proveedor, status_code=201)
async def crear_proveedor(payload: ProveedorCrear):
    return await repository.crear_proveedor(
        _pool(),
        nombre=payload.nombre,
        rnc=payload.rnc,
        telefono=payload.telefono,
        correo=payload.correo,
        direccion=payload.direccion,
    )


@app.get("/proveedores", response_model=list[Proveedor])
async def listar_proveedores(
    activo: bool | None = None, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)
):
    return await repository.listar_proveedores(_pool(), activo=activo, limite=limit, offset=offset)


@app.get("/proveedores/{proveedor_id}", response_model=Proveedor)
async def obtener_proveedor(proveedor_id: str):
    try:
        return await repository.obtener_proveedor(_pool(), _parse_id(proveedor_id))
    except ProveedorNoEncontradoError as exc:
        raise HTTPException(status_code=404, detail="Proveedor no encontrado") from exc


@app.put("/proveedores/{proveedor_id}", response_model=Proveedor)
async def actualizar_proveedor(proveedor_id: str, payload: ProveedorActualizar):
    try:
        return await repository.actualizar_proveedor(
            _pool(),
            _parse_id(proveedor_id),
            nombre=payload.nombre,
            rnc=payload.rnc,
            telefono=payload.telefono,
            correo=payload.correo,
            direccion=payload.direccion,
            activo=payload.activo,
        )
    except ProveedorNoEncontradoError as exc:
        raise HTTPException(status_code=404, detail="Proveedor no encontrado") from exc


# --- Compras (entrada de mercancía) ---------------------------------------------------------

def _items_dict(payload: CompraCrear) -> list[dict]:
    return [
        {
            "producto_id": item.producto_id,
            "cantidad": item.cantidad,
            "costo_unitario": item.costo_unitario,
            "lote_numero": item.lote_numero,
            "lote_vencimiento": item.lote_vencimiento,
        }
        for item in payload.items
    ]


@app.post("/compras", response_model=Compra, status_code=201)
async def crear_compra(payload: CompraCrear):
    return await repository.crear_compra(
        _pool(),
        proveedor_id=payload.proveedor_id,
        numero_documento=payload.numero_documento,
        notas=payload.notas,
        items=_items_dict(payload),
        almacen_id=payload.almacen_id,
    )


@app.get("/compras", response_model=list[Compra])
async def listar_compras(
    estado: str | None = None, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)
):
    return await repository.listar_compras(_pool(), estado=estado, limite=limit, offset=offset)


@app.get("/compras/{compra_id}", response_model=Compra)
async def obtener_compra(compra_id: str):
    try:
        return await repository.obtener_compra(_pool(), _parse_id(compra_id))
    except CompraNoEncontradaError as exc:
        raise HTTPException(status_code=404, detail="Compra no encontrada") from exc


@app.put("/compras/{compra_id}", response_model=Compra)
async def actualizar_compra(compra_id: str, payload: CompraCrear):
    try:
        return await repository.actualizar_compra(
            _pool(),
            _parse_id(compra_id),
            proveedor_id=payload.proveedor_id,
            numero_documento=payload.numero_documento,
            notas=payload.notas,
            items=_items_dict(payload),
            almacen_id=payload.almacen_id,
        )
    except CompraNoEncontradaError as exc:
        raise HTTPException(status_code=404, detail="Compra no encontrada") from exc
    except CompraEstadoInvalidoError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.delete("/compras/{compra_id}", status_code=204)
async def eliminar_compra(compra_id: str):
    try:
        await repository.eliminar_compra(_pool(), _parse_id(compra_id))
    except CompraNoEncontradaError as exc:
        raise HTTPException(
            status_code=404, detail="Compra no encontrada (o no está en estado 'borrador')"
        ) from exc


@app.post("/compras/{compra_id}/confirmar", response_model=Compra)
async def confirmar_compra(compra_id: str):
    """Confirma la compra: ingresa cada línea al almacén de la compra (y a
    su lote), sube el stock y el costo promedio de cada producto y registra
    los movimientos de entrada."""
    try:
        return await repository.confirmar_compra(_pool(), _parse_id(compra_id))
    except CompraNoEncontradaError as exc:
        raise HTTPException(status_code=404, detail="Compra no encontrada") from exc
    except CompraEstadoInvalidoError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ProductoNoEncontradoError as exc:
        raise HTTPException(status_code=422, detail=f"Producto no encontrado: {exc}") from exc


# --- Movimientos (kardex) ---------------------------------------------------------

@app.get("/movimientos", response_model=list[Movimiento])
async def listar_movimientos(
    producto_id: str | None = None,
    tipo: str | None = None,
    almacen_id: str | None = None,
    lote_id: str | None = None,
    referencia_tipo: str | None = None,
    referencia_id: str | None = None,
    desde: date | None = None,
    hasta: date | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    return await repository.listar_movimientos(
        _pool(),
        producto_id=_parse_id_opcional(producto_id, nombre_campo="producto_id"),
        tipo=tipo,
        almacen_id=_parse_id_opcional(almacen_id, nombre_campo="almacen_id"),
        lote_id=_parse_id_opcional(lote_id, nombre_campo="lote_id"),
        referencia_tipo=referencia_tipo,
        referencia_id=referencia_id,
        desde=desde,
        hasta=hasta,
        limite=limit,
        offset=offset,
    )


@app.get("/kardex", response_model=Kardex)
async def obtener_kardex(
    producto_id: str,
    almacen_id: str | None = None,
    desde: date | None = None,
    hasta: date | None = None,
    metodo: Literal["promedio", "peps"] = "promedio",
    tipo: Literal["entrada", "salida"] | None = None,
    referencia_tipo: str | None = None,
    formato: Literal["json", "csv"] = "json",
):
    """Kardex valorizado del producto (en un almacén o en todos). Con
    `formato=csv` devuelve el mismo contenido como archivo CSV para Excel."""
    pid = _parse_id(producto_id, nombre_campo="producto_id")
    try:
        resultado = await kardex.obtener_kardex(
            _pool(),
            producto_id=pid,
            almacen_id=_parse_id_opcional(almacen_id, nombre_campo="almacen_id"),
            desde=desde,
            hasta=hasta,
            metodo=metodo,
            tipo=tipo,
            referencia_tipo=referencia_tipo,
        )
    except ProductoNoEncontradoError as exc:
        raise HTTPException(status_code=404, detail="Producto no encontrado") from exc
    if formato == "csv":
        nombre = f"kardex_{resultado['producto_sku']}_{metodo}.csv"
        return Response(
            content=kardex.kardex_csv(resultado),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{nombre}"'},
        )
    return resultado


# --- Almacenes y transferencias ---------------------------------------------------------

@app.get("/almacenes", response_model=list[Almacen])
async def listar_almacenes(activo: bool | None = None):
    return await almacenes_repository.listar_almacenes(_pool(), activo=activo)


@app.post("/almacenes", response_model=Almacen, status_code=201)
async def crear_almacen(payload: AlmacenCrear):
    return await almacenes_repository.crear_almacen(
        _pool(), nombre=payload.nombre, direccion=payload.direccion, permite_negativo=payload.permite_negativo
    )


@app.get("/almacenes/{almacen_id}", response_model=Almacen)
async def obtener_almacen(almacen_id: str):
    return await almacenes_repository.obtener_almacen(_pool(), _parse_id(almacen_id))


@app.put("/almacenes/{almacen_id}", response_model=Almacen)
async def actualizar_almacen(almacen_id: str, payload: AlmacenActualizar):
    return await almacenes_repository.actualizar_almacen(
        _pool(),
        _parse_id(almacen_id),
        nombre=payload.nombre,
        direccion=payload.direccion,
        permite_negativo=payload.permite_negativo,
        activo=payload.activo,
        es_principal=payload.es_principal,
    )


@app.get("/almacenes/{almacen_id}/existencias", response_model=list[ExistenciaAlmacen])
async def existencias_almacen(almacen_id: str, q: str | None = None, incluir_cero: bool = False):
    return await almacenes_repository.existencias_almacen(
        _pool(), _parse_id(almacen_id), q=q, incluir_cero=incluir_cero
    )


@app.post("/transferencias", response_model=Transferencia, status_code=201)
async def crear_transferencia(payload: TransferenciaCrear):
    """Transfiere y aplica en el acto (todo o nada). 409 con `faltante` si
    alguna línea no tiene existencia en el origen."""
    return await almacenes_repository.crear_transferencia(
        _pool(),
        almacen_origen_id=payload.almacen_origen_id,
        almacen_destino_id=payload.almacen_destino_id,
        notas=payload.notas,
        items=[item.model_dump() for item in payload.items],
    )


@app.get("/transferencias", response_model=list[Transferencia])
async def listar_transferencias(
    almacen_id: str | None = None, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)
):
    return await almacenes_repository.listar_transferencias(
        _pool(), almacen_id=_parse_id_opcional(almacen_id, nombre_campo="almacen_id"), limite=limit, offset=offset
    )


@app.get("/transferencias/{transferencia_id}", response_model=Transferencia)
async def obtener_transferencia(transferencia_id: str):
    try:
        return await almacenes_repository.obtener_transferencia(_pool(), _parse_id(transferencia_id))
    except TransferenciaNoEncontradaError as exc:
        raise HTTPException(status_code=404, detail="Transferencia no encontrada") from exc


@app.get("/valuacion", response_model=Valuacion)
async def valuacion():
    return await almacenes_repository.valuacion(_pool())


# --- Lotes ---------------------------------------------------------

@app.get("/lotes", response_model=list[Lote])
async def listar_lotes(
    producto_id: str | None = None,
    almacen_id: str | None = None,
    estado: Literal["vigente", "por_vencer", "vencido", "bloqueado"] | None = None,
    q: str | None = None,
    con_existencia: bool | None = None,
    dias: int | None = Query(None, ge=0, le=3650),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    return await lotes_repository.listar_lotes(
        _pool(),
        dias_alerta=dias if dias is not None else settings.dias_alerta_vencimiento,
        producto_id=_parse_id_opcional(producto_id, nombre_campo="producto_id"),
        almacen_id=_parse_id_opcional(almacen_id, nombre_campo="almacen_id"),
        estado=estado,
        q=q,
        con_existencia=con_existencia,
        limite=limit,
        offset=offset,
    )


@app.get("/lotes/alertas", response_model=AlertasLotes)
async def alertas_lotes(dias: int | None = Query(None, ge=0, le=3650)):
    return await lotes_repository.alertas_lotes(
        _pool(), dias=dias if dias is not None else settings.dias_alerta_vencimiento
    )


@app.get("/lotes/fefo", response_model=SugerenciaFefo)
async def sugerir_fefo(producto_id: str, cantidad: Decimal = Query(..., gt=0), almacen_id: str | None = None):
    return await lotes_repository.sugerir_fefo(
        _pool(),
        producto_id=_parse_id(producto_id, nombre_campo="producto_id"),
        almacen_id=_parse_id_opcional(almacen_id, nombre_campo="almacen_id"),
        cantidad=cantidad,
    )


@app.post("/lotes", response_model=Lote, status_code=201)
async def crear_lote(payload: LoteCrear):
    return await lotes_repository.crear_lote(
        _pool(),
        producto_id=payload.producto_id,
        numero=payload.numero,
        fecha_vencimiento=payload.fecha_vencimiento,
        notas=payload.notas,
        dias_alerta=settings.dias_alerta_vencimiento,
        cantidad_inicial=payload.cantidad_inicial,
        almacen_id=payload.almacen_id,
        costo_unitario=payload.costo_unitario,
    )


@app.get("/lotes/{lote_id}", response_model=Lote)
async def obtener_lote(lote_id: str):
    return await lotes_repository.obtener_lote(
        _pool(), _parse_id(lote_id), dias_alerta=settings.dias_alerta_vencimiento
    )


@app.put("/lotes/{lote_id}", response_model=Lote)
async def actualizar_lote(lote_id: str, payload: LoteActualizar):
    return await lotes_repository.actualizar_lote(
        _pool(),
        _parse_id(lote_id),
        numero=payload.numero,
        fecha_vencimiento=payload.fecha_vencimiento,
        bloqueado=payload.bloqueado,
        notas=payload.notas,
        dias_alerta=settings.dias_alerta_vencimiento,
    )


# --- Ajustes de inventario ---------------------------------------------------------

def _ajuste_kwargs(payload: AjusteCrear) -> dict:
    return {
        "almacen_id": payload.almacen_id,
        "motivo": payload.motivo,
        "fecha": payload.fecha,
        "notas": payload.notas,
        "items": [item.model_dump() for item in payload.items],
    }


@app.get("/ajustes", response_model=list[Ajuste])
async def listar_ajustes(
    estado: Literal["borrador", "aplicado", "anulado"] | None = None,
    almacen_id: str | None = None,
    producto_id: str | None = None,
    desde: date | None = None,
    hasta: date | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    return await ajustes_repository.listar_ajustes(
        _pool(),
        estado=estado,
        almacen_id=_parse_id_opcional(almacen_id, nombre_campo="almacen_id"),
        producto_id=_parse_id_opcional(producto_id, nombre_campo="producto_id"),
        desde=desde,
        hasta=hasta,
        limite=limit,
        offset=offset,
    )


@app.post("/ajustes", response_model=Ajuste, status_code=201)
async def crear_ajuste(payload: AjusteCrear, aplicar: bool = False):
    """Crea el ajuste en 'borrador'; con `?aplicar=true` lo aplica en la
    misma llamada (si no se puede aplicar, no se crea)."""
    return await ajustes_repository.crear_ajuste(_pool(), aplicar=aplicar, **_ajuste_kwargs(payload))


@app.get("/ajustes/{ajuste_id}", response_model=Ajuste)
async def obtener_ajuste(ajuste_id: str):
    try:
        return await ajustes_repository.obtener_ajuste(_pool(), _parse_id(ajuste_id))
    except AjusteNoEncontradoError as exc:
        raise HTTPException(status_code=404, detail="Ajuste no encontrado") from exc


@app.put("/ajustes/{ajuste_id}", response_model=Ajuste)
async def actualizar_ajuste(ajuste_id: str, payload: AjusteCrear):
    try:
        return await ajustes_repository.actualizar_ajuste(_pool(), _parse_id(ajuste_id), **_ajuste_kwargs(payload))
    except AjusteNoEncontradoError as exc:
        raise HTTPException(status_code=404, detail="Ajuste no encontrado") from exc


@app.delete("/ajustes/{ajuste_id}", status_code=204)
async def eliminar_ajuste(ajuste_id: str):
    try:
        await ajustes_repository.eliminar_ajuste(_pool(), _parse_id(ajuste_id))
    except AjusteNoEncontradoError as exc:
        raise HTTPException(status_code=404, detail="Ajuste no encontrado") from exc


@app.post("/ajustes/{ajuste_id}/aplicar", response_model=Ajuste)
async def aplicar_ajuste(ajuste_id: str):
    try:
        return await ajustes_repository.aplicar_ajuste(_pool(), _parse_id(ajuste_id))
    except AjusteNoEncontradoError as exc:
        raise HTTPException(status_code=404, detail="Ajuste no encontrado") from exc


@app.post("/ajustes/{ajuste_id}/anular", response_model=Ajuste)
async def anular_ajuste(ajuste_id: str):
    try:
        return await ajustes_repository.anular_ajuste(_pool(), _parse_id(ajuste_id))
    except AjusteNoEncontradoError as exc:
        raise HTTPException(status_code=404, detail="Ajuste no encontrado") from exc


# --- Integración con ventas (facturas_api) ---------------------------------------------------------

def _items_venta(items) -> list[dict]:
    return [item.model_dump() for item in items]


@app.post("/movimientos/validar-disponibilidad", response_model=ValidarDisponibilidadResponse)
async def validar_disponibilidad(payload: ValidarDisponibilidadRequest):
    try:
        disponible, faltantes = await repository.validar_disponibilidad(_pool(), _items_venta(payload.items))
    except ProductoNoEncontradoError as exc:
        raise HTTPException(status_code=422, detail=f"Producto no encontrado: {exc}") from exc
    except (AlmacenNoEncontradoError, LoteNoEncontradoError) as exc:
        raise HTTPException(status_code=422, detail=str(exc) or exc.__class__.__name__) from exc
    return ValidarDisponibilidadResponse(disponible=disponible, faltantes=faltantes)


@app.post("/movimientos/salida-por-factura", response_model=SalidaPorFacturaResponse)
async def salida_por_factura(payload: SalidaPorFacturaRequest):
    movimientos = await repository.registrar_salida_por_factura(
        _pool(), payload.factura_id, _items_venta(payload.items), documento=payload.documento
    )
    return SalidaPorFacturaResponse(ok=True, movimientos=movimientos)
