import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient
from src.api_server import SharedApiState, create_api_app


def test_api_health():
    state = SharedApiState()
    app = create_api_app(state)
    client = TestClient(app)

    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_api_sensors_output():
    state = SharedApiState()
    app = create_api_app(state)
    client = TestClient(app)

    state.update_sensor("Temp_C", {"value": 25.0, "ts": "t", "status": "OK", "low": 0, "high": 100})

    r = client.get("/api/sensors")
    assert r.status_code == 200
    data = r.json()
    assert "system_status" in data
    assert "sensors" in data
    assert any(s["name"] == "Temp_C" for s in data["sensors"])


def test_api_alarms_output():
    state = SharedApiState()
    app = create_api_app(state)
    client = TestClient(app)

    state.add_alarm({"time": "t", "sensor": "Temp_C", "value": 999, "type": "HIGH_LIMIT"})

    r = client.get("/api/alarms")
    assert r.status_code == 200
    data = r.json()
    assert data["count"] == 1
    assert data["alarms"][0]["sensor"] == "Temp_C"
