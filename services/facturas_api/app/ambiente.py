"""Ambiente DGII de facturas_api (ver libs/ecf_common/ambiente.py).

- **Al crear** una factura se guarda el ambiente activo
  (`facturacion.facturas.ambiente`): su e-NCF sale de la secuencia de ese
  ambiente (`configuracion.secuencias` es por ambiente) y la unicidad del
  e-NCF también es por ambiente.
- **Después** todo usa el ambiente de la factura, no el activo: el envío a
  `recepcion`, la consulta del resultado, la auditoría y el QR de la
  representación impresa. Así, un e-CF enviado a TesteCF que sigue "En
  Proceso" cuando el administrador pasa a eCF se sigue consultando en TesteCF.
- **No se envía** una factura que todavía no llegó a la DGII si se creó en
  otro ambiente que el activo (409, `validar_para_enviar`): su e-NCF es de la
  secuencia de ese otro ambiente.
"""
from __future__ import annotations

from typing import Any

from libs.ecf_common.ambiente import AmbienteDGII, nombre
from libs.ecf_common.environments import DGIIEnvironment, dgii_environment_for

from .config import FacturasApiSettings, get_settings

ACTIVO = AmbienteDGII(get_settings().app_env)


class AmbienteDistintoError(Exception):
    """La factura es de otro ambiente que el activo (el mensaje va al usuario, 409)."""


def de_factura(factura: dict[str, Any], settings: FacturasApiSettings) -> str:
    return factura.get("ambiente") or settings.app_env


def dgii_de_factura(factura: dict[str, Any], settings: FacturasApiSettings) -> DGIIEnvironment:
    try:
        return dgii_environment_for(de_factura(factura, settings))
    except ValueError:
        return settings.dgii_environment


async def validar_para_enviar(pool, factura: dict[str, Any]) -> None:
    activo = await ACTIVO.actual(pool)
    propio = factura.get("ambiente")
    if propio and propio != activo:
        raise AmbienteDistintoError(
            f"Esta factura se creó en el ambiente {nombre(propio)} y el ambiente activo es {nombre(activo)}: "
            "su e-NCF es de la secuencia de aquel ambiente y no se envía. Cree una factura nueva."
        )
