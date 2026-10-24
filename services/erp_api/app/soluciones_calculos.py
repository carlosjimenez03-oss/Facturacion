"""Reglas puras (sin base de datos) de Soluciones (fase 6): precios y límites
de los planes, estado de la prueba gratis, tokens del portal de clientes,
plantillas de Kontara Ventas y cuadre de caja del POS. Las usa
`soluciones_repository.py`; viven aparte para probarlas sin Postgres.
"""
from __future__ import annotations

import hashlib
import re
import secrets
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import qrcode
import qrcode.image.svg
from lxml import etree

from libs.ecf_common.environments import DGIIEnvironment
from libs.ecf_common.timbre_qr import construir_url_timbre_ecf

from .repository import DatosInvalidosError

_CENTAVO = Decimal("0.01")
TASA_ITBIS = Decimal("0.18")

DIAS_PRUEBA = 15
DIAS_PRUEBA_VENTAS = 30
DIAS_VALIDEZ_ENLACE = 90

_GB = 1024**3


def redondear(valor: Decimal) -> Decimal:
    return Decimal(valor).quantize(_CENTAVO, rounding=ROUND_HALF_UP)


# --- Planes y suscripción ---------------------------------------------------------

# Precios de lista mensuales (RD$, sin ITBIS). Anual = 10 meses (2 gratis).
# Límites: None = ilimitado.
PLANES: dict[str, dict[str, Any]] = {
    "emprendedor": {
        "nombre": "Emprendedor",
        "descripcion": "Para negocios que inician con e-CF",
        "precio_mensual": Decimal("1950.00"),
        "ecf_mes": 200,
        "usuarios": 1,
        "almacenamiento_bytes": 2 * _GB,
        "popular": False,
        "caracteristicas": ["200 e-CF al mes", "1 usuario", "Facturación, compras y contactos", "Formatos 606, 607 y 608"],
    },
    "pyme": {
        "nombre": "Pyme",
        "descripcion": "Para empresas en crecimiento",
        "precio_mensual": Decimal("4450.00"),
        "ecf_mes": 2000,
        "usuarios": 5,
        "almacenamiento_bytes": 10 * _GB,
        "popular": True,
        "caracteristicas": [
            "2,000 e-CF al mes",
            "5 usuarios",
            "Inventario con Kardex valorizado",
            "Retenciones, IT-1 e IR-2",
            "Envío por WhatsApp y correo",
        ],
    },
    "empresarial": {
        "nombre": "Empresarial",
        "descripcion": "Para alto volumen y varias sucursales",
        "precio_mensual": Decimal("9850.00"),
        "ecf_mes": None,
        "usuarios": None,
        "almacenamiento_bytes": 100 * _GB,
        "popular": False,
        "caracteristicas": [
            "e-CF ilimitados",
            "Usuarios ilimitados",
            "Contingencia con firma local",
            "Multiempresa y API",
            "Soporte prioritario",
        ],
    },
}

# Durante la prueba gratis se usan los límites del plan Pyme.
PLAN_DE_PRUEBA = "pyme"


def precio_plan(plan: str, periodicidad: str) -> dict[str, Decimal]:
    if plan not in PLANES:
        raise DatosInvalidosError(f"Plan desconocido: {plan}")
    if periodicidad not in ("mensual", "anual"):
        raise DatosInvalidosError("La periodicidad debe ser mensual o anual")
    mensual = PLANES[plan]["precio_mensual"]
    subtotal = mensual * 10 if periodicidad == "anual" else mensual
    itbis = redondear(subtotal * TASA_ITBIS)
    return {"subtotal": redondear(subtotal), "itbis": itbis, "total": redondear(subtotal + itbis)}


def estado_suscripcion(fila: dict[str, Any], hoy: date) -> dict[str, Any]:
    """`fila` = soluciones.suscripcion. Días restantes cuenta hoy (el día 15
    de la prueba quedan 1); 0 = la prueba terminó."""
    plan = fila.get("plan")
    dias_restantes = max((fila["prueba_fin"] - hoy).days + 1, 0)
    if plan:
        estado = "activa"
    elif dias_restantes > 0:
        estado = "prueba"
    else:
        estado = "prueba_vencida"
    plan_limites = plan or PLAN_DE_PRUEBA
    return {
        "plan": plan,
        "plan_nombre": PLANES[plan]["nombre"] if plan else None,
        "periodicidad": fila.get("periodicidad"),
        "estado": estado,
        "prueba_inicio": fila["prueba_inicio"],
        "prueba_fin": fila["prueba_fin"],
        "dias_restantes": dias_restantes if not plan else None,
        "proxima_factura": fila.get("proxima_factura"),
        "plan_limites": plan_limites,
    }


