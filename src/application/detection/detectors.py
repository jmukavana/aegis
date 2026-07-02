from datetime import timedelta
from typing import Protocol

from src.application.incident.use_cases import ObservedSignal
from src.domain.detection.metrics_gateway import MetricsGateway
from src.domain.incident.value_objects import Fingerprint, Severity
from src.domain.slo.burn_rate import Slo, evaluate_burn


class Detector(Protocol):
    """Emits candidate signals. Never mutates anything (read-only, Phase 1)."""

    async def detect(self) -> list[ObservedSignal]: ...


class SloBurnDetector:
    """Emits a signal when an SLO's error budget is burning (multi-window; see domain/slo)."""

    def __init__(
        self,
        slo: Slo,
        metrics: MetricsGateway,
        long_window: timedelta = timedelta(hours=1),
        short_window: timedelta = timedelta(minutes=5),
    ) -> None:
        self._slo = slo
        self._metrics = metrics
        self._long_window = long_window
        self._short_window = short_window

    async def detect(self) -> list[ObservedSignal]:
        long_rate = await self._metrics.error_ratio(self._slo.service, self._long_window)
        short_rate = await self._metrics.error_ratio(
            self._slo.service, self._short_window
        )
        evaluation = evaluate_burn(self._slo, long_rate, short_rate)
        if not evaluation.is_burning:
            return []

        return [
            ObservedSignal(
                fingerprint=Fingerprint(self._slo.service, "slo_burn", self._slo.name),
                source="slo",
                severity=evaluation.to_severity(),
                summary=(
                    f"{self._slo.name} burning {evaluation.speed.value} "
                    f"(long {evaluation.long_window_burn:.1f}x, "
                    f"short {evaluation.short_window_burn:.1f}x)"
                ),
            )
        ]


class ThresholdDetector:
    """Emits a signal when a named gauge for a service exceeds a threshold."""

    def __init__(
        self,
        service: str,
        metric: str,
        threshold: float,
        failure_class: str,
        metrics: MetricsGateway,
        severity: Severity = Severity.WARNING,
    ) -> None:
        self._service = service
        self._metric = metric
        self._threshold = threshold
        self._failure_class = failure_class
        self._metrics = metrics
        self._severity = severity

    async def detect(self) -> list[ObservedSignal]:
        value = await self._metrics.gauge(self._service, self._metric)
        if value is None or value < self._threshold:
            return []

        return [
            ObservedSignal(
                fingerprint=Fingerprint(
                    self._service, self._failure_class, self._metric
                ),
                source="threshold",
                severity=self._severity,
                summary=f"{self._metric}={value:.3g} exceeds {self._threshold:.3g}",
            )
        ]
