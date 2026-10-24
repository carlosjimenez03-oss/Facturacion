"""Rutas de los pagos con tarjeta en línea (fase 7). Contrato con el
frontend: `frontend/src/lib/pagosApi.ts`. Ver docs/PAGOS.md.

Con sesión (como todo /api-erp):

* `GET/PUT /configuracion/pagos-en-linea` -- Configuración → Pagos en línea.
  Guardar solo lo puede un administrador. El AuthKey de Azul se guarda
  cifrado (CERTIFICADOS_CLAVE, formato `v2:`) o llega por la variable
  AZUL_AUTH_KEY (que manda); nunca se devuelve.
* `GET /pagos-en-linea` y `GET /pagos-en-linea/{id}/eventos` -- Cobros.

Públicas (bajo `/portal/publico/`, lo único que nginx y ERP_REQUIRE_AUTH
dejan pasar sin sesión; ver main.py):

* `POST /portal/publico/{token}/pagos` -- el cliente pulsa «Pagar con
  tarjeta» en su portal: crea el intento y devuelve el formulario firmado
  que su navegador publica en la página del banco.
* `GET|POST /portal/publico/pagos/{referencia}/retorno/{resultado}` -- a
  donde el banco devuelve al cliente (ApprovedUrl/DeclinedUrl/CancelUrl).
  Valida la firma ANTES de creer nada, aplica el resultado una sola vez y
  redirige a la página del resultado (/p/pago/<referencia>).
* `GET /portal/publico/pagos/{referencia}` -- estado para esa página.
* `POST /portal/publico/pagos/{referencia}/simulado` -- la «página del
  banco» del proveedor simulado (nunca en producción).
"""
from __future__ import annotations

import html
import logging
import secrets
import uuid
from datetime import datetime
from functools import lru_cache
from typing import Any, Literal
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from libs.ecf_common.auth import certificado_activo
from libs.ecf_common.auth.sesion_supabase import SesionInvalidaError, usuario_de_request
from libs.ecf_common.settings import EcfBaseSettings, load_settings

from . import configuracion_envio as preferencias
from . import contabilidad_repository
from . import pagos_repository as repo
from . import soluciones_repository as portal
from .config import get_settings
from .pagos_proveedores import (
    LLAVE_SIMULADO,
    PROVEEDORES,
    ConfigPagoInvalidaError,
    ConfigProveedor,
    IntentoPago,
    UrlsRetorno,
    azul_hash_solicitud,
    hash_coincide,
    respuesta_simulada,
)
from .pagos_schemas import (
    ConfigPagos,
    ConfigPagosPayload,
    EstadoPago,
    EventoPago,
    IniciarPagoPayload,
    PagoEnLinea,
    PagoIniciado,
    PagoPublico,
)
from .repository import DatosInvalidosError, hoy, validar_cuenta_bancaria

logger = logging.getLogger(__name__)


class PagosSettings(EcfBaseSettings):
    # AuthKey de Azul por variable de entorno (alternativa a guardarla
    # cifrada en Configuración; si están las dos, manda esta).
    azul_auth_key: str = ""
    # Origen público del frontend (p. ej. https://front.acentosdeco.lat): de
    # él salen ApprovedUrl/DeclinedUrl/CancelUrl. Obligatorio en producción;
    # fuera de producción, si falta, se deduce del request (Host /
    # X-Forwarded-*).
    pagos_url_publica: str = ""
    # Prefijo con que nginx publica erp-api (location /api-erp/).
    pagos_ruta_api: str = "/api-erp"
    # Minutos que vive un intento sin respuesta del banco.
    pagos_vigencia_minutos: int = 30


@lru_cache
def get_pagos_settings() -> PagosSettings:
    return load_settings(PagosSettings)


router = APIRouter(tags=["pagos"])

CLAVE_PREFERENCIA = "pagos_en_linea"
CAMPO_AUTH_KEY = "auth_key_cifrada"


