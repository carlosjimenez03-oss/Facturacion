from functools import lru_cache

from libs.ecf_common.settings import EcfBaseSettings, load_settings


class Formato606Settings(EcfBaseSettings):
    app_name: str = "Facturación Electrónica RD - Formato 606 (Compras)"


@lru_cache
def get_settings() -> Formato606Settings:
    return load_settings(Formato606Settings)
