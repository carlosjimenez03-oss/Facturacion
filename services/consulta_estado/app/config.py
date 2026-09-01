from functools import lru_cache

from libs.ecf_common.settings import EcfBaseSettings, load_settings


class ConsultaEstadoSettings(EcfBaseSettings):
    app_name: str = "Facturación Electrónica RD - Consulta de estado e-CF"


@lru_cache
def get_settings() -> ConsultaEstadoSettings:
    return load_settings(ConsultaEstadoSettings)
