"""Rutas de Configuración de erp-api (fase 6 · Plataforma): datos de la
empresa, secuencias e-NCF por tipo, vendedores y usuarios (Supabase Auth).
Contrato con el frontend: `frontend/src/lib/configuracionApi.ts`. Ver
docs/AUTH.md (usuarios) y docs/ERP_API.md.
"""
import asyncio
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile

from libs.ecf_common.auth import certificado_activo
from libs.ecf_common.auth.sesion_supabase import SesionInvalidaError, usuario_de_request

from . import configuracion_envio as envio
from . import configuracion_repository as repo
from . import gotrue_admin
from .config import ErpApiSettings, get_settings
from .configuracion_schemas import (
    CertificadoEstado,
    CorreoPayload,
    Empresa,
    EmpresaPayload,
    EnvioConfig,
    PreferenciaInventario,
    PruebaPayload,
    PruebaResultado,
    Secuencia,
    SecuenciaPayload,
    Usuario,
    UsuarioPayload,
    Usuarios,
    Vendedor,
    VendedorPayload,
    WhatsappPayload,
)
from .repository import DatosInvalidosError, hoy

router = APIRouter(prefix="/configuracion", tags=["configuracion"])

_VENDEDOR_NO_ENCONTRADO = "Vendedor no encontrado"
_USUARIOS_NO_CONFIGURADO = (
    "La administración de usuarios desde aquí no está configurada (falta SUPABASE_SERVICE_ROLE_KEY "
    "en erp-api). Mientras tanto, cree y desactive usuarios en Supabase Studio → Authentication → Users."
)


def _pool(request: Request):
    return request.app.state.db_pool


def _uuid(valor: str) -> uuid.UUID:
    try:
        return uuid.UUID(valor)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="id inválido") from exc


async def _llamar(funcion, *args, **kwargs):
    try:
        return await funcion(*args, **kwargs)
    except DatosInvalidosError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except repo.VendedorNoEncontradoError as exc:
        raise HTTPException(status_code=404, detail=_VENDEDOR_NO_ENCONTRADO) from exc
    except repo.SecuenciaNoEncontradaError as exc:
        raise HTTPException(status_code=404, detail="Ese tipo de e-CF no tiene secuencia registrada") from exc
    except repo.VendedorEnUsoError as exc:
        raise HTTPException(
            status_code=409, detail="El vendedor tiene facturas y no se puede eliminar; desactívelo en su lugar"
        ) from exc


# --- Empresa ---------------------------------------------------------

def _empresa(fila: dict | None, settings: ErpApiSettings) -> dict:
    """Sin fila guardada: lo que ya se sabe por las variables de entorno."""
    base = {
        "ambiente": settings.app_env,
        "ambiente_dgii": settings.dgii_environment.value,
        "emisor_rnc_configurado": "".join(c for c in settings.emisor_rnc if c.isdigit()) or None,
    }
    if fila is None:
        return {
            **base,
            "configurada": False,
            "razon_social": settings.emisor_razon_social,
            "rnc": base["emisor_rnc_configurado"],
        }
    return {**base, **fila, "configurada": True}


@router.get("/empresa", response_model=Empresa)
async def obtener_empresa(request: Request, settings: ErpApiSettings = Depends(get_settings)):
    return _empresa(await repo.obtener_empresa(_pool(request)), settings)


@router.put("/empresa", response_model=Empresa)
async def guardar_empresa(
    request: Request, payload: EmpresaPayload, settings: ErpApiSettings = Depends(get_settings)
):
    campos = payload.model_dump()
    # El RNC es el del emisor autorizado (EMISOR_RNC): no se cambia aquí.
    emisor_rnc = "".join(c for c in settings.emisor_rnc if c.isdigit())
    if emisor_rnc:
        campos["rnc"] = emisor_rnc
    fila = await _llamar(repo.guardar_empresa, _pool(request), **campos)
    return _empresa(fila, settings)


# --- Secuencias e-NCF ---------------------------------------------------------

@router.get("/secuencias", response_model=list[Secuencia])
async def listar_secuencias(request: Request):
    return await repo.listar_secuencias(_pool(request), hoy())


@router.get("/secuencias/{tipo_ecf}", response_model=Secuencia)
async def obtener_secuencia(request: Request, tipo_ecf: int):
    return await _llamar(repo.obtener_secuencia, _pool(request), tipo_ecf, hoy())


