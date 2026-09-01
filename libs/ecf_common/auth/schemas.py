from pydantic import BaseModel


class SemillaModel(BaseModel):
    """Respuesta XML `SemillaModel` del servicio GET /api/autenticacion/semilla."""

    valor: str
    fecha: str


class TokenResponse(BaseModel):
    """Respuesta del servicio POST /api/autenticacion/validarsemilla.

    `expira`/`expedido` se mantienen como texto (no se parsean a datetime):
    la DGII los entrega con offset de zona horaria y hasta 7 dígitos de
    fracción de segundo, un formato no siempre compatible con un parseo
    estricto de ISO-8601.
    """

    token: str
    expira: str
    expedido: str
