import json
import random
import socket
import time
from datetime import datetime


HOST = "127.0.0.1"   # Dashboard address (server)
PORT = 9000          # Dashboard listening port

SENSORS = [
    {"name": "Temp_C", "base": 25.0, "noise": 0.4},
    {"name": "Pressure_bar", "base": 1.7, "noise": 0.05},
    {"name": "Vibration_mm_s", "base": 2.0, "noise": 0.3},
    {"name": "Speed_rpm", "base": 1200.0, "noise": 25.0},
    {"name": "Optical_count", "base": 60.0, "noise": 6.0},
]


def iso_ts() -> str:
    return datetime.now().isoformat(timespec="milliseconds")


def generate_value(sensor) -> float:
    drift = 0.5 * random.uniform(-1, 1)
    return sensor["base"] + drift + random.gauss(0, sensor["noise"])


def maybe_alarm_value(value: float) -> float:
    # ~2% chance to force out-of-range spikes
    if random.random() < 0.02:
        return value * 0.3 if random.random() < 0.5 else value * 1.8 + 10
    return value


def maybe_fault_status() -> str:
    # ~1% chance to mark sensor as FAULT
    return "FAULT" if random.random() < 0.01 else "OK"


def run_client():
    while True:
        try:
            print(f"[SIM] Connecting to dashboard at {HOST}:{PORT} ...")
            with socket.create_connection((HOST, PORT), timeout=5) as sock:
                sock.settimeout(2.0)
                print("[SIM] Connected. Streaming sensor data...")

                while True:
                    for s in SENSORS:
                        val = generate_value(s)
                        val = maybe_alarm_value(val)
                        status = maybe_fault_status()

                        msg = {
                            "sensor": s["name"],
                            "value": float(val),
                            "ts": iso_ts(),
                            "status": status,
                        }
                        sock.sendall((json.dumps(msg) + "\n").encode("utf-8"))

                    time.sleep(0.1)

        except (ConnectionRefusedError, TimeoutError, OSError) as e:
            print(f"[SIM] Connection failed or dropped: {e}. Retrying in 1s...")
            time.sleep(1.0)
        except KeyboardInterrupt:
            print("\n[SIM] Stopped by user (Ctrl+C).")
            return


if __name__ == "__main__":
    run_client()
