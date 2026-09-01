from functools import lru_cache

from libs.ecf_common.settings import EcfBaseSettings, load_settings


class RecepcionRFCESettings(EcfBaseSettings):
    app_name: str = "Facturación Electrónica RD - Recepción RFCE"


@lru_cache
def get_settings() -> RecepcionRFCESettings:
    return load_settings(RecepcionRFCESettings)
