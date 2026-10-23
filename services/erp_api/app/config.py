from functools import lru_cache

from libs.ecf_common.settings import EcfBaseSettings, load_settings


class ErpApiSettings(EcfBaseSettings):
    app_name: str = "Facturación Electrónica RD - ERP (API)"
    # Emisor 100 % electrónico (variable EMISOR_100_ELECTRONICO): solo emite
    # e-CF, así que el 607 y el 608 no le aplican (DGII, Comunidad de Ayuda;
    # Norma General 01-20 y Ley 32-23) y las declaraciones salen "no_aplica".
    # Póngalo en false si la empresa todavía emite o anula NCF serie B.
    emisor_100_electronico: bool = True


@lru_cache
def get_settings() -> ErpApiSettings:
    return load_settings(ErpApiSettings)
