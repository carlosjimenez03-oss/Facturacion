"""Datos de prueba de la Fase 6 · Operación (tareas, sincronización y
reportes) para un Postgres real con db/init cargado: ventas, compras, cobros,
pagos, inventario, bandeja y auditoría de la DGII de octubre/septiembre 2026,
con totales calculables a mano (ver los comentarios y
test_operacion_postgres.py). "Hoy" es el 24/10/2026.

Lo usan test_operacion_postgres.py y el e2e del frontend
(scratchpad/fe-operacion), por eso vive aparte."""
from __future__ import annotations

import json
import uuid
from datetime import date
from decimal import Decimal

HOY = date(2026, 10, 24)
NUESTRO_RNC = "131897542"

# ids fijos para que las pruebas los referencien
F1, F2, F3, F4, F5, F6, F7, F8, F9, F10 = (uuid.UUID(int=i) for i in range(1, 11))
P1, P2, P3, P4 = (uuid.UUID(int=100 + i) for i in range(1, 5))
R1, R2, R3, R4 = (uuid.UUID(int=200 + i) for i in range(1, 5))
L1, L0 = uuid.UUID(int=301), uuid.UUID(int=300)
ECF_PEND, ECF_FALLIDA, ECF_ATASCADA, ECF_ENTREGA = (uuid.UUID(int=400 + i) for i in range(1, 5))
COT_POR_VENCER, COT_LEJOS = uuid.UUID(int=501), uuid.UUID(int=502)

_TASA = {"1": Decimal("0.18"), "2": Decimal("0.16"), "3": Decimal("0"), "4": Decimal("0")}


def datos_ecf(tipo, encf, fecha, items, *, rnc=None, nombre=None, credito=False, limite=None, ncf_modificado=None,
              itbis_retenido=None):
    """JSON de un e-CF como lo arma el frontend (buildDatos.ts): líneas +
    MontoGravadoTotal / MontoExento / TotalITBIS globales."""
    lineas, gravado, exento, itbis = [], Decimal("0"), Decimal("0"), Decimal("0")
    for i, (nombre_item, cantidad, precio, indicador) in enumerate(items, start=1):
        monto = Decimal(cantidad) * Decimal(precio)
        if indicador == "4":
            exento += monto
        else:
            gravado += monto
            itbis += (monto * _TASA[indicador]).quantize(Decimal("0.01"))
        lineas.append({
            "NumeroLinea": str(i), "IndicadorFacturacion": indicador, "NombreItem": nombre_item,
            "CantidadItem": str(cantidad), "PrecioUnitarioItem": f"{Decimal(precio):.2f}", "MontoItem": f"{monto:.2f}",
        })
    id_doc = {"TipoeCF": str(tipo), "eNCF": encf, "TipoPago": "2" if credito else "1"}
    if limite:
        id_doc["FechaLimitePago"] = limite
    totales = {
        "MontoGravadoTotal": f"{gravado:.2f}", "MontoExento": f"{exento:.2f}", "TotalITBIS": f"{itbis:.2f}",
        "MontoTotal": f"{gravado + exento + itbis:.2f}",
    }
    if itbis_retenido:
        totales["TotalITBISRetenido"] = itbis_retenido
    datos = {
        "Encabezado": {
            "IdDoc": id_doc,
            "Emisor": {"RNCEmisor": NUESTRO_RNC, "RazonSocialEmisor": "Distribuidora Caribe Verde, SRL", "FechaEmision": fecha},
            "Comprador": {"RNCComprador": rnc or "", "RazonSocialComprador": nombre or ""},
            "Totales": totales,
        },
        "DetallesItems": {"Item": lineas},
    }
    if ncf_modificado:
        datos["InformacionReferencia"] = {"NCFModificado": ncf_modificado, "CodigoModificacion": "3"}
    return datos


COSECHA = ("131234567", "Supermercado La Cosecha, SRL")
HOTEL = ("101010101", "Hotel Brisas de Bávaro, SA")
HISPANIOLA = ("130000001", "Constructora Hispaniola, SRL")

