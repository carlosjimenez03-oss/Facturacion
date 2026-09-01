"""Ambientes y catálogo de servicios web de la DGII (e-CF, República Dominicana).

Fuente: "Descripción Técnica Servicios DGII" (DGII, Versión 1.7, Mayo 2026),
documentado en detalle en docs/dgii/SERVICIOS_DGII.md. Cualquier cambio de
URL/endpoint debe actualizarse primero ahí y luego reflejarse aquí.
"""
from dataclasses import dataclass
from enum import Enum


class DGIIEnvironment(str, Enum):
    """Ambientes de facturación electrónica de la DGII."""

    TESTECF = "testecf"
    """Pre-Certificación: pruebas de adecuación/integración (envíos guardados 60 días)."""

    CERTECF = "certecf"
    """Certificación: valida capacidades del sistema antes de producción."""

    ECF = "ecf"
    """Producción: toda operación de envío tiene validez fiscal."""


# Ambiente de la aplicación (APP_ENV: test/qa/production) -> ambiente DGII.
APP_ENV_TO_DGII_ENV: dict[str, DGIIEnvironment] = {
    "test": DGIIEnvironment.TESTECF,
    "qa": DGIIEnvironment.CERTECF,
    "production": DGIIEnvironment.ECF,
}

# Dos dominios base según el servicio (ver docs/dgii/SERVICIOS_DGII.md).
ECF_DOMAIN = "https://ecf.dgii.gov.do"
FC_DOMAIN = "https://fc.dgii.gov.do"


@dataclass(frozen=True)
class DGIIServicePath:
    """Describe la ubicación de un servicio DGII dentro de su dominio/ambiente."""

    domain: str
    """ECF_DOMAIN o FC_DOMAIN."""

    segment: str
    """Segmento de URL tras {ambiente}/, p.ej. 'autenticacion'."""

    solo_ambientes: tuple[DGIIEnvironment, ...] | None = None
    """Si se especifica, ambientes en los que el documento indica que el
    servicio está disponible al momento de escribir esta referencia (algunos
    servicios no estaban públicos aún en CerteCF). Verificar contra la
    bitácora del PDF oficial antes de asumir que sigue vigente."""

    def base_url(self, ambiente: DGIIEnvironment) -> str:
        return f"{self.domain}/{ambiente.value}/{self.segment}"


class DGIIService(str, Enum):
    """Los 13 servicios web descritos en el documento técnico de la DGII."""

    AUTENTICACION = "autenticacion"
    RECEPCION_ECF = "recepcion_ecf"
    RECEPCION_RFCE = "recepcion_rfce"
    CONSULTA_RFCE = "consulta_rfce"
    CONSULTA_RESULTADO = "consulta_resultado"
    CONSULTA_ESTADO = "consulta_estado"
    CONSULTA_TRACKIDS = "consulta_trackids"
    APROBACION_COMERCIAL = "aprobacion_comercial"
    ANULACION_RANGOS = "anulacion_rangos"
    CONSULTA_DIRECTORIO = "consulta_directorio"
    CONSULTA_TIMBRE = "consulta_timbre"
    CONSULTA_TIMBRE_FC = "consulta_timbre_fc"
    EMISOR_RECEPTOR = "emisor_receptor"


DGII_SERVICE_PATHS: dict[DGIIService, DGIIServicePath] = {
    DGIIService.AUTENTICACION: DGIIServicePath(ECF_DOMAIN, "autenticacion"),
    DGIIService.RECEPCION_ECF: DGIIServicePath(ECF_DOMAIN, "recepcion"),
    DGIIService.RECEPCION_RFCE: DGIIServicePath(FC_DOMAIN, "recepcionfc"),
    DGIIService.CONSULTA_RFCE: DGIIServicePath(FC_DOMAIN, "consultarfce"),
    DGIIService.CONSULTA_RESULTADO: DGIIServicePath(ECF_DOMAIN, "consultaresultado"),
    DGIIService.CONSULTA_ESTADO: DGIIServicePath(
        ECF_DOMAIN,
        "consultaestado",
        solo_ambientes=(DGIIEnvironment.TESTECF, DGIIEnvironment.ECF),
    ),
    DGIIService.CONSULTA_TRACKIDS: DGIIServicePath(
        ECF_DOMAIN,
        "consultatrackids",
        solo_ambientes=(DGIIEnvironment.TESTECF, DGIIEnvironment.ECF),
    ),
    DGIIService.APROBACION_COMERCIAL: DGIIServicePath(ECF_DOMAIN, "aprobacioncomercial"),
    DGIIService.ANULACION_RANGOS: DGIIServicePath(
        ECF_DOMAIN,
        "anulacionrangos",
        solo_ambientes=(DGIIEnvironment.TESTECF, DGIIEnvironment.ECF),
    ),
    DGIIService.CONSULTA_DIRECTORIO: DGIIServicePath(
        ECF_DOMAIN,
        "consultadirectorio",
        solo_ambientes=(DGIIEnvironment.TESTECF, DGIIEnvironment.ECF),
    ),
    DGIIService.CONSULTA_TIMBRE: DGIIServicePath(ECF_DOMAIN, "consultatimbre"),
    DGIIService.CONSULTA_TIMBRE_FC: DGIIServicePath(FC_DOMAIN, "consultatimbrefc"),
    DGIIService.EMISOR_RECEPTOR: DGIIServicePath(
        ECF_DOMAIN, "testecf/emisorreceptor", solo_ambientes=(DGIIEnvironment.TESTECF,)
    ),
}


def dgii_environment_for(app_env: str) -> DGIIEnvironment:
    try:
        return APP_ENV_TO_DGII_ENV[app_env]
    except KeyError as exc:
        raise ValueError(
            f"APP_ENV={app_env!r} inválido; use 'test', 'qa' o 'production'"
        ) from exc


def dgii_service_base_url(service: DGIIService, ambiente: DGIIEnvironment) -> str:
    path = DGII_SERVICE_PATHS[service]
    if path.solo_ambientes is not None and ambiente not in path.solo_ambientes:
        raise ValueError(
            f"El servicio {service.value} no está documentado como disponible en "
            f"el ambiente {ambiente.value}; verificar contra la bitácora del PDF "
            "oficial de la DGII antes de usarlo."
        )
    return path.base_url(ambiente)
