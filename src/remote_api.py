from fastapi import FastAPI
from typing import Any, Dict, List
import threading


class SharedApiState:
    """
    Thread-safe shared state:
    - GUI thread WRITES updates (latest sensor snapshot + alarms)
    - API thread READS snapshots and returns JSON
    """
    def __init__(self):
        self._lock = threading.Lock()
        self._system_status: str = "CONNECTING"
        self._sensors: Dict[str, Dict[str, Any]] = {}  # name -> snapshot
        self._alarms: List[Dict[str, Any]] = []         # list of alarm dicts

    def update_sensor(self, name: str, snapshot: Dict[str, Any]) -> None:
        with self._lock:
            self._sensors[name] = dict(snapshot)

    def set_system_status(self, status: str) -> None:
        with self._lock:
            self._system_status = status

    def add_alarm(self, alarm: Dict[str, Any], cap: int = 500) -> None:
        with self._lock:
            self._alarms.append(dict(alarm))
            if len(self._alarms) > cap:
                self._alarms = self._alarms[-cap:]

    def snapshot_sensors(self) -> Dict[str, Any]:
        with self._lock:
            sensors_list = [{"name": k, **v} for k, v in self._sensors.items()]
            return {
                "system_status": self._system_status,
                "sensors": sensors_list,
                "alarms_count": len(self._alarms),
            }

    def snapshot_alarms(self, last_n: int = 200) -> Dict[str, Any]:
        with self._lock:
            data = self._alarms[-last_n:]
            return {"count": len(self._alarms), "alarms": list(data)}


def create_app(state: SharedApiState) -> FastAPI:
    app = FastAPI(title="Production Line Remote API", version="1.0")

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    @app.get("/api/sensors")
    def sensors():
        return state.snapshot_sensors()

    @app.get("/api/alarms")
    def alarms():
        return state.snapshot_alarms()

    return app
