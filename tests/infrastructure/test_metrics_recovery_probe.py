from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from src.domain.incident.entities import Incident, Signal
from src.domain.incident.value_objects import Fingerprint, Severity, SignalId
from src.infrastructure.remediation.metrics_recovery_probe import MetricsRecoveryProbe
from tests.fakes import FakeMetricsGateway


def _incident(service: str) -> Incident:
    signal = Signal(
        id=SignalId(uuid4()),
        fingerprint=Fingerprint(service, "slo_burn", "prod"),
        source="slo",
        severity=Severity.CRITICAL,
        summary="burning",
        observed_at=datetime(2026, 7, 2, tzinfo=timezone.utc),
    )
    return Incident.open(title="x", initial_signal=signal)


@pytest.mark.asyncio
async def test_recovered_when_error_ratio_below_threshold():
    metrics = FakeMetricsGateway(error_ratios={"checkout": 0.002})
    probe = MetricsRecoveryProbe(metrics, timedelta(minutes=5), error_threshold=0.01)
    assert await probe.is_recovered(_incident("checkout")) is True


@pytest.mark.asyncio
async def test_not_recovered_while_error_ratio_above_threshold():
    metrics = FakeMetricsGateway(error_ratios={"checkout": 0.08})
    probe = MetricsRecoveryProbe(metrics, timedelta(minutes=5), error_threshold=0.01)
    assert await probe.is_recovered(_incident("checkout")) is False


@pytest.mark.asyncio
async def test_service_with_no_error_series_is_recovered():
    # No data for the service -> error_ratio 0.0 -> recovered (nothing is failing).
    probe = MetricsRecoveryProbe(FakeMetricsGateway(), timedelta(minutes=5))
    assert await probe.is_recovered(_incident("unknown")) is True
