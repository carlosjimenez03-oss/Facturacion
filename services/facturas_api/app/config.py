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

    # Modo contingencia (ver app.contingencia y docs/dgii/CONTINGENCIA.md).
    # Timeout del envío a `recepcion` (que a su vez espera a la DGII) y de la
    # sonda corta con que se comprueba si la DGII volvió antes de enviar.
    timeout_envio_segundos: float = 60.0
    timeout_sonda_segundos: float = 5.0
    # Facturas por lote al enviar lo pendiente de una contingencia (las más
    # antiguas primero).
    contingencia_lote_tamano: int = 20
    # Plazo para enviar a la DGII los e-CF emitidos en contingencia por falta
    # de conectividad (Decreto 587-24, art. 40; Instructivo de Contingencia de
    # la DGII): 72 horas desde que se restablece la conexión.
    contingencia_horas_limite: int = 72


@lru_cache
def get_settings() -> FacturasApiSettings:
    return load_settings(FacturasApiSettings)
