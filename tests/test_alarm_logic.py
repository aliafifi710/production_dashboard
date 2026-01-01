from src.core import evaluate_alarm


def test_alarm_none_inside_range():
    assert evaluate_alarm(5.0, low=0.0, high=10.0, status="OK") is None


def test_alarm_low_limit():
    assert evaluate_alarm(-1.0, low=0.0, high=10.0, status="OK") == "LOW_LIMIT"


def test_alarm_high_limit():
    assert evaluate_alarm(11.0, low=0.0, high=10.0, status="OK") == "HIGH_LIMIT"


def test_no_alarm_if_fault():
    assert evaluate_alarm(-1.0, low=0.0, high=10.0, status="FAULT") is None


def test_no_alarm_if_none_value():
    assert evaluate_alarm(None, low=0.0, high=10.0, status="OK") is None
