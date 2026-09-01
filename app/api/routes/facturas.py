from fastapi import APIRouter, Depends

from app.config import Settings, get_settings
from app.services.dgii_client import DGIIClient

router = APIRouter(prefix="/facturas", tags=["facturas"])


@router.get("/{rnc_emisor}/{encf}/estado")
async def consultar_estado_ecf(
    rnc_emisor: str, encf: str, settings: Settings = Depends(get_settings)
) -> dict:
    client = DGIIClient(settings)
    response = await client.consultar_estado(rnc_emisor, encf)
    return {
        "ambiente_dgii": settings.dgii_environment.value,
        "status_code": response.status_code,
        "body": response.text,
    }
