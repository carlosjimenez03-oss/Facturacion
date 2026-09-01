import pytest
from fastapi.testclient import TestClient

from services.consulta_timbre.app import main


@pytest.fixture
def client() -> TestClient:
    return TestClient(main.app)


def test_health(client):
    response = client.get("/health")

    assert response.status_code == 200


def test_construir_url_timbre_ecf(client):
    response = client.get(
        "/timbre/url",
        params={
            "rnc_emisor": "130000001",
            "rnc_comprador": "130000002",
            "encf": "e310000000001",
            "fecha_emision": "10-10-2020",
            "monto_total": "02.11",
            "fecha_firma": "10-10-2020 09:00:00",
            "codigo_seguridad": "dcp79q",
        },
    )

    assert response.status_code == 200
    assert response.json()["url"].startswith("https://ecf.dgii.gov.do/testecf/consultatimbre?")


def test_construir_url_timbre_fc(client):
    response = client.get(
        "/timbre-fc/url",
        params={
            "rnc_emisor": "131880738",
            "encf": "e320000000064",
            "monto_total": "6225.09",
            "codigo_seguridad": "uabnyh",
        },
    )

    assert response.status_code == 200
    assert response.json()["url"].startswith("https://fc.dgii.gov.do/testecf/consultatimbrefc?")
