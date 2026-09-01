from pydantic import BaseModel


class MensajeValidacion(BaseModel):
    codigo: int
    valor: str


class ConsultaRFCEResponse(BaseModel):
    """Respuesta de GET /api/Consultas/Consulta (Consulta de RFCE).

    Estados posibles: No encontrado(0), Aceptado(1), Rechazado(2); también
    "Aceptado condicional" temporalmente para Facturas de Consumo < RD$250,000.
    """

    rnc: str | None = None
    encf: str | None = None
    secuenciaUtilizada: bool | None = None
    codigo: str | None = None
    estado: str | None = None
    mensajes: list[MensajeValidacion] = []
