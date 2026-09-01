"""Configuracion de la aplicacion, cargada por ambiente via variables de entorno.

El ambiente activo se selecciona con la variable APP_ENV (test|qa|production) y
determina que archivo .env.<ambiente> se carga y a que ambiente de la DGII
(TesteCF/CerteCF/eCF) se conecta el servicio.
"""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.environments import APP_ENV_TO_DGII_ENV, DGIIEnvironment, dgii_base_url

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    app_env: str = "test"
    app_name: str = "Facturacion Electronica RD"
    debug: bool = True
    log_level: str = "INFO"

    # RNC y credenciales del emisor. En QA/produccion deben inyectarse como
    # secretos (CI/CD, vault, etc.), nunca commitearse con valores reales.
    emisor_rnc: str = ""
    dgii_api_user: str = ""
    dgii_api_password: str = ""

    # Certificado digital (INDOTEL) usado para firmar los e-CF.
    certificado_path: str = "certs/emisor.p12"
    certificado_password: str = ""

    # Base de datos / almacenamiento propio del servicio.
    database_url: str = "sqlite:///./facturacion.db"

    # CORS / host permitido segun ambiente.
    allowed_origins: list[str] = ["*"]

    @property
    def dgii_environment(self) -> DGIIEnvironment:
        return APP_ENV_TO_DGII_ENV.get(self.app_env, DGIIEnvironment.TESTECF)

    @property
    def dgii_base_url(self) -> str:
        return dgii_base_url(self.dgii_environment)

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


@lru_cache
def get_settings() -> Settings:
    import os

    app_env = os.getenv("APP_ENV", "test")
    env_file = BASE_DIR / f".env.{app_env}"
    return Settings(_env_file=env_file if env_file.exists() else None)  # type: ignore[call-arg]
