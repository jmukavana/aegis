from src.application.detection.detectors import (
    Detector,
    SloBurnDetector,
    ThresholdDetector,
)
from src.domain.detection.metrics_gateway import MetricsGateway
from src.domain.slo.burn_rate import Slo

# Phase 1 seed of what to watch. In a real deployment this comes from config/CRDs; here it is
# illustrative data proving the detector wiring works end to end.
SEED_SLOS = [
    Slo(name="checkout-availability", service="checkout", objective=0.999),
]


def build_detectors(metrics: MetricsGateway) -> list[Detector]:
    detectors: list[Detector] = [SloBurnDetector(slo, metrics) for slo in SEED_SLOS]
    detectors.append(
        ThresholdDetector(
            service="checkout",
            metric="container_memory_rss_ratio",
            threshold=0.9,
            failure_class="memory",
            metrics=metrics,
        )
    )
    return detectors
