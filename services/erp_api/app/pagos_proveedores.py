"""Pasarelas de pago con tarjeta en línea (fase 7): funciones puras, sin base
de datos ni HTTP.

Una pasarela (`ProveedorPago`) sabe dos cosas:

1. armar el formulario firmado que el NAVEGADOR del cliente publica (POST)
   en la página de pago alojada por el banco (`formulario`): Kontara nunca
   ve ni recibe los datos de la tarjeta;
2. leer y validar los parámetros con que el banco devuelve al cliente a
   nuestras URL de retorno (`leer_retorno`): nada de lo que llega se cree
   hasta verificar su firma (comparación en tiempo constante).

Implementadas: **Azul** (Página de Pagos de Banco Popular Dominicano) y
**simulado** (misma firma que Azul con una llave pública fija; solo fuera de
producción, para pruebas y e2e). Para agregar CardNet u otra: una clase con
`clave`, `nombre`, `formulario` y `leer_retorno`, y registrarla en
`PROVEEDORES`. Ver docs/PAGOS.md (campos, orden del hash y fuentes).
"""
from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Protocol

_CENTAVO = Decimal("0.01")

# --- Azul: URLs y campos (docs/PAGOS.md) ---------------------------------------------------------

# "Invocar la Página de Pago" (Documento E-Commerce AZUL, Página de Pagos,
# 2023-08). Producción tiene un sitio alterno de contingencia.
AZUL_URLS = {
    "pruebas": "https://pruebas.azul.com.do/PaymentPage/",
    "produccion": "https://pagos.azul.com.do/PaymentPage/Default.aspx",
}
AZUL_URL_ALTERNA_PRODUCCION = "https://contpagos.azul.com.do/PaymentPage/Default.aspx"

# Moneda: cada MID transacciona en una sola; para pesos dominicanos Azul usa "$".
AZUL_MONEDA_DOP = "$"

# Orden EXACTO de la concatenación del AuthHash del requerimiento ("Manejo
# de la Autenticación"); al final va el AuthKey, que no viaja en el POST.
CAMPOS_HASH_SOLICITUD = (
    "MerchantId", "MerchantName", "MerchantType", "CurrencyCode", "OrderNumber", "Amount", "ITBIS",
    "ApprovedUrl", "DeclinedUrl", "CancelUrl", "UseCustomField1", "CustomField1Label", "CustomField1Value",
    "UseCustomField2", "CustomField2Label", "CustomField2Value",
)
# Orden del hash de la respuesta (los valores que llegan en el querystring
# de ApprovedUrl/DeclinedUrl) + AuthKey.
CAMPOS_HASH_RESPUESTA = (
    "OrderNumber", "Amount", "AuthorizationCode", "DateTime", "ResponseCode", "IsoCode", "ResponseMessage",
    "ErrorDescription", "RRN",
)

# Llave del proveedor simulado: es pública a propósito (no protege nada; el
# simulado nunca se acepta en producción).
LLAVE_SIMULADO = "kontara-simulado-no-es-secreta"

# Caracteres que la Página de Pagos no acepta en los textos que se muestran.
_PROHIBIDOS = ('"', "\\")


class ConfigPagoInvalidaError(ValueError):
    """Falta un dato de la pasarela o no tiene el formato esperado."""


def hmac_sha512_unicode(texto: str, llave: str) -> str:
    """HMAC-SHA512 hexadecimal (minúsculas) del texto en UTF-16LE (lo que
    Azul llama «Unicode», `Encoding.Unicode` de .NET), con la llave en UTF-8.
    Es lo que hacen los ejemplos de Azul en C# (HMACSHA512 sobre
    Encoding.Unicode.GetBytes) y PHP (mb_convert_encoding a UTF-16LE +
    hash_hmac('sha512', ..., $authKey))."""
    return hmac.new(llave.encode("utf-8"), texto.encode("utf-16-le"), hashlib.sha512).hexdigest()


def azul_hash_solicitud(campos: Mapping[str, str], auth_key: str) -> str:
    texto = "".join(str(campos.get(c, "")) for c in CAMPOS_HASH_SOLICITUD) + auth_key
    return hmac_sha512_unicode(texto, auth_key)


def azul_hash_respuesta(params: Mapping[str, str], auth_key: str) -> str:
    texto = "".join(str(params.get(c, "") or "") for c in CAMPOS_HASH_RESPUESTA) + auth_key
    return hmac_sha512_unicode(texto, auth_key)


def hash_coincide(recibido: str | None, esperado: str) -> bool:
    """Comparación en tiempo constante (sin distinguir mayúsculas: es hex)."""
    if not recibido:
        return False
    return hmac.compare_digest(recibido.strip().lower().encode("ascii", "replace"), esperado.lower().encode("ascii"))


