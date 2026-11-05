"""Diagnóstico de configuración: valores de relleno y variables peligrosas.

Cubre `libs/ecf_common/diagnostico.py` y el aviso que `EcfBaseSettings`
deja en el log al arrancar cada servicio.
"""
import logging

import pytest

from libs.ecf_common.diagnostico import (
    MINIMO_TOKEN_INTERNO,
    advertencias_de_configuracion,
    es_placeholder,
    rnc_valido,
)
from libs.ecf_common.settings import EcfBaseSettings


@pytest.mark.parametrize(
    "valor",
    [
        "<deje la que ya tiene>",
        "<pendiente>",
        "cambiame",
        "CHANGEME",
        "tu-password",
        "xxxxxxxx",
    ],
)
def test_es_placeholder_verdadero(valor):
    assert es_placeholder(valor) is True


@pytest.mark.parametrize("valor", ["", "   ", None, "S3cr3t0!", "clave-del-certificado"])
def test_es_placeholder_falso(valor):
    assert es_placeholder(valor) is False


@pytest.mark.parametrize("rnc", ["131234567", "130123456"])
def test_rnc_valido_verdadero(rnc):
    assert rnc_valido(rnc) is True


@pytest.mark.parametrize("rnc", ["", None, "13123456", "1312345678", "13123A567"])
def test_rnc_valido_falso(rnc):
    assert rnc_valido(rnc) is False


def _config_valida() -> dict:
    return {
        "app_env": "production",
        "allowed_origins": ["https://front.acentosdeco.lat"],
        "certificado_password": "clave-del-certificado",
        "emisor_rnc": "131234567",
        "kontara_token_interno": "a" * MINIMO_TOKEN_INTERNO,
        "require_auth": True,
    }


def test_sin_avisos_cuando_la_configuracion_esta_completa():
    assert advertencias_de_configuracion(**_config_valida()) == []


def test_app_env_test_avisa():
    avisos = advertencias_de_configuracion(**{**_config_valida(), "app_env": "test"})
    assert any("APP_ENV=test" in aviso for aviso in avisos)


def test_app_env_desconocido_avisa():
    avisos = advertencias_de_configuracion(**{**_config_valida(), "app_env": "staging"})
    assert any("no es un ambiente conocido" in aviso for aviso in avisos)


def test_origenes_comodin_avisa():
    avisos = advertencias_de_configuracion(**{**_config_valida(), "allowed_origins": ["*"]})
    assert any("ALLOWED_ORIGINS" in aviso for aviso in avisos)


def test_password_de_relleno_avisa():
    config = {**_config_valida(), "certificado_password": "<deje la que ya tiene>"}
    avisos = advertencias_de_configuracion(**config)
    assert any("CERTIFICADO_PASSWORD" in aviso for aviso in avisos)


def test_rnc_vacio_avisa():
    avisos = advertencias_de_configuracion(**{**_config_valida(), "emisor_rnc": ""})
    assert any("EMISOR_RNC está vacío" in aviso for aviso in avisos)


@pytest.mark.parametrize("rnc", ["101000000", "000000000", "<rnc-del-emisor>"])
def test_rnc_de_relleno_avisa(rnc):
    avisos = advertencias_de_configuracion(**{**_config_valida(), "emisor_rnc": rnc})
    assert any("de relleno" in aviso for aviso in avisos)


def test_rnc_sin_formato_avisa():
    avisos = advertencias_de_configuracion(**{**_config_valida(), "emisor_rnc": "13123456"})
    assert any("9 dígitos" in aviso for aviso in avisos)


def test_token_interno_vacio_avisa():
    avisos = advertencias_de_configuracion(**{**_config_valida(), "kontara_token_interno": ""})
    assert any("KONTARA_TOKEN_INTERNO está vacío" in aviso for aviso in avisos)


def test_token_interno_corto_avisa():
    avisos = advertencias_de_configuracion(
        **{**_config_valida(), "kontara_token_interno": "corto"}
    )
    assert any("menos de" in aviso for aviso in avisos)


def test_require_auth_apagado_avisa():
    avisos = advertencias_de_configuracion(**{**_config_valida(), "require_auth": False})
    assert any("REQUIRE_AUTH=false" in aviso for aviso in avisos)


def test_settings_deja_los_avisos_en_el_log(caplog):
    with caplog.at_level(logging.WARNING, logger="libs.ecf_common.settings"):
        EcfBaseSettings(
            _env_file=None,
            app_env="production",
            allowed_origins=["https://front.acentosdeco.lat"],
            certificado_password="<deje la que ya tiene>",
            emisor_rnc="101000000",
            kontara_token_interno="a" * MINIMO_TOKEN_INTERNO,
        )

    mensajes = [record.getMessage() for record in caplog.records]
    assert any("CERTIFICADO_PASSWORD" in mensaje for mensaje in mensajes)
    assert any("EMISOR_RNC" in mensaje for mensaje in mensajes)
