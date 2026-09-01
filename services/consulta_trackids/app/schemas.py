from pydantic import BaseModel


class TrackingDetalle(BaseModel):
    """Un elemento de la lista retornada por Consulta de trackId e-CF.

    Estados: No encontrado, Aceptado, Rechazado, Aceptado Condicional,
    En proceso (~200ms promedio de validación).
    """

    trackId: str
    estado: str
    fechaRecepcion: str