FACTURAS = [
    # id, tipo, encf, estado, datos, extra
    (F1, 31, "E310000000001", "aceptado", datos_ecf(
        31, "E310000000001", "05-10-2026", [("Cemento gris 42.5 kg", 10, 500, "1"), ("Arroz selecto", 2, 1000, "4")],
        rnc=COSECHA[0], nombre=COSECHA[1], credito=True, limite="04-11-2026")),       # 7,900.00
    (F2, 32, "E320000000001", "aceptado", datos_ecf(
        32, "E320000000001", "10-10-2026", [("Cemento gris 42.5 kg", 4, 500, "1")])),   # 2,360.00
    (F3, 31, "E310000000002", "aceptado", datos_ecf(
        31, "E310000000002", "15-09-2026", [("Varilla corrugada 3/8", 100, 250, "1")],
        rnc=COSECHA[0], nombre=COSECHA[1], credito=True, limite="30-09-2026")),       # 29,500.00
    (F4, 34, "E340000000001", "aceptado", datos_ecf(
        34, "E340000000001", "20-10-2026", [("Cemento gris 42.5 kg", 1, 500, "1")],
        rnc=COSECHA[0], nombre=COSECHA[1], ncf_modificado="E310000000001")),          # 590.00 (resta)
    (F5, 31, "E310000000003", "aceptado", datos_ecf(
        31, "E310000000003", "12-10-2026", [("Servicio de transporte", 1, 10000, "2")],
        rnc=HOTEL[0], nombre=HOTEL[1], itbis_retenido="480.00")),                     # 11,600.00 (16 %)
    (F6, 31, "E310000000004", "rechazado", datos_ecf(
        31, "E310000000004", "21-10-2026", [("Servicio de transporte", 1, 5000, "1")], rnc=HOTEL[0], nombre=HOTEL[1])),
    (F7, 32, "E320000000002", "enviado", datos_ecf(
        32, "E320000000002", "22-10-2026", [("Cemento gris 42.5 kg", 2, 500, "1")])),   # 1,180.00
    (F8, 31, "E310000000005", "error", datos_ecf(
        31, "E310000000005", "23-10-2026", [("Varilla corrugada 3/8", 10, 250, "1")],
        rnc=HISPANIOLA[0], nombre=HISPANIOLA[1])),                                     # 2,950.00
    (F9, 32, "E320000000003", "aceptado", datos_ecf(
        32, "E320000000003", "15-08-2026", [("Arroz selecto", 1, 1000, "4")])),
    (F10, 31, "E310000000006", "aceptado", datos_ecf(
        31, "E310000000006", "15-06-2026", [("Varilla corrugada 3/8", 12, 250, "1")],
        rnc=HISPANIOLA[0], nombre=HISPANIOLA[1], credito=True, limite="15-07-2026")),  # 3,540.00
]


