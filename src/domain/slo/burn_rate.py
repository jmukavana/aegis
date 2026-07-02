from dataclasses import dataclass
from enum import Enum

from src.domain.incident.value_objects import Severity


class BurnSpeed(str, Enum):
    NONE = "none"
    SLOW = "slow"
    FAST = "fast"


@dataclass(frozen=True)
class Slo:
    """A service-level objective. `objective` is the target success ratio (e.g. 0.999)."""

    name: str
    service: str
    objective: float

    def __post_init__(self) -> None:
        if not 0.0 < self.objective < 1.0:
            raise ValueError(f"objective must be in (0, 1), got {self.objective}")

    @property
    def error_budget(self) -> float:
        return 1.0 - self.objective

    def burn_rate(self, error_rate: float) -> float:
        """How fast the error budget is being consumed relative to its steady allowance.

        burn_rate = 1.0 means the budget is spent exactly on schedule; 14.4 means it would be
        exhausted in ~1/14.4 of the SLO window.
        """
        return error_rate / self.error_budget


# Google SRE multi-window multi-burn-rate thresholds. A tier fires only when BOTH its long
# and short windows exceed the burn-rate threshold (the short window guards against alerting
# on an already-recovered spike). See ARCHITECTURE.md §3.6.
FAST_BURN_THRESHOLD = 14.4  # ~2% of a 30d budget in 1h
SLOW_BURN_THRESHOLD = 3.0  # ~10% of a 30d budget in 6h


@dataclass(frozen=True)
class BurnEvaluation:
    speed: BurnSpeed
    long_window_burn: float
    short_window_burn: float

    @property
    def is_burning(self) -> bool:
        return self.speed is not BurnSpeed.NONE

    def to_severity(self) -> Severity:
        return {
            BurnSpeed.FAST: Severity.CRITICAL,
            BurnSpeed.SLOW: Severity.WARNING,
            BurnSpeed.NONE: Severity.INFO,
        }[self.speed]


def evaluate_burn(
    slo: Slo, long_window_error_rate: float, short_window_error_rate: float
) -> BurnEvaluation:
    """Classify budget burn from a (long, short) window error-rate pair.

    Pure function — the metrics-query side (fetching the two error rates) is a Phase 1
    infrastructure concern behind a MetricsGateway port, kept out of this domain logic.
    """
    long_burn = slo.burn_rate(long_window_error_rate)
    short_burn = slo.burn_rate(short_window_error_rate)

    if long_burn >= FAST_BURN_THRESHOLD and short_burn >= FAST_BURN_THRESHOLD:
        speed = BurnSpeed.FAST
    elif long_burn >= SLOW_BURN_THRESHOLD and short_burn >= SLOW_BURN_THRESHOLD:
        speed = BurnSpeed.SLOW
    else:
        speed = BurnSpeed.NONE

    return BurnEvaluation(
        speed=speed, long_window_burn=long_burn, short_window_burn=short_burn
    )