@router.put("/secuencias/{tipo_ecf}", response_model=Secuencia)
async def guardar_secuencia(request: Request, tipo_ecf: int, payload: SecuenciaPayload):
    return await _llamar(repo.guardar_secuencia, _pool(request), tipo_ecf, **payload.model_dump(), hoy=hoy())


@router.delete("/secuencias/{tipo_ecf}", status_code=204)
async def eliminar_secuencia(request: Request, tipo_ecf: int):
    await _llamar(repo.eliminar_secuencia, _pool(request), tipo_ecf)


# --- Vendedores ---------------------------------------------------------

@router.get("/vendedores", response_model=list[Vendedor])
async def listar_vendedores(request: Request, activo: bool | None = None):
    return await repo.listar_vendedores(_pool(request), activo=activo)


@router.post("/vendedores", response_model=Vendedor, status_code=201)
async def crear_vendedor(request: Request, payload: VendedorPayload):
    return await _llamar(repo.crear_vendedor, _pool(request), **payload.model_dump())


@router.put("/vendedores/{vendedor_id}", response_model=Vendedor)
async def actualizar_vendedor(request: Request, vendedor_id: str, payload: VendedorPayload):
    return await _llamar(repo.actualizar_vendedor, _pool(request), _uuid(vendedor_id), **payload.model_dump())


@router.delete("/vendedores/{vendedor_id}", status_code=204)
async def eliminar_vendedor(request: Request, vendedor_id: str):
    await _llamar(repo.eliminar_vendedor, _pool(request), _uuid(vendedor_id))


# --- Usuarios (Supabase Auth) ---------------------------------------------------------

async def _gotrue(funcion, *args):
    try:
        return await funcion(*args)
    except gotrue_admin.GoTrueNoConfiguradoError as exc:
        raise HTTPException(status_code=503, detail=_USUARIOS_NO_CONFIGURADO) from exc
    except gotrue_admin.GoTrueError as exc:
        status = exc.status if exc.status in (400, 404, 409, 422) else 502
        raise HTTPException(status_code=status, detail=f"Supabase Auth: {exc}") from exc


@router.get("/usuarios", response_model=Usuarios)
async def listar_usuarios(settings: ErpApiSettings = Depends(get_settings)):
    """Usuarios de Kontara (`app_metadata.kontara`) y, aparte, los demás del
    mismo Supabase Auth, que no pueden entrar hasta que se les dé acceso."""
    if not gotrue_admin.configurado(settings):
        return {"configurado": False, "usuarios": [], "mensaje": _USUARIOS_NO_CONFIGURADO}
    todos = await _gotrue(gotrue_admin.listar_usuarios, settings)
    return {
        "configurado": True,
        "usuarios": [u for u in todos if u["acceso"]],
        "sin_acceso": [u for u in todos if not u["acceso"]],
    }


@router.post("/usuarios", response_model=Usuario, status_code=201)
async def crear_usuario(payload: UsuarioPayload, settings: ErpApiSettings = Depends(get_settings)):
    correo = payload.correo.lower()
    if "@" not in correo:
        raise HTTPException(status_code=422, detail="El correo no tiene un formato válido")
    if payload.contrasena:
        return await _gotrue(gotrue_admin.crear_usuario, settings, correo, payload.contrasena)
    return await _gotrue(gotrue_admin.invitar_usuario, settings, correo)


def _usuario_actual(request: Request, settings: ErpApiSettings):
    """El usuario con sesión, o None si el inicio de sesión no está configurado."""
    try:
        return usuario_de_request(request, settings.supabase_jwt_secret, admins=settings.kontara_admin_emails)
    except SesionInvalidaError:
        return None


def _no_es_uno_mismo(request: Request, settings: ErpApiSettings, usuario_id: str) -> None:
    actual = _usuario_actual(request, settings)
    if actual is not None and actual.id == usuario_id:
        raise HTTPException(status_code=409, detail="No puede desactivar su propio usuario")


async def _de_kontara(settings: ErpApiSettings, usuario_id: str) -> None:
    """Activar/desactivar solo aplica a usuarios con acceso a Kontara."""
    usuario = await _gotrue(gotrue_admin.obtener_usuario, settings, usuario_id)
    if not usuario["acceso"]:
        raise HTTPException(status_code=404, detail="Ese usuario no tiene acceso a Kontara: use «Dar acceso»")


@router.post("/usuarios/{usuario_id}/desactivar", response_model=Usuario)
async def desactivar_usuario(request: Request, usuario_id: str, settings: ErpApiSettings = Depends(get_settings)):
    usuario_id = str(_uuid(usuario_id))
    _no_es_uno_mismo(request, settings, usuario_id)
    await _de_kontara(settings, usuario_id)
    return await _gotrue(gotrue_admin.cambiar_activo, settings, usuario_id, False)


