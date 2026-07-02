from datetime import timedelta

from src.domain.detection.metrics_gateway import MetricsGateway
from src.domain.incident.entities import Incident
from src.domain.remediation.recovery import RecoveryProbe


class MetricsRecoveryProbe(RecoveryProbe):
    """Recovery judged from the actual error signal, not just silence.

    Reuses the Phase 1 `MetricsGateway`: an incident's service is recovered once its error ratio
    over the window has fallen to (or below) the threshold — i.e. the SLO has stopped burning.
    Stronger than `SignalQuietRecoveryProbe` (which only asks "did new signals stop arriving?")
    because it reads the health signal directly; both sit behind the same `RecoveryProbe` port,
    so the choice is a config swap.
    """

    def __init__(
        self,
        metrics: MetricsGateway,
        window: timedelta = timedelta(minutes=5),
        error_threshold: float = 0.01,
    ) -> None:
        self._metrics = metrics
        self._window = window
        self._error_threshold = error_threshold

    async def is_recovered(self, incident: Incident) -> bool:
        error_ratio = await self._metrics.error_ratio(
            incident.fingerprint.service, self._window
        )
        return error_ratio <= self._error_threshold