def _por_defecto(settings: PagosSettings) -> dict[str, Any]:
    return {
        "activo": False,
        # Fuera de producción, sin nada configurado, el proveedor es el simulado.
        "proveedor": "azul" if settings.is_production else "simulado",
        "ambiente": "pruebas",
        "merchant_id": "",
        "merchant_name": "",
        "merchant_type": "ECommerce",
        "permitir_parcial": False,
        "cuenta_bancaria_id": None,
    }


def simulado_permitido(settings: PagosSettings) -> bool:
    return not settings.is_production


def _pool(request: Request):
    return request.app.state.db_pool


def _ip(request: Request) -> str | None:
    return request.headers.get("x-real-ip") or (request.client.host if request.client else None)


async def leer_config(pool, settings: PagosSettings) -> dict[str, Any]:
    return await preferencias.leer_preferencia(pool, CLAVE_PREFERENCIA, _por_defecto(settings))


def _auth_key(valor: dict[str, Any], settings: PagosSettings) -> tuple[str, str | None]:
    if settings.azul_auth_key:
        return settings.azul_auth_key, "variable"
    if valor.get(CAMPO_AUTH_KEY):
        try:
            return preferencias.secreto(valor, CAMPO_AUTH_KEY), "guardada"
        except preferencias.EnvioError:
            logger.warning("No se pudo descifrar el AuthKey de Azul: ¿cambió CERTIFICADOS_CLAVE?")
            return "", "guardada"
    return "", None


def config_proveedor(valor: dict[str, Any], settings: PagosSettings) -> ConfigProveedor:
    return ConfigProveedor(
        proveedor=valor["proveedor"],
        ambiente=valor["ambiente"],
        merchant_id=valor.get("merchant_id") or "",
        merchant_name=valor.get("merchant_name") or "",
        merchant_type=valor.get("merchant_type") or "",
        auth_key=_auth_key(valor, settings)[0],
    )


def faltantes(valor: dict[str, Any], settings: PagosSettings) -> list[str]:
    proveedor = PROVEEDORES.get(valor["proveedor"])
    if proveedor is None:
        return ["proveedor"]
    if proveedor.clave == "simulado" and not simulado_permitido(settings):
        return ["proveedor (el simulado no se permite en producción)"]
    faltan = list(proveedor.validar_config(config_proveedor(valor, settings)))
    if settings.is_production and not settings.pagos_url_publica:
        faltan.append("PAGOS_URL_PUBLICA")
    return faltan


def _base_api(request: Request, settings: PagosSettings) -> str | None:
    """URL pública de erp-api (para las URL de retorno)."""
    if settings.pagos_url_publica:
        return settings.pagos_url_publica.rstrip("/") + settings.pagos_ruta_api
    if settings.is_production:
        return None
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    via_proxy = "x-forwarded-for" in request.headers
    return f"{proto}://{host}{settings.pagos_ruta_api if via_proxy else ''}"


def _usuario(request: Request):
    settings = get_settings()
    if not settings.supabase_jwt_secret:
        return None  # sin inicio de sesión configurado (aviso amarillo del frontend)
    try:
        return usuario_de_request(request, settings.supabase_jwt_secret, admins=settings.kontara_admin_emails)
    except SesionInvalidaError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


def _puede_editar(request: Request) -> bool:
    usuario = _usuario(request)
    return usuario is None or usuario.admin


def _publica(valor: dict[str, Any], settings: PagosSettings, *, puede_editar: bool, base: str | None) -> dict:
    _, origen = _auth_key(valor, settings)
    falta = faltantes(valor, settings)
    disponibles = [
        {"clave": p.clave, "nombre": p.nombre}
        for p in PROVEEDORES.values()
        if p.clave != "simulado" or simulado_permitido(settings)
    ]
    return {
        **{k: valor.get(k, v) for k, v in _por_defecto(settings).items()},
        "auth_key_configurada": origen is not None,
        "auth_key_origen": origen,
        "proveedores": disponibles,
        "listo": not falta,
        "faltantes": falta,
        "url_retorno": f"{base}/portal/publico/pagos/…/retorno/aprobado" if base else None,
        "cifrado_disponible": certificado_activo.cifrado_disponible(),
        "puede_editar": puede_editar,
        "produccion": settings.is_production,
    }


