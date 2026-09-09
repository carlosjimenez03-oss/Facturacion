from functools import lru_cache

from libs.ecf_common.settings import EcfBaseSettings, load_settings


class ReceptorAutenticacionSettings(EcfBaseSettings):
    app_name: str = "Facturación Electrónica RD - Receptor: Autenticación"

    # Vigencia de una semilla emitida por GET /fe/autenticacion/api/semilla
    # antes de que expire si nadie la devuelve firmada (anti-replay, ver
    # libs/ecf_common/auth/semilla_store.py).
    semilla_ttl_segundos: int = 300


@lru_cache
def get_settings() -> ReceptorAutenticacionSettings:
    return load_settings(ReceptorAutenticacionSettings)