def porcentaje_uso(uso: int | None, limite: int | None) -> int | None:
    """Porcentaje entero (tope 100) para la barra de consumo; None si no hay
    límite o no se conoce el uso."""
    if uso is None or not limite:
        return None
    return min(round(uso * 100 / limite), 100)


def formato_bytes(n: int) -> str:
    if n >= _GB:
        return f"{n / _GB:.1f} GB"
    if n >= 1024**2:
        return f"{n / 1024**2:.1f} MB"
    if n >= 1024:
        return f"{n / 1024:.0f} KB"
    return f"{n} B"


# --- Portal de clientes ---------------------------------------------------------

METODOS_PAGO_PORTAL = ("tarjeta", "transferencia", "pago_movil", "deposito")


def generar_token() -> tuple[str, str]:
    """Token del enlace (URL-safe, 43 caracteres) y su hash, que es lo único
    que se guarda."""
    token = secrets.token_urlsafe(32)
    return token, hash_token(token)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def token_con_forma_valida(token: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9_-]{32,64}", token or ""))


# --- Kontara Ventas ---------------------------------------------------------

# Configuración inicial de cada automatización. `dias`: motivar = sin
# comprar desde hace N días (y menos que reactivación); cobranza = N días
# antes del vencimiento; fidelización = e-CF aceptados en los últimos N
# días (desde que se activó); reactivación = sin comprar desde hace más de
# N días.
AUTOMATIZACIONES: dict[str, dict[str, Any]] = {
    "motivar": {
        "categoria": "Motivar ventas",
        "titulo": "Presenta tus productos y servicios",
        "descripcion": "Envía tu catálogo por categorías a clientes que no compran hace 30 días.",
        "dias": 30,
        "asunto": "Novedades de {empresa}",
        "plantilla": (
            "Hola, {cliente}. En {empresa} tenemos lo que necesitas: {categorias}. "
            "Escríbenos y con gusto te preparamos una cotización."
        ),
        "variables": ["cliente", "empresa", "categorias", "dias_sin_comprar"],
    },
    "cobranza": {
        "categoria": "Cobranza",
        "titulo": "Recordatorio de pago amable",
        "descripcion": "Avisa 3 días antes del vencimiento de cada factura a crédito.",
        "dias": 3,
        "asunto": "Recordatorio: factura {encf} vence el {vence}",
        "plantilla": (
            "Saludos, {cliente}. Le recordamos amablemente que la factura {encf} tiene un saldo pendiente "
            "de RD$ {monto}, con vencimiento el {vence}. Si ya realizó el pago, por favor ignore este "
            "mensaje. ¡Gracias por su preferencia! {empresa}"
        ),
        "variables": ["cliente", "empresa", "encf", "monto", "vence"],
    },
    "fidelizacion": {
        "categoria": "Fidelización",
        "titulo": "Gracias por tu compra",
        "descripcion": "Mensaje de agradecimiento con la representación impresa del e-CF aceptado.",
        "dias": 2,
        "asunto": "Gracias por su compra · {encf}",
        "plantilla": (
            "¡Gracias por su compra, {cliente}! La DGII aceptó su comprobante {encf} por RD$ {total}. "
            "Le adjuntamos la representación impresa. {empresa}"
        ),
        "variables": ["cliente", "empresa", "encf", "total"],
    },
    "reactivacion": {
        "categoria": "Reactivación",
        "titulo": "Te extrañamos",
        "descripcion": "Oferta especial para clientes inactivos por más de 90 días.",
        "dias": 90,
        "asunto": "¡Te extrañamos en {empresa}!",
        "plantilla": (
            "Hola, {cliente}. Hace {dias_sin_comprar} días que no nos visitas y te extrañamos. "
            "Escríbenos y te preparamos una oferta especial. {empresa}"
        ),
        "variables": ["cliente", "empresa", "dias_sin_comprar"],
    },
}


def renderizar_plantilla(plantilla: str, variables: dict[str, Any]) -> str:
    """Sustituye {variable} por su valor; una llave desconocida se deja tal
    cual (no rompe el envío por un error de tipeo en la plantilla)."""
    def _valor(m: re.Match[str]) -> str:
        clave = m.group(1)
        return str(variables[clave]) if clave in variables and variables[clave] is not None else m.group(0)

    return re.sub(r"\{([a-z_]+)\}", _valor, plantilla)


def telefono_whatsapp(telefono: str | None) -> str | None:
    """Número en formato internacional sin '+' (wa.me / WhatsApp Cloud API).
    Los 10 dígitos de RD (809/829/849) llevan el código de país 1."""
    digitos = re.sub(r"\D", "", telefono or "")
    if len(digitos) == 10 and digitos[:3] in ("809", "829", "849"):
        return "1" + digitos
    if len(digitos) == 11 and digitos.startswith("1"):
        return digitos
    return digitos if len(digitos) >= 11 else None


def correo_valido(correo: str | None) -> bool:
    return bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", (correo or "").strip()))


