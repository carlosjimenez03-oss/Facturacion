import pytest

from libs.ecf_common.environments import (
    DGIIEnvironment,
    DGIIService,
    dgii_environment_for,
    dgii_service_base_url,
)


@pytest.mark.parametrize(
    "app_env,esperado",
    [
        ("test", DGIIEnvironment.TESTECF),
        ("qa", DGIIEnvironment.CERTECF),
        ("production", DGIIEnvironment.ECF),
    ],
)
def test_dgii_environment_for(app_env, esperado):
    assert dgii_environment_for(app_env) is esperado


def test_dgii_environment_for_invalido():
    with pytest.raises(ValueError):
        dgii_environment_for("staging")


def test_autenticacion_base_url_ecf_domain():
    url = dgii_service_base_url(DGIIService.AUTENTICACION, DGIIEnvironment.TESTECF)
    assert url == "https://ecf.dgii.gov.do/testecf/autenticacion"


def test_recepcion_rfce_usa_dominio_fc():
    url = dgii_service_base_url(DGIIService.RECEPCION_RFCE, DGIIEnvironment.ECF)
    assert url == "https://fc.dgii.gov.do/ecf/recepcionfc"


def test_servicio_restringido_en_ambiente_no_documentado():
    with pytest.raises(ValueError):
        dgii_service_base_url(DGIIService.CONSULTA_ESTADO, DGIIEnvironment.CERTECF)


def test_servicio_restringido_permitido_en_ambiente_documentado():
    url = dgii_service_base_url(DGIIService.CONSULTA_ESTADO, DGIIEnvironment.TESTECF)
    assert url == "https://ecf.dgii.gov.do/testecf/consultaestado"
