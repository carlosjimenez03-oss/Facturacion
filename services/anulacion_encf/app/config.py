from functools import lru_cache

from libs.ecf_common.settings import EcfBaseSettings, load_settings


class AnulacionSettings(EcfBaseSettings):
    app_name: str = "Facturación Electrónica RD - Anulación de e-NCF"


@lru_cache
def get_settings() -> AnulacionSettings:
    return load_settings(AnulacionSettings)