async def pago_en_linea_del_portal(pool, metodos_pago: list[str]) -> dict[str, Any] | None:
    """Lo que la página pública necesita para ofrecer «Pagar con tarjeta»
    (None = no se ofrece: apagado, incompleto o el enlace no tiene tarjeta)."""
    if "tarjeta" not in metodos_pago:
        return None
    settings = get_pagos_settings()
    valor = await leer_config(pool, settings)
    if not valor.get("activo") or faltantes(valor, settings):
        return None
    proveedor = PROVEEDORES[valor["proveedor"]]
    return {
        "proveedor": proveedor.clave,
        "nombre": proveedor.nombre,
        "pruebas": proveedor.clave == "simulado" or valor["ambiente"] != "produccion",
        "permitir_parcial": bool(valor.get("permitir_parcial")),
    }


# --- Configuración → Pagos en línea ---------------------------------------------------------

@router.get("/configuracion/pagos-en-linea", response_model=ConfigPagos)
async def obtener_config(request: Request):
    settings = get_pagos_settings()
    valor = await leer_config(_pool(request), settings)
    return _publica(valor, settings, puede_editar=_puede_editar(request), base=_base_api(request, settings))


@router.put("/configuracion/pagos-en-linea", response_model=ConfigPagos)
async def guardar_config(request: Request, payload: ConfigPagosPayload):
    if not _puede_editar(request):
        raise HTTPException(status_code=403, detail="Solo un administrador puede cambiar los pagos en línea")
    settings = get_pagos_settings()
    if payload.proveedor == "simulado" and not simulado_permitido(settings):
        raise HTTPException(status_code=422, detail="El proveedor simulado no se puede usar en producción")
    if payload.merchant_id and not payload.merchant_id.isdigit():
        raise HTTPException(status_code=422, detail="El MerchantId de Azul tiene solo dígitos")
    for campo in ("merchant_name", "merchant_type"):
        if any(c in getattr(payload, campo) for c in ('"', "\\")):
            raise HTTPException(status_code=422, detail=f"{campo} no puede tener comillas dobles ni barra invertida")
    if payload.cuenta_bancaria_id is not None:
        async with _pool(request).acquire() as conn:
            try:
                await validar_cuenta_bancaria(conn, payload.cuenta_bancaria_id, hoy(), documento="cobro")
            except DatosInvalidosError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
    actual = await leer_config(_pool(request), settings)
    nuevo = {**actual, **payload.model_dump(exclude={"auth_key", "borrar_auth_key"})}
    nuevo["cuenta_bancaria_id"] = str(payload.cuenta_bancaria_id) if payload.cuenta_bancaria_id else None
    try:
        nuevo = preferencias._con_secreto(nuevo, CAMPO_AUTH_KEY, payload.auth_key, borrar=payload.borrar_auth_key)
    except preferencias.EnvioError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if nuevo["activo"] and faltantes(nuevo, settings):
        raise HTTPException(
            status_code=422,
            detail=f"Para activar los pagos en línea faltan: {', '.join(faltantes(nuevo, settings))}",
        )
    await preferencias.guardar_preferencia(_pool(request), CLAVE_PREFERENCIA, nuevo)
    return _publica(nuevo, settings, puede_editar=True, base=_base_api(request, settings))


# --- Cobros: pagos en línea recibidos ---------------------------------------------------------

@router.get("/pagos-en-linea", response_model=list[PagoEnLinea])
async def listar_pagos(
    request: Request,
    factura_id: str | None = None,
    estado: EstadoPago | None = None,
    limit: int = Query(200, ge=1, le=500),
):
    try:
        fid = uuid.UUID(factura_id) if factura_id else None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="factura_id inválido") from exc
    return await repo.listar(_pool(request), factura_id=fid, estado=estado, limite=limit)


