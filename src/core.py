import json
from typing import Optional, Dict, Any


def normalize_status(status: Any) -> str:
    """
    Normalize incoming status into one of:
    - "OK"
    - "FAULT"
    """
    s = str(status).strip().upper()
    return "OK" if s == "OK" else "FAULT"


def parse_sensor_message(line: str) -> Optional[Dict[str, Any]]:
    """
    Parse one newline JSON message from TCP stream.
    Expected sensor message keys:
      {"sensor": str, "value": number, "ts": str, "status": "OK"|"FAULT"|...}

    Returns:
      dict with normalized fields (status normalized to OK/FAULT), plus "_type":"data"
      OR None if invalid / not a sensor data message.
    """
    if not line:
        return None

    line = line.strip()
    if not line:
        return None

    try:
        msg = json.loads(line)
    except json.JSONDecodeError:
        return None

    # We only treat messages with the 4 mandatory keys as sensor data
    required = {"sensor", "value", "ts", "status"}
    if not required.issubset(msg.keys()):
        return None

    try:
        value = float(msg["value"])
    except (TypeError, ValueError):
        return None

    sensor = str(msg["sensor"]).strip()
    if not sensor:
        return None

    ts = str(msg["ts"]).strip()
    if not ts:
        return None

    status = normalize_status(msg["status"])

    return {
        "_type": "data",
        "sensor": sensor,
        "value": value,
        "ts": ts,
        "status": status,
    }


def evaluate_alarm(value: Optional[float], low: float, high: float, status: str) -> Optional[str]:
    """
    Return alarm type based on limits:
      - "LOW_LIMIT" if value < low
      - "HIGH_LIMIT" if value > high
      - None otherwise

    Business rules:
      - If status != "OK" => no alarm (sensor faulty/untrusted)
      - If value is None => no alarm
    """
    if value is None:
        return None
    if normalize_status(status) != "OK":
        return None

    if value < low:
        return "LOW_LIMIT"
    if value > high:
        return "HIGH_LIMIT"
    return None
