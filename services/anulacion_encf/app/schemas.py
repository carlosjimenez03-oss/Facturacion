from pydantic import BaseModel


class AnulacionRangoResponse(BaseModel):
    """Respuesta de POST /api/operaciones/anularrango (Anulación de e-NCF, ANECF)."""

    rnc: str | None = None
    codigo: str | None = None
    nombre: str | None = None
    mensajes: list[str] = []
