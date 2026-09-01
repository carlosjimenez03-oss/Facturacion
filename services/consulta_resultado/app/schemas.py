from pydantic import BaseModel


class MensajeValidacion(BaseModel):
    codigo: int
    valor: str


class ConsultaResultadoResponse(BaseModel):
    """Respuesta de GET /api/consultas/estado?trackid= (Consulta de resultado e-CF).

    Estados: No encontrado(0), Aceptado(1), Rechazado(2), En Proceso(3, ~200ms
    promedio de validación), Aceptado Condicional(4).
    """

    trackId: str | None = None
    codigo: int | None = None
    estado: str | None = None
    rnc: str | None = None
    eNCF: str | None = None
    secuenciaUtilizada: bool | None = None
    fechaRecepcion: str | None = None
    mensajes: list[MensajeValidacion] = []
