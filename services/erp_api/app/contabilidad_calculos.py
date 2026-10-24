"""Cálculos puros de Contabilidad (fase 5): catálogo de cuentas sugerido,
cuentas por defecto de los asientos automáticos, armado de los asientos
desde los documentos (e-CF de venta, 606, cobros, pagos, movimientos y
transferencias bancarias) y la depreciación de los activos fijos.

Sin base de datos: `contabilidad_repository.py` lee los documentos, resuelve
los conceptos a cuentas del catálogo y guarda. Ver docs/ERP_API.md,
"Contabilidad", para la tabla de asientos automáticos.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from .formatos import a_decimal, desglose_por_tasa

_CERO = Decimal("0")
_CENTAVO = Decimal("0.01")


def redondear(valor: Decimal) -> Decimal:
    return Decimal(valor).quantize(_CENTAVO, rounding=ROUND_HALF_UP)


# --- Catálogo de cuentas ---------------------------------------------------------

TIPOS_CUENTA = ("activo", "pasivo", "patrimonio", "ingreso", "costo", "gasto")
# Grupo (primer dígito del código) de cada tipo, como en el catálogo sugerido.
GRUPO_TIPO = {"1": "activo", "2": "pasivo", "3": "patrimonio", "4": "ingreso", "5": "costo", "6": "gasto"}
NATURALEZA_POR_TIPO = {
    "activo": "deudora", "pasivo": "acreedora", "patrimonio": "acreedora", "ingreso": "acreedora",
    "costo": "deudora", "gasto": "deudora",
}

# Catálogo sugerido (estilo NIIF para PYMES, agrupado como lo usan las
# PYMES dominicanas): (código, nombre, naturaleza o None = la del tipo,
# acepta movimientos). El tipo sale del primer dígito. Las cuentas
# "contra" (depreciación acumulada, devoluciones en ventas) tienen la
# naturaleza contraria a su grupo.
CATALOGO_SUGERIDO: tuple[tuple[str, str, str | None, bool], ...] = (
    ("1", "Activos", None, False),
    ("1.1", "Activos corrientes", None, False),
    ("1.1.01", "Efectivo y equivalentes de efectivo", None, False),
    ("1.1.01.01", "Caja general", None, True),
    ("1.1.01.02", "Caja chica", None, True),
    ("1.1.01.03", "Bancos", None, True),
    ("1.1.02", "Cuentas por cobrar", None, False),
    ("1.1.02.01", "Cuentas por cobrar clientes", None, True),
    ("1.1.02.02", "Otras cuentas por cobrar", None, True),
    ("1.1.03", "Inventarios", None, False),
    ("1.1.03.01", "Inventario de mercancías", None, True),
    ("1.1.04", "Impuestos y anticipos", None, False),
    ("1.1.04.01", "ITBIS pagado por compensar (adelantado)", None, True),
    ("1.1.04.02", "Anticipos de ISR", None, True),
    ("1.1.04.03", "ISR retenido por terceros", None, True),
    ("1.1.04.04", "ITBIS retenido por terceros", None, True),
    ("1.1.05", "Gastos pagados por anticipado", None, False),
    ("1.1.05.01", "Seguros pagados por anticipado", None, True),
    ("1.2", "Activos no corrientes", None, False),
    ("1.2.01", "Propiedad, planta y equipo", None, False),
    ("1.2.01.01", "Edificaciones (categoría 1)", None, True),
    ("1.2.01.02", "Vehículos, mobiliario y equipos de oficina y cómputo (categoría 2)", None, True),
    ("1.2.01.03", "Maquinaria y otros activos depreciables (categoría 3)", None, True),
    ("1.2.01.04", "Terrenos", None, True),
    ("1.2.02", "Depreciación acumulada", "acreedora", False),
    ("1.2.02.01", "Depreciación acumulada edificaciones", "acreedora", True),
    ("1.2.02.02", "Depreciación acumulada vehículos y equipos", "acreedora", True),
    ("1.2.02.03", "Depreciación acumulada maquinaria y otros", "acreedora", True),
    ("2", "Pasivos", None, False),
    ("2.1", "Pasivos corrientes", None, False),
    ("2.1.01", "Cuentas por pagar", None, False),
    ("2.1.01.01", "Cuentas por pagar proveedores", None, True),
    ("2.1.01.02", "Tarjetas de crédito por pagar", None, True),
    ("2.1.02", "Impuestos por pagar", None, False),
    ("2.1.02.01", "ITBIS por pagar", None, True),
    ("2.1.02.02", "Otros impuestos y propina legal por pagar", None, True),
    ("2.1.02.03", "ISR por pagar", None, True),
    ("2.1.03", "Retenciones por pagar", None, False),
    ("2.1.03.01", "ITBIS retenido por pagar", None, True),
    ("2.1.03.02", "ISR retenido por pagar", None, True),
    ("2.1.04", "Nómina y acumulaciones por pagar", None, False),
    ("2.1.04.01", "Nómina y TSS por pagar", None, True),
    ("2.2", "Pasivos no corrientes", None, False),
    ("2.2.01", "Préstamos bancarios a largo plazo", None, True),
    ("3", "Patrimonio", None, False),
    ("3.1", "Capital", None, False),
    ("3.1.01", "Capital social", None, True),
    ("3.2", "Reservas", None, False),
    ("3.2.01", "Reserva legal", None, True),
    ("3.3", "Resultados", None, False),
    ("3.3.01", "Resultados acumulados", None, True),
    ("3.3.02", "Resultado del ejercicio", None, True),
    ("4", "Ingresos", None, False),
    ("4.1", "Ingresos operacionales", None, False),
    ("4.1.01", "Ventas gravadas", None, True),
    ("4.1.02", "Ventas exentas", None, True),
    ("4.1.03", "Exportaciones", None, True),
    ("4.1.04", "Devoluciones y descuentos en ventas", "deudora", True),
    ("4.2", "Otros ingresos", None, False),
    ("4.2.01", "Ingresos financieros", None, True),
    ("4.2.02", "Otros ingresos", None, True),
    ("5", "Costos", None, False),
    ("5.1", "Costo de ventas", None, False),
    ("5.1.01", "Costo de ventas (compras de mercancías)", None, True),
    ("6", "Gastos", None, False),
    ("6.1", "Gastos operacionales", None, False),
    ("6.1.01", "Gastos de personal", None, True),
    ("6.1.02", "Trabajos, suministros y servicios", None, True),
    ("6.1.03", "Arrendamientos", None, True),
    ("6.1.04", "Gastos de activos fijos", None, True),
    ("6.1.05", "Gastos de representación", None, True),
    ("6.1.06", "Otras deducciones admitidas", None, True),
    ("6.1.07", "Gastos de seguros", None, True),
    ("6.1.08", "Depreciación", None, True),
    ("6.1.09", "Gastos extraordinarios", None, True),
    ("6.1.10", "Diferencias por redondeo", None, True),
    ("6.2", "Gastos financieros", None, False),
    ("6.2.01", "Intereses y gastos financieros", None, True),
    ("6.2.02", "Comisiones y cargos bancarios", None, True),
)

# Cuentas por defecto de los asientos automáticos: concepto -> (grupo de
# la pantalla, etiqueta, código del catálogo sugerido). Se siembran con el
# catálogo; el usuario las cambia en "Cuentas por defecto".
CONCEPTOS: dict[str, tuple[str, str, str]] = {
    # Ventas y cobros
    "caja": ("ventas", "Caja (ventas al contado y cobros en efectivo)", "1.1.01.01"),
    "cxc_clientes": ("ventas", "Cuentas por cobrar clientes", "1.1.02.01"),
    "ventas_gravadas": ("ventas", "Ventas gravadas (ITBIS 18 % / 16 %)", "4.1.01"),
    "ventas_exentas": ("ventas", "Ventas exentas", "4.1.02"),
    "exportaciones": ("ventas", "Exportaciones (tasa 0 %)", "4.1.03"),
    "devoluciones_ventas": ("ventas", "Devoluciones y descuentos en ventas (notas de crédito)", "4.1.04"),
    "itbis_por_pagar": ("ventas", "ITBIS por pagar (facturado)", "2.1.02.01"),
    "otros_impuestos_por_pagar": ("ventas", "Otros impuestos y propina legal de las ventas", "2.1.02.02"),
    "redondeo": ("ventas", "Diferencias por redondeo", "6.1.10"),
    # Bancos
    "banco": ("bancos", "Bancos (cuentas corrientes/ahorro sin cuenta contable propia)", "1.1.01.03"),
    "caja_chica": ("bancos", "Caja chica", "1.1.01.02"),
    "tarjeta_credito": ("bancos", "Tarjetas de crédito por pagar", "2.1.01.02"),
    "gastos_bancarios": ("bancos", "Comisiones y cargos bancarios", "6.2.02"),
    "ingresos_financieros": ("bancos", "Intereses ganados", "4.2.01"),
    # Compras y pagos
    "cxp_proveedores": ("compras", "Cuentas por pagar proveedores", "2.1.01.01"),
    "itbis_adelantado": ("compras", "ITBIS adelantado (pagado por compensar)", "1.1.04.01"),
    "itbis_retenido_por_pagar": ("compras", "ITBIS retenido a proveedores por pagar", "2.1.03.01"),
    "isr_retenido_por_pagar": ("compras", "ISR retenido a proveedores por pagar", "2.1.03.02"),
    "gasto_606_01": ("compras", "606 · 01 Gastos de personal", "6.1.01"),
    "gasto_606_02": ("compras", "606 · 02 Gastos por trabajos, suministros y servicios", "6.1.02"),
    "gasto_606_03": ("compras", "606 · 03 Arrendamientos", "6.1.03"),
    "gasto_606_04": ("compras", "606 · 04 Gastos de activos fijos", "6.1.04"),
    "gasto_606_05": ("compras", "606 · 05 Gastos de representación", "6.1.05"),
    "gasto_606_06": ("compras", "606 · 06 Otras deducciones admitidas", "6.1.06"),
    "gasto_606_07": ("compras", "606 · 07 Gastos financieros", "6.2.01"),
    "gasto_606_08": ("compras", "606 · 08 Gastos extraordinarios", "6.1.09"),
    "gasto_606_09": ("compras", "606 · 09 Compras que forman parte del costo de venta", "5.1.01"),
    "gasto_606_10": ("compras", "606 · 10 Adquisiciones de activos", "1.2.01.03"),
    "gasto_606_11": ("compras", "606 · 11 Gastos de seguros", "6.1.07"),
    # Activos fijos
    "activo_fijo_1": ("activos", "Activo fijo · categoría 1 (edificaciones)", "1.2.01.01"),
    "activo_fijo_2": ("activos", "Activo fijo · categoría 2 (vehículos y equipos de oficina)", "1.2.01.02"),
    "activo_fijo_3": ("activos", "Activo fijo · categoría 3 (otros activos)", "1.2.01.03"),
    "depreciacion_acumulada_1": ("activos", "Depreciación acumulada · categoría 1", "1.2.02.01"),
    "depreciacion_acumulada_2": ("activos", "Depreciación acumulada · categoría 2", "1.2.02.02"),
    "depreciacion_acumulada_3": ("activos", "Depreciación acumulada · categoría 3", "1.2.02.03"),
    "gasto_depreciacion": ("activos", "Gasto de depreciación", "6.1.08"),
}

ORIGENES_AUTOMATICOS = (
    "venta", "nota_credito", "nota_debito", "cobro", "compra", "pago", "movimiento", "transferencia",
)
ORIGENES = ("manual", *ORIGENES_AUTOMATICOS, "depreciacion")


def ddl_semilla_catalogo() -> str:
    """Bloque SQL que siembra el catálogo sugerido y sus cuentas por defecto
    solo si `contabilidad.cuentas` está vacía (idempotente: nunca toca un
    catálogo que el usuario ya tenga). Se genera desde CATALOGO_SUGERIDO y
    CONCEPTOS; db/init/012 tiene el mismo texto (lo verifica
    tests/test_migraciones.py)."""
    def texto(valor: str) -> str:
        return "'" + valor.replace("'", "''") + "'"

    cuentas = ",\n            ".join(
        f"({texto(codigo)}, {texto(nombre)}, {texto(GRUPO_TIPO[codigo[0]])}, "
        f"{texto(naturaleza or NATURALEZA_POR_TIPO[GRUPO_TIPO[codigo[0]]])}, {'true' if acepta else 'false'})"
        for codigo, nombre, naturaleza, acepta in CATALOGO_SUGERIDO
    )
    conceptos = ",\n            ".join(
        f"({texto(concepto)}, {texto(codigo)})" for concepto, (_, _, codigo) in CONCEPTOS.items()
    )
    return f"""DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM contabilidad.cuentas) THEN
        INSERT INTO contabilidad.cuentas (codigo, nombre, tipo, naturaleza, acepta_movimientos, sistema)
        SELECT v.codigo, v.nombre, v.tipo, v.naturaleza, v.acepta, true
        FROM (VALUES
            {cuentas}
        ) AS v (codigo, nombre, tipo, naturaleza, acepta);
        UPDATE contabilidad.cuentas h
        SET padre_id = p.id
        FROM contabilidad.cuentas p
        WHERE p.codigo = regexp_replace(h.codigo, '[.][^.]+$', '') AND h.codigo LIKE '%.%';
        INSERT INTO contabilidad.cuentas_por_defecto (concepto, cuenta_id)
        SELECT v.concepto, c.id
        FROM (VALUES
            {conceptos}
        ) AS v (concepto, codigo)
        JOIN contabilidad.cuentas c ON c.codigo = v.codigo
        ON CONFLICT (concepto) DO NOTHING;
    END IF;
