from functools import lru_cache

from libs.ecf_common.settings import EcfBaseSettings, load_settings


class ReceptorAprobacionComercialSettings(EcfBaseSettings):
    app_name: str = "Facturación Electrónica RD - Receptor: Aprobación Comercial"

    # Ver el mismo campo en services/receptor_recepcion/app/config.py.
    requiere_autenticacion: bool = True


@lru_cache
def get_settings() -> ReceptorAprobacionComercialSettings:
    return load_settings(ReceptorAprobacionComercialSettings)