@router.get("/pagos-en-linea/{pago_id}/eventos", response_model=list[EventoPago])
async def eventos_pago(request: Request, pago_id: str):
    try:
        pid = uuid.UUID(pago_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="id inválido") from exc
    return await repo.eventos(_pool(request), pid)


# --- Portal público ---------------------------------------------------------

_NO_DISPONIBLE = "El pago con tarjeta en línea no está disponible"


@router.post("/portal/publico/{token}/pagos", response_model=PagoIniciado, status_code=201)
async def iniciar_pago(request: Request, token: str, payload: IniciarPagoPayload):
    settings = get_pagos_settings()
    pool = _pool(request)
    valor = await leer_config(pool, settings)
    if not valor.get("activo") or faltantes(valor, settings):
        raise HTTPException(status_code=409, detail=_NO_DISPONIBLE)
    base = _base_api(request, settings)
    if base is None:  # pragma: no cover (faltantes ya lo exige en producción)
        raise HTTPException(status_code=409, detail=_NO_DISPONIBLE)
    proveedor = PROVEEDORES[valor["proveedor"]]
    try:
        intento = await repo.crear_intento(
            pool,
            token=token,
            factura_id=payload.factura_id,
            monto=payload.monto,
            permitir_parcial=bool(valor.get("permitir_parcial")),
            proveedor=proveedor.clave,
            ambiente="pruebas" if proveedor.clave == "simulado" else valor["ambiente"],
            vigencia_minutos=settings.pagos_vigencia_minutos,
            ip=_ip(request),
        )
    except portal.NoEncontradoError as exc:
        # Mismo 404 para token inválido, factura ajena o enlace sin tarjeta.
        raise HTTPException(status_code=404, detail="Enlace o factura no válidos") from exc
    except repo.DemasiadosIntentosError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except DatosInvalidosError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    ref = intento["referencia"]
    retorno = f"{base}/portal/publico/pagos/{ref}/retorno"
    urls = UrlsRetorno(
        aprobada=f"{retorno}/aprobado",
        declinada=f"{retorno}/declinado",
        cancelada=f"{retorno}/cancelado",
        simulado=f"{base}/portal/publico/pagos/{ref}/simulado",
    )
    try:
        formulario = proveedor.formulario(
            config_proveedor(valor, settings),
            IntentoPago(orden=intento["orden"], monto=intento["monto"], itbis=intento["itbis"],
                        descripcion=intento["encf"] or ""),
            urls,
        )
    except ConfigPagoInvalidaError as exc:
        raise HTTPException(status_code=409, detail=f"{_NO_DISPONIBLE}: {exc}") from exc
    return {
        "referencia": ref,
        "accion": formulario.accion,
        "campos": formulario.campos,
        "monto": intento["monto"],
        "itbis": intento["itbis"],
        "encf": intento["encf"],
        "expira_en": intento["expira_en"],
        "proveedor": proveedor.clave,
    }


async def _pago(request: Request, referencia: str) -> dict[str, Any]:
    try:
        return await repo.obtener_por_referencia(_pool(request), referencia)
    except repo.PagoNoEncontradoError as exc:
        raise HTTPException(status_code=404, detail="Pago no encontrado") from exc


@router.get("/portal/publico/pagos/{referencia}", response_model=PagoPublico)
async def estado_pago(request: Request, referencia: str):
    pago = await _pago(request, referencia)
    async with _pool(request).acquire() as conn:
        empresa = await portal._razon_social_empresa(conn, get_settings().emisor_razon_social)
    return {
        "referencia": pago["referencia"],
        "estado": pago["estado"],
        "monto": pago["monto"],
        "encf": pago["encf"],
        "empresa": empresa,
        "proveedor": PROVEEDORES[pago["proveedor"]].nombre,
        "pruebas": pago["ambiente"] != "produccion",
        "codigo_autorizacion": pago["codigo_autorizacion"] if pago["estado"] == "aprobado" else None,
        "mensaje": pago["respuesta"] or pago["error_descripcion"],
        "cobro_registrado": pago["cobro_id"] is not None,
        "requiere_revision": bool(pago["incidencia"]),
        "respondido_en": pago["respondido_en"],
        "creado_en": pago["creado_en"],
    }


