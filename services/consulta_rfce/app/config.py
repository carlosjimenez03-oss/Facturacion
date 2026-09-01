from functools import lru_cache

from libs.ecf_common.settings import EcfBaseSettings, load_settings


class ConsultaRFCESettings(EcfBaseSettings):
    app_name: str = "Facturación Electrónica RD - Consulta RFCE"


@lru_cache
def get_settings() -> ConsultaRFCESettings:
    return load_settings(ConsultaRFCESettings)
