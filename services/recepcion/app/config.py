from functools import lru_cache

from libs.ecf_common.settings import EcfBaseSettings, load_settings


class RecepcionSettings(EcfBaseSettings):
    app_name: str = "Facturación Electrónica RD - Recepción de e-CF"


@lru_cache
def get_settings() -> RecepcionSettings:
    return load_settings(RecepcionSettings)