def _resultado_url(referencia: str, settings: PagosSettings, aviso: str | None = None) -> str:
    base = settings.pagos_url_publica.rstrip("/") if settings.pagos_url_publica else ""
    return f"{base}/p/pago/{referencia}" + (f"?aviso={aviso}" if aviso else "")


async def _parametros(request: Request) -> dict[str, str]:
    params = {k: v for k, v in request.query_params.items()}
    if request.method == "POST":
        formulario = await request.form()
        params.update({k: v for k, v in formulario.items() if isinstance(v, str)})
    return params


@router.api_route("/portal/publico/pagos/{referencia}/retorno/{resultado}", methods=["GET", "POST"])
async def retorno_pago(request: Request, referencia: str, resultado: Literal["aprobado", "declinado", "cancelado"]):
    settings = get_pagos_settings()
    pool = _pool(request)
    pago = await _pago(request, referencia)
    ip = _ip(request)
    params = await _parametros(request)
    destino = RedirectResponse(_resultado_url(referencia, settings), status_code=303)
    destino.headers["Cache-Control"] = "no-store"
    if resultado == "cancelado" and not params.get("AuthHash"):
        # CancelUrl llega sin parámetros (ni firma): solo cancela un pendiente.
        await repo.cancelar(pool, referencia, ip)
        return destino
    proveedor = PROVEEDORES[pago["proveedor"]]
    if proveedor.clave == "simulado" and not simulado_permitido(settings):
        await repo.registrar_evento_suelto(pool, pago["id"], "proveedor_no_permitido", {}, ip)
        return RedirectResponse(_resultado_url(referencia, settings, "firma"), status_code=303)
    valor = await leer_config(pool, settings)
    lectura = proveedor.leer_retorno(config_proveedor(valor, settings), params)
    if not lectura.valido:
        logger.warning("Retorno de pago rechazado (%s) para la orden %s desde %s", lectura.motivo, pago["orden"], ip)
        await repo.registrar_evento_suelto(
            pool, pago["id"], "firma_invalida", repo.parametros_para_bitacora(params) | {"motivo": lectura.motivo}, ip
        )
        return RedirectResponse(_resultado_url(referencia, settings, "firma"), status_code=303)
    cuenta = valor.get("cuenta_bancaria_id")
    aplicado = await repo.aplicar_retorno(
        pool,
        referencia,
        lectura,
        cuenta_bancaria_id=uuid.UUID(cuenta) if cuenta else None,
        nombre_proveedor=proveedor.nombre,
        params=params,
        ip=ip,
    )
    if aplicado["rechazo"]:
        logger.warning("Retorno de pago rechazado para la orden %s: %s", pago["orden"], aplicado["rechazo"])
        return RedirectResponse(_resultado_url(referencia, settings, "datos"), status_code=303)
    if aplicado["cobro_nuevo"]:
        await _contabilizar(pool, aplicado["pago"]["cobro_id"])
    return destino


async def _contabilizar(pool, cobro_id: str) -> None:
    """El asiento del cobro (Banco contra CxC) en el momento; si no se puede
    (catálogo incompleto, factura sin aceptar) lo hará «Generar asientos»."""
    try:
        await contabilidad_repository.contabilizar_documento(pool, "cobro", cobro_id)
    except Exception:  # el cobro ya quedó registrado: el asiento puede esperar
        logger.exception("No se pudo contabilizar el cobro en línea %s (se hará al generar asientos)", cobro_id)


# --- Proveedor simulado: la «página del banco» ---------------------------------------------------------

_CSP_SIMULADO = "default-src 'none'; style-src 'unsafe-inline'; form-action 'none'; frame-ancestors 'none'"


def _pagina(titulo: str, cuerpo: str, status: int = 200) -> HTMLResponse:
    contenido = f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(titulo)}</title>