@router.post("/usuarios/{usuario_id}/activar", response_model=Usuario)
async def activar_usuario(usuario_id: str, settings: ErpApiSettings = Depends(get_settings)):
    usuario_id = str(_uuid(usuario_id))
    await _de_kontara(settings, usuario_id)
    return await _gotrue(gotrue_admin.cambiar_activo, settings, usuario_id, True)


@router.post("/usuarios/{usuario_id}/acceso", response_model=Usuario)
async def dar_acceso_usuario(usuario_id: str, settings: ErpApiSettings = Depends(get_settings)):
    """Da acceso a Kontara a un usuario que ya existía en Supabase Auth
    (`app_metadata.kontara = true`). Entra desde su próximo inicio de sesión."""
    return await _gotrue(gotrue_admin.dar_acceso, settings, str(_uuid(usuario_id)))


# --- Certificado digital ---------------------------------------------------------

_P12_MAX_BYTES = 100_000


def _rnc_empresa(settings: ErpApiSettings, fila: dict | None) -> str:
    return "".join(c for c in (settings.emisor_rnc or (fila or {}).get("rnc") or "") if c.isdigit())


@router.get("/certificado", response_model=CertificadoEstado)
async def obtener_certificado(request: Request, settings: ErpApiSettings = Depends(get_settings)):
    rnc = _rnc_empresa(settings, await repo.obtener_empresa(_pool(request)))
    estado = await asyncio.to_thread(
        certificado_activo.estado_certificado, settings.certificado_path, settings.certificado_password, rnc
    )
    return {**estado, "carga_disponible": certificado_activo.cifrado_disponible()}


@router.post("/certificado", response_model=CertificadoEstado)
async def cargar_certificado(
    request: Request,
    archivo: UploadFile = File(..., description="Certificado .p12/.pfx del emisor"),
    contrasena: str = Form(..., min_length=1, max_length=500),
    settings: ErpApiSettings = Depends(get_settings),
):
    """Valida el .p12 (contraseña, llave RSA, vigencia y que sea del RNC de la
    empresa) y lo deja como certificado activo para TODOS los servicios que
    firman (volumen compartido, cambio atómico). El anterior sigue en uso
    hasta que este pasa todas las validaciones."""
    motivo = certificado_activo.motivo_sin_cifrado()
    if motivo:
        raise HTTPException(
            status_code=503,
            detail=f"Para cargar certificados desde aquí configure CERTIFICADOS_CLAVE en Dokploy: {motivo} (ver docs/AUTH.md).",
        )
    nombre = (archivo.filename or "certificado.p12").replace("\\", "/").rsplit("/", 1)[-1][:120]
    if not nombre.lower().endswith((".p12", ".pfx")):
        raise HTTPException(status_code=422, detail=f"«{nombre}» no es un certificado: seleccione un archivo .p12 o .pfx")
    contenido = await archivo.read(_P12_MAX_BYTES + 1)
    if len(contenido) > _P12_MAX_BYTES:
        raise HTTPException(status_code=422, detail="El archivo es demasiado grande para ser un certificado .p12")
    try:
        _, cert = certificado_activo.abrir_p12(contenido, contrasena)
    except certificado_activo.CertificadoInvalidoError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    datos = certificado_activo.datos_certificado(cert)
    if datos["estado"] == "vencido":
        raise HTTPException(status_code=422, detail=f"El certificado venció el {datos['vence']:%d/%m/%Y}")
    rnc = _rnc_empresa(settings, await repo.obtener_empresa(_pool(request)))
    if not rnc:
        # Sin RNC no hay cómo comprobar que el certificado es de la empresa.
        raise HTTPException(
            status_code=422,
            detail="Registre primero el RNC de la empresa (Configuración → Empresa) o EMISOR_RNC en Dokploy",
        )
    if not certificado_activo.pertenece_al_rnc(cert, rnc):
        raise HTTPException(
            status_code=422,
            detail=f"El certificado no pertenece al RNC {rnc} de la empresa (titular: {datos['titular']})",
        )
    metadatos = {"archivo": nombre, "numero_serie": datos["numero_serie"]}
    try:
        await asyncio.to_thread(certificado_activo.guardar_activo, contenido, contrasena, metadatos)
    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail="No se pudo guardar el certificado en el volumen compartido (CERTIFICADO_ACTIVO_PATH)",
        ) from exc
    return await obtener_certificado(request, settings)


