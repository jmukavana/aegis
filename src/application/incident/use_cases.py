from dataclasses import dataclass

from src.domain.incident.entities import Incident, Signal
from src.domain.incident.repository import IncidentRepository
from src.domain.incident.value_objects import (
    Fingerprint,
    IncidentId,
    IncidentStatus,
    Severity,
)


@dataclass(frozen=True)
class ObservedSignal:
    fingerprint: Fingerprint
    source: str
    severity: Severity
    summary: str


class RecordIncident:
    """Phase 0: open an incident by hand from a single observed signal.

    Phase 1 replaces the manual entry point with the detection + correlation engine, which
    will decide whether a signal opens a new incident or attaches to an existing one.
    """

    def __init__(self, incidents: IncidentRepository) -> None:
        self._incidents = incidents

    async def execute(self, title: str, signal: ObservedSignal) -> Incident:
        observed = Signal.observe(
            fingerprint=signal.fingerprint,
            source=signal.source,
            severity=signal.severity,
            summary=signal.summary,
        )
        incident = Incident.open(title=title, initial_signal=observed)
        await self._incidents.add(incident)
        return incident


class ListIncidents:
    def __init__(self, incidents: IncidentRepository) -> None:
        self._incidents = incidents

    async def execute(self) -> list[Incident]:
        return await self._incidents.list_all()


class TransitionIncident:
    def __init__(self, incidents: IncidentRepository) -> None:
        self._incidents = incidents

    async def execute(
        self, incident_id: IncidentId, status: IncidentStatus
    ) -> Incident | None:
        incident = await self._incidents.get_by_id(incident_id)
        if incident is None:
            return None
        updated = incident.transition_to(status)
        await self._incidents.update(updated)
        return updated
