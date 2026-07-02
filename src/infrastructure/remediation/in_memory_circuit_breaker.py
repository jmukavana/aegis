from collections import defaultdict
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from src.domain.incident.value_objects import IncidentId
from src.domain.remediation.circuit_breaker import CircuitBreaker


class InMemoryCircuitBreaker(CircuitBreaker):
    """In-process sliding-window breaker: at most `max_actions` per incident per `window`.

    Fine for a single-instance Phase 3; a durable/shared breaker (Postgres/Redis) replaces it
    behind the port when Aegis runs more than one replica.
    """

    def __init__(
        self,
        max_actions: int = 3,
        window: timedelta = timedelta(hours=1),
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._max_actions = max_actions
        self._window = window
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._actions: dict[IncidentId, list[datetime]] = defaultdict(list)

    def _recent(self, incident_id: IncidentId) -> list[datetime]:
        cutoff = self._clock() - self._window
        return [t for t in self._actions[incident_id] if t >= cutoff]

    async def allow(self, incident_id: IncidentId) -> bool:
        return len(self._recent(incident_id)) < self._max_actions

    async def record(self, incident_id: IncidentId) -> None:
        self._actions[incident_id] = self._recent(incident_id) + [self._clock()]
