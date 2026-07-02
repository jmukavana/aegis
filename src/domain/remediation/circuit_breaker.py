from abc import ABC, abstractmethod

from src.domain.incident.value_objects import IncidentId


class CircuitBreaker(ABC):
    """Caps how many remediations may run for one incident within a window (§3.5, §5.1).

    Stops remediation loops / flapping: if too many actions fire for the same incident in the
    window, `allow` returns False and the caller escalates to a human instead of acting again.
    """

    @abstractmethod
    async def allow(self, incident_id: IncidentId) -> bool: ...

    @abstractmethod
    async def record(self, incident_id: IncidentId) -> None: ...
