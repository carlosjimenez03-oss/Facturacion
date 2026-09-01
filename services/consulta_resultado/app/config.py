from functools import lru_cache

from libs.ecf_common.settings import EcfBaseSettings, load_settings


class ConsultaResultadoSettings(EcfBaseSettings):
    app_name: str = "Facturación Electrónica RD - Consulta de resultado e-CF"


@lru_cache
def get_settings() -> ConsultaResultadoSettings:
    return load_settings(ConsultaResultadoSettings)
