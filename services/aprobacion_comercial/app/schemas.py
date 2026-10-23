from pydantic import BaseModel


class AprobacionComercialResponse(BaseModel):
    """Respuesta de POST /api/aprobacioncomercial (Recepción de aprobación comercial).

    Estados: Aprobación comercial aprobada(1), rechazada(2) — esta última
    también cuando la factura referenciada no fue encontrada.
    """

    mensaje: list[str] = []
    estado: str | None = None
    codigo: str | None = None
    # No viene de la DGII: el ACECF firmado que se le envió, para que quien
    # llama (erp-api) lo entregue también al emisor del e-CF.
    xml_firmado: str | None = None