def canales_de(canal: str) -> list[str]:
    return ["whatsapp", "correo"] if canal == "ambos" else [canal]


# --- POS ---------------------------------------------------------

METODOS_POS = ("efectivo", "tarjeta", "transferencia")


def validar_pagos(total: Decimal, pagos: list[dict[str, Any]]) -> dict[str, Decimal]:
    """Pagos de una venta de contado: cubren el total; el cambio solo sale
    del efectivo (tarjeta y transferencia no pueden pasarse del total).
    Devuelve lo recibido y el cambio."""
    total = redondear(total)
    if total <= 0:
        raise DatosInvalidosError("El total de la venta debe ser mayor que cero")
    if not pagos:
        raise DatosInvalidosError("Indique al menos una forma de pago")
    efectivo = Decimal("0")
    otros = Decimal("0")
    for pago in pagos:
        if pago["metodo"] not in METODOS_POS:
            raise DatosInvalidosError(f"Forma de pago desconocida: {pago['metodo']}")
        monto = redondear(Decimal(pago["monto"]))
        if monto <= 0:
            raise DatosInvalidosError("Cada pago debe ser mayor que cero")
        if pago["metodo"] == "efectivo":
            efectivo += monto
        else:
            otros += monto
    if otros > total:
        raise DatosInvalidosError("Tarjeta y transferencia no pueden pasar del total: el cambio solo se da en efectivo")
    recibido = efectivo + otros
    if recibido < total:
        raise DatosInvalidosError(f"Faltan RD$ {redondear(total - recibido)} para completar el pago")
    return {"recibido": redondear(recibido), "cambio": redondear(recibido - total)}


def esperado_en_caja(fondo_inicial: Decimal, ventas: list[dict[str, Any]]) -> dict[str, Decimal]:
    """Lo que debe haber al cerrar, por método: el efectivo incluye el fondo
    y descuenta el cambio entregado."""
    esperado = {m: Decimal("0") for m in METODOS_POS}
    esperado["efectivo"] = Decimal(fondo_inicial)
    for venta in ventas:
        for pago in venta["pagos"]:
            esperado[pago["metodo"]] += Decimal(str(pago["monto"]))
        esperado["efectivo"] -= Decimal(str(venta.get("cambio") or 0))
    return {m: redondear(v) for m, v in esperado.items()}


def diferencias_arqueo(esperado: dict[str, Decimal], contado: dict[str, Decimal]) -> dict[str, Decimal]:
    """Contado - esperado por método (negativo = faltante)."""
    return {m: redondear(Decimal(contado.get(m) or 0) - Decimal(esperado.get(m) or 0)) for m in METODOS_POS}


# --- Timbre del ticket (QR y código de seguridad) ---------------------------------------------------------

_DSIG_NS = {"ds": "http://www.w3.org/2000/09/xmldsig#"}


def timbre_desde_xml(
    xml_firmado: str | None, *, rnc_emisor: str, encf: str | None, datos: dict[str, Any], ambiente: DGIIEnvironment
) -> dict[str, Any]:
    """Código de seguridad (6 primeros caracteres del SignatureValue,
    Informe Técnico 18.2.3), fecha de firma y QR de "Consulta timbre" --
    los mismos datos que pone facturas_api en la representación impresa.
    Sin XML firmado (o sin firma) devuelve todo en None."""
    vacio = {"encf": encf, "codigo_seguridad": None, "fecha_firma": None, "url_qr": None, "qr_svg": None}
    if not xml_firmado:
        return vacio
    try:
        doc = etree.fromstring(xml_firmado.encode("utf-8"))
    except etree.XMLSyntaxError:
        return vacio
    firma = doc.find(".//ds:SignatureValue", namespaces=_DSIG_NS)
    fecha = doc.find(".//FechaHoraFirma")
    if firma is None or not firma.text or fecha is None or not fecha.text:
        return vacio
    codigo = "".join(firma.text.split())[:6]
    encabezado = datos.get("Encabezado", {})
    url = construir_url_timbre_ecf(
        ambiente=ambiente,
        rnc_emisor=rnc_emisor,
        rnc_comprador=str((encabezado.get("Comprador") or {}).get("RNCComprador") or ""),
        encf=encf or "",
        fecha_emision=str(encabezado.get("Emisor", {}).get("FechaEmision") or ""),
        monto_total=str(encabezado.get("Totales", {}).get("MontoTotal") or ""),
        fecha_firma=fecha.text,
        codigo_seguridad=codigo,
    )
    imagen = qrcode.make(url, image_factory=qrcode.image.svg.SvgPathImage, border=1)
    svg = imagen.to_string(encoding="unicode")
    return {"encf": encf, "codigo_seguridad": codigo, "fecha_firma": fecha.text, "url_qr": url, "qr_svg": svg}
