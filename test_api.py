from fastapi.testclient import TestClient
from src.api_server import SharedApiState, create_api_app


def test_health():
    state = SharedApiState()
    app = create_api_app(state)
    client = TestClient(app)

    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_sensors_shape_and_update():
    state = SharedApiState()
    app = create_api_app(state)
    client = TestClient(app)

    # initial
    r = client.get("/api/sensors")
    assert r.status_code == 200
    j = r.json()
    assert "system_status" in j
    assert "sensors" in j
    assert isinstance(j["sensors"], list)

    # update one sensor
    state.update_sensor("Temp_C", {"value": 25.0, "ts": "t", "status": "OK", "low": 0, "high": 100})
    r2 = client.get("/api/sensors")
    j2 = r2.json()
    assert j2["alarms_count"] == 0
    assert any(s["name"] == "Temp_C" for s in j2["sensors"])


def test_alarms_output():
    state = SharedApiState()
    app = create_api_app(state)
    client = TestClient(app)

    state.add_alarm({"time": "t", "sensor": "Temp_C", "value": 999, "type": "HIGH_LIMIT"})
    r = client.get("/api/alarms")
    assert r.status_code == 200
    j = r.json()
    assert j["count"] == 1
    assert j["alarms"][0]["sensor"] == "Temp_C"
