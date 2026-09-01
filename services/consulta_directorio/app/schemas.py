from pydantic import BaseModel


class ContribuyenteDirectorio(BaseModel):
    """Un contribuyente electrónico autorizado y las URLs de sus servicios."""

    nombre: str
    rnc: str
    urlRecepcion: str
    urlAceptacion: str
    urlOpcional: str | None = None
