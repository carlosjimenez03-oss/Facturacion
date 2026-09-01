from pydantic import BaseModel


class RecepcionResponse(BaseModel):
    """Respuesta del servicio POST /api/facturaselectronicas (Recepción de e-CF).

    `error`/`mensaje` solo vienen presentes si la DGII rechazó el envío.
    """

    trackId: str | None = None
    error: str | None = None
    mensaje: str | None = None
