from functools import lru_cache

from libs.ecf_common.settings import EcfBaseSettings, load_settings


class FacturasApiSettings(EcfBaseSettings):
    app_name: str = "Facturación Electrónica RD - Facturas (API)"

    # URL interna (red Docker) del módulo `recepcion`, al que este servicio
    # le delega firmar y enviar el e-CF a la DGII vía POST /ecf. En
    # docker-compose.dokploy.yml resuelve al nombre del servicio Compose.
    recepcion_service_url: str = "http://recepcion:8000"


@lru_cache
def get_settings() -> FacturasApiSettings:
    return load_settings(FacturasApiSettings)
