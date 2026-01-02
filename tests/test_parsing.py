import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core import parse_sensor_message


def test_parse_valid_message():
    line = '{"sensor":"Temp_C","value":25.5,"ts":"2026-01-01T12:00:00.000","status":"ok"}'
    msg = parse_sensor_message(line)
    assert msg is not None
    assert msg["_type"] == "data"
    assert msg["sensor"] == "Temp_C"
    assert msg["value"] == 25.5
    assert msg["status"] == "OK"


def test_parse_invalid_json():
    assert parse_sensor_message("{bad json") is None


def test_parse_missing_fields():
    assert parse_sensor_message('{"sensor":"Temp_C","value":25.5}') is None
