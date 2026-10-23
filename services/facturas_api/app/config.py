from functools import lru_cache

from libs.ecf_common.settings import EcfBaseSettings, load_settings


class FacturasApiSettings(EcfBaseSettings):
    app_name: str = "Facturación Electrónica RD - Facturas (API)"

    # URL interna (red Docker) del módulo `recepcion`, al que este servicio
    # le delega firmar y enviar el e-CF a la DGII vía POST /ecf. En
    # docker-compose.dokploy.yml resuelve al nombre del servicio Compose.
    recepcion_service_url: str = "http://recepcion:8000"

    # URL interna (red Docker) de `inventario_api`, a la que este servicio le
    # pide validar disponibilidad de stock antes de enviar un e-CF y
    # registrar la salida de mercancía después de un envío exitoso. Ver
    # `app.inventario_client`.
    inventario_api_url: str = "http://inventario-api:8000"

    # URL interna (red Docker) de `consulta_resultado`, a la que este
    # servicio le pregunta el resultado de la validación de la DGII de cada
    # e-CF enviado (por su trackId) para pasarlo de 'enviado' a 'aceptado' o
    # 'rechazado'. Ver `app.estado_dgii`.
    consulta_resultado_url: str = "http://consulta-resultado:8000"

    # Cada cuántos segundos la tarea de fondo (iniciada en el lifespan)
    # consulta el resultado de las facturas que siguen en 'enviado'. 0 la
    # desactiva (solo queda la consulta inmediata tras enviar y el botón
    # "Actualizar estado DGII" del frontend).
    sincronizar_estado_cada_segundos: int = 120


@lru_cache
def get_settings() -> FacturasApiSettings:
    return load_settings(FacturasApiSettings)
