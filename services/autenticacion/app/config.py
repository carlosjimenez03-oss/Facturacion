from functools import lru_cache

from libs.ecf_common.settings import EcfBaseSettings, env_file_for


class AutenticacionSettings(EcfBaseSettings):
    app_name: str = "Facturación Electrónica RD - Autenticación"

    # Duración informativa del cache local del token; la DGII es quien decide
    # la expiración real (campo `expira` de la respuesta de ValidarSemilla).
    token_cache_margen_segundos: int = 60


@lru_cache
def get_settings() -> AutenticacionSettings:
    import os

    app_env = os.getenv("APP_ENV", "test")
    return AutenticacionSettings(_env_file=env_file_for(app_env))  # type: ignore[call-arg]
