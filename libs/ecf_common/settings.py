"""Configuración base compartida por todos los módulos/servicios.

Cada servicio (services/<modulo>/app/config.py) extiende `EcfBaseSettings`
para añadir los parámetros propios de ese módulo. El ambiente activo se
selecciona con la variable de entorno APP_ENV (test|qa|production), que
determina qué archivo .env.<ambiente> se carga (en la raíz del repo) y a qué
ambiente de la DGII se conecta el servicio.
"""
import logging
import os
from pathlib import Path
from typing import Any, TypeVar

from pydantic_settings import BaseSettings, SettingsConfigDict

from libs.ecf_common.diagnostico import advertencias_de_configuracion
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
    # Razón social (EMISOR_RAZON_SOCIAL): nombre de la empresa 1 al crear el
    # registro de empresas si Configuración → Empresa todavía no tiene datos
    # (ver libs/ecf_common/multiempresa_ddl.py).
    emisor_razon_social: str = ""
    certificado_path: str = "certs/emisor.p12"
    certificado_password: str = ""

    allowed_origins: list[str] = ["*"]

    # Auditoría en PostgreSQL (opcional). Si queda vacío, los módulos que
    # registran envíos a la DGII simplemente no auditan — no es un
    # requisito duro para operar. Formato: postgresql://user:pass@host:5432/db
    database_url: str = ""

    # Sesión de los usuarios (Supabase Auth, ver docs/AUTH.md y
    # libs/ecf_common/auth/sesion_supabase.py). `supabase_jwt_secret` es el
    # JWT_SECRET del recurso de Supabase. Con `require_auth` (REQUIRE_AUTH)
    # la API misma rechaza las peticiones sin sesión, además de nginx;
    # apagado por defecto para no cortar las llamadas internas entre servicios.
    require_auth: bool = False
    supabase_jwt_secret: str = ""
    # Correos (separados por comas) que entran aunque su usuario de GoTrue
    # todavía no tenga `app_metadata.kontara` (el primer usuario, creado a
    # mano en Studio); erp-api le pone la marca en su primer request.
    kontara_admin_emails: str = ""
    # Secreto compartido de las llamadas internas entre servicios (cabecera
    # X-Kontara-Interno, 32+ caracteres): pasan el REQUIRE_AUTH sin sesión.
    # nginx borra esa cabecera de los requests del navegador.
    kontara_token_interno: str = ""

    def model_post_init(self, __context: Any) -> None:
        """Deja en el log los valores de configuración que parecen de relleno.

        No bloquea el arranque a propósito: un servicio que ya está sirviendo
        no debe caerse por una variable mal puesta (el 2026-11-05 producción
        arrancó con APP_ENV=test y un CERTIFICADO_PASSWORD de ejemplo). Los
        avisos quedan en el log del contenedor (Dokploy → Logs), nombrando la
        variable que hay que corregir. La única excepción es la contraseña del
        certificado: `libs/ecf_common.auth.signing` falla en firme al firmar,
        porque con un placeholder la firma no puede funcionar.
        """
        for aviso in advertencias_de_configuracion(
            app_env=self.app_env,
            allowed_origins=self.allowed_origins,
            certificado_password=self.certificado_password,
            emisor_rnc=self.emisor_rnc,
            kontara_token_interno=self.kontara_token_interno,
        ):
            logging.getLogger(__name__).warning("config: %s", aviso)

    @property
    def dgii_environment(self) -> DGIIEnvironment:
        return dgii_environment_for(self.app_env)

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


def env_file_for(app_env: str) -> Path | None:
    env_file = REPO_ROOT / f".env.{app_env}"
    return env_file if env_file.exists() else None


T = TypeVar("T", bound=EcfBaseSettings)


def load_settings(settings_cls: type[T]) -> T:
    """Instancia `settings_cls` cargando el .env.<APP_ENV> correspondiente.

    Uso típico en cada módulo (`services/<modulo>/app/config.py`):

        @lru_cache
        def get_settings() -> MiModuloSettings:
            return load_settings(MiModuloSettings)
    """
    app_env = os.getenv("APP_ENV", "test")
    return settings_cls(_env_file=env_file_for(app_env))  # type: ignore[call-arg]
