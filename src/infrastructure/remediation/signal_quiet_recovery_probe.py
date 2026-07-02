from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from src.domain.incident.entities import Incident
from src.domain.remediation.recovery import RecoveryProbe


class SignalQuietRecoveryProbe(RecoveryProbe):
    """Recovery = the incident has gone quiet.

    An incident's `last_activity_at` advances every time a fresh signal correlates into it. If
    nothing has fired for at least `quiet_window`, the triggering failure has stopped recurring
    and we call it recovered; if a new signal arrived after the remediation, it hasn't. Needs no
    external metrics backend — it reads the incident's own signal timeline — so it works in any
    environment (a metrics/SLO-burn probe is an alternative adapter behind the same port).
    """

    def __init__(
        self,
        quiet_window: timedelta,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._quiet_window = quiet_window
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    async def is_recovered(self, incident: Incident) -> bool:
        return self._clock() - incident.last_activity_at >= self._quiet_window