def a_centavos(monto: Decimal) -> str:
    """1250.50 -> "125050" (sin punto ni coma; los dos últimos dígitos son
    los centavos). 0 -> "000", como el ejemplo de ITBIS exento de Azul."""
    centavos = int((Decimal(monto).quantize(_CENTAVO, rounding=ROUND_HALF_UP) * 100).to_integral_value())
    if centavos < 0:
        raise ConfigPagoInvalidaError("Monto negativo")
    return f"{centavos:03d}"


def desde_centavos(valor: str | None) -> Decimal | None:
    """El Amount de la respuesta. Azul lo describe como «monto original de la
    transacción»: se acepta en centavos ("125050") y, por si llegara con
    punto decimal ("1250.50"), también así. None si no es un número."""
    texto = (valor or "").strip().replace(",", "")
    if not texto:
        return None
    try:
        if "." in texto:
            return Decimal(texto).quantize(_CENTAVO)
        if not texto.isdigit():
            return None
        return (Decimal(int(texto)) / 100).quantize(_CENTAVO)
    except InvalidOperation:
        return None


# --- Contrato de una pasarela ---------------------------------------------------------

@dataclass(frozen=True)
class ConfigProveedor:
    """Datos del comercio en la pasarela (Configuración → Pagos en línea)."""

    proveedor: str
    ambiente: str = "pruebas"
    merchant_id: str = ""
    merchant_name: str = ""
    merchant_type: str = ""
    auth_key: str = ""


@dataclass(frozen=True)
class IntentoPago:
    orden: str
    monto: Decimal
    itbis: Decimal
    descripcion: str = ""


@dataclass(frozen=True)
class UrlsRetorno:
    aprobada: str
    declinada: str
    cancelada: str
    # Solo el simulado: la "página de pago" la sirve el mismo erp-api.
    simulado: str = ""


@dataclass(frozen=True)
class Formulario:
    accion: str
    campos: dict[str, str]
    accion_alterna: str | None = None


@dataclass(frozen=True)
class RetornoPago:
    """Lo que dijo la pasarela, ya validado. `valido` False = la firma no
    coincide (o faltan datos): no se usa nada de lo demás."""

    valido: bool
    aprobado: bool = False
    orden: str = ""
    monto: Decimal | None = None
    codigo_autorizacion: str = ""
    rrn: str = ""
    iso_code: str = ""
    response_code: str = ""
    mensaje: str = ""
    error_descripcion: str = ""
    fecha: str = ""
    orden_proveedor: str = ""
    motivo: str = ""
    extra: dict[str, str] = field(default_factory=dict)


class ProveedorPago(Protocol):
    clave: str
    nombre: str

    def validar_config(self, config: ConfigProveedor) -> list[str]:
        """Qué falta para poder cobrar (lista vacía = lista)."""

    def formulario(self, config: ConfigProveedor, intento: IntentoPago, urls: UrlsRetorno) -> Formulario: ...

    def leer_retorno(self, config: ConfigProveedor, params: Mapping[str, str]) -> RetornoPago: ...


def _texto_seguro(valor: str, campo: str, maximo: int) -> str:
    valor = (valor or "").strip()
    if any(c in valor for c in _PROHIBIDOS):
        raise ConfigPagoInvalidaError(f"{campo} no puede tener comillas dobles ni barra invertida")
    return valor[:maximo]


