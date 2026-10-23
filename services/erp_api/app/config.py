from functools import lru_cache

from libs.ecf_common.settings import EcfBaseSettings, load_settings


class ErpApiSettings(EcfBaseSettings):
    app_name: str = "Facturación Electrónica RD - ERP (API)"


@lru_cache
def get_settings() -> ErpApiSettings:
    return load_settings(ErpApiSettings)
