from pydantic import BaseModel


class ConsultaEstadoResponse(BaseModel):
    """Respuesta de GET /api/consultas/estado (Consulta de estado e-CF).

    Estados: No encontrado(0), Aceptado(1), Rechazado(2); Aceptado condicional
    para Facturas de Consumo < RD$250,000.
    """

    codigo: int | None = None
    estado: str | None = None
    rncEmisor: str | None = None
    ncfElectronico: str | None = None
    montoTotal: float | None = None
    totalITBIS: float | None = None
    fechaEmision: str | None = None
    fechaFirma: str | None = None
    rncComprador: str | None = None
    codigoSeguridad: str | None = None
    idExtranjero: str | None = None
