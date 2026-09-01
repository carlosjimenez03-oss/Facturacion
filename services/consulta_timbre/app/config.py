from functools import lru_cache

from libs.ecf_common.settings import EcfBaseSettings, load_settings


class ConsultaTimbreSettings(EcfBaseSettings):
    app_name: str = "Facturación Electrónica RD - Consulta Timbre (QR)"


@lru_cache
def get_settings() -> ConsultaTimbreSettings:
    return load_settings(ConsultaTimbreSettings)
