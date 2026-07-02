import pytest

from src.domain.incident.value_objects import Severity
from src.domain.slo.burn_rate import BurnSpeed, Slo, evaluate_burn


def test_slo_rejects_impossible_objective():
    with pytest.raises(ValueError):
        Slo(name="x", service="checkout", objective=1.0)


def test_burn_rate_is_error_rate_over_budget():
    slo = Slo(name="checkout-availability", service="checkout", objective=0.99)
    # budget = 0.01; error_rate 0.10 => 10x burn
    assert slo.burn_rate(0.10) == pytest.approx(10.0)


def test_fast_burn_when_both_windows_exceed_threshold():
    slo = Slo(name="checkout", service="checkout", objective=0.999)
    # budget = 0.001; error_rate 0.02 => 20x burn (> 14.4) in both windows
    evaluation = evaluate_burn(slo, long_window_error_rate=0.02, short_window_error_rate=0.02)

    assert evaluation.speed is BurnSpeed.FAST
    assert evaluation.is_burning
    assert evaluation.to_severity() is Severity.CRITICAL


def test_no_alert_when_short_window_has_recovered():
    slo = Slo(name="checkout", service="checkout", objective=0.999)
    # long window still hot, but short window recovered => don't alert (guards flapping)
    evaluation = evaluate_burn(slo, long_window_error_rate=0.02, short_window_error_rate=0.0)

    assert evaluation.speed is BurnSpeed.NONE
    assert not evaluation.is_burning


def test_slow_burn_between_thresholds():
    slo = Slo(name="checkout", service="checkout", objective=0.99)
    # budget = 0.01; error_rate 0.05 => 5x burn (>= 3 slow, < 14.4 fast)
    evaluation = evaluate_burn(slo, long_window_error_rate=0.05, short_window_error_rate=0.05)

    assert evaluation.speed is BurnSpeed.SLOW
    assert evaluation.to_severity() is Severity.WARNING
