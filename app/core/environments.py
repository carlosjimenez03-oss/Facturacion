"""Mapeo de los ambientes de la aplicacion a los ambientes oficiales de e-CF de la DGII.

La DGII (Republica Dominicana) expone tres ambientes para el Comprobante Fiscal
Electronico (e-CF), cada uno con su propia base URL bajo ecf.dgii.gov.do:

- TesteCF: ambiente de pruebas del contribuyente, sin validez fiscal.
- CerteCF: ambiente de certificacion formal ante la DGII, previo a produccion.
- eCF:     ambiente de produccion, comprobantes con validez fiscal.

Estas URLs deben confirmarse contra el Reporte Tecnico e-CF vigente publicado
por la DGII (https://dgii.gov.do), ya que la DGII las ha actualizado entre
versiones del estandar.
"""
from enum import Enum


class DGIIEnvironment(str, Enum):
    TESTECF = "testecf"
    CERTECF = "certecf"
    ECF = "ecf"


DGII_BASE_URLS: dict[DGIIEnvironment, str] = {
    DGIIEnvironment.TESTECF: "https://ecf.dgii.gov.do/testecf",
    DGIIEnvironment.CERTECF: "https://ecf.dgii.gov.do/certecf",
    DGIIEnvironment.ECF: "https://ecf.dgii.gov.do/ecf",
}

DGII_API_PATHS: dict[str, str] = {
    "semilla": "/autenticacion/api/semilla",
    "validar_certificado": "/autenticacion/api/validacioncertificado",
    "recepcion": "/recepcion/api/ecf",
    "consulta_estado": "/consultas/api/consultas/estado",
    "consulta_resultado": "/consultas/api/consultas/resultado",
    "aprobacion_comercial": "/aprobacioncomercial/api/aprobacioncomercial",
}

# Ambiente de esta aplicacion (test / qa / production) -> ambiente DGII correspondiente.
APP_ENV_TO_DGII_ENV: dict[str, DGIIEnvironment] = {
    "test": DGIIEnvironment.TESTECF,
    "qa": DGIIEnvironment.CERTECF,
    "production": DGIIEnvironment.ECF,
}


def dgii_base_url(dgii_environment: DGIIEnvironment) -> str:
    return DGII_BASE_URLS[dgii_environment]
