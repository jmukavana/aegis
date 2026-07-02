from datetime import datetime, timedelta

from src.domain.incident.entities import Incident
from src.domain.incident.repository import IncidentRepository

DEFAULT_RECOVERY_WINDOW = timedelta(minutes=15)


class ResolveStaleIncidents:
    """Auto-close incidents whose signals stopped recurring (ARCHITECTURE.md §3.6).

    Read-only w.r.t. the cluster: this only transitions incident state. `now` is injected so
    the reconcile is deterministic and testable; a scheduler passes the wall clock in prod.
    """

    def __init__(
        self,
        incidents: IncidentRepository,
        recovery_window: timedelta = DEFAULT_RECOVERY_WINDOW,
    ) -> None:
        self._incidents = incidents
        self._recovery_window = recovery_window

    async def execute(self, now: datetime) -> list[Incident]:
        resolved: list[Incident] = []
        for incident in await self._incidents.list_open():
            if now - incident.last_activity_at >= self._recovery_window:
                updated = incident.resolve()
                await self._incidents.update(updated)
                resolved.append(updated)
        return resolved
