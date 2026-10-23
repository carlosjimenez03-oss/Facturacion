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


@lru_cache
def get_settings() -> ErpApiSettings:
    return load_settings(ErpApiSettings)