# --- Método de inventario ---------------------------------------------------------

@router.get("/inventario", response_model=PreferenciaInventario)
async def obtener_inventario(request: Request):
    return await envio.leer_preferencia(_pool(request), "inventario", envio.INVENTARIO_POR_DEFECTO)


@router.put("/inventario", response_model=PreferenciaInventario)
async def guardar_inventario(request: Request, payload: PreferenciaInventario):
    await envio.guardar_preferencia(_pool(request), "inventario", payload.model_dump())
    return payload


# --- Envío automático al cliente ---------------------------------------------------------

async def _envio(funcion, *args, **kwargs):
    try:
        return await funcion(*args, **kwargs)
    except (envio.EnvioError, DatosInvalidosError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


async def _config_envio(pool) -> dict:
    correo = await envio.leer_preferencia(pool, "correo", envio.CORREO_POR_DEFECTO)
    whatsapp = await envio.leer_preferencia(pool, "whatsapp", envio.WHATSAPP_POR_DEFECTO)
    return {
        "correo": envio.correo_publico(correo),
        "whatsapp": envio.whatsapp_publico(whatsapp),
        "enviados_mes": await envio.enviados_del_mes(pool),
        "cifrado_disponible": certificado_activo.cifrado_disponible(),
    }


@router.get("/envio", response_model=EnvioConfig)
async def obtener_envio(request: Request):
    return await _config_envio(_pool(request))


@router.put("/envio/correo", response_model=EnvioConfig)
async def guardar_correo(request: Request, payload: CorreoPayload):
    await _envio(envio.guardar_correo, _pool(request), payload.model_dump())
    return await _config_envio(_pool(request))


@router.post("/envio/correo/prueba", response_model=PruebaResultado)
async def probar_correo(request: Request, payload: PruebaPayload, settings: ErpApiSettings = Depends(get_settings)):
    """El correo de prueba va al usuario con sesión (su correo del token):
    así el servidor SMTP de la empresa no sirve para escribirle a cualquiera.
    Un administrador (`app_metadata.kontara_admin` o KONTARA_ADMIN_EMAILS)
    puede indicar otra dirección. Sin inicio de sesión configurado, la que
    se indique."""
    actual = _usuario_actual(request, settings)
    if actual is not None:
        propio = (actual.correo or "").strip().lower()
        if payload.destino.strip().lower() != propio and not actual.admin:
            raise HTTPException(
                status_code=403,
                detail=f"El correo de prueba solo se envía a su propio correo ({actual.correo or 'sin correo'})",
            )
    config = await envio.leer_preferencia(_pool(request), "correo", envio.CORREO_POR_DEFECTO)
    empresa = await repo.obtener_empresa(_pool(request)) or {}
    nombre = empresa.get("nombre_comercial") or empresa.get("razon_social") or "Kontara"
    await _envio(
        envio.enviar_correo,
        config,
        payload.destino,
        f"{nombre}: correo de prueba",
        "Este es un correo de prueba de Kontara. Si lo recibe, el envío automático de comprobantes está listo.",
    )
    return {"ok": True, "mensaje": f"Correo de prueba enviado a {payload.destino}"}


@router.put("/envio/whatsapp", response_model=EnvioConfig)
async def guardar_whatsapp(request: Request, payload: WhatsappPayload):
    await _envio(envio.guardar_whatsapp, _pool(request), payload.model_dump())
    return await _config_envio(_pool(request))


@router.delete("/envio/whatsapp", response_model=EnvioConfig)
async def desconectar_whatsapp(request: Request):
    await _envio(envio.guardar_whatsapp, _pool(request), {}, desconectar=True)
    return await _config_envio(_pool(request))


@router.post("/envio/whatsapp/prueba", response_model=PruebaResultado)
async def probar_whatsapp(request: Request, payload: PruebaPayload):
    config = await envio.leer_preferencia(_pool(request), "whatsapp", envio.WHATSAPP_POR_DEFECTO)
    await _envio(envio.enviar_whatsapp_prueba, config, payload.destino)
    return {"ok": True, "mensaje": f"Mensaje de prueba enviado a {payload.destino}"}


async def iniciar_envio_automatico(pool, settings: ErpApiSettings) -> None:
    """Tarea de fondo del lifespan (main.py)."""

    async def nombre_empresa() -> str:
        fila = await repo.obtener_empresa(pool) or {}
        return fila.get("nombre_comercial") or fila.get("razon_social") or settings.emisor_razon_social or "Kontara"

    await envio.bucle_envio_automatico(pool, settings, nombre_empresa)
