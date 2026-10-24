from functools import lru_cache

from libs.ecf_common.settings import EcfBaseSettings, load_settings


class ErpApiSettings(EcfBaseSettings):
    app_name: str = "Facturación Electrónica RD - ERP (API)"
    # Emisor 100 % electrónico (variable EMISOR_100_ELECTRONICO): solo emite
    # e-CF, así que el 607 y el 608 no le aplican (DGII, Comunidad de Ayuda;
    # Norma General 01-20 y Ley 32-23) y las declaraciones salen "no_aplica".
    # Póngalo en false si la empresa todavía emite o anula NCF serie B.
    emisor_100_electronico: bool = True

    # URL interna (red Docker) del módulo `aprobacion_comercial`, al que la
    # bandeja de entrada de Compras le delega firmar y enviar a la DGII la
    # aprobación/rechazo comercial (ACECF) de un e-CF recibido (POST
    # /aprobacion). En docker-compose resuelve al nombre del servicio.
    aprobacion_comercial_url: str = "http://aprobacion-comercial:8000"
    aprobacion_comercial_timeout_segundos: float = 60.0

    # Consulta Directorio (`consulta_directorio`, GET /directorio/{rnc}): da
    # la `urlAceptacion` del proveedor para entregarle el ACECF aprobado o
    # rechazado (best-effort, después de que la DGII lo acepta). En
    # docker-compose resuelve al nombre del servicio.
    consulta_directorio_url: str = "http://consulta-directorio:8000"
    entrega_emisor_timeout_segundos: float = 30.0

    # Razón social de la empresa (agente de retención) para el certificado
    # de retención; vacío = el frontend usa la que tenga configurada.
    emisor_razon_social: str = ""

    # --- Plataforma (fase 6): usuarios y estado de las soluciones ---
    #
    # Gateway de Supabase por la red interna `supabase` (servicio `api-gw`
    # del recurso de Supabase en Dokploy) y su SERVICE_ROLE_KEY, para
    # administrar usuarios con la API admin de GoTrue
    # (/auth/v1/admin/users). La llave SOLO vive aquí, nunca en el frontend.
    # Vacía = la pantalla Usuarios explica cómo hacerlo en Supabase Studio.
    supabase_url: str = "http://api-gw:8000"
    supabase_service_role_key: str = ""
    supabase_timeout_segundos: float = 10.0

    # facturas-api por la red interna: el envío automático por correo le pide
    # el PDF de cada factura aceptada. Cada cuántos segundos corre esa tarea
    # (0 = apagada; además hay que encenderla en Configuración).
    facturas_api_url: str = "http://facturas-api:8000"
    envio_automatico_cada_segundos: int = 300

    # Healthchecks de "Estado de las soluciones" (GET /estado-servicios):
    # cada servicio de la red interna con su /health. Segundos por consulta.
    estado_servicios_timeout_segundos: float = 2.5
    estado_servicios: dict[str, str] = {
        "facturas-api": "http://facturas-api:8000",
        "inventario-api": "http://inventario-api:8000",
        "formato-606": "http://formato-606:8000",
        "autenticacion": "http://autenticacion:8000",
        "recepcion": "http://recepcion:8000",
        "recepcion-rfce": "http://recepcion-rfce:8000",
        "consulta-resultado": "http://consulta-resultado:8000",
        "consulta-estado": "http://consulta-estado:8000",
        "consulta-trackids": "http://consulta-trackids:8000",
        "consulta-rfce": "http://consulta-rfce:8000",
        "consulta-directorio": "http://consulta-directorio:8000",
        "consulta-timbre": "http://consulta-timbre:8000",
        "aprobacion-comercial": "http://aprobacion-comercial:8000",
        "anulacion-encf": "http://anulacion-encf:8000",
        "receptor-autenticacion": "http://receptor-autenticacion:8000",
        "receptor-recepcion": "http://receptor-recepcion:8000",
        "receptor-aprobacion-comercial": "http://receptor-aprobacion-comercial:8000",
    }


@lru_cache
def get_settings() -> ErpApiSettings:
    return load_settings(ErpApiSettings)
