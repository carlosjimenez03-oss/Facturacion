from pydantic import BaseModel


class MensajeValidacion(BaseModel):
    codigo: str
    valor: str


class RecepcionRFCEResponse(BaseModel):
    """Respuesta del servicio POST /api/recepcion/ecf (Recepción RFCE).

    `secuenciaUtilizada=True` implica que, si el resultado fue Rechazado, la
    secuencia (e-NCF) ya NO puede reutilizarse en otro RFCE.
    """

    codigo: int
    estado: str
    mensajes: list[MensajeValidacion] = []
    encf: str | None = None
    secuenciaUtilizada: bool | None = None
