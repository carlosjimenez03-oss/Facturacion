from functools import lru_cache

from libs.ecf_common.settings import EcfBaseSettings, load_settings


class ReceptorRecepcionSettings(EcfBaseSettings):
    app_name: str = "Facturación Electrónica RD - Receptor: Recepción de e-CF"

    # Si está en True, exige el header Authorization: Bearer <token> emitido
    # por receptor_autenticacion. El estándar lo hace condicional a que se
    # haya "declarado autenticación" -- si este contribuyente despliega
    # receptor_autenticacion, corresponde exigirlo aquí también.
    requiere_autenticacion: bool = True


@lru_cache
def get_settings() -> ReceptorRecepcionSettings:
    return load_settings(ReceptorRecepcionSettings)
