from pydantic import BaseModel


class TimbreURLResponse(BaseModel):
    """URL de verificación pública a codificar en el QR de la representación impresa (RI)."""

    url: str


class TimbreContenidoResponse(BaseModel):
    """Contenido crudo devuelto por la URL pública de verificación de la DGII.

    La "Descripción Técnica Servicios DGII" no publica un esquema JSON para
    este servicio (a diferencia del resto): describe únicamente los campos
    que la página de verificación muestra (RNC Emisor, Razón social, e-NCF,
    Monto Total, Estado, etc.), pensada para abrirse en un navegador al
    escanear el QR. Por eso este módulo retorna el contenido y content-type
    tal cual los entrega la DGII, sin forzar un parseo a campos específicos.
    """

    content_type: str
    contenido: str
