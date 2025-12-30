import json
import random
import socket
import time
from datetime import datetime, timezone, timedelta

# Cairo timezone (+02:00) - nice for your demo timestamps
CAIRO_TZ = timezone(timedelta(hours=2))

SENSORS = [
    {"name": "Temp_C", "base": 25.0, "noise": 0.4},
    {"name": "Pressure_bar", "base": 1.7, "noise": 0.05},
    {"name": "Vibration_mm_s", "base": 2.0, "noise": 0.3},
    {"name": "Speed_rpm", "base": 1200.0, "noise": 25.0},
    {"name": "Optical_count", "base": 60.0, "noise": 6.0},
]

HOST = "127.0.0.1"
PORT = 9000

def iso_ts() -> str:
    return datetime.now(tz=CAIRO_TZ).isoformat(timespec="milliseconds")

def generate_value(sensor, t: float) -> float:
    # gentle drift + noise
    drift = 0.5 * random.uniform(-1, 1)
    return sensor["base"] + drift + random.gauss(0, sensor["noise"])

def maybe_alarm_value(name: str, value: float) -> float:
    # Occasionally force out-of-range values to test alarm logic
    # ~2% chance per reading
    if random.random() < 0.02:
        if random.random() < 0.5:
            return value * 0.3  # low spike
        else:
            return value * 1.8 + 10  # high spike
    return value

def maybe_fault_status() -> str:
    # ~1% chance to mark a packet as faulty sensor
    return "FAULT" if random.random() < 0.01 else "OK"

def main():
    print(f"[SIM] Starting TCP server on {HOST}:{PORT}")
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((HOST, PORT))
    server.listen(1)

    while True:
        print("[SIM] Waiting for client...")
        conn, addr = server.accept()
        print(f"[SIM] Client connected: {addr}")

        with conn:
            # send around 10 Hz per sensor => 50 msgs/sec total (fine for demo)
            # Each loop sends one message per sensor.
            try:
                while True:
                    now = time.time()
                    for s in SENSORS:
                        val = generate_value(s, now)
                        val = maybe_alarm_value(s["name"], val)
                        status = maybe_fault_status()

                        msg = {
                            "sensor": s["name"],
                            "value": float(val),
                            "ts": iso_ts(),
                            "status": status,   # "OK" or "FAULT"
                        }
                        line = json.dumps(msg) + "\n"
                        conn.sendall(line.encode("utf-8"))

                    time.sleep(0.1)  # 10 Hz cycles
            except (BrokenPipeError, ConnectionResetError):
                print("[SIM] Client disconnected.")
            except KeyboardInterrupt:
                print("[SIM] Stopping simulator.")
                return

if __name__ == "__main__":
    main()
