import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core import evaluate_alarm


def test_inside_limits_no_alarm():
    assert evaluate_alarm(5.0, low=0.0, high=10.0, status="OK") is None


def test_low_limit_alarm():
    assert evaluate_alarm(-1.0, low=0.0, high=10.0, status="OK") == "LOW_LIMIT"


def test_high_limit_alarm():
    assert evaluate_alarm(11.0, low=0.0, high=10.0, status="OK") == "HIGH_LIMIT"


def test_no_alarm_if_fault():
    assert evaluate_alarm(-1.0, low=0.0, high=10.0, status="FAULT") is None