<style>
body{{font-family:system-ui,-apple-system,"Segoe UI",sans-serif;background:#F6F7F9;margin:0;padding:32px 16px;color:#111827}}
main{{max-width:460px;margin:0 auto;background:#fff;border:1px solid #E5E7EB;border-radius:12px;padding:24px}}
h1{{font-size:20px;margin:0 0 4px}} p{{color:#4B5563;font-size:14px;line-height:1.5}}
.aviso{{background:#FEF3C7;color:#92400E;border-radius:8px;padding:8px 12px;font-size:13px}}
dl{{display:grid;grid-template-columns:auto 1fr;gap:6px 16px;font-size:14px}} dt{{color:#6B7280}} dd{{margin:0;font-weight:600}}
.acc{{display:flex;flex-direction:column;gap:10px;margin-top:20px}}
a{{display:block;text-align:center;padding:12px;border-radius:8px;text-decoration:none;font-weight:600;font-size:15px}}
.ok{{background:#0F766E;color:#fff}} .no{{background:#fff;color:#B91C1C;border:1px solid #FCA5A5}} .cn{{color:#374151;border:1px solid #D1D5DB}}
</style></head><body><main>{cuerpo}</main></body></html>"""
    return HTMLResponse(
        contenido,
        status_code=status,
        headers={"Content-Security-Policy": _CSP_SIMULADO, "Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
    )


@router.post("/portal/publico/pagos/{referencia}/simulado", response_class=HTMLResponse)
async def pagina_simulada(request: Request, referencia: str):
    """Hace de Página de Pagos para el proveedor simulado: comprueba la firma
    del formulario igual que lo haría Azul y ofrece aprobar, declinar o
    cancelar; cada botón vuelve a nuestras URL de retorno con una respuesta
    firmada como las de Azul. No pide ni recibe datos de tarjeta."""
    settings = get_pagos_settings()
    pago = await _pago(request, referencia)
    if pago["proveedor"] != "simulado" or not simulado_permitido(settings):
        raise HTTPException(status_code=404, detail="Pago no encontrado")
    campos = {k: v for k, v in (await request.form()).items() if isinstance(v, str)}
    firma_ok = hash_coincide(campos.get("AuthHash"), azul_hash_solicitud(campos, LLAVE_SIMULADO))
    if not firma_ok or campos.get("OrderNumber") != pago["orden"]:
        return _pagina("Solicitud rechazada", "<h1>Solicitud rechazada</h1><p>La firma (AuthHash) no es válida.</p>", 400)
    fecha = datetime.now().strftime("%Y%m%d%H%M%S")
    rrn = f"{secrets.randbelow(10**12):012d}"
    autorizacion = f"{secrets.randbelow(10**6):06d}"

    def enlace(resultado: str, base: str) -> str:
        datos = respuesta_simulada(campos, resultado, rrn=rrn, autorizacion=autorizacion, fecha=fecha)
        return html.escape(f"{base}?{urlencode(datos)}")

    monto = html.escape(f"RD$ {pago['monto']:,.2f}")
    cuerpo = f"""
<p class="aviso">Pasarela SIMULADA para pruebas: no se cobra nada ni se piden datos de tarjeta.</p>
<h1>{html.escape(campos.get('MerchantName', ''))}</h1>
<p>Página de pago de prueba con el mismo formato que la Página de Pagos de Azul.</p>
<dl><dt>Orden</dt><dd>{html.escape(pago['orden'])}</dd><dt>Factura</dt><dd>{html.escape(pago['encf'] or '')}</dd>
<dt>Monto</dt><dd data-monto>{monto}</dd></dl>
<div class="acc">
<a class="ok" id="aprobar" href="{enlace('aprobado', campos.get('ApprovedUrl', ''))}">Aprobar pago</a>
<a class="no" id="declinar" href="{enlace('declinado', campos.get('DeclinedUrl', ''))}">Declinar</a>
<a class="cn" id="cancelar" href="{html.escape(campos.get('CancelUrl', ''))}">Cancelar y volver</a>
</div>"""
    return _pagina("Pago simulado", cuerpo)
