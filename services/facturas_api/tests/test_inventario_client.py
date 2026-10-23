"""Lo que facturas_api manda a inventario_api por cada línea vinculada al
catálogo (ver app/inventario_client.py)."""
import uuid

from services.facturas_api.app import inventario_client


def test_items_venta_incluye_linea_almacen_y_lote_cuando_vienen():
    producto_id, almacen_id, lote_id = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())

    items = inventario_client._items_venta(
        [
            {"numero_linea": "1", "producto_id": producto_id, "cantidad": "2", "almacen_id": almacen_id,
             "lote_id": lote_id},
            {"numero_linea": "2", "producto_id": producto_id, "cantidad": "1", "almacen_id": None, "lote_id": None},
            {"producto_id": producto_id, "cantidad": "3"},  # factura guardada antes de la Fase 4
        ]
    )

    assert items == [
        {"producto_id": producto_id, "cantidad": "2", "numero_linea": "1", "almacen_id": almacen_id,
         "lote_id": lote_id},
        {"producto_id": producto_id, "cantidad": "1", "numero_linea": "2"},
        {"producto_id": producto_id, "cantidad": "3"},
    ]
