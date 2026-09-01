from functools import lru_cache

from libs.ecf_common.settings import EcfBaseSettings, load_settings


class AutenticacionSettings(EcfBaseSettings):
    app_name: str = "Facturación Electrónica RD - Autenticación"

    # Duración informativa del cache local del token; la DGII es quien decide
    # la expiración real (campo `expira` de la respuesta de ValidarSemilla).
    token_cache_margen_segundos: int = 60


@lru_cache
def get_settings() -> AutenticacionSettings:
    return load_settings(AutenticacionSettings)
