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


def test_parse_invalid_value():
    assert parse_sensor_message('{"sensor":"Temp_C","value":"NaNxx","ts":"t","status":"OK"}') is None
    

def test_parse_unknown_status_becomes_fault():
    msg = parse_sensor_message('{"sensor":"Temp_C","value":1,"ts":"t","status":"UNKNOWN"}')
    assert msg is not None
    assert msg["status"] == "FAULT"
