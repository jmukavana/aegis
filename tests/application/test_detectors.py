import pytest

from src.application.correlation.use_cases import CorrelateSignal
from src.application.detection.detectors import SloBurnDetector, ThresholdDetector
from src.application.detection.run_cycle import RunDetectionCycle
from src.domain.incident.value_objects import Severity
from src.domain.slo.burn_rate import Slo
from tests.fakes import (
    FakeChangeLog,
    FakeMetricsGateway,
    FakeServiceGraph,
    InMemoryIncidentRepository,
)


@pytest.mark.asyncio
async def test_slo_burn_detector_emits_signal_when_burning():
    slo = Slo(name="checkout-availability", service="checkout", objective=0.999)
    gateway = FakeMetricsGateway(error_ratios={"checkout": 0.02})  # 20x burn

    signals = await SloBurnDetector(slo, gateway).detect()

    assert len(signals) == 1
    assert signals[0].fingerprint.failure_class == "slo_burn"
    assert signals[0].severity is Severity.CRITICAL


@pytest.mark.asyncio
async def test_slo_burn_detector_silent_when_healthy():
    slo = Slo(name="checkout-availability", service="checkout", objective=0.999)
    gateway = FakeMetricsGateway(error_ratios={"checkout": 0.0})

    assert await SloBurnDetector(slo, gateway).detect() == []


@pytest.mark.asyncio
async def test_threshold_detector_emits_when_over_threshold():
    gateway = FakeMetricsGateway(
        gauges={("checkout", "container_memory_rss_ratio"): 0.95}
    )
    detector = ThresholdDetector(
        service="checkout",
        metric="container_memory_rss_ratio",
        threshold=0.9,
        failure_class="memory",
        metrics=gateway,
    )

    signals = await detector.detect()

    assert len(signals) == 1
    assert signals[0].fingerprint.failure_class == "memory"


@pytest.mark.asyncio
async def test_threshold_detector_silent_below_threshold_or_absent():
    below = ThresholdDetector(
        "checkout", "container_memory_rss_ratio", 0.9, "memory",
        FakeMetricsGateway(gauges={("checkout", "container_memory_rss_ratio"): 0.5}),
    )
    absent = ThresholdDetector(
        "checkout", "container_memory_rss_ratio", 0.9, "memory", FakeMetricsGateway()
    )

    assert await below.detect() == []
    assert await absent.detect() == []


@pytest.mark.asyncio
async def test_detection_cycle_turns_detector_output_into_incidents():
    slo = Slo(name="checkout-availability", service="checkout", objective=0.999)
    gateway = FakeMetricsGateway(error_ratios={"checkout": 0.02})
    repo = InMemoryIncidentRepository()
    correlate = CorrelateSignal(repo, FakeServiceGraph(), FakeChangeLog())

    results = await RunDetectionCycle([SloBurnDetector(slo, gateway)], correlate).execute()

    assert len(results) == 1
    assert results[0].newly_opened is True
    assert len(await repo.list_open()) == 1
