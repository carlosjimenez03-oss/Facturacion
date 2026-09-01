from functools import lru_cache

from libs.ecf_common.settings import EcfBaseSettings, load_settings


class ConsultaTrackIdsSettings(EcfBaseSettings):
    app_name: str = "Facturación Electrónica RD - Consulta de trackId e-CF"


@lru_cache
def get_settings() -> ConsultaTrackIdsSettings:
    return load_settings(ConsultaTrackIdsSettings)
