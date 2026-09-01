from functools import lru_cache

from libs.ecf_common.settings import EcfBaseSettings, load_settings


class ConsultaDirectorioSettings(EcfBaseSettings):
    app_name: str = "Facturación Electrónica RD - Consulta Directorio de Servicios"


@lru_cache
def get_settings() -> ConsultaDirectorioSettings:
    return load_settings(ConsultaDirectorioSettings)