async def sembrar(conn) -> None:
    """Carga todo (una base recién creada con db/init y las migraciones de erp-api)."""
    for fid, tipo, encf, estado, datos, in FACTURAS:
        await conn.execute(
            """
            INSERT INTO facturacion.facturas (id, tipo_ecf, ambiente, rnc_emisor, encf, estado, datos, xml_generado,
                                              track_id, dgii_estado, dgii_respuesta, error, actualizado_en)
            VALUES ($1, $2, 'test', $3, $4, $5, $6::json, '<ECF/>', $7, $8, $9::jsonb, $10, now())
            """,
            fid, tipo, NUESTRO_RNC, encf, estado, json.dumps(datos),
            None if estado == "error" else f"track-{encf}",
            {"aceptado": "Aceptado", "rechazado": "Rechazado", "enviado": "En Proceso"}.get(estado),
            json.dumps({"mensajes": [{"codigo": "2", "valor": "RNC del comprador no válido"}]})
            if estado == "rechazado" else None,
            "No se pudo contactar recepcion: timeout" if estado == "error" else None,
        )
    # F7 sigue 'enviado' desde hace 3 h, y su salida de inventario falló.
    await conn.execute(
        """
        UPDATE facturacion.facturas
        SET actualizado_en = now() - interval '3 hours', items_inventario = $2::jsonb,
            inventario_sincronizado = false, inventario_error = 'No se pudo contactar inventario-api'
        WHERE id = $1
        """,
        F7, json.dumps([{"numero_linea": 1, "producto_id": str(P1), "cantidad": "2"}]),
    )
    await conn.execute("UPDATE facturacion.facturas SET inventario_sincronizado = true WHERE id <> $1", F7)

    # Cobros: F3 9,500 (transferencia), F1 1,000 (efectivo) y uno anulado que no cuenta.
    await conn.executemany(
        "INSERT INTO ventas.cobros (factura_id, fecha, monto, metodo, anulado) VALUES ($1, $2, $3, $4, $5)",
        [
            (F3, date(2026, 10, 10), Decimal("9500"), "transferencia", False),
            (F1, date(2026, 10, 22), Decimal("1000"), "efectivo", False),
            (F1, date(2026, 10, 23), Decimal("500"), "efectivo", True),
        ],
    )

    # Contactos y cotizaciones.
    cliente = await conn.fetchval(
        "INSERT INTO ventas.clientes (tipo, tipo_identificacion, rnc, nombre) VALUES ('cliente', 'rnc', $1, $2) RETURNING id",
        *COSECHA,
    )
    await conn.executemany(
        """
        INSERT INTO ventas.cotizaciones (id, numero, cliente_id, cliente_nombre, cliente_rnc, fecha, vence, total)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        """,
        [
            (COT_POR_VENCER, "COT-0001", cliente, COSECHA[1], COSECHA[0], date(2026, 10, 11), date(2026, 10, 26), Decimal("15000")),
            (COT_LEJOS, "COT-0002", cliente, COSECHA[1], COSECHA[0], date(2026, 10, 20), date(2026, 11, 30), Decimal("8000")),
        ],
    )

    # 606: R1 Cementos (B01, por vencer 28/10), R2 Juan Pérez (servicios con retenciones, pagado),
    # R3 Cementos de septiembre (vencida, ITBIS al costo), R4 nota de crédito de R1.
    registros = [
        (R1, "202610", "131111111", 1, 9, "B0100000101", None, "20261005", "20261015", 0, 20000, 3600, 0, 0, None, 0),
        (R2, "202610", "40212345678", 2, 2, "B0100000202", None, "20261008", "20261020", 10000, 0, 1800, 1800, 0, 2, 1000),
        (R3, "202609", "131111111", 1, 9, "B0100000099", None, "20260910", None, 0, 5000, 900, 0, 900, None, 0),
        (R4, "202610", "131111111", 1, 9, "B0400000005", "B0100000101", "20261012", None, 0, 1000, 180, 0, 0, None, 0),
    ]
    await conn.executemany(
        """
        INSERT INTO compras606.registros (id, periodo, origen, rnc_cedula, tipo_id, tipo_bienes_servicios, ncf,
            ncf_modificado, fecha_comprobante, fecha_pago, monto_servicios, monto_bienes, itbis_facturado,
            itbis_retenido, itbis_costo, tipo_retencion_isr, monto_retencion_renta, forma_pago)
        VALUES ($1, $2, 'compras', $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, 2)
        """,
        registros,
    )
    await conn.executemany(
        "INSERT INTO compras.facturas (registro606_id, proveedor_nombre, vence) VALUES ($1, $2, $3)",
        [
            (R1, "Cementos del Atlántico, SA", date(2026, 10, 28)),
            (R2, "Juan Pérez", date(2026, 11, 7)),
            (R3, "Cementos del Atlántico, SA", date(2026, 9, 25)),
            (R4, "Cementos del Atlántico, SA", None),
        ],
    )
    await conn.execute(
        """
        INSERT INTO compras.pagos (registro606_id, fecha, monto, metodo, tipo_retencion_isr, retencion_isr, retencion_itbis)
        VALUES ($1, '2026-10-20', 9000, 'transferencia', 2, 1000, 1800)
        """,
        R2,
    )

    # Inventario.
    principal = await conn.fetchval("SELECT id FROM inventario.almacenes WHERE es_principal")
    sucursal = await conn.fetchval(
        "INSERT INTO inventario.almacenes (nombre) VALUES ('Sucursal Santiago') RETURNING id"
    )
    await conn.executemany(
        """
        INSERT INTO inventario.productos (id, sku, nombre, tipo, categoria, precio_venta, costo_promedio, stock_actual,
                                          stock_minimo, maneja_lotes, unidad_medida)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, 'UND')
        """,
        [
            (P1, "CEM-425", "Cemento gris 42.5 kg", "bien", "Construcción", 500, 350, 50, 60, False),
            (P2, "VAR-038", "Varilla corrugada 3/8", "bien", "Construcción", 250, 180, 100, 0, False),
            (P3, "PIN-001", "Pintura acrílica blanca", "bien", "Pinturas", 1200, 800, 10, 0, True),
            (P4, "SRV-TRA", "Servicio de transporte", "servicio", None, 10000, 0, 0, 0, False),
        ],
    )
    await conn.executemany(
        "INSERT INTO inventario.existencias (producto_id, almacen_id, cantidad) VALUES ($1, $2, $3)",
        [(P1, principal, 50), (P2, principal, 80), (P2, sucursal, 20), (P3, principal, 10)],
    )
    await conn.executemany(
        "INSERT INTO inventario.lotes (id, producto_id, numero, fecha_vencimiento) VALUES ($1, $2, $3, $4)",
        [(L1, P3, "L-001", date(2026, 11, 10)), (L0, P3, "L-000", date(2026, 10, 1))],
    )
    await conn.executemany(
        "INSERT INTO inventario.existencias_lote (lote_id, almacen_id, cantidad) VALUES ($1, $2, $3)",
        [(L1, principal, 10), (L0, principal, 0)],
    )
    movimientos = [
        # producto, tipo, cantidad, costo, referencia_tipo, referencia_id, fecha (hora de RD)
        (P1, "entrada", 40, 350, "compra", None, "2026-09-01 10:00"),
        (P1, "entrada", 30, 350, "compra", None, "2026-10-02 10:00"),
        (P1, "salida", 10, 350, "factura", str(F1), "2026-10-05 11:00"),
        (P1, "salida", 4, 350, "factura", str(F2), "2026-10-10 11:00"),
        (P1, "salida", 6, 350, "ajuste", None, "2026-10-15 09:00"),
        (P2, "entrada", 200, 180, "compra", None, "2026-08-01 10:00"),
        (P2, "salida", 100, 180, "factura", str(F3), "2026-09-15 12:00"),
        (P2, "salida", 20, 180, "transferencia", None, "2026-10-18 08:00"),
        (P2, "entrada", 20, 180, "transferencia", None, "2026-10-18 08:00"),
        (P3, "entrada", 10, 800, "lote", None, "2026-10-03 10:00"),
    ]
    await conn.executemany(
        """
        INSERT INTO inventario.movimientos (producto_id, tipo, cantidad, costo_unitario, referencia_tipo,
                                            referencia_id, almacen_id, creado_en)
        VALUES ($1, $2, $3, $4, $5, $6, $7, ($8::text::timestamp AT TIME ZONE 'America/Santo_Domingo'))
        """,
        [(*m[:6], principal, m[6]) for m in movimientos],
    )

    # Bandeja de entrada: pendiente, aprobación fallida, envío atascado y entrega al proveedor fallida.
    await conn.executemany(
        """
        INSERT INTO recepcion.ecf_recibidos (id, rnc_emisor, razon_social_emisor, rnc_comprador, encf, tipo_ecf,
            fecha_emision, monto_total, itbis_total, xml, estado_aprobacion, decision_en_envio, envio_iniciado_en,
            error_aprobacion, aprobado_en, acecf_xml, enviado_al_emisor, error_envio_emisor)
        VALUES ($1, $2, $3, $4, $5, 31, '2026-10-20', $6, $7, '<ECF/>', $8, $9, $10, $11, $12, $13, false, $14)
        """,
        [
            (ECF_PEND, "132222222", "Pinturas Tropical, SRL", NUESTRO_RNC, "E310000009001", Decimal("5900"),
             Decimal("900"), "pendiente", None, None, None, None, None, None),
            (ECF_FALLIDA, "132222222", "Pinturas Tropical, SRL", NUESTRO_RNC, "E310000009002", Decimal("1180"),
             Decimal("180"), "pendiente", None, None, "La DGII no respondió (timeout)", None, None, None),
            (ECF_ATASCADA, "133333333", "Aceros del Yaque, SRL", NUESTRO_RNC, "E310000009003", Decimal("2360"),
             Decimal("360"), "enviando", "aprobado", None, None, None, None, None),
            (ECF_ENTREGA, "133333333", "Aceros del Yaque, SRL", NUESTRO_RNC, "E310000009004", Decimal("4720"),
             Decimal("720"), "aprobado", None, None, None, None, "<ACECF>firmado</ACECF>",
             "El proveedor no publica urlAceptacion"),
        ],
    )
    await conn.execute(
        "UPDATE recepcion.ecf_recibidos SET envio_iniciado_en = now() - interval '1 hour' WHERE id = $1", ECF_ATASCADA
    )
    await conn.execute("UPDATE recepcion.ecf_recibidos SET aprobado_en = now() - interval '2 days' WHERE id = $1", ECF_ENTREGA)

    # Declaraciones presentadas hasta agosto 2026 (y el IR-2 2025): quedan las de septiembre (vencidas)
    # y las de octubre (por vencer).
    marcas = [(f, f"2025{m:02d}") for m in range(1, 13) for f in ("606", "it1", "ir17")]
    marcas += [(f, f"2026{m:02d}") for m in range(1, 9) for f in ("606", "it1", "ir17")]
    marcas.append(("ir2", "2025"))
    await conn.executemany(
        "INSERT INTO impuestos.declaraciones_presentadas (formulario, periodo, presentada_en) VALUES ($1, $2, '2026-01-01')",
        marcas,
    )

    # Auditoría de la DGII: envíos y consultas de los últimos días.
    eventos = [
        ("recepcion", "E310000000003", "track-E310000000003", None, None, None, "3 days"),
        ("consulta_resultado", "E310000000003", "track-E310000000003", "Aceptado", "1", None, "3 days"),
        ("recepcion", "E310000000004", "track-E310000000004", None, None, None, "2 days"),
        ("consulta_resultado", "E310000000004", "track-E310000000004", "Rechazado", "2", None, "2 days"),
        ("recepcion", "E320000000002", "track-E320000000002", None, None, None, "3 hours"),
        ("consulta_resultado", "E320000000002", "track-E320000000002", "En Proceso", "3", None, "170 minutes"),
        ("recepcion", "E310000000005", None, None, None, "Timeout al contactar la DGII", "40 minutes"),
    ]
    await conn.executemany(
        """
        INSERT INTO facturacion.envios_dgii (modulo, ambiente, rnc_emisor, encf, track_id, estado, codigo, error, creado_en)
        VALUES ($1, 'test', $8, $2, $3, $4, $5, $6, now() - $7::text::interval)
        """,
        [(*e, NUESTRO_RNC) for e in eventos],
    )

    # Una factura en papel sin su e-CF de reemplazo.
    await conn.execute(
        """
        INSERT INTO operacion.facturas_papel (ncf, fecha, cliente_rnc, cliente_nombre, gravado_18, itbis_18, total)
        VALUES ('B0100004521', '2026-10-23', '131234567', 'Supermercado La Cosecha, SRL', 23500, 4230, 27730)
        """
    )
