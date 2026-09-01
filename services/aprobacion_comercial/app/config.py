from functools import lru_cache

from libs.ecf_common.settings import EcfBaseSettings, load_settings


class AprobacionComercialSettings(EcfBaseSettings):
    app_name: str = "Facturación Electrónica RD - Aprobación Comercial"


@lru_cache
def get_settings() -> AprobacionComercialSettings:
    return load_settings(AprobacionComercialSettings)
