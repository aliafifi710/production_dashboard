import json
import random
import socket
import time
import select
import os
import sys
from datetime import datetime


HOST = "127.0.0.1"   # Dashboard (server)
PORT = 9000

SENSORS = [
    {"name": "Temp_C", "base": 25.0, "noise": 0.4},
    {"name": "Pressure_bar", "base": 1.7, "noise": 0.05},
    {"name": "Vibration_mm_s", "base": 2.0, "noise": 0.3},
    {"name": "Speed_rpm", "base": 1200.0, "noise": 25.0},
    {"name": "Optical_count", "base": 60.0, "noise": 6.0},
]

PAUSED = False


def iso_ts() -> str:
    return datetime.now().isoformat(timespec="milliseconds")


def send_json(sock: socket.socket, obj: dict):
    sock.sendall((json.dumps(obj) + "\n").encode("utf-8"))


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


def send_snapshot(sock: socket.socket):
    for s in SENSORS:
        val = maybe_alarm_value(generate_value(s))
        status = maybe_fault_status()
        msg = {"sensor": s["name"], "value": float(val), "ts": iso_ts(), "status": status}
        send_json(sock, msg)


def send_detailed_snapshot(sock: socket.socket):
    sensors_payload = []
    for s in SENSORS:
        val = maybe_alarm_value(generate_value(s))
        status = maybe_fault_status()
        sensors_payload.append({
            "sensor": s["name"],
            "value": float(val),
            "status": status,
            "base": s["base"],
            "noise": s["noise"],
        })

    send_json(sock, {
        "_type": "snapshot",
        "ts": iso_ts(),
        "sensors": sensors_payload
    })


def handle_command(sock: socket.socket, cmd: dict):
    global PAUSED
    name = cmd.get("cmd", "")

    if name == "SNAPSHOT_DETAIL":
        send_json(sock, {"_type": "log", "ts": iso_ts(), "message": "Detailed snapshot requested"})
        send_detailed_snapshot(sock)

    elif name == "CLEAR_ALARMS":
        # simulator doesn't store alarms, but acknowledge for traceability
        send_json(sock, {"_type": "log", "ts": iso_ts(), "message": "CLEAR_ALARMS received (acknowledged)"})

    elif name == "RESTART_SIM":
        send_json(sock, {"_type": "log", "ts": iso_ts(), "message": "Restart command received. Restarting simulator..."})
        try:
            time.sleep(0.1)  # give time to flush log
        except Exception:
            pass
        os.execv(sys.executable, [sys.executable] + sys.argv)

    elif name == "PAUSE":
        PAUSED = True
        send_json(sock, {"_type": "log", "ts": iso_ts(), "message": "Streaming paused"})

    elif name == "RESUME":
        PAUSED = False
        send_json(sock, {"_type": "log", "ts": iso_ts(), "message": "Streaming resumed"})

    else:
        send_json(sock, {"_type": "log", "ts": iso_ts(), "message": f"Unknown command: {name}"})


def run_client():
    rx_buf = ""

    while True:
        try:
            print(f"[SIM] Connecting to dashboard at {HOST}:{PORT} ...")
            with socket.create_connection((HOST, PORT), timeout=5) as sock:
                sock.setblocking(False)
                print("[SIM] Connected. Streaming...")

                send_json(sock, {"_type": "log", "ts": iso_ts(), "message": "Simulator online"})

                last_send = 0.0

                while True:
                    # 1) check incoming commands (non-blocking)
                    rlist, _, _ = select.select([sock], [], [], 0.05)
                    if rlist:
                        try:
                            data = sock.recv(4096)
                            if not data:
                                raise ConnectionError("Disconnected")
                            rx_buf += data.decode("utf-8", errors="ignore")

                            while "\n" in rx_buf:
                                line, rx_buf = rx_buf.split("\n", 1)
                                line = line.strip()
                                if not line:
                                    continue
                                try:
                                    cmd = json.loads(line)
                                    if cmd.get("_type") == "cmd":
                                        handle_command(sock, cmd)
                                except json.JSONDecodeError:
                                    continue

                        except BlockingIOError:
                            pass

                    # 2) send periodic data
                    now = time.time()
                    if not PAUSED and (now - last_send) >= 0.1:
                        for s in SENSORS:
                            val = maybe_alarm_value(generate_value(s))
                            status = maybe_fault_status()
                            msg = {"sensor": s["name"], "value": float(val), "ts": iso_ts(), "status": status}
                            send_json(sock, msg)
                        last_send = now

        except (ConnectionRefusedError, TimeoutError, OSError, ConnectionError) as e:
            print(f"[SIM] Connection failed/dropped: {e}. Retrying in 1s...")
            time.sleep(1.0)
        except KeyboardInterrupt:
            print("\n[SIM] Stopped by user (Ctrl+C).")
            return


if __name__ == "__main__":
    run_client()
