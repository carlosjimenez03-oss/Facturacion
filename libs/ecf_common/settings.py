"""Configuración base compartida por todos los módulos/servicios.

Cada servicio (services/<modulo>/app/config.py) extiende `EcfBaseSettings`
para añadir los parámetros propios de ese módulo. El ambiente activo se
selecciona con la variable de entorno APP_ENV (test|qa|production), que
determina qué archivo .env.<ambiente> se carga (en la raíz del repo) y a qué
ambiente de la DGII se conecta el servicio.
"""
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from libs.ecf_common.environments import DGIIEnvironment, dgii_environment_for

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


class EcfBaseSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    app_env: str = "test"
    debug: bool = True
    log_level: str = "INFO"

    # Identidad del emisor autorizado por la DGII y su certificado digital
    # (INDOTEL) para firmar los archivos XML. En QA/producción se inyectan
    # como secretos (CI/CD, vault, etc.), nunca se commitean con valores reales.
    emisor_rnc: str = ""
    certificado_path: str = "certs/emisor.p12"
    certificado_password: str = ""

    allowed_origins: list[str] = ["*"]

    @property
    def dgii_environment(self) -> DGIIEnvironment:
        return dgii_environment_for(self.app_env)

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


def env_file_for(app_env: str) -> Path | None:
    env_file = REPO_ROOT / f".env.{app_env}"
    return env_file if env_file.exists() else None