END;
$$;"""


def codigo_padre(codigo: str) -> str | None:
    """"1.1.01" -> "1.1"; "1" -> None."""
    return codigo.rsplit(".", 1)[0] if "." in codigo else None


# --- Asientos propuestos ---------------------------------------------------------

@dataclass
class LineaPropuesta:
    """`clave`: un concepto de CONCEPTOS o el id (uuid) de una cuenta del
    catálogo (la cuenta contable de una cuenta bancaria, la contrapartida de
    un movimiento)."""

    clave: str | uuid.UUID
    debito: Decimal = _CERO
    credito: Decimal = _CERO
    descripcion: str | None = None


@dataclass
class AsientoPropuesto:
    origen: str
    origen_id: str
    fecha: date
    concepto: str
    referencia: str | None
    lineas: list[LineaPropuesta] = field(default_factory=list)

    def debe(self, clave, monto: Decimal, descripcion: str | None = None) -> None:
        self._agregar(clave, redondear(monto), descripcion)

    def haber(self, clave, monto: Decimal, descripcion: str | None = None) -> None:
        self._agregar(clave, -redondear(monto), descripcion)

    def _agregar(self, clave, monto: Decimal, descripcion: str | None) -> None:
        # Monto con signo (+ débito, − crédito): un negativo invierte el lado.
        if monto > 0:
            self.lineas.append(LineaPropuesta(clave, debito=monto, descripcion=descripcion))
        elif monto < 0:
            self.lineas.append(LineaPropuesta(clave, credito=-monto, descripcion=descripcion))


class NoContabilizableError(Exception):
    """El documento no se puede contabilizar automáticamente (el mensaje va
    al reporte de "documentos sin contabilizar")."""


def totales(lineas: list[dict[str, Any]]) -> tuple[Decimal, Decimal]:
    """(total débito, total crédito) de líneas {debito, credito}."""
    debito = sum((Decimal(x["debito"]) for x in lineas), _CERO)
    credito = sum((Decimal(x["credito"]) for x in lineas), _CERO)
    return redondear(debito), redondear(credito)


def huella(lineas: list[dict[str, Any]], fecha: date, concepto: str, referencia: str | None) -> str:
    """Huella de un asiento automático ya resuelto a cuentas: si el documento
    o las cuentas por defecto cambian, cambia y el asiento se regenera."""
    datos = {
        "fecha": fecha.isoformat(),
        "concepto": concepto,
        "referencia": referencia,
        "lineas": sorted(
            (str(x["cuenta_id"]), f"{redondear(x['debito']):.2f}", f"{redondear(x['credito']):.2f}")
            for x in lineas
        ),
    }
    return hashlib.sha256(json.dumps(datos, sort_keys=True).encode()).hexdigest()[:32]


# Ventas (e-CF de facturacion.facturas). Tipos de venta del 607.
NOMBRE_TIPO_ECF = {
    31: "Factura de crédito fiscal", 32: "Factura de consumo", 33: "Nota de débito", 34: "Nota de crédito",
    44: "Régimen especial", 45: "Gubernamental", 46: "Exportación",
}


def _cliente(datos: dict[str, Any]) -> str:
    comprador = (datos.get("Encabezado") or {}).get("Comprador") or {}
    return str(comprador.get("RazonSocialComprador") or comprador.get("RNCComprador") or "consumidor final")


def asiento_venta(
    *,
    factura_id: str,
    tipo_ecf: int,
    encf: str | None,
    fecha: date,
    datos: dict[str, Any],
    a_credito: bool,
) -> AsientoPropuesto:
    """e-CF de venta aceptado. Factura / nota de débito:

        Débito  CxC clientes (a crédito) o Caja (contado) .... MontoTotal
        Crédito Ventas gravadas / exentas / exportaciones ..... montos sin ITBIS por tasa
        Crédito ITBIS por pagar ............................... ITBIS 18 % + 16 %
        Crédito Otros impuestos / Redondeo .................... la diferencia con MontoTotal

    El desglose por tasa es el mismo del IT-1 y del 607
    (`formatos.desglose_por_tasa`), así que el ITBIS por pagar de los
    asientos coincide con el ITBIS facturado del IT-1. Nota de crédito (E34):
    lo mismo al revés, con "Devoluciones y descuentos en ventas" en lugar de
    las ventas; `a_credito` = la factura que modifica fue a crédito (rebaja
    la CxC; si no, se devuelve por caja).
    """
    encabezado = datos.get("Encabezado") or {}
    id_doc = encabezado.get("IdDoc") or {}
    if str(id_doc.get("TipoPago") or "1") == "3":
        raise NoContabilizableError(
            "Venta gratuita (TipoPago 3): no tiene contrapartida automática; regístrela con un asiento manual"
        )
    total = redondear(a_decimal((encabezado.get("Totales") or {}).get("MontoTotal")))
    if total <= 0:
        raise NoContabilizableError("El e-CF no tiene MontoTotal")
    d = desglose_por_tasa(datos)
    gravadas = redondear(d["g18"] + d["g16"])
    exentas = redondear(d["exento"])
    exportaciones = redondear(d["g0"])
    itbis = redondear(d["i18"] + d["i16"])
    diferencia = total - gravadas - exentas - exportaciones - itbis
    nombre = NOMBRE_TIPO_ECF.get(tipo_ecf, "e-CF")
    es_nota_credito = tipo_ecf == 34
    origen = "nota_credito" if es_nota_credito else "nota_debito" if tipo_ecf == 33 else "venta"
    asiento = AsientoPropuesto(
        origen=origen,
        origen_id=factura_id,
        fecha=fecha,
        concepto=f"{nombre} {encf or ''} · {_cliente(datos)}".replace("  ", " "),
        referencia=encf,
    )
    signo = Decimal(-1) if es_nota_credito else Decimal(1)
    contrapartida = "cxc_clientes" if a_credito else "caja"
    asiento.debe(contrapartida, signo * total)
    if es_nota_credito:
        asiento.debe("devoluciones_ventas", gravadas + exentas + exportaciones)
    else:
        asiento.haber("ventas_gravadas", gravadas)
        asiento.haber("ventas_exentas", exentas)
        asiento.haber("exportaciones", exportaciones)
    asiento.haber("itbis_por_pagar", signo * itbis)
    # Lo que no es base ni ITBIS (impuesto selectivo, otros impuestos,
    # propina legal) va a "otros impuestos"; centavos sueltos, a redondeo.
    clave_diferencia = "otros_impuestos_por_pagar" if abs(diferencia) > 1 else "redondeo"
    asiento.haber(clave_diferencia, signo * diferencia)
    return asiento


def asiento_cobro(
    *, cobro_id: str, fecha: date, monto: Decimal, encf: str | None, cliente: str, cuenta_banco: str | uuid.UUID
) -> AsientoPropuesto:
    """Débito Banco/Caja · Crédito CxC clientes."""
    asiento = AsientoPropuesto(
        origen="cobro", origen_id=cobro_id, fecha=fecha,
        concepto=f"Cobro {encf or ''} · {cliente}".replace("  ", " ").strip(" ·"), referencia=encf,
    )
    asiento.debe(cuenta_banco, monto)
    asiento.haber("cxc_clientes", monto)
    return asiento


def concepto_gasto_606(tipo_bienes_servicios: int | None) -> str:
    if tipo_bienes_servicios is None:
        raise NoContabilizableError(
            "Sin 'Tipo de bienes y servicios' en el 606: clasifíquelo para saber a qué gasto va"
        )
    return f"gasto_606_{tipo_bienes_servicios:02d}"


def asiento_compra(
    *,
    registro_id: str,
    fecha: date,
    ncf: str,
    proveedor: str,
    tipo_bienes_servicios: int | None,
    total: Decimal,
    itbis_facturado: Decimal,
    itbis_costo: Decimal,
    es_nota_credito: bool,
    pagada_al_contado: bool = False,
) -> AsientoPropuesto:
    """Factura de compra (renglón del 606):

        Débito  Gasto / costo / activo (según el tipo de bienes y servicios) .. total − ITBIS adelantado
        Débito  ITBIS adelantado ............................................ ITBIS facturado − llevado al costo
        Crédito Cuentas por pagar proveedores (o Caja si es un gasto menor E43) total por pagar

    El ITBIS llevado al costo (campo 15 del 606), el selectivo, otros
    impuestos y la propina son parte del gasto. Una nota de crédito del
    proveedor (B04/E34) va al revés.
    """
    asiento = AsientoPropuesto(
        origen="compra", origen_id=registro_id, fecha=fecha,
        concepto=f"{'Nota de crédito de compra' if es_nota_credito else 'Compra'} {ncf} · {proveedor}",
        referencia=ncf,
    )
    if total <= 0:
        raise NoContabilizableError("La compra no tiene montos en el 606")
    gasto = concepto_gasto_606(tipo_bienes_servicios)
    adelantado = redondear(itbis_facturado - itbis_costo)
    signo = Decimal(-1) if es_nota_credito else Decimal(1)
    asiento.debe(gasto, signo * (total - adelantado))
    asiento.debe("itbis_adelantado", signo * adelantado)
    asiento.haber("caja_chica" if pagada_al_contado else "cxp_proveedores", signo * total)
    return asiento


def asiento_pago(
    *,
    pago_id: str,
    fecha: date,
    ncf: str | None,
    proveedor: str,
    monto: Decimal,
    retencion_isr: Decimal,
    retencion_itbis: Decimal,
    cuenta_banco: str | uuid.UUID,
) -> AsientoPropuesto:
    """Débito CxP (lo pagado + las retenciones) · Crédito Banco/Caja (lo
    pagado) · Crédito ITBIS / ISR retenido por pagar."""
    asiento = AsientoPropuesto(
        origen="pago", origen_id=pago_id, fecha=fecha,
        concepto=f"Pago a proveedor {ncf or ''} · {proveedor}".replace("  ", " "), referencia=ncf,
    )
    asiento.debe("cxp_proveedores", monto + retencion_isr + retencion_itbis)
    asiento.haber(cuenta_banco, monto)
    asiento.haber("itbis_retenido_por_pagar", retencion_itbis)
    asiento.haber("isr_retenido_por_pagar", retencion_isr)
    return asiento


# Movimientos bancarios manuales: entradas suman al banco, salidas restan.
TIPOS_MOVIMIENTO_ENTRADA = ("deposito", "interes")
TIPOS_MOVIMIENTO_SALIDA = ("retiro", "cargo_bancario", "comision")
TIPOS_MOVIMIENTO = TIPOS_MOVIMIENTO_ENTRADA + TIPOS_MOVIMIENTO_SALIDA
# Contrapartida por defecto de cada tipo (si el movimiento no trae una).
CONTRAPARTIDA_MOVIMIENTO = {
    "deposito": "caja", "retiro": "caja", "interes": "ingresos_financieros",
    "cargo_bancario": "gastos_bancarios", "comision": "gastos_bancarios",
}
NOMBRE_MOVIMIENTO = {
    "deposito": "Depósito", "retiro": "Retiro", "interes": "Intereses ganados", "cargo_bancario": "Cargo bancario",
    "comision": "Comisión bancaria", "transferencia_entrada": "Transferencia recibida",
    "transferencia_salida": "Transferencia enviada", "cobro": "Cobro", "pago": "Pago a proveedor",
}


def asiento_movimiento(
    *,
    movimiento_id: str,
    fecha: date,
    tipo: str,
    monto_dop: Decimal,
    descripcion: str | None,
    referencia: str | None,
    cuenta_banco: str | uuid.UUID,
    contrapartida: str | uuid.UUID | None,
) -> AsientoPropuesto:
    """Depósito/interés: Débito Banco · Crédito contrapartida (Caja / Ingresos
    financieros). Retiro/cargo/comisión: Débito contrapartida (Caja / Gastos
    bancarios) · Crédito Banco."""
    asiento = AsientoPropuesto(
        origen="movimiento", origen_id=movimiento_id, fecha=fecha,
        concepto=descripcion or NOMBRE_MOVIMIENTO.get(tipo, tipo), referencia=referencia,
    )
    otra = contrapartida or CONTRAPARTIDA_MOVIMIENTO[tipo]
    signo = Decimal(1) if tipo in TIPOS_MOVIMIENTO_ENTRADA else Decimal(-1)
    asiento.debe(cuenta_banco, signo * monto_dop)
    asiento.haber(otra, signo * monto_dop)
    return asiento


def asiento_transferencia(
    *,
    transferencia_id: str,
    fecha: date,
    monto_dop: Decimal,
    concepto: str | None,
    referencia: str | None,
    cuenta_origen: str | uuid.UUID,
    cuenta_destino: str | uuid.UUID,
) -> AsientoPropuesto:
    """Débito cuenta destino · Crédito cuenta origen."""
    asiento = AsientoPropuesto(
        origen="transferencia", origen_id=transferencia_id, fecha=fecha,
        concepto=concepto or "Transferencia entre cuentas", referencia=referencia,
    )
    asiento.debe(cuenta_destino, monto_dop)
    asiento.haber(cuenta_origen, monto_dop)
    return asiento


# --- Activos fijos y depreciación ---------------------------------------------------------

# Art. 287, literal e) del Código Tributario (Ley 11-92): tasa anual de
# depreciación de cada categoría de bienes depreciables. Categoría 1:
# edificaciones y sus componentes estructurales; 2: automóviles y camiones
# livianos de uso común, equipos y muebles de oficina, computadoras y equipos
# de procesamiento de datos; 3: cualquier otra propiedad depreciable.
TASAS_CATEGORIA = {1: Decimal("0.05"), 2: Decimal("0.25"), 3: Decimal("0.15")}
NOMBRE_CATEGORIA = {
    1: "Categoría 1 · edificaciones",
    2: "Categoría 2 · vehículos livianos, mobiliario y equipos de oficina y cómputo",
    3: "Categoría 3 · otros bienes depreciables",
}
METODOS_DEPRECIACION = ("linea_recta", "dgii")


def periodo_de(fecha: date) -> str:
    return f"{fecha.year:04d}{fecha.month:02d}"


def siguiente_periodo(periodo: str) -> str:
    anio, mes = int(periodo[:4]), int(periodo[4:])
    return f"{anio + 1:04d}01" if mes == 12 else f"{anio:04d}{mes + 1:02d}"


def ultimo_dia(periodo: str) -> date:
    anio, mes = int(periodo[:4]), int(periodo[4:])
    siguiente = date(anio + 1, 1, 1) if mes == 12 else date(anio, mes + 1, 1)
    return date.fromordinal(siguiente.toordinal() - 1)


@dataclass
class Activo:
    categoria: int
    metodo: str
    costo: Decimal
    valor_residual: Decimal
    vida_util_meses: int | None
    depreciar_desde: str               # AAAAMM: primer mes que se deprecia
    depreciacion_acumulada_inicial: Decimal = _CERO
    baja_periodo: str | None = None    # AAAAMM de la baja: ese mes ya no se deprecia


def _cuotas_linea_recta(activo: Activo, hasta: str) -> dict[str, Decimal]:
    """(costo − residual − acumulada inicial) / vida útil, por mes; el último
    mes ajusta los centavos para no pasar del valor depreciable."""
    if not activo.vida_util_meses:
        raise ValueError("La línea recta requiere la vida útil en meses")
    base = redondear(activo.costo - activo.valor_residual - activo.depreciacion_acumulada_inicial)
    cuota = redondear(base / activo.vida_util_meses) if base > 0 else _CERO
    cuotas: dict[str, Decimal] = {}
    acumulado = _CERO
    periodo = activo.depreciar_desde
    for numero in range(1, activo.vida_util_meses + 1):
        if periodo > hasta or (activo.baja_periodo and periodo >= activo.baja_periodo) or acumulado >= base:
            break
        monto = base - acumulado if numero == activo.vida_util_meses else min(cuota, base - acumulado)
        cuotas[periodo] = monto
        acumulado += monto
        periodo = siguiente_periodo(periodo)
    return cuotas


def _cuotas_dgii(activo: Activo, hasta: str) -> dict[str, Decimal]:
    """Método fiscal del Art. 287 literal e): categoría 1 sobre el costo
    (5 % anual, en línea recta); categorías 2 y 3 por saldo decreciente: la
    tasa se aplica al valor fiscal pendiente al inicio de cada año (o al
    empezar a depreciarse). Por mes = cuota anual / 12; el primer año solo
    cuentan los meses en uso."""
    tasa = TASAS_CATEGORIA[activo.categoria]
    pendiente = redondear(activo.costo - activo.depreciacion_acumulada_inicial)
    cuotas: dict[str, Decimal] = {}
    periodo = activo.depreciar_desde
    anual = _CERO
    anio_base = None
    while periodo <= hasta and pendiente > 0:
        if activo.baja_periodo and periodo >= activo.baja_periodo:
            break
        if anio_base != periodo[:4]:
            anio_base = periodo[:4]
            base = activo.costo if activo.categoria == 1 else pendiente
            anual = redondear(base * tasa)
        monto = min(redondear(anual / 12), pendiente)
        if monto <= 0:
            break
        cuotas[periodo] = monto
        pendiente -= monto
        periodo = siguiente_periodo(periodo)
    return cuotas


def cuotas_depreciacion(activo: Activo, hasta: str, *, metodo: str | None = None) -> dict[str, Decimal]:
    """Depreciación de cada mes (AAAAMM -> monto) desde `depreciar_desde`
    hasta `hasta` (incluido), con el método del activo o el indicado. Sale
    de los datos del activo (sin estado): siempre da lo mismo."""
    if (metodo or activo.metodo) == "linea_recta":
        return _cuotas_linea_recta(activo, hasta)
    return _cuotas_dgii(activo, hasta)


def depreciacion_del_anio(activo: Activo, anio: int, *, metodo: str | None = None) -> Decimal:
    cuotas = cuotas_depreciacion(activo, f"{anio:04d}12", metodo=metodo)
    return sum((monto for periodo, monto in cuotas.items() if periodo[:4] == f"{anio:04d}"), _CERO)


def asiento_depreciacion(periodo: str, por_categoria: dict[int, Decimal], nombre_mes: str) -> AsientoPropuesto:
    """Débito Gasto de depreciación · Crédito Depreciación acumulada de cada
    categoría."""
    asiento = AsientoPropuesto(
        origen="depreciacion", origen_id=periodo, fecha=ultimo_dia(periodo),
        concepto=f"Depreciación de activos fijos · {nombre_mes}", referencia=f"DEP-{periodo}",
    )
    for categoria in sorted(por_categoria):
        monto = por_categoria[categoria]
        asiento.debe("gasto_depreciacion", monto, NOMBRE_CATEGORIA[categoria])
        asiento.haber(f"depreciacion_acumulada_{categoria}", monto)
    return asiento
