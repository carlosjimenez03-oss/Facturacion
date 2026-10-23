from functools import lru_cache

from libs.ecf_common.settings import EcfBaseSettings, load_settings


class InventarioApiSettings(EcfBaseSettings):
    app_name: str = "Facturación Electrónica RD - Inventario (API)"
    # Días antes del vencimiento en que un lote pasa a "por vencer" (alertas
    # de la pantalla Lotes). Cada consulta puede pedir otro con `?dias=`.
    dias_alerta_vencimiento: int = 30


@lru_cache
def get_settings() -> InventarioApiSettings:
    return load_settings(InventarioApiSettings)
