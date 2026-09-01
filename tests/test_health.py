def test_health_reports_test_environment(client):
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["app_env"] == "test"
    assert body["dgii_environment"] == "testecf"
