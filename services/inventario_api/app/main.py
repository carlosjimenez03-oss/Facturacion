"""Servicio de Inventario: catálogo de productos, proveedores, compras
(entrada de mercancía) y kardex de movimientos.

La salida de mercancía por venta NO se registra aquí por iniciativa propia
-- `facturas_api` la dispara vía `POST /movimientos/validar-disponibilidad`
(antes de enviar el e-CF a la DGII, para bloquear la operación si no hay
stock) y `POST /movimientos/salida-por-factura` (después de un envío
exitoso, para descontar el stock realmente). Ver
`services/facturas_api/app/inventario_client.py`.
"""
import asyncio
import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from libs.ecf_common.db import cerrar_pool, crear_pool

from . import repository
from .config import InventarioApiSettings, get_settings
from .repository import (
    CompraEstadoInvalidoError,
    CompraNoEncontradaError,
    ProductoNoEncontradoError,
    ProveedorNoEncontradoError,
)
from .schemas import (
    Compra,
    CompraCrear,
    Movimiento,
    Producto,
    ProductoActualizar,
    ProductoCrear,
    Proveedor,
    ProveedorActualizar,
    ProveedorCrear,
    SalidaPorFacturaRequest,
    SalidaPorFacturaResponse,
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
    app.state.db_pool = pool
    yield
    await cerrar_pool(app.state.db_pool)


app = FastAPI(title=settings.app_name, debug=settings.debug, lifespan=lifespan)
app.state.db_pool = None

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _pool():
    return app.state.db_pool


def _parse_id(id_: str, *, nombre_campo: str = "id") -> uuid.UUID:
    try:
        return uuid.UUID(id_)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"{nombre_campo} inválido") from exc


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
    )


@app.get("/productos", response_model=list[Producto])
async def listar_productos(
    q: str | None = None,
    activo: bool | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    return await repository.listar_productos(_pool(), q=q, activo=activo, limite=limit, offset=offset)


@app.get("/productos/{producto_id}", response_model=Producto)
async def obtener_producto(producto_id: str):
    try:
        return await repository.obtener_producto(_pool(), _parse_id(producto_id))
    except ProductoNoEncontradoError as exc:
        raise HTTPException(status_code=404, detail="Producto no encontrado") from exc


@app.put("/productos/{producto_id}", response_model=Producto)
async def actualizar_producto(producto_id: str, payload: ProductoActualizar):
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
        {"producto_id": item.producto_id, "cantidad": item.cantidad, "costo_unitario": item.costo_unitario}
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
    """Confirma la compra: sube el stock y el costo promedio de cada
    producto involucrado y registra los movimientos de entrada."""
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
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    pid = _parse_id(producto_id, nombre_campo="producto_id") if producto_id else None
    return await repository.listar_movimientos(_pool(), producto_id=pid, tipo=tipo, limite=limit, offset=offset)


# --- Integración con ventas (facturas_api) ---------------------------------------------------------

@app.post("/movimientos/validar-disponibilidad", response_model=ValidarDisponibilidadResponse)
async def validar_disponibilidad(payload: ValidarDisponibilidadRequest):
    try:
        disponible, faltantes = await repository.validar_disponibilidad(
            _pool(), [{"producto_id": item.producto_id, "cantidad": item.cantidad} for item in payload.items]
        )
    except ProductoNoEncontradoError as exc:
        raise HTTPException(status_code=422, detail=f"Producto no encontrado: {exc}") from exc
    return ValidarDisponibilidadResponse(disponible=disponible, faltantes=faltantes)


@app.post("/movimientos/salida-por-factura", response_model=SalidaPorFacturaResponse)
async def salida_por_factura(payload: SalidaPorFacturaRequest):
    movimientos = await repository.registrar_salida_por_factura(
        _pool(),
        payload.factura_id,
        [{"producto_id": item.producto_id, "cantidad": item.cantidad} for item in payload.items],
    )
    return SalidaPorFacturaResponse(ok=True, movimientos=movimientos)
