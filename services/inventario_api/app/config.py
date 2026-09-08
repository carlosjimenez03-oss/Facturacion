from functools import lru_cache

from libs.ecf_common.settings import EcfBaseSettings, load_settings


class InventarioApiSettings(EcfBaseSettings):
    app_name: str = "Facturación Electrónica RD - Inventario (API)"


@lru_cache
def get_settings() -> InventarioApiSettings:
    return load_settings(InventarioApiSettings)
