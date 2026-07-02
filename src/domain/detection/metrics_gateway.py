from abc import ABC, abstractmethod
from datetime import timedelta


class MetricsGateway(ABC):
    """Port over the metrics backend (Prometheus in Phase 1).

    Kept semantic — the detectors ask for an *error ratio* or a *gauge*, not PromQL — so the
    query language stays isolated in the infrastructure adapter (a boundary we own).
    """

    @abstractmethod
    async def error_ratio(self, service: str, window: timedelta) -> float:
        """Fraction of failed requests for `service` over `window`, in [0, 1]."""

    @abstractmethod
    async def gauge(self, service: str, metric: str) -> float | None:
        """Latest value of a named gauge for `service`, or None if the series is absent."""