class Azul:
    """Página de Pagos AZUL (hosted payment page). El navegador publica el
    formulario en `AZUL_URLS[ambiente]`; Azul devuelve al cliente a
    ApprovedUrl / DeclinedUrl con el resultado en el querystring (firmado con
    AuthHash) o a CancelUrl sin parámetros."""

    clave = "azul"
    nombre = "Azul (Banco Popular)"
    llave_fija: str | None = None

    def validar_config(self, config: ConfigProveedor) -> list[str]:
        faltan = []
        if not config.merchant_id:
            faltan.append("MerchantId")
        if not config.merchant_name:
            faltan.append("MerchantName")
        if not config.merchant_type:
            faltan.append("MerchantType")
        if not self._llave(config):
            faltan.append("AuthKey")
        if config.ambiente not in AZUL_URLS:
            faltan.append("ambiente")
        return faltan

    def _llave(self, config: ConfigProveedor) -> str:
        return self.llave_fija if self.llave_fija is not None else config.auth_key

    def _accion(self, config: ConfigProveedor, urls: UrlsRetorno) -> tuple[str, str | None]:
        if config.ambiente == "produccion":
            return AZUL_URLS["produccion"], AZUL_URL_ALTERNA_PRODUCCION
        return AZUL_URLS["pruebas"], None

    def formulario(self, config: ConfigProveedor, intento: IntentoPago, urls: UrlsRetorno) -> Formulario:
        faltan = self.validar_config(config)
        if faltan:
            raise ConfigPagoInvalidaError(f"Faltan datos de {self.nombre}: {', '.join(faltan)}")
        if intento.itbis > intento.monto:
            raise ConfigPagoInvalidaError("El ITBIS no puede ser mayor que el monto")
        campos = {
            "MerchantId": config.merchant_id,
            "MerchantName": _texto_seguro(config.merchant_name, "MerchantName", 60),
            "MerchantType": _texto_seguro(config.merchant_type, "MerchantType", 40),
            "CurrencyCode": AZUL_MONEDA_DOP,
            "OrderNumber": intento.orden,
            "Amount": a_centavos(intento.monto),
            "ITBIS": a_centavos(intento.itbis),
            "ApprovedUrl": urls.aprobada,
            "DeclinedUrl": urls.declinada,
            "CancelUrl": urls.cancelada,
            "UseCustomField1": "0",
            "CustomField1Label": "",
            "CustomField1Value": "",
            "UseCustomField2": "0",
            "CustomField2Label": "",
            "CustomField2Value": "",
        }
        if intento.descripcion:
            campos["UseCustomField1"] = "1"
            campos["CustomField1Label"] = "Factura"
            campos["CustomField1Value"] = _texto_seguro(intento.descripcion, "Descripción", 40)
        campos["AuthHash"] = azul_hash_solicitud(campos, self._llave(config))
        campos["Locale"] = "ES"
        accion, alterna = self._accion(config, urls)
        return Formulario(accion=accion, campos=campos, accion_alterna=alterna)

    def leer_retorno(self, config: ConfigProveedor, params: Mapping[str, str]) -> RetornoPago:
        llave = self._llave(config)
        if not llave:
            return RetornoPago(valido=False, motivo="La pasarela no tiene AuthKey configurada")
        if not hash_coincide(params.get("AuthHash"), azul_hash_respuesta(params, llave)):
            return RetornoPago(valido=False, motivo="AuthHash inválido")
        iso = (params.get("IsoCode") or "").strip()
        respuesta = (params.get("ResponseCode") or "").strip()
        # Aprobada = IsoCode 00 (tabla de códigos de Azul). ResponseCode trae
        # "ISO8583" cuando el emisor respondió; "Error" si falló el proceso.
        aprobado = iso == "00" and respuesta.upper() in ("ISO8583", "")
        return RetornoPago(
            valido=True,
            aprobado=aprobado,
            orden=(params.get("OrderNumber") or "").strip(),
            monto=desde_centavos(params.get("Amount")),
            codigo_autorizacion=(params.get("AuthorizationCode") or "").strip()[:40],
            rrn=(params.get("RRN") or "").strip()[:40],
            iso_code=iso[:10],
            response_code=respuesta[:40],
            mensaje=(params.get("ResponseMessage") or "").strip()[:200],
            error_descripcion=(params.get("ErrorDescription") or "").strip()[:300],
            fecha=(params.get("DateTime") or "").strip()[:40],
            orden_proveedor=(params.get("AzulOrderId") or "").strip()[:40],
        )


class Simulado(Azul):
    """Mismos campos y misma firma que Azul, con `LLAVE_SIMULADO`; la página
    de pago es `urls.simulado` (la sirve erp-api, ver pagos.py). Nunca en
    producción (lo decide pagos.py)."""

    clave = "simulado"
    nombre = "Simulado (pruebas)"
    llave_fija = LLAVE_SIMULADO

    def validar_config(self, config: ConfigProveedor) -> list[str]:
        return []

    def formulario(self, config: ConfigProveedor, intento: IntentoPago, urls: UrlsRetorno) -> Formulario:
        datos = ConfigProveedor(
            proveedor=self.clave, ambiente="pruebas", merchant_id=config.merchant_id or "000000000",
            merchant_name=config.merchant_name or "Comercio simulado", merchant_type=config.merchant_type or "ECommerce",
        )
        base = super().formulario(datos, intento, urls)
        return Formulario(accion=urls.simulado, campos=base.campos)


def respuesta_simulada(campos: Mapping[str, str], resultado: str, *, rrn: str, autorizacion: str, fecha: str) -> dict[str, str]:
    """Los parámetros que el simulado manda a ApprovedUrl / DeclinedUrl,
    firmados como los de Azul."""
    aprobada = resultado == "aprobado"
    params = {
        "OrderNumber": campos.get("OrderNumber", ""),
        "Amount": campos.get("Amount", ""),
        "AuthorizationCode": autorizacion if aprobada else "",
        "DateTime": fecha,
        "ResponseCode": "ISO8583",
        "IsoCode": "00" if aprobada else "05",
        "ResponseMessage": "APROBADA" if aprobada else "DECLINADA",
        "ErrorDescription": "",
        "RRN": rrn,
        "AzulOrderId": "SIM" + rrn[-8:],
    }
    params["AuthHash"] = azul_hash_respuesta(params, LLAVE_SIMULADO)
    return params


PROVEEDORES: dict[str, ProveedorPago] = {"azul": Azul(), "simulado": Simulado()}
